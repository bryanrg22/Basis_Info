"""``python -m evals.compare_counts``: count systems against a baseline, paired over photos.

The statistic of EVAL_REPORT section 15: for each photo, the mean absolute count error over
its verified classes and the repeats (the ``photo_counts`` truth of ``evals.datasets``, the
verified boxes; ``door`` pools interior and exterior doors, as ``evals.run`` scores it), minus
the baseline's on the same photo; the mean of those differences over photos with a 95%
percentile interval from a paired bootstrap over photos (``--resamples``, fixed ``--seed``).
Photos are the unit because there is one document, so the harness's clustered SE is not
available. A photo missing from either system is left out of that comparison (and counted).

Also per system: count MAE, exact rate, signed bias, count precision and recall over
(photo, class, repeat) pairs, overall and per class, exactly as ``evals.run``'s
``score_photo_counts`` computes them.

Usage (from ``backEnd/``)::

    python -m evals.compare_counts --baseline P/counts_fused_sol_low_opus55_med_loo \\
        --system marks=OUT --system loop=OUT/loop_masked --repeats 3 --out report.md
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from evals import datasets as ds
from evals.run import _repeats, predicted_count

CLASSES: Final[tuple[str, ...]] = (
    "window",
    "door",
    "light_fixture",
    "outdoor_lighting",
    "electrical_outlet",
    "kitchen_cabinet",
)


@dataclass(frozen=True)
class Scores:
    mae: float
    exact: float
    bias: float
    precision: float
    recall: float
    n: int


def scores(pairs: Sequence[tuple[int, int]]) -> Scores:
    """The ``score_photo_counts`` metrics of ``(predicted, true)`` pairs."""
    if not pairs:
        return Scores(0.0, 0.0, 0.0, 0.0, 0.0, 0)
    errors = [abs(p - t) for p, t in pairs]
    tp = sum(min(p, t) for p, t in pairs)
    fp = sum(max(0, p - t) for p, t in pairs)
    fn = sum(max(0, t - p) for p, t in pairs)
    return Scores(
        mae=statistics.mean(errors),
        exact=sum(1 for e in errors if e == 0) / len(errors),
        bias=statistics.mean(p - t for p, t in pairs),
        precision=tp / (tp + fp) if tp + fp else 0.0,
        recall=tp / (tp + fn) if tp + fn else 0.0,
        n=len(pairs),
    )


def pairs_by_photo(
    truth: ds.PhotoCounts, saved: Mapping[str, Any], repeats: int, split: str = "dev"
) -> dict[str, list[tuple[str, int, int]]]:
    """``{file: [(class, predicted, true), ...]}`` over the repeats, for photos with a saved
    prediction in every repeat (a missing repeat drops the photo)."""
    out: dict[str, list[tuple[str, int, int]]] = {}
    for photo in truth.photos:
        if photo.split != split:
            continue
        runs = _repeats(saved.get(photo.file), repeats)
        if any(r is None for r in runs):
            continue
        out[photo.file] = [
            (label, predicted_count(counts, label), true)
            for counts in runs
            for label, true in sorted(photo.counts.items())
        ]
    return out


def photo_errors(pairs: Mapping[str, Sequence[tuple[str, int, int]]]) -> dict[str, float]:
    return {f: statistics.mean(abs(p - t) for _, p, t in rows) for f, rows in pairs.items() if rows}


def paired_bootstrap(
    diffs: Sequence[float], resamples: int = 5000, seed: int = 0
) -> tuple[float, float, float]:
    """``(mean, low, high)``: the mean of ``diffs`` and its 95% percentile interval over
    ``resamples`` resamples of the photos with replacement."""
    if not diffs:
        raise ValueError("no paired photos")
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(statistics.fmean(rng.choices(diffs, k=n)) for _ in range(resamples))
    low = means[int(0.025 * resamples)]
    high = means[min(resamples - 1, int(0.975 * resamples))]
    return statistics.fmean(diffs), low, high


def compare(
    truth: ds.PhotoCounts,
    baseline: Mapping[str, Any],
    system: Mapping[str, Any],
    repeats: int,
    *,
    resamples: int = 5000,
    seed: int = 0,
) -> dict[str, Any]:
    base_pairs = pairs_by_photo(truth, baseline, repeats)
    mine = pairs_by_photo(truth, system, repeats)
    shared = sorted(set(base_pairs) & set(mine))
    base_err, my_err = photo_errors(base_pairs), photo_errors(mine)
    diffs = [my_err[f] - base_err[f] for f in shared]
    mean, low, high = paired_bootstrap(diffs, resamples, seed)
    flat = [(c, p, t) for rows in mine.values() for c, p, t in rows]
    return {
        "overall": scores([(p, t) for _, p, t in flat]),
        "per_class": {
            c: scores([(p, t) for k, p, t in flat if k == c])
            for c in CLASSES
            if any(k == c for k, _, _ in flat)
        },
        "diff": (mean, low, high),
        "photos": len(mine),
        "paired": len(shared),
        "photo_errors": my_err,
    }


def _load(directory: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((directory / "photo_counts.json").read_text("utf-8"))
    return data


def _named(value: str) -> tuple[str, Path]:
    name, sep, path = value.partition("=")
    if not sep or not name or not path:
        raise argparse.ArgumentTypeError(f"expected NAME=DIR, got {value!r}")
    return name, Path(path)


def render(results: Mapping[str, Mapping[str, Any]], repeats: int) -> str:
    lines = [
        f"Counts against the verified boxes, {repeats} repeat(s); diff = paired bootstrap over "
        "photos of the per-photo count error minus the baseline's (95% interval).",
        "",
        "| System | Count MAE | Exact | Bias | Count P / R | Diff [95% CI] | Photos |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        o: Scores = r["overall"]
        mean, low, high = r["diff"]
        lines.append(
            f"| {name} | {o.mae:.3f} | {o.exact:.3f} | {o.bias:+.2f} | "
            f"{o.precision:.2f} / {o.recall:.2f} | {mean:+.3f} [{low:+.3f}, {high:+.3f}] | "
            f"{r['photos']} ({r['paired']} paired) |"
        )
    lines += ["", "Per class (MAE / exact / P / R):", ""]
    lines.append("| System | " + " | ".join(CLASSES) + " |")
    lines.append("|---|" + "---|" * len(CLASSES))
    for name, r in results.items():
        cells = []
        for c in CLASSES:
            s = r["per_class"].get(c)
            cells.append(
                f"{s.mae:.2f} / {s.exact:.2f} / {s.precision:.2f} / {s.recall:.2f}" if s else ""
            )
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.compare_counts", description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--system", type=_named, action="append", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--resamples", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=None, help="per-photo errors, as JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    data = ds.load_available(ds.data_dir(args.data_dir))
    truth = data.get("photo_counts")
    if not isinstance(truth, ds.PhotoCounts):
        print("no photo_counts truth (detection_boxes) in the data directory", file=sys.stderr)
        return 2
    baseline = _load(args.baseline)
    results: dict[str, dict[str, Any]] = {
        "baseline": compare(truth, baseline, baseline, args.repeats, resamples=args.resamples)
    }
    for name, directory in args.system:
        results[name] = compare(
            truth,
            baseline,
            _load(directory),
            args.repeats,
            resamples=args.resamples,
            seed=args.seed,
        )
    text = render(results, args.repeats)
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    if args.json is not None:
        args.json.write_text(
            json.dumps({k: v["photo_errors"] for k, v in results.items()}, indent=1, sort_keys=True)
        )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
