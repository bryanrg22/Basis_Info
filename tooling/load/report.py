"""Summarize one load-test run directory: ``python tooling/load/report.py RUN_DIR [--scale S]``.

Reads what ``run_step.sh`` leaves in RUN_DIR (``studies.jsonl`` from the Locust users,
``locust_stats.csv``, ``samples.jsonl`` from the sampler, ``pg_statements.json``, the
process logs) and, with ``--pg``, the run's studies in the database (cost and calls per
study, failed activities). Prints Markdown; writes ``summary.json``.

Times in the "real" columns are divided by ``--scale`` (the run's ``LOAD_LATENCY_SCALE``):
the fake providers' sleeps scale exactly; the system's own work (HTTP, Postgres, Temporal)
does not, so at a scale below 1 the real-equivalent times are an upper bound on that part
(LOAD_TESTING.md section 4).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import percentile  # noqa: E402

COST_SQL = """
select s.id::text as study_id, s.status::text as status,
  (select coalesce(sum(cost_usd), 0) from llm_calls c where c.study_id = s.id) as llm_usd,
  (select coalesce(sum(cost_usd), 0) from photo_localizations l where l.study_id = s.id) as sam_usd,
  (select count(*) from llm_calls c where c.study_id = s.id) as calls,
  (select count(*) from assets a where a.study_id = s.id) as assets,
  (select count(*) from activity_results a join runs r on r.id = a.run_id
     where r.study_id = s.id and a.status = 'failed') as failed_activities
from studies s where s.id = any($1::uuid[])
"""
LOG_PATTERNS = {
    "pool_timeout": re.compile(r"QueuePool limit|pool.*timed out|TimeoutError: QueuePool"),
    "traceback": re.compile(r"^Traceback"),
    "error_lines": re.compile(r"\bERROR\b"),
    "api_429": re.compile(r"\" 429\b"),
    "provider_429": re.compile(r"returned 429"),
    "provider_5xx": re.compile(r"returned 5\d\d"),
    "pool_fallback": re.compile(r"this call falls back to"),
    "activity_failed": re.compile(r"Completing activity as failed|activity failed", re.I),
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarize_studies(rows: list[dict[str, Any]], scale: float) -> dict[str, Any]:
    done = [r for r in rows if r.get("execution") == "COMPLETED"]
    starts = [r["started_at"] for r in rows if "started_at" in r]
    ends = [r["finished_at"] for r in rows if "finished_at" in r]
    makespan = (max(ends) - min(starts)) if starts and ends else None
    run_s = [r["run_s"] for r in done if "run_s" in r]
    out: dict[str, Any] = {
        "studies": len(rows),
        "completed": len(done),
        "failed": len(rows) - len(done),
        "errors": sorted({r["error"] for r in rows if r.get("error")}),
        "makespan_s": makespan,
        "makespan_real_s": makespan / scale if makespan else None,
        "studies_per_hour_real": (len(done) / (makespan / scale) * 3600) if makespan else None,
        "run_real_s": {q: _div(percentile(run_s, q), scale) for q in (50, 95, 100)},
        "uploads_s": {q: percentile([r["uploads_s"] for r in rows if "uploads_s" in r], q)
                      for q in (50, 95, 100)},
        "rate_limited_429": sum(r.get("rate_limited", 0) for r in rows),
        "polls": sum(r.get("polls", 0) for r in rows),
    }
    for gate in ("appraisal_review", "rooms_review", "components_review", "assets_review"):
        vals = [r["gates"][gate] for r in done if gate in r.get("gates", {})]
        out[f"to_{gate}_real_s"] = {q: _div(percentile(vals, q), scale) for q in (50, 95, 100)}
    return out


def _div(value: float | None, scale: float) -> float | None:
    return None if value is None else round(value / scale, 1)


def summarize_routes(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    routes = []
    with path.open() as f:
        for row in csv.DictReader(f):
            if row["Type"] == "STUDY" or row["Name"] == "Aggregated":
                continue
            count, fails = int(row["Request Count"]), int(row["Failure Count"])
            routes.append({
                "route": row["Name"], "count": count, "failures": fails,
                "p50": _num(row["50%"]), "p95": _num(row["95%"]), "p99": _num(row["99%"]),
                "max": _round(_num(row["Max Response Time"])),
            })
    return sorted(routes, key=lambda r: -r["count"])


def _round(value: float | None) -> float | None:
    return None if value is None else round(value)


def _num(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def aggregate(path: Path) -> dict[str, Any]:
    """Locust's ``Aggregated`` row: requests, failures, requests per second, p95, p99."""
    if not path.exists():
        return {}
    with path.open() as f:
        for row in csv.DictReader(f):
            if row["Name"] == "Aggregated":
                return {"count": int(row["Request Count"]), "failures": int(row["Failure Count"]),
                        "rps": _num(row["Requests/s"]), "p95": _num(row["95%"]),
                        "p99": _num(row["99%"])}
    return {}


def failures(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as f:
        return [{"route": r["Name"], "error": r["Error"][:120], "count": int(r["Occurrences"])}
                for r in csv.DictReader(f)]


def _metric(sample: dict[str, Any], prefix: str) -> dict[str, float]:
    """The worker samples under ``prefix``, keys without an empty label set (``x{}``)."""
    return {
        _NOISE.sub("", k).replace(",}", "}").removesuffix("{}"): v
        for k, v in (sample.get("worker") or {}).items()
        if k.startswith(prefix)
    }


_NOISE = re.compile(r'(namespace|service_name|task_queue)="[^"]*",?')


def histogram_delta(first: dict[str, Any], last: dict[str, Any], name: str) -> dict[str, Any]:
    """Count, mean and bucket shares of a worker histogram over the run."""
    a, b = _metric(first, name), _metric(last, name)
    count = b.get(f"{name}_count", 0) - a.get(f"{name}_count", 0)
    total = b.get(f"{name}_sum", 0) - a.get(f"{name}_sum", 0)
    buckets = {}
    for key, value in b.items():
        m = re.search(r'le="([^"]+)"', key)
        if key.startswith(f"{name}_bucket") and m:
            buckets[m.group(1)] = value - a.get(key, 0)
    share = {le: round(n / count, 4) for le, n in buckets.items()} if count else {}
    return {"count": count, "mean_ms": round(total / count, 1) if count else None,
            "share_le_ms": share}


def execution_means(first: dict[str, Any], last: dict[str, Any]) -> dict[str, float]:
    """Mean execution time (s) per activity type over the run (including pool waits)."""
    name = "temporal_activity_execution_latency"
    a, b = _metric(first, name), _metric(last, name)
    out = {}
    for key, count in b.items():
        m = re.match(rf'{name}_count{{activity_type="([^"]+)"', key)
        if not m:
            continue
        n = count - a.get(key, 0)
        total_key = key.replace("_count{", "_sum{")
        total = b.get(total_key, 0) - a.get(total_key, 0)
        if n:
            out[m.group(1)] = round(total / n / 1000, 2)
    return out


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {}
    out: dict[str, Any] = {"samples": len(samples)}
    for kind in ("activity", "workflow"):
        backlog = [s["temporal"][kind]["backlog"] for s in samples if "temporal" in s]
        age = [s["temporal"][kind]["backlog_age_s"] for s in samples if "temporal" in s]
        out[f"{kind}_backlog_max"] = max(backlog, default=None)
        out[f"{kind}_backlog_age_max_s"] = max(age, default=None)
    slots = [v for s in samples for k, v in (s.get("worker") or {}).items()
             if k.startswith("temporal_worker_task_slots_used") and "ActivityWorker" in k
             and "Local" not in k]
    out["activity_slots_used_max"] = max(slots, default=None)
    wf_slots = [v for s in samples for k, v in (s.get("worker") or {}).items()
                if k.startswith("temporal_worker_task_slots_used") and "WorkflowWorker" in k]
    out["workflow_slots_used_max"] = max(wf_slots, default=None)
    with_worker = [s for s in samples if s.get("worker")]
    if len(with_worker) >= 2:
        first, last = with_worker[0], with_worker[-1]
        out["activity_schedule_to_start"] = histogram_delta(
            first, last, "temporal_activity_schedule_to_start_latency")
        out["workflow_task_schedule_to_start"] = histogram_delta(
            first, last, "temporal_workflow_task_schedule_to_start_latency")
        out["activity_execution_mean_s"] = execution_means(first, last)
    pg_total, idle_tx, lock_wait, oldest = [], [], [], []
    by_app: dict[str, int] = {}
    for s in samples:
        rows = s.get("pg", [])
        pg_total.append(sum(r["n"] for r in rows))
        idle_tx.append(sum(r["n"] for r in rows if r["state"] == "idle in transaction"))
        lock_wait.append(sum(r["waiting_lock"] for r in rows))
        oldest.append(max((r["oldest_xact_s"] for r in rows if r["state"] != "idle"), default=0))
        for r in rows:
            by_app[r["app"]] = max(by_app.get(r["app"], 0), sum(
                x["n"] for x in rows if x["app"] == r["app"]))
    out.update(pg_connections_max=max(pg_total), pg_connections_by_app_max=by_app,
               pg_idle_in_transaction_max=max(idle_tx), pg_waiting_lock_max=max(lock_wait),
               pg_oldest_xact_max_s=round(max(oldest), 1))
    procs: dict[str, dict[str, Any]] = {}
    for name in ("api", "worker", "relay"):
        cpu = [s["procs"][name]["cpu_pct"] for s in samples
               if (s.get("procs") or {}).get(name)]
        rss = [s["procs"][name]["rss_mb"] for s in samples if (s.get("procs") or {}).get(name)]
        if cpu:
            procs[name] = {"cpu_p50": percentile(cpu, 50), "cpu_p95": percentile(cpu, 95),
                           "cpu_max": max(cpu), "rss_max_mb": max(rss)}
    pg_cpu = [s["postgres_container"]["cpu_pct"] for s in samples if s.get("postgres_container")]
    if pg_cpu:
        procs["postgres"] = {"cpu_p50": percentile(pg_cpu, 50), "cpu_p95": percentile(pg_cpu, 95),
                             "cpu_max": max(pg_cpu)}
    out["procs"] = procs
    return out


def scan_logs(run_dir: Path) -> dict[str, dict[str, int]]:
    out = {}
    for name in ("worker", "api", "relay"):
        path = run_dir / f"{name}.log"
        if not path.exists():
            continue
        counts = dict.fromkeys(LOG_PATTERNS, 0)
        for line in path.read_text(errors="replace").splitlines():
            for key, pattern in LOG_PATTERNS.items():
                if pattern.search(line):
                    counts[key] += 1
        out[name] = counts
    return out


async def db_costs(dsn: str, ids: list[str]) -> list[dict[str, Any]]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        return [dict(r) for r in await conn.fetch(COST_SQL, ids)]
    finally:
        await conn.close()


def render(summary: dict[str, Any]) -> str:
    s, smp = summary["studies"], summary.get("samples", {})
    lines = [f"# Load run `{summary['run_dir']}` (scale {summary['scale']})", ""]
    lines += [
        f"- studies {s['studies']}: completed {s['completed']}, failed {s['failed']}"
        + (f" ({'; '.join(s['errors'])[:300]})" if s["errors"] else ""),
        f"- makespan {s['makespan_s'] and round(s['makespan_s'], 1)} s "
        f"(real-equivalent {s['makespan_real_s'] and round(s['makespan_real_s'])} s); "
        f"studies/hour (real) {s['studies_per_hour_real'] and round(s['studies_per_hour_real'], 2)}",
        f"- study run time real p50/p95/max {s['run_real_s']}",
        f"- to appraisal gate {s['to_appraisal_review_real_s']}, rooms gate "
        f"{s['to_rooms_review_real_s']}, assets gate {s['to_assets_review_real_s']}",
        f"- uploads (wall s) p50/p95/max {s['uploads_s']}; 429s seen {s['rate_limited_429']}",
    ]
    if summary.get("costs"):
        costs = summary["costs"]
        usd = [float(c["llm_usd"]) + float(c["sam_usd"]) for c in costs]
        lines.append(
            f"- cost per study (recorded) mean ${sum(usd) / len(usd):.2f}; calls mean "
            f"{sum(c['calls'] for c in costs) / len(costs):.0f}; failed activities "
            f"{sum(c['failed_activities'] for c in costs)}"
        )
    if smp:
        lines += [
            f"- Temporal backlog max: activity {smp['activity_backlog_max']} "
            f"(age {smp['activity_backlog_age_max_s']} s), workflow {smp['workflow_backlog_max']}",
            f"- activity slots used max {smp['activity_slots_used_max']} / 100; workflow slots "
            f"{smp['workflow_slots_used_max']}",
            f"- activity schedule-to-start {smp.get('activity_schedule_to_start')}",
            f"- workflow task schedule-to-start {smp.get('workflow_task_schedule_to_start')}",
            f"- activity execution mean s (real = / scale) {smp.get('activity_execution_mean_s')}",
            f"- Postgres connections max {smp['pg_connections_max']} {smp['pg_connections_by_app_max']}; "
            f"idle in transaction max {smp['pg_idle_in_transaction_max']}; waiting on locks max "
            f"{smp['pg_waiting_lock_max']}; oldest transaction {smp['pg_oldest_xact_max_s']} s",
            f"- processes {json.dumps(smp['procs'])}",
        ]
    lines.append(f"- logs {json.dumps(summary['logs'])}")
    if summary.get("aggregate"):
        lines.append(f"- all requests {json.dumps(summary['aggregate'])}")
    lines += ["", "| Route | Requests | Failures | p50 ms | p95 ms | p99 ms | max ms |",
              "|---|---|---|---|---|---|---|"]
    for r in summary["routes"]:
        lines.append(f"| `{r['route']}` | {r['count']} | {r['failures']} | {r['p50']} | "
                     f"{r['p95']} | {r['p99']} | {r['max']} |")
    if summary["failures"]:
        lines += ["", "| Failure | Route | Count |", "|---|---|---|"]
        lines += [f"| {f['error']} | `{f['route']}` | {f['count']} |" for f in summary["failures"]]
    if summary.get("statements"):
        lines += ["", "Top statements by total time:", "",
                  "| calls | total ms | mean ms | max ms | query |", "|---|---|---|---|---|"]
        for st in summary["statements"][:8]:
            q = st["query"][:110].replace("|", "\\|")
            lines.append(f"| {st['calls']} | {st['total_ms']} | {st['mean_ms']} | "
                         f"{st['max_ms']} | `{q}` |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--pg", default="postgresql://basis:basis@127.0.0.1:5561/basis")  # pragma: allowlist secret
    args = parser.parse_args()
    run_dir = args.run_dir
    studies = load_jsonl(run_dir / "studies.jsonl")
    summary: dict[str, Any] = {
        "run_dir": run_dir.name,
        "scale": args.scale,
        "settings": (run_dir / "settings.env").read_text().split()
        if (run_dir / "settings.env").exists() else [],
        "studies": summarize_studies(studies, args.scale),
        "routes": summarize_routes(run_dir / "locust_stats.csv"),
        "aggregate": aggregate(run_dir / "locust_stats.csv"),
        "failures": failures(run_dir / "locust_failures.csv"),
        "samples": summarize_samples(load_jsonl(run_dir / "samples.jsonl")),
        "logs": scan_logs(run_dir),
    }
    statements = run_dir / "pg_statements.json"
    if statements.exists():
        data = json.loads(statements.read_text())
        summary["statements"] = data if isinstance(data, list) else []
    ids = [r["study_id"] for r in studies if r.get("study_id")]
    if ids and args.pg:
        try:
            summary["costs"] = asyncio.run(db_costs(args.pg, ids))
        except OSError as exc:
            summary["costs_error"] = str(exc)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(render(summary))


if __name__ == "__main__":
    main()
