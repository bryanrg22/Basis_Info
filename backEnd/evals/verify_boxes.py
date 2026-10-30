"""``python -m evals.verify_boxes``: a vision model checks each SAM 3 detection, then counts.

Two-stage detection: a detector proposes regions and a classifier decides what each one is
(R-CNN, Girshick et al. 2014; for open vocabulary, ViLD, Gu et al. 2021, classifies
proposals with CLIP). SAM 3 over-counts on our photos (EVAL_REPORT 10.2 and 15: precision
about 0.5 at score 0.5), mostly duplicates across phrases, fragments and look-alikes; a
vision-language model that looks at one detection at a time can reject those. What the
literature warns about, and what this runner does about it:

* A crop alone misleads (ViLD and MarvelOVD, Wang et al. 2024: VLMs label background
  fragments of tight crops as objects): every request shows the whole photo with the box
  outlined in red, then the box enlarged with ``--context`` of its size on each side.
* Yes/no questions get "yes" too often (POPE, Li et al., EMNLP 2023): the model picks what
  the box contains from the count classes plus ``part_or_several`` and ``none``
  (``photo_verify_box/v1``), and the detection counts only when it picks the detection's
  own class.

Detections are those of ``--classes`` scoring at least ``--min-score`` in an
``evals.predict_boxes`` run (``instances_raw.jsonl``), after greedy box NMS at
``--nms-iou`` within each count class (``door`` pools interior and exterior doors, which
SAM 3 finds with overlapping phrases). Output in ``--out``: ``photo_counts.json`` (per photo
and repeat, the confirmed detections per label; the door classes keep SAM 3's label),
``verdicts.jsonl`` (one line per detection and repeat), ``run.json`` and the run record
(``llm_calls.jsonl``, ``llm/``, ``console.log``, ``manifest.json``, and ``results.json`` /
``report.md`` with one case per detection and repeat: passed when the model gave a verdict,
failed when the answer stayed unusable or the input was refused, test_error when the
provider or the harness failed; :mod:`evals.record`). Feed it to
``evals.fuse_counts --counts verified=OUT`` or score it alone with ``evals.run``.

Usage (from ``backEnd/``)::

    python -m evals.verify_boxes --sam3 out/sam3 --photos DIR --out OUT --model gpt-6.1-sol \\
        --reasoning-effort low --max-tokens 6000 --repeats 3
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from basis.config import load_settings
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget, ChatRequest, ImagePart, Message
from basis.vision.fusion import CLASS_LABELS
from basis.vision.prep import JPEG_QUALITY, PreparedImage, prepare_image
from evals.account_stop import guard, stops_on_account_error, track
from evals.predict_photos import photo_files
from evals.predict_tiles import ask_structured
from evals.record import FAILED, PASSED, TEST_ERROR, EvalRun

VerdictLabel = Literal[
    "window",
    "interior_door",
    "exterior_door",
    "light_fixture",
    "outdoor_lighting",
    "electrical_outlet",
    "kitchen_cabinet",
    "part_or_several",
    "none",
]
CLASSES: Final = ("door", "kitchen_cabinet", "outdoor_lighting", "light_fixture")
USER_TEXT: Final = "What does the region outlined in red contain?"


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: VerdictLabel
    confidence: float = Field(ge=0.0, le=1.0)


def count_class(label: str) -> str:
    """The count class a label belongs to (``interior_door`` -> ``door``)."""
    for name, labels in CLASS_LABELS.items():
        if label in labels:
            return name
    return label


def box_iou(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    ix = max(0.0, min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]))
    iy = max(0.0, min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]))
    inter = ix * iy
    union = (
        (a["x2"] - a["x1"]) * (a["y2"] - a["y1"])
        + (b["x2"] - b["x1"]) * (b["y2"] - b["y1"])
        - inter
    )
    return inter / union if union > 0 else 0.0


def detections(
    instances: Sequence[Mapping[str, Any]], classes: Sequence[str], min_score: float, nms_iou: float
) -> list[dict[str, Any]]:
    """The instances of ``classes`` at or above ``min_score``, highest score first, with
    greedy NMS (box IoU above ``nms_iou``) within each count class."""
    ranked = sorted(
        (
            dict(i)
            for i in instances
            if count_class(str(i["label"])) in classes and float(i["score"]) >= min_score
        ),
        key=lambda i: -float(i["score"]),
    )
    kept: list[dict[str, Any]] = []
    for instance in ranked:
        cls = count_class(instance["label"])
        if all(
            count_class(k["label"]) != cls or box_iou(k["box"], instance["box"]) <= nms_iou
            for k in kept
        ):
            kept.append(instance)
    return kept


def _jpeg(image: Image.Image) -> PreparedImage:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return prepare_image(buffer.getvalue())


def views(
    photo: PreparedImage, box: Mapping[str, float], context: float, min_side: int = 512
) -> tuple[PreparedImage, PreparedImage]:
    """``(outlined, enlarged)``: the photo with ``box`` (normalized) outlined in red, and
    the box padded by ``context`` of its size on each side, clipped to the photo and
    enlarged (Lanczos, at most 4 times) so its longer side reaches ``min_side``."""
    with Image.open(io.BytesIO(photo.jpeg)) as image:
        image.load()
        width, height = image.size
        x1, x2 = box["x1"] * width, box["x2"] * width
        y1, y2 = box["y1"] * height, box["y2"] * height
        outlined = image.copy()
        line = max(2, round(max(width, height) / 300))
        ImageDraw.Draw(outlined).rectangle((x1, y1, x2, y2), outline=(255, 0, 0), width=line)
        pad_x, pad_y = (x2 - x1) * context, (y2 - y1) * context
        crop = image.crop(
            (
                max(0, round(x1 - pad_x)),
                max(0, round(y1 - pad_y)),
                min(width, max(round(x2 + pad_x), round(x1 - pad_x) + 1)),
                min(height, max(round(y2 + pad_y), round(y1 - pad_y) + 1)),
            )
        )
        scale = min(4.0, max(1.0, min_side / max(crop.width, crop.height)))
        if scale > 1:
            crop = crop.resize(
                (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                Image.Resampling.LANCZOS,
            )
        if min(crop.size) < 64:  # prepare_image's floor: pad a sliver onto a gray canvas
            canvas = Image.new("RGB", (max(64, crop.width), max(64, crop.height)), (128, 128, 128))
            canvas.paste(crop, (0, 0))
            crop = canvas
        return _jpeg(outlined), _jpeg(crop)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.verify_boxes", description=__doc__)
    parser.add_argument("--sam3", type=Path, required=True)
    parser.add_argument("--photos", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--classes", nargs="+", default=list(CLASSES))
    parser.add_argument("--min-score", type=float, default=0.3)
    parser.add_argument("--nms-iou", type=float, default=0.5)
    parser.add_argument("--context", type=float, default=0.5)
    parser.add_argument("--prompt", default="v1")
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--budget-usd", type=float, default=8.0)
    return parser.parse_args(argv)


def _image(prepared: PreparedImage) -> ImagePart:
    return ImagePart(media_type="image/jpeg", data_base64=prepared.base64, detail="high")


@stops_on_account_error
async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    pool = guard(ProviderPool.from_settings(settings))
    if pool is None:
        print("no model provider configured", file=sys.stderr)
        return 2
    photos = {p.name: p for p in photo_files(args.photos)}
    records = [
        json.loads(line)
        for line in (args.sam3 / "instances_raw.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    found = {
        r["file"]: detections(r["instances"], args.classes, args.min_score, args.nms_iou)
        for r in records
        if "instances" in r and r["file"] in photos
    }
    prompt = prompts.load("photo_verify_box", args.prompt)
    eval_run = EvalRun.begin(args.out, harness=__name__, args=args, settings=settings)
    eval_run.input("photos", args.photos)
    eval_run.input("sam3", args.sam3)
    eval_run.prompt(prompt)
    budget = Budget(remaining_usd=args.budget_usd)
    track(args, budget)
    recorder = eval_run.recorder
    call = CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="evals.verify_boxes",
        budget=budget,
        recorder=recorder,
    )
    model = args.model or settings.vision_model
    semaphore = asyncio.Semaphore(args.concurrency)
    prepared = {name: prepare_image(photos[name].read_bytes()) for name in found}

    async def verify(file: str, index: int, repeat: int) -> dict[str, Any]:
        instance = found[file][index]
        outlined, enlarged = views(prepared[file], instance["box"], args.context)
        request = ChatRequest(
            model=model,
            system=prompt.text,
            messages=(
                Message(
                    role="user", content=USER_TEXT, images=(_image(outlined), _image(enlarged))
                ),
            ),
            response_model=Verdict,
            max_tokens=args.max_tokens,
            temperature=0.0,
            reasoning_effort=args.reasoning_effort,
            prompt_version=prompt.version_id,
            study_id=str(call.study_id),
            activity=call.activity,
        )
        async with semaphore:
            started = time.monotonic()
            try:
                parsed, attempts, errors, reported = await ask_structured(pool, call, request)
            except Exception as exc:
                # Propagates as before (an outage after every retry, an account error, a
                # harness fault); the case says nothing about the model.
                eval_run.case(
                    f"{file}:{index}#{repeat}", TEST_ERROR, kind="detection", observed_error=exc
                )
                raise
            seconds = round(time.monotonic() - started, 2)
        verdict = parsed.model_dump() if isinstance(parsed, Verdict) else None
        return {
            "file": file,
            "repeat": repeat,
            "index": index,
            "label": instance["label"],
            "score": instance["score"],
            "box": instance["box"],
            "verdict": verdict,
            "confirmed": verdict is not None
            and count_class(verdict["label"]) == count_class(instance["label"]),
            "attempts": attempts,
            "errors": errors,
            "model": reported,
            "seconds": seconds,
        }

    counts: dict[str, list[dict[str, int]]] = {f: [] for f in found}
    unanswered = 0
    with (args.out / "verdicts.jsonl").open("w", encoding="utf-8") as out:
        for repeat in range(1, args.repeats + 1):
            results = await asyncio.gather(
                *(verify(f, i, repeat) for f in found for i in range(len(found[f])))
            )
            per_file: dict[str, dict[str, int]] = {f: {} for f in found}
            for result in results:
                out.write(json.dumps(result, sort_keys=True) + "\n")
                unanswered += result["verdict"] is None
                eval_run.case(
                    f"{result['file']}:{result['index']}#{repeat}",
                    PASSED if result["verdict"] is not None else FAILED,
                    kind="detection",
                    detail={
                        "label": result["label"],
                        "verdict": (result["verdict"] or {}).get("label"),
                        "confirmed": result["confirmed"],
                        "attempts": result["attempts"],
                        "errors": result["errors"],
                    },
                    seconds=result["seconds"],
                )
                if result["confirmed"]:
                    tally = per_file[result["file"]]
                    tally[result["label"]] = tally.get(result["label"], 0) + 1
            for file, tally in per_file.items():
                counts[file].append(dict(sorted(tally.items())))
            print(
                f"repeat {repeat}/{args.repeats}: {len(results)} detections, "
                f"spent ${budget.spent_usd:.4f}",
                file=sys.stderr,
            )

    def fold(values: list[Any]) -> Any:
        return values[0] if len(values) == 1 else {"repeats": values}

    (args.out / "photo_counts.json").write_text(
        json.dumps({f: fold(v) for f, v in counts.items()}, indent=1, sort_keys=True)
    )
    report = {
        "sam3": str(args.sam3),
        "classes": list(args.classes),
        "min_score": args.min_score,
        "nms_iou": args.nms_iou,
        "context": args.context,
        "detections": sum(len(v) for v in found.values()),
        "photos": len(found),
        "repeats": args.repeats,
        "prompt": prompt.version_id,
        "model_requested": model,
        "models_reported": sorted({str(getattr(r, "model", "")) for r in recorder.records} - {""}),
        "reasoning_effort": args.reasoning_effort,
        "max_tokens": args.max_tokens,
        "calls": len(recorder.records),
        "unanswered": unanswered,
        "spent_usd": round(budget.spent_usd, 6),
    }
    print(f"wrote {args.out} (spent ${budget.spent_usd:.4f})", file=sys.stderr)
    eval_run.finish(report)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
