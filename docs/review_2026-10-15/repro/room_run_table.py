"""One table row per ``evals.predict_rooms`` run, for EVAL_REPORT section 16.

Run from ``backEnd/`` (it imports the harness, so the numbers are the harness's)::

    BASIS_ENV=test uv run --no-sync python ../docs/review_2026-10-15/repro/room_run_table.py \\
        ~/basis-eval-data/predictions/rooms16_base_v2b_gpt61sol ...

Options:

* ``--heuristic``: score each run's ``room_inventory.heuristic.json`` (the deterministic
  largest-per-photo-count merge, with the per-photo materials of ``rooms.json``) instead of the
  reconciled inventory;
* ``--single-photo-heuristic``: the reconciled inventory for rooms with several photos and the
  heuristic for one-photo rooms (what ``--single-photo-rooms per-photo`` produces), composed
  in memory from the same run;
* ``--clamp``: labels the label guide lists once per room or property (finishes, trim, floor
  types, roof, siding, gutter, landscaping) set to 1 where present;
* ``--rooms a,b``: score only these rooms (the multi-photo subset);
* ``--data-dir D``: the labels to score against (default ``$BASIS_EVAL_DATA_DIR`` or
  ``~/basis-eval-data``; a copy with the pre-2026-10-16 room labels gives the "before" rows).

Columns: count MAE, exact-count rate and signed bias over (room, label) pairs, rooms with the
exact inventory and label F1 over rooms, each the mean of the per-repeat values (the harness
reports repeat 1; with three repeats the mean is steadier); rooms whose inventory is identical
on every repeat; report rows evidenced of the photo-derivable rows on repeat 1 and pass^k
(evidenced on every repeat) from the harness's ``report_rows`` scorer; material present of the
rows that need one; ``run.json`` ``spent_usd`` and ``models_reported``.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
from pathlib import Path
from typing import Any

from evals import datasets as ds
from evals.metrics import inventory_metrics
from evals.run import _inventory, _repeats, evaluate

ONCE_PER_ROOM = frozenset(
    {
        "wall_finish",
        "ceiling_finish",
        "wainscoting",
        "baseboard",
        "crown_molding",
        "carpet",
        "hardwood_floor",
        "vinyl_flooring",
        "laminate_flooring",
        "roof",
        "siding",
        "gutter",
        "landscaping",
    }
)


def clamp(counts: dict[str, int]) -> dict[str, int]:
    return {k: (1 if k in ONCE_PER_ROOM and v > 0 else v) for k, v in counts.items()}


def prepared(run: Path, args: argparse.Namespace, work: Path) -> Path:
    """A directory the harness can score: the run itself, or a transformed copy."""
    if not (args.heuristic or args.single_photo_heuristic or args.clamp):
        return run
    rooms = json.loads((run / "rooms.json").read_text())
    heuristic = json.loads((run / "room_inventory.heuristic.json").read_text())
    single = {r["room_id"] for r in rooms if len(r["photos"]) == 1}
    by_room = {r["room_id"]: r for r in rooms}
    out = work / run.name
    out.mkdir(parents=True)
    raw: list[dict[str, Any]] = []
    if args.heuristic:
        saved: dict[str, Any] = heuristic
        raw = [
            {"room_id": r["room_id"], "repeat": 1, "objects": r["heuristic_objects"]} for r in rooms
        ]
    else:
        saved = json.loads((run / "room_inventory.json").read_text())
        for line in (run / "room_raw.jsonl").read_text().splitlines():
            record = json.loads(line)
            if args.single_photo_heuristic and record["room_id"] in single:
                record["objects"] = by_room[record["room_id"]]["heuristic_objects"]
            raw.append(record)
        if args.single_photo_heuristic:
            saved = {k: (heuristic[k] if k in single else v) for k, v in saved.items()}
    if args.clamp:
        saved = {
            k: {"repeats": [clamp(x) for x in v["repeats"]]} if "repeats" in v else clamp(v)
            for k, v in saved.items()
        }
    (out / "room_inventory.json").write_text(json.dumps(saved))
    (out / "room_raw.jsonl").write_text("".join(json.dumps(r) + "\n" for r in raw))
    for name in ("rooms.json", "run.json"):
        (out / name).write_text((run / name).read_text())
    return out


def row(run: Path, scored: Path, data: dict[str, Any], rooms: set[str] | None) -> str:
    labels = [r for r in data["room_inventory"].rooms if r.split == "dev"]
    if rooms is not None:
        labels = [r for r in labels if r.room_id in rooms]
    gold = {r.room_id: {k: v for k, v in r.counts().items() if k != "other"} for r in labels}
    saved = json.loads((scored / "room_inventory.json").read_text())
    repeats = max(
        (len(v["repeats"]) for v in saved.values() if isinstance(v, dict) and "repeats" in v),
        default=1,
    )
    runs: list[dict[str, dict[str, int]]] = [{} for _ in range(repeats)]
    for room in gold:
        for i, value in enumerate(_repeats(saved.get(room), repeats)):
            runs[i][room] = _inventory(value)
    metrics = [inventory_metrics(gold, r) for r in runs]

    def mean(attr: str) -> float:
        return statistics.mean(getattr(m, attr) for m in metrics)

    exact = statistics.mean(sum(s.exact for s in m.per_room.values()) for m in metrics)
    identical = sum(all(r[room] == runs[0][room] for r in runs) for room in gold)
    results = evaluate(data, split="dev", repeats=repeats, predictions=scored, corpus_dir=None)
    report = {r.metric: r for r in next(t for t in results if t.dataset == "report_rows").rows}
    coverage = report["row_coverage"]
    missed = [x["system"] for x in coverage.details["rows"] if x["outcome"] == "missed"]
    material = report.get("material_present_rate")
    stable = report.get(f"pass^{repeats}") if repeats > 1 else None
    summary = json.loads((run / "run.json").read_text())
    return (
        f"| {run.name} | {mean('count_mae'):.2f} | {mean('exact_count_rate'):.2f} | "
        f"{mean('signed_bias'):+.2f} | {exact:.1f} of {len(gold)} | {mean('label_f1'):.2f} | "
        f"{identical} of {len(gold)} | {round(coverage.value * coverage.n)} of {coverage.n}"
        f"{f' (pass^{repeats} {stable.value:.2f})' if stable else ''} | "
        f"{round(material.value * material.n) if material else '-'} of "
        f"{material.n if material else '-'} | ${summary.get('spent_usd', 0):.2f} "
        f"{', '.join(summary.get('models_reported', []))} | missed: {', '.join(missed) or 'none'} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--heuristic", action="store_true")
    parser.add_argument("--single-photo-heuristic", action="store_true")
    parser.add_argument("--clamp", action="store_true")
    parser.add_argument("--rooms", default=None)
    args = parser.parse_args()
    root = args.data_dir or Path(os.environ.get(ds.ENV_VAR, Path.home() / "basis-eval-data"))
    data = ds.load_available(root)
    rooms = set(args.rooms.split(",")) if args.rooms else None
    print(
        "| run | count MAE | exact-count rate | signed bias | rooms exact | label F1 | "
        "identical across repeats | report rows evidenced | material present | cost, models | "
        "rows missed |"
    )
    with tempfile.TemporaryDirectory() as work:
        for run in args.runs:
            print(row(run, prepared(run, args, Path(work)), data, rooms))


if __name__ == "__main__":
    main()
