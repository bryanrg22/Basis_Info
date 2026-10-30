"""``python -m evals.sam3_select``: choose SAM 3 phrases and count thresholds per class,
offline, and score the choice out of sample (EVAL_REPORT section 19).

Input: a phrase-pool run of :mod:`evals.predict_boxes` (``--phrase-set pool
--phrase-dedupe off``: every candidate phrase prompted once per photo, every instance kept
with its phrase, score and mask) and the ``detection_boxes`` labels. Because SAM 3 answers
each phrase independently (the same phrase gives the same instances whatever else is
prompted; checked on the saved v1 and v2 runs), any per-class subset of the pool can be
scored without a new call: its instances are the union of its phrases' instances,
deduplicated within the class exactly as the localizer does
(:func:`basis.vision.localize.suppress` at :data:`~basis.vision.localize.PHRASE_DEDUPE_IOU`).

Selection rule, fixed before looking at the results:

* phrases: per class, the candidate subset (every subset of at most ``--max-phrases`` of
  the class's pool phrases, plus the v1 and v2 tables) with the highest AP@0.5 on the
  training photos; ties go to fewer phrases, then to the earlier candidate;
* threshold: per class, on the training photos and that class's chosen phrases, the grid
  value (0.30 to 0.90 by 0.05, ``--grid-min`` to start lower; the product keeps candidates from
  :data:`~basis.vision.localize.CANDIDATE_THRESHOLD` 0.3, so a count threshold cannot go
  lower) with the smallest total absolute count error over the photos verified for the
  class; ties go to the higher F1, then to the value nearest 0.5.

Out-of-sample estimate: leave one photo out. For each photo, the phrases and thresholds
are chosen on the other 51 and applied to it; AP is computed over the pooled held-out
detections, counts and precision/recall at the held-out thresholds. That estimates the
selection procedure, selection optimism included. The table the product ships is the same
rule applied to all photos (``final``), reported in sample next to it.

Writes ``<out>/selection.json`` (choices, per-fold choices, every metric),
``<out>/detection_boxes.json`` / ``photo_counts.json`` (the out-of-sample predictions, so
``evals.run --predictions <out>`` reproduces the out-of-sample AP) and ``<out>/console.log`` /
``manifest.json`` / ``results.json`` / ``report.md``, the run record (:mod:`evals.record`;
filed under ``detection`` as ``select-<pool version or --candidates>``, one passed case per
class chosen). ``score`` mode scores
a normal ``predict_boxes`` run at the localizer's per-class thresholds (the confirmation
run of the shipped phrase set)::

    python -m evals.sam3_select select --pool ~/basis-eval-data/predictions/sam3_phrase_pool_v1 \\
        --dataset ~/basis-eval-data/detection --out ~/basis-eval-data/predictions/sam3_select_v1
    python -m evals.sam3_select score --predictions ~/basis-eval-data/predictions/sam3_photos_v3 \\
        --dataset ~/basis-eval-data/detection
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import numpy as np

from basis.vision.localize import (
    PHRASE_DEDUPE_IOU,
    PHRASE_SETS,
    Instance,
    Mask,
    box_iou,
    count_threshold,
    suppress,
)
from basis.vision.schemas import ComponentLabel
from evals import record
from evals.datasets import DATASET_FILES, DetectionBoxes
from evals.merge_photo_runs import source_version
from evals.metrics import (
    DetectionImage,
    GoldBox,
    PredictedBox,
    average_precision,
    match_class,
)

GRID_MIN: Final = 0.30
GRID_MAX: Final = 0.90


def grid(low: float = GRID_MIN, high: float = GRID_MAX, step: float = 0.05) -> tuple[float, ...]:
    """Threshold candidates from ``low`` to ``high`` inclusive, rounded to the step."""
    count = int(round((high - low) / step)) + 1
    return tuple(round(low + step * i, 2) for i in range(count))


DEFAULT_GRID: Final[tuple[float, ...]] = grid()
IOU: Final = 0.5


# --------------------------------------------------------------------------- data


@dataclass(frozen=True)
class PhotoClass:
    """One photo, one class, one phrase subset: the detections scored against the gold.

    ``scored`` is ``(score, true positive)`` per detection in match order (highest score
    first), so the detections at or above a threshold are a prefix and their true
    positives are the prefix's hits (matching is greedy by score)."""

    scored: tuple[tuple[float, bool], ...]
    positives: int
    gold_count: int

    def at(self, threshold: float) -> tuple[int, int]:
        """``(detections, true positives)`` at or above ``threshold``."""
        kept = [hit for score, hit in self.scored if score >= threshold]
        return len(kept), sum(kept)


Candidate = tuple[str, ...]


@dataclass
class ClassData:
    label: str
    candidates: list[Candidate]
    # candidate -> file -> PhotoClass (only photos verified for the class)
    table: dict[Candidate, dict[str, PhotoClass]] = field(default_factory=dict)


def gold_images(data: DetectionBoxes, split: str = "dev") -> list[DetectionImage]:
    return [
        DetectionImage(
            file=i.file,
            document=i.document,
            boxes=tuple(GoldBox(b.label, b.x1, b.y1, b.x2, b.y2, b.group_of) for b in i.boxes),
            verified=frozenset(i.verified) if i.verified is not None else None,
        )
        for i in data.images
        if i.split == split
    ]


def load_dataset(directory: Path) -> DetectionBoxes:
    return DetectionBoxes.model_validate_json(
        (directory / DATASET_FILES["detection_boxes"]).read_text(encoding="utf-8")
    )


def load_pool(path: Path) -> dict[str, list[Instance]]:
    """``{file: instances}`` from a pool run's ``instances_raw.jsonl`` (first repeat)."""
    pool: dict[str, list[Instance]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "instances" not in record or record["file"] in pool:
            continue
        pool[record["file"]] = [Instance.model_validate(i) for i in record["instances"]]
    return pool


def candidates_for(label: ComponentLabel, max_phrases: int) -> list[Candidate]:
    """Every subset of at most ``max_phrases`` of the label's pool phrases, then the v1
    and v2 tables when not already listed (in pool order, smaller subsets first)."""
    pool = PHRASE_SETS["pool"][label]
    found: list[Candidate] = []
    for size in range(1, min(max_phrases, len(pool)) + 1):
        found += list(itertools.combinations(pool, size))
    for name in ("v1", "v2"):
        fixed = tuple(PHRASE_SETS[name][label])
        if set(fixed) <= set(pool) and fixed not in found:
            ordered = tuple(p for p in pool if p in fixed)
            if ordered not in found:
                found.append(ordered)
    return found


def detections(
    instances: Sequence[Instance], label: ComponentLabel, phrases: Candidate
) -> list[PredictedBox]:
    """The localizer's output for ``label`` prompted with ``phrases``: the phrases'
    instances relabelled, same-label duplicates suppressed as in the pipeline."""
    chosen = [
        i.model_copy(update={"label": label}) for p in phrases for i in instances if i.phrase == p
    ]
    kept = suppress(chosen, PHRASE_DEDUPE_IOU)
    return [
        PredictedBox(label.value, i.score, i.box.x1, i.box.y1, i.box.x2, i.box.y2) for i in kept
    ]


class Deduper:
    """:func:`detections` for many phrase subsets of one photo and label, with each mask
    decoded once and each pair's overlap computed once. Same result as
    :func:`~basis.vision.localize.suppress` (greedy by score, then input order; mask IoU,
    box IoU when a mask is missing); ``tests/unit/evals/test_sam3_select.py`` checks the
    two agree."""

    def __init__(self, instances: Sequence[Instance], label: ComponentLabel) -> None:
        self.label = label
        self.instances = list(instances)
        # None: no mask (box IoU); bounds None: an empty mask (overlaps nothing)
        self._masks: dict[int, tuple[Mask, tuple[int, int, int, int] | None] | None] = {}
        self._overlap: dict[tuple[int, int], float] = {}

    def _mask(self, i: int) -> tuple[Mask, tuple[int, int, int, int] | None] | None:
        if i not in self._masks:
            rle = self.instances[i].mask
            self._masks[i] = (rle.decode(), rle.bbox()) if rle is not None else None
        return self._masks[i]

    def overlap(self, i: int, j: int) -> float:
        key = (min(i, j), max(i, j))
        if key not in self._overlap:
            self._overlap[key] = self._compute(i, j)
        return self._overlap[key]

    def _compute(self, i: int, j: int) -> float:
        a, b = self._mask(i), self._mask(j)
        if a is None or b is None or a[0].shape != b[0].shape:
            return box_iou(self.instances[i].box, self.instances[j].box)
        (mask_a, bounds_a), (mask_b, bounds_b) = a, b
        if bounds_a is None or bounds_b is None:
            return 0.0  # an empty mask intersects nothing
        ax1, ay1, ax2, ay2 = bounds_a
        bx1, by1, bx2, by2 = bounds_b
        if ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1:
            return 0.0
        # both masks lie inside the union of their bounds, so IoU there is the full IoU
        x1, y1, x2, y2 = min(ax1, bx1), min(ay1, by1), max(ax2, bx2), max(ay2, by2)
        crop_a, crop_b = mask_a[y1:y2, x1:x2], mask_b[y1:y2, x1:x2]
        union = int(np.logical_or(crop_a, crop_b).sum())
        return int(np.logical_and(crop_a, crop_b).sum()) / union if union else 0.0

    def detections(self, phrases: Candidate) -> list[PredictedBox]:
        chosen = [i for p in phrases for i, inst in enumerate(self.instances) if inst.phrase == p]
        order = sorted(range(len(chosen)), key=lambda k: (-self.instances[chosen[k]].score, k))
        kept: list[int] = []
        for k in order:
            i = chosen[k]
            if all(self.overlap(i, j) <= PHRASE_DEDUPE_IOU for j in kept):
                kept.append(i)
        return [
            PredictedBox(
                self.label.value, inst.score, inst.box.x1, inst.box.y1, inst.box.x2, inst.box.y2
            )
            for inst in (self.instances[i] for i in kept)
        ]


def photo_class(image: DetectionImage, label: str, boxes: Sequence[PredictedBox]) -> PhotoClass:
    gold = [b for b in image.boxes if b.label == label]
    scored, positives = match_class(gold, boxes, IOU)
    ordered = tuple(sorted(scored, key=lambda s: -s[0]))
    return PhotoClass(ordered, positives, sum(1 for b in gold if not b.group_of))


def observed_candidates(
    pool: Mapping[str, Sequence[Instance]], label: ComponentLabel, max_phrases: int
) -> list[Candidate]:
    """Every subset of at most ``max_phrases`` of the phrases ``pool`` used for ``label``
    (sorted), for detectors whose phrases are not the SAM 3 pool: a trained segmenter's
    class names (``evals.predict_detector``). A label with no instance gets one empty
    candidate (no detections)."""
    phrases = sorted({i.phrase for found in pool.values() for i in found if i.label == label})
    found: list[Candidate] = []
    for size in range(1, min(max_phrases, len(phrases)) + 1):
        found += list(itertools.combinations(phrases, size))
    return found or [()]


def build(
    images: Sequence[DetectionImage],
    pool: Mapping[str, Sequence[Instance]],
    labels: Sequence[ComponentLabel],
    max_phrases: int,
    progress: Callable[[str], None] | None = None,
    candidates: Callable[[ComponentLabel], list[Candidate]] | None = None,
) -> list[ClassData]:
    """The scored table of every candidate phrase subset per class; ``candidates`` gives
    a label's subsets (default :func:`candidates_for`, the SAM 3 phrase pool). With
    ``candidates`` only the instances carrying the class's label are read (one class name,
    for example a segmenter's ``light``, can stand for an interior or an exterior label)."""
    out: list[ClassData] = []
    for label in labels:
        data = ClassData(
            label.value,
            candidates(label) if candidates is not None else candidates_for(label, max_phrases),
        )
        phrases = {p for c in data.candidates for p in c}
        dedupers = {
            image.file: Deduper(
                [
                    i
                    for i in pool.get(image.file, ())
                    if i.phrase in phrases and (candidates is None or i.label == label)
                ],
                label,
            )
            for image in images
            if image.scores(label.value)
        }
        for candidate in data.candidates:
            data.table[candidate] = {
                image.file: photo_class(
                    image, label.value, dedupers[image.file].detections(candidate)
                )
                for image in images
                if image.scores(label.value)
            }
        out.append(data)
        if progress is not None:
            progress(f"{label.value}: {len(data.candidates)} candidates")
    return out


# --------------------------------------------------------------------------- choice


def class_ap(rows: Sequence[PhotoClass]) -> float:
    return average_precision(
        [s for row in rows for s in row.scored], sum(row.positives for row in rows)
    )


def f1(detected: int, hits: int, positives: int) -> float:
    return 2 * hits / (detected + positives) if detected + positives else 0.0


def choose_phrases(data: ClassData, files: Sequence[str]) -> Candidate:
    best: tuple[float, int, int] | None = None
    chosen = data.candidates[0]
    for order, candidate in enumerate(data.candidates):
        ap = class_ap([data.table[candidate][f] for f in files if f in data.table[candidate]])
        key = (-(ap if not math.isnan(ap) else -1.0), len(candidate), order)
        if best is None or key < best:
            best, chosen = key, candidate
    return chosen


def choose_threshold(rows: Sequence[PhotoClass], grid: Sequence[float]) -> float:
    best: tuple[int, float, float] | None = None
    chosen = grid[0]
    positives = sum(r.positives for r in rows)
    for t in grid:
        error = 0
        detected = hits = 0
        for row in rows:
            n, tp = row.at(t)
            error += abs(n - row.gold_count)
            detected += n
            hits += tp
        key = (error, -f1(detected, hits, positives), abs(t - 0.5))
        if best is None or key < best:
            best, chosen = key, t
    return chosen


@dataclass(frozen=True)
class Choice:
    phrases: Candidate
    threshold: float


def choose(data: ClassData, files: Sequence[str], grid: Sequence[float]) -> Choice:
    phrases = choose_phrases(data, files)
    rows = [data.table[phrases][f] for f in files if f in data.table[phrases]]
    return Choice(phrases, choose_threshold(rows, grid))


# --------------------------------------------------------------------------- metrics


@dataclass
class Tally:
    """Per-class pooled detections and per-photo counts at a threshold."""

    scored: list[tuple[float, bool]] = field(default_factory=list)
    positives: int = 0
    detected: int = 0
    hits: int = 0
    gold_at: int = 0
    pairs: list[tuple[int, int]] = field(default_factory=list)  # (predicted, gold)

    def add(self, row: PhotoClass, threshold: float) -> None:
        self.scored += row.scored
        self.positives += row.positives
        n, tp = row.at(threshold)
        self.detected += n
        self.hits += tp
        self.gold_at += row.positives
        if n or row.gold_count:
            self.pairs.append((n, row.gold_count))


def summarize(tallies: Mapping[str, Tally]) -> dict[str, Any]:
    per_class: dict[str, Any] = {}
    for label, t in tallies.items():
        errors = [abs(p - g) for p, g in t.pairs]
        per_class[label] = {
            "ap": average_precision(t.scored, t.positives),
            "positives": t.positives,
            "precision": t.hits / t.detected if t.detected else math.nan,
            "recall": t.hits / t.gold_at if t.gold_at else math.nan,
            "count_mae": sum(errors) / len(errors) if errors else math.nan,
            "count_bias": sum(p - g for p, g in t.pairs) / len(t.pairs) if t.pairs else math.nan,
            "pairs": len(t.pairs),
        }
    aps = [c["ap"] for c in per_class.values() if c["positives"] > 0]
    detected = sum(t.detected for t in tallies.values())
    hits = sum(t.hits for t in tallies.values())
    gold = sum(t.gold_at for t in tallies.values())
    pairs = [p for t in tallies.values() for p in t.pairs]
    return {
        "map_50": sum(aps) / len(aps) if aps else math.nan,
        "precision": hits / detected if detected else math.nan,
        "recall": hits / gold if gold else math.nan,
        "count_mae": sum(abs(p - g) for p, g in pairs) / len(pairs) if pairs else math.nan,
        "count_bias": sum(p - g for p, g in pairs) / len(pairs) if pairs else math.nan,
        "count_pairs": len(pairs),
        "classes": per_class,
    }


def evaluate_fixed(
    classes: Sequence[ClassData], choices: Mapping[str, Choice], files: Sequence[str]
) -> dict[str, Any]:
    tallies: dict[str, Tally] = {}
    for data in classes:
        choice = choices[data.label]
        tally = tallies.setdefault(data.label, Tally())
        for f in files:
            row = data.table[choice.phrases].get(f)
            if row is not None:
                tally.add(row, choice.threshold)
    return summarize(tallies)


def leave_one_out(
    classes: Sequence[ClassData], files: Sequence[str], grid: Sequence[float]
) -> tuple[dict[str, Any], dict[str, dict[str, Choice]]]:
    tallies: dict[str, Tally] = {}
    folds: dict[str, dict[str, Choice]] = {}
    for held in files:
        train = [f for f in files if f != held]
        folds[held] = {}
        for data in classes:
            choice = choose(data, train, grid)
            folds[held][data.label] = choice
            row = data.table[choice.phrases].get(held)
            tally = tallies.setdefault(data.label, Tally())
            if row is not None:
                tally.add(row, choice.threshold)
    return summarize(tallies), folds


# --------------------------------------------------------------------------- score mode


def score_predictions(
    images: Sequence[DetectionImage],
    boxes: Mapping[str, Sequence[PredictedBox]],
    threshold: Callable[[str], float],
) -> dict[str, Any]:
    labels = sorted({b.label for image in images for b in image.boxes})
    tallies: dict[str, Tally] = {}
    for label in labels:
        tally = tallies.setdefault(label, Tally())
        for image in images:
            if not image.scores(label):
                continue
            mine = [b for b in boxes.get(image.file, ()) if b.label == label]
            tally.add(photo_class(image, label, mine), threshold(label))
    return summarize(tallies)


def read_boxes(path: Path) -> dict[str, list[PredictedBox]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, list[PredictedBox]] = {}
    for file, value in raw.items():
        first = value["repeats"][0] if isinstance(value, dict) else value
        out[file] = [
            PredictedBox(b["label"], float(b["score"]), b["x1"], b["y1"], b["x2"], b["y2"])
            for b in first
        ]
    return out


# --------------------------------------------------------------------------- cli


def _jsonable(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def table(result: Mapping[str, Any]) -> str:
    lines = [
        f"mAP@0.5 {result['map_50']:.3f}  P/R {result['precision']:.2f}/{result['recall']:.2f}  "
        f"count MAE {result['count_mae']:.2f} bias {result['count_bias']:+.2f} "
        f"({result['count_pairs']} pairs)"
    ]
    for label, c in result["classes"].items():
        lines.append(
            f"  {label:18s} AP {c['ap']:.3f}  P/R {c['precision']:.2f}/{c['recall']:.2f}  "
            f"MAE {c['count_mae']:.2f} bias {c['count_bias']:+.2f}  (n={c['positives']})"
        )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.sam3_select", description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    select = sub.add_parser("select", help="choose phrases and thresholds from a pool run")
    select.add_argument("--pool", type=Path, required=True, help="predict_boxes pool run dir")
    select.add_argument("--dataset", type=Path, required=True, help="detection_boxes dataset dir")
    select.add_argument("--out", type=Path, required=True)
    select.add_argument("--max-phrases", type=int, default=3)
    select.add_argument(
        "--grid-min",
        type=float,
        default=GRID_MIN,
        help="lowest count threshold tried (below 0.3 needs a lower candidate threshold)",
    )
    select.add_argument("--split", default="dev")
    select.add_argument(
        "--candidates",
        choices=("pool", "observed"),
        default="pool",
        help="phrase subsets per class: the SAM 3 phrase pool (default), or the phrases the "
        "pool run used for the class (a trained segmenter's class names)",
    )
    score = sub.add_parser("score", help="score a predict_boxes run at per-class thresholds")
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--dataset", type=Path, required=True)
    score.add_argument("--split", default="dev")
    score.add_argument(
        "--threshold", type=float, default=None, help="one threshold for every class instead"
    )
    args = parser.parse_args(argv)
    if args.mode == "select" and args.max_phrases < 1:
        parser.error("--max-phrases must be at least 1")
    if args.mode == "select" and not 0.0 < args.grid_min <= GRID_MAX:
        parser.error(f"--grid-min must be in (0, {GRID_MAX}]")
    return args


def labelled_classes(images: Sequence[DetectionImage]) -> list[ComponentLabel]:
    return [ComponentLabel(label) for label in sorted({b.label for i in images for b in i.boxes})]


def select_version(args: argparse.Namespace) -> str:
    """``select-<pool version>`` (the pool run's manifest: ``sam3-pool``, a detector's name)
    or, for a pool without a manifest, ``select-<--candidates>``."""
    pool = source_version([args.pool])
    return f"select-{args.candidates if pool == record.UNVERSIONED else pool}"


def run_select(args: argparse.Namespace) -> int:
    run = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component="detection", version=select_version(args)
    )
    try:
        return _select(args, run)
    except BaseException as exc:
        record.finish_pending(args, exit_code=1, error=exc)
        raise


def _select(args: argparse.Namespace, run: record.EvalRun) -> int:
    run.input("pool", args.pool)
    run.dataset(args.dataset / DATASET_FILES["detection_boxes"])
    run.note(candidates=args.candidates, max_phrases=args.max_phrases, grid_min=args.grid_min)
    images = gold_images(load_dataset(args.dataset), args.split)
    pool = load_pool(args.pool / "instances_raw.jsonl")
    missing = sorted(i.file for i in images if i.file not in pool)
    if missing:
        print(f"{len(missing)} photos have no pool instances: {missing}", file=sys.stderr)
        for file in missing:
            run.case(file, record.TEST_ERROR, kind="photo", detail="no pool instances")
        run.finish(None, exit_code=2)
        return 2
    files = [i.file for i in images]
    labels = labelled_classes(images)
    observed = args.candidates == "observed"
    classes = build(
        images,
        pool,
        labels,
        args.max_phrases,
        lambda m: print(m, file=sys.stderr),
        (lambda label: observed_candidates(pool, label, args.max_phrases)) if observed else None,
    )
    thresholds = grid(args.grid_min)
    loo, folds = leave_one_out(classes, files, thresholds)
    final = {data.label: choose(data, files, thresholds) for data in classes}
    in_sample = evaluate_fixed(classes, final, files)
    baselines: dict[str, Any] = {}

    def v1_product(label: str) -> float:
        return count_threshold(label, "v1")  # EVAL_REPORT 10.2: window 0.6, others 0.5

    def at_one_half(label: str) -> float:
        return 0.5

    rules: list[tuple[str, str, Callable[[str], float]]] = [
        ("v1_product", "v1", v1_product),
        ("v1_at_0.5", "v1", at_one_half),
        ("v2_at_0.5", "v2", at_one_half),
    ]
    for name, phrase_set, rule in [] if observed else rules:
        fixed = {
            data.label: Choice(
                tuple(
                    p
                    for p in PHRASE_SETS["pool"][ComponentLabel(data.label)]
                    if p in PHRASE_SETS[phrase_set][ComponentLabel(data.label)]
                ),
                rule(data.label),
            )
            for data in classes
        }
        for data in classes:
            if fixed[data.label].phrases not in data.table:
                raise SystemExit(f"{name}: {data.label} phrases are not a candidate")
        baselines[name] = evaluate_fixed(classes, fixed, files)
    stability = {
        data.label: sorted(
            {(fold[data.label].phrases, fold[data.label].threshold) for fold in folds.values()},
            key=lambda c: (
                -sum(
                    1
                    for fold in folds.values()
                    if (fold[data.label].phrases, fold[data.label].threshold) == c
                )
            ),
        )
        for data in classes
    }
    report = {
        "pool": str(args.pool),
        "photos": len(files),
        "max_phrases": args.max_phrases,
        "candidates": args.candidates,
        "grid": list(thresholds),
        "rule": "phrases: max AP@0.5 on training photos (ties: fewer phrases); threshold: "
        "min total |count error| on training photos (ties: F1, then nearest 0.5)",
        "final": {
            label: {"phrases": list(c.phrases), "threshold": c.threshold}
            for label, c in final.items()
        },
        "leave_one_photo_out": loo,
        "in_sample_final": in_sample,
        "baselines": baselines,
        "fold_choices": {
            label: [
                {
                    "phrases": list(p),
                    "threshold": t,
                    "folds": sum(
                        1
                        for fold in folds.values()
                        if (fold[label].phrases, fold[label].threshold) == (p, t)
                    ),
                }
                for p, t in choices
            ]
            for label, choices in stability.items()
        },
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "selection.json").write_text(json.dumps(_jsonable(report), indent=1))
    boxes_out: dict[str, list[dict[str, Any]]] = {}
    counts_out: dict[str, dict[str, int]] = {}
    for file in files:
        boxes_out[file] = []
        counts_out[file] = {}
        for data in classes:
            choice = folds[file][data.label]
            component = ComponentLabel(data.label)
            found = detections(pool[file], component, choice.phrases)
            boxes_out[file] += [
                {"label": b.label, "score": b.score, "x1": b.x1, "y1": b.y1, "x2": b.x2, "y2": b.y2}
                for b in found
            ]
            n = sum(1 for b in found if b.score >= choice.threshold)
            if n:
                counts_out[file][data.label] = n
    (args.out / "detection_boxes.json").write_text(json.dumps(boxes_out, indent=1))
    (args.out / "photo_counts.json").write_text(json.dumps(counts_out, indent=1, sort_keys=True))
    print("leave one photo out:\n" + table(loo))
    print("in sample (the shipped choice):\n" + table(in_sample))
    for name, result in baselines.items():
        print(f"{name}:\n" + table(result))
    for label, c in final.items():
        print(f"final {label}: {list(c.phrases)} @ {c.threshold}")
        run.case(
            label,
            record.PASSED,
            kind="class",
            detail={"phrases": list(c.phrases), "threshold": c.threshold},
        )
    run.finish(None)
    return 0


def run_score(args: argparse.Namespace) -> int:
    images = gold_images(load_dataset(args.dataset), args.split)
    boxes = read_boxes(args.predictions / "detection_boxes.json")
    fixed: float | None = args.threshold
    result = score_predictions(
        images, boxes, (lambda _: fixed) if fixed is not None else count_threshold
    )
    print(table(result))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return run_select(args) if args.mode == "select" else run_score(args)


if __name__ == "__main__":
    raise SystemExit(main())
