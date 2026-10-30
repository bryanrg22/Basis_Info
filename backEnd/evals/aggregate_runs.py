"""``python -m evals.aggregate_runs``: self-consistency over the repeats of per-photo runs.

Self-consistency (Wang et al., 2022, arXiv 2203.11171) samples the same question several
times and keeps the answer most samples agree on. Here a "sample" is one repeat of one
photo in one ``evals.predict_photos`` (or ``evals.merge_photo_runs``) output; the samples of
every run in ``--runs`` are pooled, so one run with three repeats is three-sample
self-consistency and two runs from different models are an ensemble of six samples.

Per photo, with ``--rule``:

* counts, per label (a label a sample does not list counts 0 in that sample):

  - ``median``: the lower median (``statistics.median_low``), so the answer is a count some
    sample gave and an even number of samples does not round up;
  - ``vote``: the most frequent count (plurality, the original self-consistency rule); a
    tie goes to the lower median of the tied counts;
  - ``max``: the largest count (recall first: what any sample saw).

* label set: the same rule applied to each label's presence (0 or 1 per sample): ``median``
  and ``vote`` keep a label more than half of the samples list, ``max`` keeps the union.
* room type: the most frequent answer; a tie goes to the earliest sample (run order, then
  repeat order), so ``--runs`` lists the preferred room source first.

The output is one aggregated prediction per photo (score it with ``evals.run --repeats 1``
or any ``--repeats``; every repeat is the same answer): ``photo_counts.json``,
``photo_components.json``, ``photo_rooms.json`` and ``run.json`` (sources, rule, samples per
photo, summed spend), plus ``run.log`` and ``manifest.json``, the run record
(:mod:`evals.record`). An aggregate over N samples costs N single runs.

``--by-repeat`` aggregates repeat i of every run together instead of pooling them all, and
writes one aggregated answer per repeat (``{"repeats": [...]}``): an ensemble of k runs
scored over independent repeats, at k samples per answer (``evals.run --repeats N``).

Usage (from ``backEnd/``)::

    python -m evals.aggregate_runs --rule median --runs out/decomposed_sol --out out/sc_sol
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from evals import record
from evals.merge_photo_runs import source_manifest_id

__all__ = ["RULES", "aggregate", "aggregate_runs", "main", "parse_args", "samples"]


def _median(values: Sequence[int]) -> int:
    return int(statistics.median_low(values))


def _vote(values: Sequence[int]) -> int:
    tally = Counter(values)
    best = max(tally.values())
    return _median([v for v in values if tally[v] == best])


def _max(values: Sequence[int]) -> int:
    return max(values)


RULES: Final[dict[str, Callable[[Sequence[int]], int]]] = {
    "median": _median,
    "vote": _vote,
    "max": _max,
}


def aggregate(values: Sequence[int], rule: str) -> int:
    """One count from the samples' counts by ``rule`` (``median``, ``vote`` or ``max``)."""
    if not values:
        raise ValueError("nothing to aggregate")
    try:
        return RULES[rule](values)
    except KeyError:
        raise ValueError(f"unknown rule {rule!r}; expected one of {sorted(RULES)}") from None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.aggregate_runs", description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rule", choices=sorted(RULES), required=True)
    parser.add_argument(
        "--by-repeat",
        action="store_true",
        help="aggregate repeat i of every run together; one answer per repeat",
    )
    return parser.parse_args(argv)


def _per_repeat(value: Any) -> list[Any]:
    if isinstance(value, dict) and "repeats" in value:
        return list(value["repeats"])
    return [value]


def _load(run: Path, name: str) -> dict[str, Any]:
    path = run / f"{name}.json"
    if not path.exists():
        return {}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def samples(runs: Sequence[Path]) -> dict[str, list[dict[str, Any]]]:
    """``{file: [{"counts": {...}, "labels": [...], "room": str | None, "repeat": i}, ...]}``:
    every repeat of every run that has counts for the photo, in run then repeat order
    (``repeat`` is the 0-based repeat index within its run)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        counts = _load(run, "photo_counts")
        labels = _load(run, "photo_components")
        rooms = _load(run, "photo_rooms")
        for file, value in counts.items():
            per_counts = _per_repeat(value)
            per_labels = _per_repeat(labels.get(file)) if file in labels else []
            per_rooms = _per_repeat(rooms.get(file)) if file in rooms else []
            for i, sample_counts in enumerate(per_counts):
                if sample_counts is None:
                    continue
                out.setdefault(file, []).append(
                    {
                        "counts": {str(k): int(v) for k, v in sample_counts.items()},
                        "labels": list(per_labels[i]) if i < len(per_labels) else None,
                        "room": per_rooms[i] if i < len(per_rooms) else None,
                        "repeat": i,
                    }
                )
    return out


def _aggregate_photo(photo_samples: Sequence[Mapping[str, Any]], rule: str) -> dict[str, Any]:
    count_labels = sorted({k for s in photo_samples for k in s["counts"]})
    counts = {
        label: aggregate([int(s["counts"].get(label, 0)) for s in photo_samples], rule)
        for label in count_labels
    }
    with_labels = [s for s in photo_samples if s["labels"] is not None]
    label_set = sorted(
        label
        for label in {x for s in with_labels for x in s["labels"]}
        if aggregate([int(label in s["labels"]) for s in with_labels], rule)
    )
    rooms = [s["room"] for s in photo_samples if s["room"] is not None]
    room = None
    if rooms:
        tally = Counter(rooms)
        best = max(tally.values())
        room = next(r for r in rooms if tally[r] == best)
    return {
        "counts": {k: v for k, v in counts.items() if v > 0},
        "labels": label_set if with_labels else None,
        "room": room,
    }


def _fold(values: list[Any]) -> Any:
    return values[0] if len(values) == 1 else {"repeats": values}


def aggregate_runs(
    runs: Sequence[Path], out: Path, rule: str, by_repeat: bool = False
) -> dict[str, Any]:
    run = record.EvalRun.begin(out, harness=__name__)
    try:
        summary = _aggregate(runs, out, rule, by_repeat, run)
    except BaseException as exc:
        run.finish(None, status="error", exit_code=1, error=repr(exc))
        raise
    run.finish(summary)
    return summary


def _aggregate(
    runs: Sequence[Path], out: Path, rule: str, by_repeat: bool, run: record.EvalRun
) -> dict[str, Any]:
    for i, source in enumerate(runs, 1):
        run.input(f"run{i}", source)
    run.note(
        rule=rule,
        by_repeat=by_repeat,
        sources=[{"run": str(p), "manifest_id": source_manifest_id(p)} for p in runs],
    )
    pooled = samples(runs)
    results: dict[str, list[dict[str, Any]]] = {}
    for file, photo_samples in sorted(pooled.items()):
        if by_repeat:
            groups = [
                [s for s in photo_samples if s["repeat"] == i]
                for i in sorted({s["repeat"] for s in photo_samples})
            ]
        else:
            groups = [photo_samples]
        results[file] = [_aggregate_photo(g, rule) for g in groups]
    out_files = {
        "photo_counts": {f: _fold([r["counts"] for r in rs]) for f, rs in results.items()},
        "photo_components": {
            f: _fold([r["labels"] for r in rs])
            for f, rs in results.items()
            if all(r["labels"] is not None for r in rs)
        },
        "photo_rooms": {
            f: _fold([r["room"] for r in rs])
            for f, rs in results.items()
            if all(r["room"] is not None for r in rs)
        },
    }
    for name, data in out_files.items():
        (out / f"{name}.json").write_text(json.dumps(data, indent=1, sort_keys=True))
    spent = 0.0
    sources = []
    for source in runs:
        summary = _load(source, "run")
        spent += float(summary.get("spent_usd") or 0.0)
        sources.append({"run": str(source), "spent_usd": summary.get("spent_usd")})
    per_photo = sorted(
        {len(s) // (len({x["repeat"] for x in s}) if by_repeat else 1) for s in pooled.values()}
    )
    summary = {
        "aggregated_from": sources,
        "rule": rule,
        "by_repeat": by_repeat,
        "photos": len(results),
        "samples_per_photo": per_photo,
        "spent_usd": round(spent, 6),
    }
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = aggregate_runs(args.runs, args.out, args.rule, by_repeat=args.by_repeat)
    print(
        f"wrote {args.out}: {summary['photos']} photos, {summary['rule']} over "
        f"{summary['samples_per_photo']} samples each",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
