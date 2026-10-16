"""``python -m evals.merge_photo_runs``: combine several ``evals.predict_photos`` runs into one.

Task decomposition (EVAL_REPORT section 11): instead of one call per photo that asks for
everything, one call per photo per task (``photo_countables``: find and count the countable
objects; ``photo_surfaces``: name the surfaces and materials), each with its own released
prompt. This tool merges the per-photo outputs of N such runs into one run's worth of
prediction files, so ``evals.run --predictions`` scores the combination exactly as it
scores a single run:

* ``photo_rooms.json``: the room type of one run (``--room-from N``, 1-based, default the
  first; every prompt answers that question, so take it from the pass that answers it best).
* ``photo_components.json``: per photo the union of the runs' label sets, per repeat.
* ``photo_raw.jsonl``: one merged record per (photo, repeat): components are the union by
  label with the largest count, material and attachment from the run that listed the label
  first; ``model`` and ``prompt`` name every source; ``seconds`` and the cost add up.
* ``run.json``: the sources, their prompts and the summed spend.

Usage (from ``backEnd/``)::

    python -m evals.merge_photo_runs --runs out/countables out/surfaces --out out/decomposed
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.merge_photo_runs", description=__doc__)
    parser.add_argument(
        "--runs", type=Path, nargs="+", required=True, help="predict_photos outputs"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--room-from",
        type=int,
        default=1,
        help="1-based index of the run in --runs whose room type is kept (default 1)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.room_from <= len(args.runs):
        parser.error("--room-from must index one of --runs")
    return args


def read_raw(run: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """``{(file, repeat): record}`` for the records that carry an analysis."""
    records: dict[tuple[str, int], dict[str, Any]] = {}
    path = run / "photo_raw.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "analysis" in record:
            records[(record["file"], int(record["repeat"]))] = record
    return records


def merge_components(parts: Sequence[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Union by label; the first run that lists a label gives its material and attachment,
    the count is the largest; ``other`` entries are kept from every run (they are distinct
    unlisted items, excluded from scoring anyway)."""
    merged: dict[str, dict[str, Any]] = {}
    others: list[dict[str, Any]] = []
    for components in parts:
        for component in components:
            label = component["label"]
            if label == "other":
                others.append(component)
            elif label in merged:
                merged[label]["count"] = max(int(merged[label]["count"]), int(component["count"]))
                merged[label]["confidence"] = max(
                    float(merged[label]["confidence"]), float(component["confidence"])
                )
            else:
                merged[label] = dict(component)
    return [*merged.values(), *others]


def merge_records(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    first = records[0]
    analysis = dict(first["analysis"])
    analysis["components"] = merge_components([r["analysis"]["components"] for r in records])
    return {
        "file": first["file"],
        "repeat": first["repeat"],
        "analysis": analysis,
        "provider": first.get("provider"),
        "model": sorted({str(r.get("model")) for r in records}),
        "attempts": sum(int(r.get("attempts", 1)) for r in records),
        "errors": [e for r in records for e in r.get("errors", [])],
        "seconds": round(sum(float(r.get("seconds", 0.0)) for r in records), 2),
        "image": first.get("image"),
        "sources": len(records),
    }


def merge_runs(runs: Sequence[Path], out: Path, room_from: int = 1) -> dict[str, Any]:
    """``room_from`` is the 1-based index of the run whose room type the merge keeps."""
    raws = [read_raw(run) for run in runs]
    keys = sorted(set(raws[0]))
    out.mkdir(parents=True, exist_ok=True)
    rooms: dict[str, list[str]] = {}
    components: dict[str, list[list[str]]] = {}
    with (out / "photo_raw.jsonl").open("w", encoding="utf-8") as raw_out:
        for key in keys:
            present = [raw[key] for raw in raws if key in raw]
            merged = merge_records(present)
            room_source = raws[room_from - 1].get(key)
            if room_source is not None:
                merged["analysis"]["room_type"] = room_source["analysis"]["room_type"]
            raw_out.write(json.dumps(merged, sort_keys=True) + "\n")
            file = key[0]
            rooms.setdefault(file, []).append(merged["analysis"]["room_type"])
            components.setdefault(file, []).append(
                sorted({c["label"] for c in merged["analysis"]["components"]})
            )

    def fold(values: list[Any]) -> Any:
        return values[0] if len(values) == 1 else {"repeats": values}

    (out / "photo_rooms.json").write_text(
        json.dumps({f: fold(v) for f, v in rooms.items()}, indent=1, sort_keys=True)
    )
    (out / "photo_components.json").write_text(
        json.dumps({f: fold(v) for f, v in components.items()}, indent=1, sort_keys=True)
    )
    sources = []
    spent = 0.0
    for run in runs:
        summary_path = run / "run.json"
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        sources.append(
            {
                "run": str(run),
                "prompt": summary.get("prompt"),
                "spent_usd": summary.get("spent_usd"),
            }
        )
        spent += float(summary.get("spent_usd") or 0.0)
    summary = {
        "merged_from": sources,
        "room_from": str(runs[room_from - 1]),
        "photos": len(rooms),
        "repeats": max((len(v) for v in rooms.values()), default=0),
        "spent_usd": round(spent, 6),
        "missing_in_some_run": sorted(
            {f"{k[0]}#{k[1]}" for raw in raws[1:] for k in keys if k not in raw}
        ),
    }
    (out / "run.json").write_text(json.dumps(summary, indent=1))
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = merge_runs(args.runs, args.out, room_from=args.room_from)
    print(
        f"wrote {args.out}: {summary['photos']} photos, {summary['repeats']} repeats",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
