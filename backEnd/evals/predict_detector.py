"""``python -m evals.predict_detector``: a trained segmenter or an open-vocabulary detector on
a photo directory, saved in the SAM 3 pool format (EVAL_REPORT section 26).

Detectors (``--detector``), all run locally through Hugging Face ``transformers`` (weights
fetched once from the named checkpoint, safetensors only, no remote code):

* ``mask2former`` (``facebook/mask2former-swin-large-ade-panoptic``) and ``oneformer``
  (``shi-labs/oneformer_ade20k_swin_large``): panoptic segmentation trained on ADE20K's
  150 classes. Every segment of a class :mod:`basis.vision.ade20k` maps becomes an instance
  whose phrase is the ADE20K class name and whose label depends on whether the photo is an
  exterior view (:func:`basis.vision.ade20k.is_exterior`); the wall, floor and ceiling
  segments are written to ``surfaces_raw.jsonl`` for the surface crops.
* ``owlv2`` (``google/owlv2-large-patch14-ensemble``), ``gdino``
  (``IDEA-Research/grounding-dino-base``), ``llmdet`` (``iSEE-Laboratory/llmdet_base``;
  the Swin-L checkpoints mis-scale boxes, EVAL_REPORT 26.12)
  and ``mmgdino`` (``openmmlab-community/mm_grounding_dino_large_all``): open-vocabulary
  detectors prompted with SAM 3's phrase pool (:data:`basis.vision.localize.PHRASE_SETS`
  ``pool``), each phrase on its own as SAM 3 answers each phrase on its own, so
  ``evals.sam3_select select`` chooses phrases and thresholds per class for them exactly as
  it did for SAM 3 (section 19). Within a phrase, boxes overlapping a higher-scoring box by
  IoU > :data:`PHRASE_NMS_IOU` are dropped (none of the four applies NMS itself).

Output::

    <out>/instances_raw.jsonl   {"file", "repeat": 1, "width", "height", "seconds",
                                 "exterior" (segmenters), "instances": [Instance]}
    <out>/surfaces_raw.jsonl    segmenters only: {"file", "surfaces": {"wall": Rle, ...},
                                 "areas": {ade class: share}}
    <out>/photo_components.json segmenters only: the labels of segments scoring >= 0.5
    <out>/run.json              detector, checkpoint, device, phrases, timing
    <out>/run.log, manifest.json   the run record (evals.record; no paid calls)

The detectors are deterministic, so one pass is the three repeats of the other systems.
Then (from ``backEnd/``)::

    python -m evals.sam3_select select --pool OUT --dataset ~/basis-eval-data/detection \\
        --out OUT/select [--candidates observed] [--grid-min 0.05]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from basis.vision import ade20k
from basis.vision.localize import PHRASE_SETS, Box, Instance, Rle
from basis.vision.schemas import ComponentLabel
from evals.predict_photos import photo_files
from evals.record import EvalRun, finish_pending

SEGMENTERS: Final[dict[str, str]] = {
    "mask2former": "facebook/mask2former-swin-large-ade-panoptic",
    "oneformer": "shi-labs/oneformer_ade20k_swin_large",
}
OPEN_VOCABULARY: Final[dict[str, str]] = {
    "owlv2": "google/owlv2-large-patch14-ensemble",
    "gdino": "IDEA-Research/grounding-dino-base",
    # The Swin-L checkpoints (llmdet_large, mm_grounding_dino_large_*) give vertically squashed
    # boxes under transformers 5.19 (EVAL_REPORT 26.12); the Swin-B one is correct.
    "llmdet": "iSEE-Laboratory/llmdet_base",
    "mmgdino": "openmmlab-community/mm_grounding_dino_large_all",
}
# Mask2Former's post-processing reads OneFormer's outputs too (same query/mask heads, same
# 150 classes); transformers' OneFormer post-processor fails without torchvision.
POSTPROCESSOR: Final = SEGMENTERS["mask2former"]
CANDIDATE_SCORE: Final = 0.05  # keep everything above this; sam3_select picks thresholds
LABEL_SET_SCORE: Final = 0.5
PHRASE_NMS_IOU: Final = 0.5
COUNTABLE_POOL: Final[tuple[ComponentLabel, ...]] = (
    ComponentLabel.WINDOW,
    ComponentLabel.INTERIOR_DOOR,
    ComponentLabel.EXTERIOR_DOOR,
    ComponentLabel.LIGHT_FIXTURE,
    ComponentLabel.OUTDOOR_LIGHTING,
    ComponentLabel.ELECTRICAL_OUTLET,
    ComponentLabel.KITCHEN_CABINET,
)


def pool_phrases() -> list[tuple[str, ComponentLabel]]:
    """``(phrase, label)`` for every phrase of SAM 3's pool for the counted classes."""
    return [(p, label) for label in COUNTABLE_POOL for p in PHRASE_SETS["pool"][label]]


# --------------------------------------------------------------------------- pure helpers


@dataclass(frozen=True)
class Segment:
    """One panoptic segment: its ADE20K class name, score and binary mask."""

    name: str
    score: float
    mask: npt.NDArray[np.bool_]


def mask_box(mask: npt.NDArray[np.bool_]) -> Box | None:
    """The normalized box around a mask's pixels, or None for an empty mask."""
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if rows.size == 0:
        return None
    height, width = mask.shape
    return Box.from_pixels(cols[0], rows[0], cols[-1] + 1, rows[-1] + 1, width, height)


def class_areas(segments: Sequence[Segment]) -> dict[str, float]:
    """ADE20K class name -> share of the image its segments cover."""
    areas: dict[str, float] = {}
    for s in segments:
        name = ade20k.normalize(s.name)
        areas[name] = areas.get(name, 0.0) + float(s.mask.mean())
    return {k: round(v, 5) for k, v in areas.items()}


def segment_instances(segments: Sequence[Segment], exterior: bool) -> list[Instance]:
    """The segments of mapped classes as instances (phrase = ADE20K class name)."""
    found: list[Instance] = []
    for s in segments:
        target = ade20k.target(s.name)
        box = mask_box(s.mask)
        if target is None or box is None or s.score < CANDIDATE_SCORE:
            continue
        found.append(
            Instance(
                label=target.label(exterior),
                phrase=ade20k.normalize(s.name),
                score=round(min(1.0, max(0.0, s.score)), 4),
                box=box,
                mask=Rle.encode(s.mask),
                area=round(float(s.mask.mean()), 6),
                source="model",
            )
        )
    return found


def surface_masks(segments: Sequence[Segment]) -> dict[str, Rle]:
    """``wall`` / ``floor`` / ``ceiling`` -> the union of that class's segments."""
    merged: dict[str, npt.NDArray[np.bool_]] = {}
    for s in segments:
        kind = ade20k.surface(s.name)
        if kind is None:
            continue
        merged[kind] = merged[kind] | s.mask if kind in merged else s.mask.copy()
    return {kind: Rle.encode(mask) for kind, mask in sorted(merged.items())}


def nms(boxes: Sequence[tuple[float, Box]], iou: float = PHRASE_NMS_IOU) -> list[tuple[float, Box]]:
    """Greedy NMS by score (input order breaks ties)."""
    order = sorted(range(len(boxes)), key=lambda i: (-boxes[i][0], i))
    kept: list[int] = []
    for i in order:
        if all(boxes[i][1].iou(boxes[j][1]) <= iou for j in kept):
            kept.append(i)
    return [boxes[i] for i in kept]


def box_instances(
    phrase: str, label: ComponentLabel, scored: Sequence[tuple[float, Box]]
) -> list[Instance]:
    """One phrase's detections as instances, NMS applied, low scores dropped."""
    kept = nms([(s, b) for s, b in scored if s >= CANDIDATE_SCORE])
    return [
        Instance(
            label=label,
            phrase=phrase,
            score=round(min(1.0, max(0.0, s)), 4),
            box=b,
            area=round(b.area, 6),
            source="model",
        )
        for s, b in kept
    ]


def label_set(instances: Sequence[Instance], min_score: float = LABEL_SET_SCORE) -> list[str]:
    return sorted({i.label.value for i in instances if i.score >= min_score})


# --------------------------------------------------------------------------- models


Image = Any  # PIL.Image.Image


def _transformers() -> Any:
    """``transformers``, imported lazily and untyped (its stubs do not cover the
    auto classes' ``from_pretrained``)."""
    import importlib

    return importlib.import_module("transformers")


def _device(name: str) -> str:
    import torch

    if name != "auto":
        return name
    return "mps" if torch.backends.mps.is_available() else "cpu"


class Segmenter:
    """An ADE20K panoptic checkpoint: image -> segments (every query above the score
    floor; ``label_ids_to_fuse`` empty, so each instance of a class stays separate)."""

    def __init__(self, checkpoint: str, device: str) -> None:
        hf = _transformers()
        self.oneformer = "oneformer" in checkpoint
        # OneFormer keeps float64 buffers MPS cannot hold.
        self.device = "cpu" if self.oneformer and device == "mps" else device
        if self.oneformer:
            self.processor = hf.OneFormerProcessor.from_pretrained(checkpoint)
            self.model = hf.OneFormerForUniversalSegmentation.from_pretrained(
                checkpoint, use_safetensors=True
            )
        else:
            self.processor = hf.AutoImageProcessor.from_pretrained(checkpoint)
            self.model = hf.AutoModelForUniversalSegmentation.from_pretrained(
                checkpoint, use_safetensors=True
            )
        self.post = hf.AutoImageProcessor.from_pretrained(POSTPROCESSOR)
        self.model.to(self.device).eval()
        self.names = {int(k): str(v) for k, v in self.model.config.id2label.items()}

    def __call__(self, image: Image) -> list[Segment]:
        import torch

        if self.oneformer:
            inputs = self.processor(images=image, task_inputs=["panoptic"], return_tensors="pt")
        else:
            inputs = self.processor(images=image, return_tensors="pt")
        inputs = {k: v.to(self.device) if hasattr(v, "to") else v for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self.model(**inputs)
        result = self.post.post_process_panoptic_segmentation(
            outputs,
            threshold=CANDIDATE_SCORE,
            label_ids_to_fuse=set(),
            target_sizes=[(image.height, image.width)],
        )[0]
        segmentation = result["segmentation"].cpu().numpy()
        return [
            Segment(self.names[int(s["label_id"])], float(s["score"]), segmentation == s["id"])
            for s in result["segments_info"]
        ]


class OpenVocabularyDetector:
    """An open-vocabulary checkpoint: (image, phrase) -> scored boxes, one phrase per pass
    (OWLv2 scores every phrase of one pass independently, so its phrases share a pass)."""

    def __init__(self, checkpoint: str, device: str) -> None:
        hf = _transformers()
        self.owl = "owlv2" in checkpoint
        self.device = device
        self.processor = hf.AutoProcessor.from_pretrained(checkpoint)
        self.model = hf.AutoModelForZeroShotObjectDetection.from_pretrained(
            checkpoint, use_safetensors=True
        )
        self.model.to(device).eval()

    def owl_pass(self, image: Image, phrases: Sequence[str]) -> list[list[tuple[float, Box]]]:
        import torch

        inputs = self.processor(text=[list(phrases)], images=image, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self.model(**inputs)
        # OWLv2 pads the image to a square; boxes are relative to the padded square.
        side = max(image.width, image.height)
        logits = outputs.logits[0].sigmoid().cpu().numpy()  # (boxes, phrases)
        boxes = outputs.pred_boxes[0].cpu().numpy()  # cx, cy, w, h on the padded square
        found: list[list[tuple[float, Box]]] = [[] for _ in phrases]
        for b, (cx, cy, w, h) in enumerate(boxes):
            x1, y1, x2, y2 = (
                (cx - w / 2) * side,
                (cy - h / 2) * side,
                (cx + w / 2) * side,
                (cy + h / 2) * side,
            )
            if x1 >= image.width or y1 >= image.height:
                continue  # entirely in the padding
            box = Box.from_pixels(x1, y1, x2, y2, image.width, image.height)
            for p in range(len(phrases)):
                score = float(logits[b, p])
                if score >= CANDIDATE_SCORE:
                    found[p].append((score, box))
        return found

    def grounded_pass(self, image: Image, phrase: str) -> list[tuple[float, Box]]:
        import torch

        inputs = self.processor(images=image, text=f"{phrase}.", return_tensors="pt")
        inputs = {k: v.to(self.device) if hasattr(v, "to") else v for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self.model(**inputs)
        result = self.processor.post_process_grounded_object_detection(
            outputs,
            input_ids=inputs.get("input_ids"),
            threshold=CANDIDATE_SCORE,
            text_threshold=0.0,
            target_sizes=[(image.height, image.width)],
        )[0]
        scores, boxes = result["scores"].cpu().numpy(), result["boxes"].cpu().numpy()
        return [
            (float(s), Box.from_pixels(float(x1), float(y1), float(x2), float(y2), *image.size))
            for s, (x1, y1, x2, y2) in zip(scores, boxes, strict=True)
        ]

    def __call__(
        self, image: Image, phrases: Sequence[tuple[str, ComponentLabel]]
    ) -> list[Instance]:
        texts = [p for p, _ in phrases]
        if self.owl:
            per_phrase = self.owl_pass(image, texts)
        else:
            per_phrase = [self.grounded_pass(image, p) for p in texts]
        found: list[Instance] = []
        for (phrase, label), scored in zip(phrases, per_phrase, strict=True):
            found += box_instances(phrase, label, scored)
        return found


# --------------------------------------------------------------------------- runner


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.predict_detector", description=__doc__)
    parser.add_argument("--photos", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--detector", required=True, choices=sorted({**SEGMENTERS, **OPEN_VOCABULARY})
    )
    parser.add_argument("--checkpoint", default=None, help="override the detector's checkpoint")
    parser.add_argument("--device", default="auto", help="auto (mps when available), cpu, mps")
    parser.add_argument("--limit", type=int, default=None, help="first N photos (smoke test)")
    return parser.parse_args(argv)


def run(
    args: argparse.Namespace,
    segmenter: Callable[[Image], list[Segment]] | None = None,
    detector: Callable[[Image, Sequence[tuple[str, ComponentLabel]]], list[Instance]] | None = None,
) -> int:
    """Run one detector (``segmenter`` / ``detector`` inject fakes in tests)."""
    from PIL import Image as PILImage

    files = photo_files(args.photos)[: args.limit]
    if not files:
        print(f"no photos in {args.photos}", file=sys.stderr)
        return 2
    is_segmenter = args.detector in SEGMENTERS
    checkpoint = args.checkpoint or {**SEGMENTERS, **OPEN_VOCABULARY}[args.detector]
    device = _device(args.device) if segmenter is None and detector is None else "fake"
    started = time.monotonic()
    if is_segmenter and segmenter is None:
        segmenter = Segmenter(checkpoint, device)
    if not is_segmenter and detector is None:
        detector = OpenVocabularyDetector(checkpoint, device)
    load_seconds = time.monotonic() - started
    # the device the model actually runs on (OneFormer moves itself to the CPU)
    device = str(getattr(segmenter if is_segmenter else detector, "device", device))
    phrases = pool_phrases()
    args.out.mkdir(parents=True, exist_ok=True)
    rec = EvalRun.begin(args.out, harness=__name__, args=args)
    rec.input("photos", args.photos)
    rec.note(detector=args.detector, checkpoint=checkpoint, device=device)
    try:
        components: dict[str, list[str]] = {}
        seconds: list[float] = []
        with (
            (args.out / "instances_raw.jsonl").open("w", encoding="utf-8") as raw,
            (args.out / "surfaces_raw.jsonl").open("w", encoding="utf-8") as surfaces,
        ):
            for number, file in enumerate(files, start=1):
                image = PILImage.open(file).convert("RGB")
                t0 = time.monotonic()
                record: dict[str, Any] = {
                    "file": file.name,
                    "repeat": 1,
                    "width": image.width,
                    "height": image.height,
                }
                if is_segmenter:
                    assert segmenter is not None
                    segments = segmenter(image)
                    areas = class_areas(segments)
                    exterior = ade20k.is_exterior(areas)
                    instances = segment_instances(segments, exterior)
                    record["exterior"] = exterior
                    surfaces.write(
                        json.dumps(
                            {
                                "file": file.name,
                                "exterior": exterior,
                                "areas": areas,
                                "surfaces": {
                                    k: v.model_dump(mode="json")
                                    for k, v in surface_masks(segments).items()
                                },
                            }
                        )
                        + "\n"
                    )
                    components[file.name] = label_set(instances)
                else:
                    assert detector is not None
                    instances = detector(image, phrases)
                elapsed = time.monotonic() - t0
                seconds.append(elapsed)
                record["seconds"] = round(elapsed, 3)
                record["instances"] = [i.model_dump(mode="json") for i in instances]
                raw.write(json.dumps(record) + "\n")
                print(
                    f"{number}/{len(files)} {file.name}: {len(instances)} instances, "
                    f"{elapsed:.1f}s",
                    file=sys.stderr,
                )
        if not is_segmenter:
            (args.out / "surfaces_raw.jsonl").unlink()
        else:
            (args.out / "photo_components.json").write_text(
                json.dumps(components, indent=1, sort_keys=True)
            )
        print(f"wrote {args.out}", file=sys.stderr)
        rec.finish(  # run.json and the manifest
            {
                "detector": args.detector,
                "checkpoint": checkpoint,
                "device": device,
                "photos": len(files),
                "load_seconds": round(load_seconds, 2),
                "seconds_per_photo": round(sum(seconds) / len(seconds), 3),
                "candidate_score": CANDIDATE_SCORE,
                "phrases": None if is_segmenter else [p for p, _ in phrases],
                "class_map": {
                    k: [v.interior.value, v.exterior.value] for k, v in ade20k.CLASS_MAP.items()
                }
                if is_segmenter
                else None,
                "exterior_rule": [ade20k.EXTERIOR_SKY_SHARE, ade20k.EXTERIOR_OUTDOOR_SHARE]
                if is_segmenter
                else None,
                "cost_usd": 0.0,
            }
        )
        return 0
    except BaseException as exc:  # a failed run still leaves its manifest and log
        finish_pending(args, exit_code=1, error=exc)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
