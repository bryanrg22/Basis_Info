"""``python -m evals.predict_boxes``: run the localizer on photos and save its instances.

Runs :func:`basis.vision.localize.localize_photo` (the pipeline step: phrases for the
photo's labels plus the countable set, one full-image prediction, optional 2 x 2 tiles
for the countable phrases, merged) on every photo, either of a ``detection_boxes``
dataset directory (``--dataset``: ``detection_boxes_v1.json`` and the photos in
``images/``, as :mod:`evals.openimages` writes it) or of any photo directory
(``--photos``). Writes::

    <out>/detection_boxes.json   {"<file>": [{"label", "score", "x1", "y1", "x2", "y2"}]}
                                 or {"repeats": [...]}: what evals.run scores
    <out>/photo_counts.json      {"<file>": {"<label>": count}} at each class's threshold
                                 of the phrase set (--count-threshold: one for all)
    <out>/instances_raw.jsonl    one line per (file, repeat): every instance with its
                                 run-length mask (for evals.render_masks and the UI
                                 experiment), the calls, predict seconds and cost
    <out>/run.json               settings, model version, calls, predict seconds, spent
    <out>/llm_calls.jsonl, llm/, run.log, manifest.json   the run record (evals.record):
                                 one localization row per SAM 3 prediction, as production
                                 records them (basis.activities.localize_photo)

Which labels are prompted (``--phrases-from``), always together with the countable set:

* ``countable``: nothing else (the default for ``--photos``);
* ``vlm``: the labels the per-photo model named, read from a ``predict_photos``
  ``photo_raw.jsonl`` (``--photo-raw``; its first repeat with an analysis);
* ``dataset``: every label of the dataset's gold boxes (``door`` -> interior and
  exterior door), so verified-absent labels can produce false positives (the default
  for ``--dataset``);
* ``all``: every label of the vocabulary.

``--threshold`` is sent to the model and filters what is kept; keep it low (0.1) when
the predictions are for AP, which needs the whole precision-recall curve, and count at
``evals.run --detection-threshold``. Usage (from ``backEnd/``, ``BASIS_ENV=dev`` with
``REPLICATE_API_TOKEN`` in the environment)::

    python -m evals.predict_boxes --dataset ~/basis-eval-data/openimages --out out/sam3
    python -m evals.predict_boxes --photos ~/basis-eval-data/inputs/photos --out out/sam3p \\
        --phrases-from vlm --photo-raw out/vision/photo_raw.jsonl --tiling on
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from basis.config import Settings, load_settings
from basis.llm.call import CallContext, step_entry
from basis.llm.provider import Budget, BudgetExceededError
from basis.llm.recorder import KIND_LOCALIZATION
from basis.vision.localize import (
    DEFAULT_PHRASE_SET,
    PHRASE_SETS,
    Localization,
    LocalizeCall,
    Localizer,
    LocalizerError,
    build_replicate_localizer,
    class_counts,
    counts,
    localize_photo,
    prompt_labels,
    unlocalized,
)
from basis.vision.prep import ImageRejected, prepare_image
from basis.vision.schemas import ComponentLabel
from evals.datasets import DATASET_FILES, DetectionBoxes
from evals.metrics import CLASS_UNION
from evals.predict_photos import photo_files
from evals.record import EvalRun, finish_pending


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.predict_boxes", description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", type=Path, help="a detection_boxes dataset directory")
    source.add_argument("--photos", type=Path, help="a directory of photos")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--split", default="dev", help="dataset split to run (with --dataset)")
    parser.add_argument("--tiling", choices=("on", "off"), default="off")
    parser.add_argument("--merge-metric", choices=("iou", "ios"), default="iou")
    parser.add_argument("--phrase-set", choices=tuple(PHRASE_SETS), default=DEFAULT_PHRASE_SET)
    parser.add_argument(
        "--phrase-dedupe",
        choices=("on", "off"),
        default="on",
        help="off keeps every phrase's instances (the phrase-pool experiment; full frame only)",
    )
    parser.add_argument("--threshold", type=float, default=0.1, help="sent to the model")
    parser.add_argument(
        "--count-threshold",
        type=float,
        default=None,
        help="one count threshold for every class (default: the phrase set's per-class table)",
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument(
        "--phrases-from", choices=("vlm", "countable", "all", "dataset"), default=None
    )
    parser.add_argument("--photo-raw", type=Path, default=None, help="predict_photos output")
    parser.add_argument("--concurrency", type=int, default=None, help="photos at a time")
    parser.add_argument("--limit", type=int, default=None, help="first N photos only")
    parser.add_argument("--budget-usd", type=float, default=5.0, help="stop when exceeded")
    args = parser.parse_args(argv)
    if args.phrases_from is None:
        args.phrases_from = "dataset" if args.dataset else "countable"
    if args.phrases_from == "dataset" and args.dataset is None:
        parser.error("--phrases-from dataset needs --dataset")
    if args.phrases_from == "vlm" and args.photo_raw is None:
        parser.error("--phrases-from vlm needs --photo-raw (a predict_photos photo_raw.jsonl)")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.phrase_dedupe == "off" and args.tiling == "on":
        parser.error("--phrase-dedupe off is for the full frame only (--tiling off)")
    for name in ("threshold", "count_threshold"):
        value = getattr(args, name)
        if value is not None and not 0.0 <= value <= 1.0:
            parser.error(f"--{name.replace('_', '-')} must be in [0, 1]")
    return args


def make_localizer(settings: Settings, budget: Budget) -> Localizer | None:
    """The Replicate SAM 3 localizer from the settings, or None without a token."""
    if settings.replicate_api_token is None:
        return None
    token = settings.replicate_api_token.get_secret_value().strip()
    if not token:
        return None
    return build_replicate_localizer(
        token,
        settings.sam3_replicate_version,
        concurrency=settings.localizer_concurrency,
        budget=budget,
    )


def vlm_labels(path: Path) -> dict[str, list[str]]:
    """``{file: labels}`` from a ``predict_photos`` ``photo_raw.jsonl`` (first repeat with
    an analysis per file)."""
    labels: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        analysis = record.get("analysis")
        if analysis is None or record["file"] in labels:
            continue
        labels[record["file"]] = sorted({c["label"] for c in analysis.get("components", [])})
    return labels


def dataset_labels(data: DetectionBoxes) -> list[str]:
    labels: set[str] = set()
    for image in data.images:
        for box in image.boxes:
            labels |= set(CLASS_UNION.get(box.label, {box.label}))
    return sorted(labels)


def _photos(args: argparse.Namespace) -> tuple[list[tuple[str, Path]], DetectionBoxes | None]:
    """``[(key, path)]``: the key is the dataset's ``file`` (or the photo's name), which
    the predictions are written under."""
    if args.photos is not None:
        return [(p.name, p) for p in photo_files(args.photos)], None
    root: Path = args.dataset
    data = DetectionBoxes.model_validate_json(
        (root / DATASET_FILES["detection_boxes"]).read_text(encoding="utf-8")
    )
    paths: list[tuple[str, Path]] = []
    for image in data.images:
        if image.split != args.split:
            continue
        candidates = [root / "images" / image.file, root / image.file]
        paths.append((image.file, next((p for p in candidates if p.exists()), candidates[0])))
    return paths, data


async def record_calls(
    ledger: CallContext, localizer: Localizer, calls: Sequence[LocalizeCall], phrase_set: str
) -> None:
    """One ledger row per SAM 3 prediction (``kind = localization``): the predict seconds at
    the backend's rate and the cost as metered, as ``basis.activities.localize_photo`` records
    a production prediction."""
    model = str(getattr(localizer, "model", None) or type(localizer).__name__)
    version = getattr(localizer, "version", None)
    for call in calls:
        entry = step_entry(
            ledger,
            kind=KIND_LOCALIZATION,
            provider=str(getattr(localizer, "provider", type(localizer).__name__)),
            model=f"{model}@{version}" if version else model,
            quantity=call.predict_seconds,
            unit="second",
            unit_price_usd=float(getattr(localizer, "usd_per_second", 0.0) or 0.0),
            cost_usd=call.cost_usd,
            response_id=call.prediction_id,
            params={"prompts": list(call.prompts), "phrase_set": phrase_set},
            response={"status": call.status, "predict_seconds": call.predict_seconds},
        )
        await ledger.recorder.record(entry)


def _box_record(localization: Localization) -> list[dict[str, Any]]:
    return [
        {
            "label": i.label.value,
            "score": round(i.score, 6),
            "x1": round(i.box.x1, 6),
            "y1": round(i.box.y1, 6),
            "x2": round(i.box.x2, 6),
            "y2": round(i.box.y2, 6),
        }
        for i in sorted(localization.instances, key=lambda i: (i.label.value, -i.score))
    ]


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    budget = Budget(remaining_usd=args.budget_usd)
    files, data = _photos(args)
    if args.limit is not None:
        files = files[: args.limit]
    if not files:
        print("no photos to run", file=sys.stderr)
        return 2
    per_file_labels: dict[str, list[str]] = {}
    notes: list[str] = []
    if args.phrases_from == "vlm":
        if not args.photo_raw.exists():
            print(f"{args.photo_raw} not found", file=sys.stderr)
            return 2
        per_file_labels = vlm_labels(args.photo_raw)
        absent = sorted(key for key, _ in files if key not in per_file_labels)
        if absent:
            notes.append(f"{len(absent)} photos have no VLM labels: countable set only")
    fixed: list[str] = []
    if args.phrases_from == "all":
        fixed = [label.value for label in ComponentLabel]
    elif args.phrases_from == "dataset" and data is not None:
        fixed = dataset_labels(data)
    localizer = make_localizer(settings, budget)
    if localizer is None:
        print("no localizer configured (REPLICATE_API_TOKEN)", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    rec = EvalRun.begin(args.out, harness=__name__, args=args, settings=settings)
    try:
        if args.dataset is not None:
            rec.dataset(args.dataset / DATASET_FILES["detection_boxes"])
            rec.input("images", args.dataset / "images")
        else:
            rec.input("photos", args.photos)
        if args.photo_raw is not None:
            rec.input("photo_raw", args.photo_raw)
        rec.note(phrase_set=args.phrase_set, phrases_from=args.phrases_from)
        ledger = CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="evals.predict_boxes",
            budget=budget,
            recorder=rec.recorder,
        )
        semaphore = asyncio.Semaphore(args.concurrency or settings.localizer_concurrency)
        tiling = args.tiling == "on"

        async def one(key: str, path: Path, repeat: int) -> dict[str, Any]:
            labels = per_file_labels.get(key, []) if args.phrases_from == "vlm" else fixed
            base: dict[str, Any] = {
                "file": key,
                "repeat": repeat,
                "labels": [label.value for label in prompt_labels(labels)],
            }
            async with semaphore:
                started = time.monotonic()
                try:
                    image = prepare_image(path.read_bytes())
                except (ImageRejected, OSError) as exc:
                    return base | {"rejected": str(exc)}
                try:
                    found = await localize_photo(
                        image,
                        labels,
                        localizer,
                        tiling=tiling,
                        threshold=args.threshold,
                        merge_metric=args.merge_metric,
                        phrase_set=args.phrase_set,
                        dedupe=args.phrase_dedupe == "on",
                    )
                except (LocalizerError, BudgetExceededError) as exc:
                    return base | {"error": f"{type(exc).__name__}: {exc}"}
                await record_calls(ledger, localizer, found.calls, args.phrase_set)
            return base | {
                "image": {
                    "width": found.width,
                    "height": found.height,
                    "sha256": found.image_sha256,
                },
                "instances": [i.model_dump(mode="json") for i in found.instances],
                "calls": [c.model_dump(mode="json") for c in found.calls],
                "counts": (
                    class_counts(found, args.phrase_set)
                    if args.count_threshold is None
                    else counts(found, args.count_threshold)
                ),
                "unlocalized": unlocalized(labels, found) if args.phrases_from == "vlm" else [],
                "boxes": _box_record(found),
                "predict_seconds": round(found.predict_seconds, 3),
                "cost_usd": round(found.cost_usd, 6),
                "seconds": round(time.monotonic() - started, 2),
            }

        boxes: dict[str, list[Any]] = {key: [] for key, _ in files}
        photo_counts: dict[str, list[Any]] = {key: [] for key, _ in files}
        errors: list[dict[str, Any]] = []
        calls = 0
        predict_seconds = 0.0
        try:
            with (args.out / "instances_raw.jsonl").open("w", encoding="utf-8") as raw:
                for repeat in range(1, args.repeats + 1):
                    records = await asyncio.gather(*(one(k, p, repeat) for k, p in files))
                    for record in records:
                        boxes_out = record.pop("boxes", None)
                        raw.write(json.dumps(record, sort_keys=True) + "\n")
                        if "error" in record or "rejected" in record:
                            errors.append(
                                {
                                    "file": record["file"],
                                    "repeat": repeat,
                                    "error": record.get("error") or record.get("rejected"),
                                }
                            )
                            continue
                        boxes[record["file"]].append(boxes_out)
                        photo_counts[record["file"]].append(record["counts"])
                        calls += len(record["calls"])
                        predict_seconds += record["predict_seconds"]
                    print(
                        f"repeat {repeat}/{args.repeats}: {len(records)} photos, "
                        f"spent ${budget.spent_usd:.4f}",
                        file=sys.stderr,
                    )
        finally:
            client = getattr(localizer, "client", None)
            if client is not None:
                await client.aclose()

        def fold(values: list[Any]) -> Any:
            return values[0] if len(values) == 1 else {"repeats": values}

        (args.out / "detection_boxes.json").write_text(
            json.dumps({f: fold(v) for f, v in boxes.items() if v}, indent=1, sort_keys=True)
        )
        (args.out / "photo_counts.json").write_text(
            json.dumps({f: fold(v) for f, v in photo_counts.items() if v}, indent=1, sort_keys=True)
        )
        print(f"wrote {args.out} (spent ${budget.spent_usd:.4f})", file=sys.stderr)
        rec.finish(  # run.json and the manifest
            {
                "source": str(args.dataset or args.photos),
                "photos": len(files),
                "repeats": args.repeats,
                "tiling": tiling,
                "merge_metric": args.merge_metric,
                "phrase_set": args.phrase_set,
                "phrase_dedupe": args.phrase_dedupe,
                "threshold": args.threshold,
                "count_threshold": (
                    "per_class" if args.count_threshold is None else args.count_threshold
                ),
                "phrases_from": args.phrases_from,
                "model": "vufinder/sam3",
                "version": settings.sam3_replicate_version,
                "calls": calls,
                "predict_seconds": round(predict_seconds, 3),
                "spent_usd": round(budget.spent_usd, 6),
                "errors": errors,
                "notes": notes,
            }
        )
        return 0
    except BaseException as exc:  # a failed run still leaves its manifest and log
        finish_pending(args, exit_code=1, error=exc)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
