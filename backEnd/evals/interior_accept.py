"""``python -m evals.interior_accept``: which rooms' truth is kept, by a rule fixed before any
method was scored (EVAL_REPORT 107.1), and the truth's repeatability.

A room is kept when its truth was measured (a closed outline, a ceiling) on two of its iPad scans
and the two floor areas agree within :data:`AREA_AGREEMENT_PCT`; a quantity of a kept room is
kept when the second scan gives it too and they agree within :data:`QUANTITY_AGREEMENT_PCT`
(the counters and the ceiling height are dropped from a room whose second scan disagrees, the
rest of the room stays). Writes ``accepted.json`` beside the truth files: the rooms, per room the
quantities kept, and every scan-to-scan difference (the truth's repeatability table).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from evals import interior_truth as it
from evals.interior_data import interiors_dir

AREA_AGREEMENT_PCT: Final = 5.0
QUANTITY_AGREEMENT_PCT: Final = 10.0


def accept(base: Path) -> dict[str, Any]:
    rooms: dict[str, Any] = {}
    diffs: dict[str, dict[str, float]] = {}
    refused: dict[str, str] = {}
    for path in sorted((base / "truth").glob("*.json")):
        truth = it.load(path)
        repeat_path = base / "truth_repeat" / path.name
        if truth.status != "ok":
            refused[truth.id] = f"first scan: {truth.reason}"
            continue
        if not repeat_path.exists():
            refused[truth.id] = "no second scan"
            continue
        repeat = it.load(repeat_path)
        if repeat.status != "ok":
            refused[truth.id] = f"second scan: {repeat.reason}"
            continue
        qa, qb = truth.quantities(), repeat.quantities()
        d = {k: 100 * (qb[k] - qa[k]) / qa[k] for k in qa if k in qb and qa[k]}
        diffs[truth.id] = d
        if abs(d.get("floor_area_sf", 1e9)) > AREA_AGREEMENT_PCT:
            refused[truth.id] = f"scans disagree on floor area ({d.get('floor_area_sf', 0):+.1f} %)"
            continue
        kept = [k for k in qa if k in d and abs(d[k]) <= QUANTITY_AGREEMENT_PCT]
        rooms[truth.id] = {"room_type": truth.room_type, "quantities": kept}
    summary = {}
    keys = sorted({k for d in diffs.values() for k in d})
    for k in keys:
        vals = [abs(d[k]) for rid, d in diffs.items() if k in d and rid in rooms]
        if vals:
            summary[k] = {
                "rooms": len(vals),
                "median_abs_pct": statistics.median(vals),
                "max_abs_pct": max(vals),
            }
    return {
        "rule": {
            "area_agreement_pct": AREA_AGREEMENT_PCT,
            "quantity_agreement_pct": QUANTITY_AGREEMENT_PCT,
        },
        "rooms": rooms,
        "refused": refused,
        "differences": diffs,
        "repeatability": summary,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.interior_accept", description=__doc__)
    parser.add_argument("--dir", type=Path, default=None)
    args = parser.parse_args(argv)
    base = args.dir or interiors_dir()
    result = accept(base)
    (base / "accepted.json").write_text(json.dumps(result, indent=1) + "\n")
    types: dict[str, int] = {}
    for r in result["rooms"].values():
        types[r["room_type"]] = types.get(r["room_type"], 0) + 1
    print("kept", len(result["rooms"]), types)
    for rid, why in result["refused"].items():
        print("refused", rid, why)
    for k, v in result["repeatability"].items():
        median, worst = v["median_abs_pct"], v["max_abs_pct"]
        print(f"{k}: {v['rooms']} rooms, median {median:.1f} %, max {worst:.1f} %")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
