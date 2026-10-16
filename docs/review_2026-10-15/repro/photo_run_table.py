"""One table row per ``evals.predict_photos`` run, for EVAL_REPORT sections 8, 11 and 12.

Reads what the runner and the harness wrote, nothing else::

    python docs/review_2026-10-15/repro/photo_run_table.py \\
        ~/basis-eval-data/predictions/vision_v1_gpt41 \\
        ~/basis-eval-data/predictions/vision_v1_fable51 ...

Each run directory must hold ``run.json``, ``photo_raw.jsonl``, ``photo_components.json`` and
``eval.md`` (``evals.run --predictions <dir> --report <dir>/eval.md``). Columns:

* room accuracy and pass^3: ``photo_rooms`` rows of ``eval.md`` (repeat 1 scored; pass^3 is
  the share of photos right on all three repeats);
* component P / R / F1: ``photo_components`` rows of ``eval.md`` (repeat 1; ``other`` excluded);
* label set identical across repeats: photos whose sorted label list (``other`` included, as
  the runner writes it) is the same on every repeat, from ``photo_components.json``; this is
  the method behind the "identical across 3 repeats" columns of sections 8 and 11 (26, 35,
  42 and 31 of 52 for the gpt-4.1 runs reproduce);
* cost per 52 photos: ``run.json`` ``spent_usd`` divided by the repeats (a merged run sums
  its sources' spend);
* seconds per photo: the mean of the raw records' ``seconds`` (a merged run sums its sources'
  seconds, so divide by the number of calls for the parallel figure).
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path


def metric(report: str, section: str, name: str) -> float | None:
    block = report.split(f"## {section} ", 1)
    if len(block) < 2:
        return None
    body = block[1].split("\n## ", 1)[0]
    match = re.search(rf"^\| {re.escape(name)} \| ([0-9.]+) \|", body, re.MULTILINE)
    return float(match.group(1)) if match else None


def row(directory: Path) -> str:
    run = json.loads((directory / "run.json").read_text())
    report = (directory / "eval.md").read_text()
    components = json.loads((directory / "photo_components.json").read_text())

    def repeats(value: object) -> list[object]:
        return value["repeats"] if isinstance(value, dict) else [value]

    identical = sum(1 for v in components.values() if len({json.dumps(r) for r in repeats(v)}) == 1)
    records = [
        json.loads(line) for line in (directory / "photo_raw.jsonl").read_text().splitlines()
    ]
    seconds = [r["seconds"] for r in records if "seconds" in r]
    n_repeats = int(run.get("repeats") or 1)
    pass3 = metric(report, "photo_rooms", "pass^3")

    def fmt(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2f}"

    return (
        f"| `{directory.name}` | {fmt(metric(report, 'photo_rooms', 'accuracy'))} | {fmt(pass3)} | "
        f"{fmt(metric(report, 'photo_components', 'precision'))} / "
        f"{fmt(metric(report, 'photo_components', 'recall'))} / "
        f"{fmt(metric(report, 'photo_components', 'f1'))} | "
        f"{identical} of {len(components)} | "
        f"${run['spent_usd'] / n_repeats:.2f} | {statistics.mean(seconds):.1f} |"
    )


def main(argv: list[str]) -> int:
    print(
        "| Run | Room accuracy | Room pass^3 | Component P / R / F1 | "
        "Label set identical across repeats | Cost per 52 photos | Seconds per photo |"
    )
    print("|---|---|---|---|---|---|---|")
    for arg in argv:
        print(row(Path(arg).expanduser()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
