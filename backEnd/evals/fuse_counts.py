"""``python -m evals.fuse_counts``: per-class fusion of VLM and SAM 3 counts, cross-validated.

Sources (:mod:`basis.vision.fusion`):

* ``--vlm NAME=DIR`` (repeatable; the first is the *base*): a per-photo run's
  ``photo_counts.json`` (``evals.predict_photos``, ``merge_photo_runs`` or
  ``aggregate_runs``). Repeat i of every VLM source is fused with repeat i of the base.
* ``--sam3 DIR``: an ``evals.predict_boxes`` run's ``instances_raw.jsonl``; it becomes one
  source per ``--thresholds`` value, ``sam3@T`` = the instances scoring at least T.
* ``--counts NAME=DIR`` (repeatable): any other ``photo_counts.json`` (crop verification).

Candidate rules per class: every source alone, ``max`` and ``min`` of the base with each
other source (a detector as floor or ceiling), and, with two VLM sources, the ``median`` of
both VLMs and each other source. The base alone is listed first, so a tie keeps it.

Selection (``--cv``): ``loo`` (default) scores each photo with the rules fitted on the other
photos (leave-one-photo-out; the honest number), ``none`` fits on every photo and scores the
same photos (in-sample; reported only to show the optimism). The truth is the verified
boxes' ``photo_counts`` (``evals.datasets.photo_counts``) of ``--split``.

Output in ``--out``: ``photo_counts.json`` (one fused answer per base repeat),
``photo_components.json`` and ``photo_rooms.json`` copied from the base (fusion changes
counts only), and ``run.json``: the rules fitted on every photo, how often each rule was
chosen across the folds per class, the sources and the summed spend; ``run.log`` and
``manifest.json`` are the run record (:mod:`evals.record`). Score with
``evals.run --predictions OUT --repeats N``.

Usage (from ``backEnd/``)::

    python -m evals.fuse_counts --vlm sol=out/decomposed_sol --sam3 out/sam3 --out out/fused
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from basis.vision.fusion import Example, Rule, fit, fuse
from evals import datasets as ds
from evals import record

CLASSES: Final[tuple[str, ...]] = (
    "window",
    "door",
    "light_fixture",
    "outdoor_lighting",
    "electrical_outlet",
    "kitchen_cabinet",
)
THRESHOLDS: Final[tuple[float, ...]] = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def _named(value: str) -> tuple[str, Path]:
    name, sep, path = value.partition("=")
    if not sep or not name or not path:
        raise argparse.ArgumentTypeError(f"expected NAME=DIR, got {value!r}")
    return name, Path(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.fuse_counts", description=__doc__)
    parser.add_argument("--vlm", type=_named, action="append", required=True)
    parser.add_argument("--sam3", type=Path, default=None)
    parser.add_argument("--thresholds", type=float, nargs="+", default=list(THRESHOLDS))
    parser.add_argument("--counts", type=_named, action="append", default=[])
    parser.add_argument("--cv", choices=("loo", "none"), default="loo")
    parser.add_argument("--classes", nargs="+", default=list(CLASSES))
    parser.add_argument("--split", default="dev")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    names = [n for n, _ in args.vlm] + [n for n, _ in args.counts]
    if len(set(names)) != len(names):
        parser.error("source names must be distinct")
    return args


def per_repeat(value: Any) -> list[dict[str, int]]:
    runs = value["repeats"] if isinstance(value, dict) and "repeats" in value else [value]
    return [{str(k): int(v) for k, v in (r or {}).items()} for r in runs]


def load_counts(run: Path) -> dict[str, list[dict[str, int]]]:
    data: dict[str, Any] = json.loads((run / "photo_counts.json").read_text(encoding="utf-8"))
    return {file: per_repeat(value) for file, value in data.items()}


def sam3_counts(run: Path, thresholds: Sequence[float]) -> dict[str, dict[str, dict[str, int]]]:
    """``{source: {file: {label: count}}}`` with ``source`` = ``sam3@T`` for every T."""
    out: dict[str, dict[str, dict[str, int]]] = {f"sam3@{t:g}": {} for t in thresholds}
    for line in (run / "instances_raw.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "instances" not in record:
            continue
        for t in thresholds:
            counts: dict[str, int] = {}
            for instance in record["instances"]:
                if float(instance["score"]) >= t:
                    counts[instance["label"]] = counts.get(instance["label"], 0) + 1
            out[f"sam3@{t:g}"][record["file"]] = counts
    return out


def candidates(base: str, vlms: Sequence[str], others: Sequence[str]) -> list[Rule]:
    rules = [Rule("pick", (base,))]
    for source in [*vlms[1:], *others]:
        rules.append(Rule("pick", (source,)))
        rules.append(Rule("max", (base, source)))
        rules.append(Rule("min", (base, source)))
    if len(vlms) >= 2:
        for source in others:
            rules.append(Rule("median", (vlms[0], vlms[1], source)))
    return rules


def build_examples(
    files: Sequence[str],
    repeats: int,
    vlm_counts: Mapping[str, Mapping[str, list[dict[str, int]]]],
    single_counts: Mapping[str, Mapping[str, dict[str, int] | list[dict[str, int]]]],
    truth: Mapping[str, Mapping[str, int]],
) -> dict[str, list[Example]]:
    """``{file: [Example per repeat]}``: repeat i of each VLM source (cycled when a source
    has fewer repeats) with the single-answer sources; a photo some source lacks is skipped."""
    out: dict[str, list[Example]] = {}
    for file in files:
        if any(file not in c for c in (*vlm_counts.values(), *single_counts.values())):
            continue
        examples = []
        for r in range(repeats):
            by_source: dict[str, dict[str, int]] = {}
            for name, counts in vlm_counts.items():
                runs = counts[file]
                by_source[name] = runs[r % len(runs)]
            for name, single in single_counts.items():
                value = single[file]
                by_source[name] = value[r % len(value)] if isinstance(value, list) else value
            examples.append(Example(photo=file, by_source=by_source, truth=truth.get(file, {})))
        out[file] = examples
    return out


def fuse_counts(args: argparse.Namespace) -> dict[str, Any]:
    run = record.EvalRun.begin(args.out, harness=__name__, args=args)
    try:
        summary = _fuse(args, run)
    except BaseException as exc:
        record.finish_pending(args, exit_code=1, error=exc)
        raise
    run.finish(summary)
    return summary


def _fuse(args: argparse.Namespace, run: record.EvalRun) -> dict[str, Any]:
    for name, path in [*args.vlm, *args.counts]:
        run.input(name, path)
    if args.sam3 is not None:
        run.input("sam3", args.sam3)
    try:
        run.dataset(ds.data_dir(args.data_dir) / ds.DATASET_FILES["detection_boxes"])
    except ds.DatasetError:
        pass  # no data directory resolvable: the labels come from elsewhere (tests)
    run.note(cv=args.cv, thresholds=list(args.thresholds) if args.sam3 is not None else [])
    boxes = ds.load("detection_boxes", args.data_dir)
    assert isinstance(boxes, ds.DetectionBoxes)
    labels = ds.photo_counts(boxes)
    truth = {p.file: dict(p.counts) for p in labels.photos if p.split == args.split}
    vlm_counts = {name: load_counts(path) for name, path in args.vlm}
    base, base_dir = args.vlm[0]
    single: dict[str, Any] = {}
    if args.sam3 is not None:
        single.update(sam3_counts(args.sam3, args.thresholds))
    for name, path in args.counts:
        single[name] = load_counts(path)
    repeats = max(len(v) for v in vlm_counts[base].values())
    examples = build_examples(sorted(vlm_counts[base]), repeats, vlm_counts, single, truth)
    rules = candidates(base, [n for n, _ in args.vlm], sorted(single))
    labelled = {f: e for f, e in examples.items() if f in truth}
    every = [x for e in labelled.values() for x in e]
    fitted_all = fit(every, rules, args.classes)
    chosen: dict[str, Counter[str]] = {c: Counter() for c in args.classes}
    fused: dict[str, Any] = {}
    for file, file_examples in examples.items():
        if args.cv == "loo" and file in labelled:
            fold = [x for f, e in labelled.items() if f != file for x in e]
            file_rules = fit(fold, rules, args.classes)
        else:
            file_rules = fitted_all
        for count_class, rule in file_rules.items():
            chosen[count_class][rule.name] += 1
        answers = [fuse(x.by_source, file_rules, base) for x in file_examples]
        fused[file] = answers[0] if len(answers) == 1 else {"repeats": answers}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "photo_counts.json").write_text(json.dumps(fused, indent=1, sort_keys=True))
    for name in ("photo_components.json", "photo_rooms.json"):
        if (base_dir / name).exists():
            shutil.copyfile(base_dir / name, args.out / name)
    spent = 0.0
    for path in (
        [p for _, p in args.vlm]
        + [p for _, p in args.counts]
        + ([args.sam3] if args.sam3 is not None else [])
    ):
        summary_path = path / "run.json"
        if summary_path.exists():
            spent += float(json.loads(summary_path.read_text()).get("spent_usd") or 0.0)
    summary = {
        "base": base,
        "vlm": {n: str(p) for n, p in args.vlm},
        "sam3": str(args.sam3) if args.sam3 is not None else None,
        "thresholds": list(args.thresholds) if args.sam3 is not None else [],
        "counts": {n: str(p) for n, p in args.counts},
        "cv": args.cv,
        "split": args.split,
        "photos": len(fused),
        "labelled_photos": len(labelled),
        "candidates": [r.name for r in rules],
        "rules_fitted_on_all": {c: r.name for c, r in fitted_all.items()},
        "rules_chosen_per_photo": {c: dict(v.most_common()) for c, v in chosen.items()},
        "spent_usd": round(spent, 6),
    }
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = fuse_counts(args)
    print(
        f"wrote {args.out}: {summary['photos']} photos ({args.cv}); rules on all photos: "
        + ", ".join(f"{c}={r}" for c, r in summary["rules_fitted_on_all"].items()),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
