"""One table row per run: ``python tooling/load/compare.py RUN_DIR... [--api]``.

Reads each run's ``summary.json`` (written by ``report.py``) and prints Markdown: the study
table (outcome, throughput, turnaround, worker and database signals, cost) or, with
``--api``, the API latency table (p50 / p95 / p99 per route group). Standard library only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

READS = ("GET /v1/studies/{id}", "GET /v1/studies/{id}/fields", "GET /v1/studies/{id}/rooms",
         "GET /v1/studies/{id}/assets", "GET /v1/studies/{id}/evidence",
         "GET /v1/studies/{id}/photos/{id}/objects", "GET /v1/studies")
POLL = "GET /v1/studies/{id}/workflow"


def _f(value: Optional[float], digits: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def study_row(name: str, s: Dict[str, Any]) -> str:
    st, smp = s["studies"], s.get("samples", {})
    costs = s.get("costs") or []
    held = sum(1 for c in costs if c["status"] == "failed")
    # Every failed attempt logs "Completing activity as failed" (the activity_results row
    # keeps only a key's last outcome).
    attempts = (s.get("logs", {}).get("worker") or {}).get("activity_failed", 0)
    usd = [float(c["llm_usd"]) + float(c["sam_usd"]) for c in costs if c["status"] == "completed"]
    procs = smp.get("procs", {})
    worker = procs.get("worker", {})
    s2s = smp.get("activity_schedule_to_start") or {}
    share = (s2s.get("share_le_ms") or {}).get("1000")
    return " | ".join([
        f"| {name}", str(st["studies"]), str(st["completed"]), str(held), str(attempts),
        _f(st["makespan_real_s"] and st["makespan_real_s"] / 60, 1),
        _f(st["studies_per_hour_real"], 1),
        _f(st["run_real_s"].get("50")), _f(st["run_real_s"].get("95")),
        _f(st["to_appraisal_review_real_s"].get("95")),
        _f(st["to_assets_review_real_s"].get("95")),
        f"{_f(smp.get('activity_slots_used_max'))} / {_f(smp.get('activity_backlog_max'))}",
        "-" if share is None else f"{share:.0%}",
        f"{_f(smp.get('pg_connections_max'))} / {_f(smp.get('pg_idle_in_transaction_max'))}",
        f"{_f(worker.get('cpu_p95'))} / {_f(worker.get('cpu_max'))}",
        _f(worker.get("rss_max_mb")),
        "-" if not usd else f"${sum(usd) / len(usd):.2f}",
    ]) + " |"


STUDY_HEADER = (
    "| Run | Studies | Completed | Held failed | Failed attempts | Makespan (min, real) "
    "| Studies/hour | Run p50 (s) | Run p95 (s) | Appraisal gate p95 (s) | Assets gate p95 (s) "
    "| Activity slots max / backlog max | Schedule-to-start <= 1 s | PG conns max / idle in tx "
    "| Worker CPU % p95 / max | Worker RSS MB | Cost / completed study |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def api_row(name: str, s: Dict[str, Any]) -> str:
    routes = {r["route"]: r for r in s.get("routes", [])}

    def group(names: tuple) -> Dict[str, Optional[float]]:
        rows = [routes[n] for n in names if n in routes]
        if not rows:
            return {"n": None, "p95": None, "p99": None, "fail": None}
        worst95 = max((r["p95"] or 0) for r in rows)
        worst99 = max((r["p99"] or 0) for r in rows)
        return {"n": sum(r["count"] for r in rows), "p95": worst95, "p99": worst99,
                "fail": sum(r["failures"] for r in rows)}

    mutations = tuple(n for n in routes if n.startswith("POST") or n.startswith("DELETE"))
    poll, reads, muts = group((POLL,)), group(READS), group(mutations)
    poll_row = routes.get(POLL, {})
    fails = [f for f in s.get("failures", []) if "429" not in f["error"]]
    rate = sum(f["count"] for f in s.get("failures", []) if "429" in f["error"])
    return " | ".join([
        f"| {name}", _f(poll["n"]), _f(poll_row.get("p50")), _f(poll["p95"]), _f(poll["p99"]),
        _f(reads["n"]), _f(reads["p95"]), _f(reads["p99"]),
        _f(muts["n"]), _f(muts["p95"]), _f(muts["p99"]),
        str(rate), str(sum(f["count"] for f in fails)),
    ]) + " |"


API_HEADER = (
    "| Run | Polls | Poll p50 ms | Poll p95 ms | Poll p99 ms | Reads | Reads worst p95 ms "
    "| Reads worst p99 ms | Mutations | Mutations worst p95 ms | Mutations worst p99 ms "
    "| 429s | Other errors |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--api", action="store_true")
    args = parser.parse_args()
    rows: List[str] = [API_HEADER if args.api else STUDY_HEADER]
    for run in args.runs:
        summary = json.loads((run / "summary.json").read_text())
        rows.append((api_row if args.api else study_row)(run.name, summary))
    print("\n".join(rows))


if __name__ == "__main__":
    main()
