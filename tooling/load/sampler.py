"""Sample the system while a load test runs: ``python tooling/load/sampler.py RUN_DIR``.

Run with the backend's venv (asyncpg, psutil, temporalio). Every ``--interval`` seconds,
until SIGTERM/SIGINT, one JSON line goes to ``RUN_DIR/samples.jsonl``:

* ``temporal``: per task-queue type (activity, workflow) the server's
  ``approximate_backlog_count`` / ``approximate_backlog_age`` and add/dispatch rates
  (``DescribeTaskQueue`` with ``report_stats``);
* ``worker``: the worker's Prometheus metrics that size it (slots used/available per
  worker type, the schedule-to-start histograms' counts, sums and buckets, activity
  execution latency count and sum), raw, for ``report.py`` to difference;
* ``pg``: connections by application and state (``idle in transaction`` is a connection
  held by an open transaction), connections waiting on a lock, the oldest open
  transaction's age;
* ``procs``: CPU % and RSS of the API, worker and relay (pids from ``RUN_DIR/*.pid``),
  and of the Postgres container (``docker stats``, every ``--docker-every`` samples).

At exit it writes ``RUN_DIR/pg_statements.json``: the top statements by total time from
``pg_stat_statements`` (reset by ``backEnd/tests/load/setup_db.py`` before the run).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

import asyncpg
import psutil
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client

WORKER_METRICS = (
    "temporal_worker_task_slots_used",
    "temporal_worker_task_slots_available",
    "temporal_activity_schedule_to_start_latency",
    "temporal_workflow_task_schedule_to_start_latency",
    "temporal_activity_execution_latency_count",
    "temporal_activity_execution_latency_sum",
    "temporal_activity_execution_failed",
    "temporal_activity_task_received",
)
PG_SQL = """
select coalesce(application_name, '') as app, coalesce(state, '') as state, count(*) as n,
       count(*) filter (where wait_event_type = 'Lock') as waiting_lock,
       coalesce(max(extract(epoch from now() - xact_start)), 0) as oldest_xact_s
from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid()
group by 1, 2
"""
STATEMENTS_SQL = """
select calls, round(total_exec_time::numeric, 1) as total_ms,
       round(mean_exec_time::numeric, 2) as mean_ms, round(max_exec_time::numeric, 1) as max_ms,
       rows, left(regexp_replace(query, '\\s+', ' ', 'g'), 300) as query
from pg_stat_statements where dbid = (select oid from pg_database where datname = current_database())
order by total_exec_time desc limit 25
"""


def parse_metrics(text: str) -> dict[str, float]:
    """Selected Prometheus samples ``{name{labels}: value}`` (labels kept but trimmed)."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name = line.split("{", 1)[0].split(" ", 1)[0]
        if not any(name.startswith(m) for m in WORKER_METRICS):
            continue
        key, _, value = line.rpartition(" ")
        key = re.sub(r'(namespace|service_name|task_queue)="[^"]*",?', "", key)
        key = key.replace(",}", "}").removesuffix("{}")
        out[key] = float(value)
    return out


async def temporal_stats(client: Client, queue: str) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for kind, value in (("activity", TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
                        ("workflow", TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW)):
        response = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace="default", task_queue=TaskQueue(name=queue),
                task_queue_type=value, report_stats=True,
            )
        )
        s = response.stats
        stats[kind] = {
            "backlog": s.approximate_backlog_count,
            "backlog_age_s": s.approximate_backlog_age.ToTimedelta().total_seconds(),
            "add_rate": round(s.tasks_add_rate, 3),
            "dispatch_rate": round(s.tasks_dispatch_rate, 3),
            "pollers": len(response.pollers),
        }
    return stats


def docker_stats(container: str) -> dict[str, Any] | None:
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        row = json.loads(out)
        return {"cpu_pct": float(row["CPUPerc"].rstrip("%")), "mem": row["MemUsage"]}
    except (subprocess.SubprocessError, ValueError, KeyError):
        return None


def processes(run_dir: Path) -> dict[str, psutil.Process]:
    procs: dict[str, psutil.Process] = {}
    for pid_file in run_dir.glob("*.pid"):
        try:
            proc = psutil.Process(int(pid_file.read_text().strip()))
            proc.cpu_percent(None)  # prime
            procs[pid_file.stem] = proc
        except (psutil.Error, ValueError):
            continue
    return procs


def proc_stats(procs: dict[str, psutil.Process]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, proc in procs.items():
        try:
            with proc.oneshot():
                out[name] = {
                    "cpu_pct": proc.cpu_percent(None),
                    "rss_mb": round(proc.memory_info().rss / 2**20, 1),
                    "threads": proc.num_threads(),
                }
        except psutil.Error:
            out[name] = None
    return out


async def run(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    client = await Client.connect(args.temporal)
    pg = await asyncpg.connect(args.pg)
    procs = processes(run_dir)
    n = 0
    with (run_dir / "samples.jsonl").open("a") as out:
        while not stop.is_set():
            sample: dict[str, Any] = {"t": time.time()}
            try:
                sample["temporal"] = await temporal_stats(client, args.task_queue)
            except Exception as exc:  # keep sampling the rest
                sample["temporal_error"] = repr(exc)[:200]
            try:
                with urllib.request.urlopen(args.metrics_url, timeout=5) as r:
                    sample["worker"] = parse_metrics(r.read().decode())
            except OSError as exc:
                sample["worker_error"] = repr(exc)[:200]
            sample["pg"] = [dict(r) for r in await pg.fetch(PG_SQL)]
            if not procs:
                procs = processes(run_dir)
            sample["procs"] = proc_stats(procs)
            if args.postgres_container and n % args.docker_every == 0:
                sample["postgres_container"] = await asyncio.to_thread(
                    docker_stats, args.postgres_container
                )
            out.write(json.dumps(sample, default=float) + "\n")
            out.flush()
            n += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.interval)
            except TimeoutError:
                pass
    try:
        rows = [dict(r) for r in await pg.fetch(STATEMENTS_SQL)]
        (run_dir / "pg_statements.json").write_text(json.dumps(rows, indent=1, default=float))
    except asyncpg.PostgresError as exc:
        (run_dir / "pg_statements.json").write_text(json.dumps({"error": str(exc)}))
    await pg.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--pg", default="postgresql://basis:basis@127.0.0.1:5561/basis")  # pragma: allowlist secret
    parser.add_argument("--temporal", default="127.0.0.1:7261")
    parser.add_argument("--task-queue", default="basis-studies")
    parser.add_argument("--metrics-url", default="http://127.0.0.1:9471/metrics")
    parser.add_argument("--postgres-container", default="basis-load-postgres-1")
    parser.add_argument("--docker-every", type=int, default=3)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
