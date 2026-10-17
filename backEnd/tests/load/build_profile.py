"""Build a latency profile for the load-test worker from a live study's database rows.

``python tests/load/build_profile.py DSN STUDY_ID OUT.json [--azure-di-seconds S ...]``

Reads only timings and token counts, never content: per model call (``llm_calls``) its
activity, model, prompt family, latency and tokens; per SAM 3 prediction
(``photo_localizations``) its ``predict_seconds``. The output is what
:class:`load_fakes.LatencyModel` samples from (docs/perf/LOAD_TESTING.md section 3):

* ``llm[<family>@<model>]``: ``latency_ms`` (every measured call, sorted), the mean
  ``input_tokens`` / ``output_tokens`` (so a fake call is priced like the real one) and
  ``calls`` (how many the live study made, for the calibration table);
* ``localize``: ``predict_seconds`` per prediction;
* ``azure_di``: seconds per appraisal analysis, given on the command line because Azure DI
  calls are not recorded in the database (read them off the run's timeline).

The committed profile ``latency_profile_live_27_8.json`` was built from the live study of
EVAL_REPORT section 27.8 (the client package, 52 photos, the production defaults).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg

LLM_SQL = """
select activity, model, split_part(prompt_version, '/', 1) as family,
       latency_ms, input_tokens, output_tokens
from llm_calls where study_id = $1 and latency_ms is not null
"""
LOCALIZE_SQL = """
select predict_seconds from photo_localizations
where study_id = $1 and status = 'done' and predict_seconds is not null
"""


def build(
    llm_rows: list[dict[str, Any]],
    predict_seconds: list[float],
    azure_di_seconds: list[float],
    *,
    source: str,
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in llm_rows:
        groups.setdefault(f"{row['family']}@{row['model']}", []).append(row)
    llm = {
        key: {
            "activity": rows[0]["activity"],
            "calls": len(rows),
            "latency_ms": sorted(int(r["latency_ms"]) for r in rows),
            "input_tokens": round(sum(r["input_tokens"] for r in rows) / len(rows)),
            "output_tokens": round(sum(r["output_tokens"] for r in rows) / len(rows)),
        }
        for key, rows in sorted(groups.items())
    }
    return {
        "schema_version": 1,
        "source": source,
        "llm": llm,
        "localize": {"predict_seconds": sorted(round(s, 3) for s in predict_seconds)},
        "azure_di": {"seconds": sorted(azure_di_seconds)},
    }


async def _read(dsn: str, study_id: uuid.UUID) -> tuple[list[dict[str, Any]], list[float]]:
    conn = await asyncpg.connect(dsn)
    try:
        llm = [dict(r) for r in await conn.fetch(LLM_SQL, study_id)]
        loc = [float(r["predict_seconds"]) for r in await conn.fetch(LOCALIZE_SQL, study_id)]
    finally:
        await conn.close()
    return llm, loc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dsn")
    parser.add_argument("study_id", type=uuid.UUID)
    parser.add_argument("out", type=Path)
    parser.add_argument("--azure-di-seconds", type=float, nargs="+", default=[])
    parser.add_argument("--source", default="live study")
    args = parser.parse_args()
    llm, loc = asyncio.run(_read(args.dsn, args.study_id))
    profile = build(llm, loc, args.azure_di_seconds, source=args.source)
    args.out.write_text(json.dumps(profile, indent=1) + "\n")
    print(
        f"{args.out}: {sum(v['calls'] for v in profile['llm'].values())} calls in "
        f"{len(profile['llm'])} groups, {len(loc)} localizations"
    )


if __name__ == "__main__":
    main()
