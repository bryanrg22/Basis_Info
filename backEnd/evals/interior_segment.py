"""``python -m evals.interior_segment``: the ADE20K class of every pixel of the interior photos
(EVAL_REPORT 107), with the segmenter production already runs.

The checkpoint and post-processing are :class:`basis.vision.surface_segmenter.LocalMask2Former`'s
(``facebook/mask2former-swin-large-ade-panoptic``, panoptic at score 0.05, nothing fused), run
locally on the CPU; production keeps only wall, floor and ceiling, this keeps every class, because
the measuring needs the doors and counters too (:mod:`evals.room_measure`). Each photo's
map is written beside it as ``photo_<k>.ade.png`` (8-bit: the ADE20K class id, 255 where no
segment), with the seconds each took.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image

from basis.vision.surface_segmenter import LOCAL_CHECKPOINT, SCORE_FLOOR
from evals.interior_data import interiors_dir

NONE: int = 255


def segment(model: object, processor: object, image: Image.Image) -> np.ndarray:
    import torch

    inputs = processor(images=image, return_tensors="pt")  # type: ignore[operator]
    with torch.no_grad():
        outputs = model(**inputs)  # type: ignore[operator]
    result = processor.post_process_panoptic_segmentation(  # type: ignore[attr-defined]
        outputs,
        threshold=SCORE_FLOOR,
        label_ids_to_fuse=set(),
        target_sizes=[(image.height, image.width)],
    )[0]
    seg = result["segmentation"].cpu().numpy()
    out = np.full(seg.shape, NONE, dtype=np.uint8)
    for s in result["segments_info"]:
        out[seg == s["id"]] = int(s["label_id"])
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.interior_segment", description=__doc__)
    parser.add_argument("--dir", type=Path, default=None)
    args = parser.parse_args(argv)
    base = (args.dir or interiors_dir()) / "photos"
    import transformers

    processor = transformers.AutoImageProcessor.from_pretrained(  # type: ignore[no-untyped-call]
        LOCAL_CHECKPOINT
    )
    model = transformers.AutoModelForUniversalSegmentation.from_pretrained(
        LOCAL_CHECKPOINT, use_safetensors=True
    ).eval()
    for meta in sorted(base.glob("*/photos.json")):
        data = json.loads(meta.read_text())
        for photo in data["photos"]:
            path = meta.parent / photo["file"]
            out = path.with_suffix(".ade.png")
            if out.exists():
                continue
            started = time.monotonic()
            labels = segment(model, processor, Image.open(path).convert("RGB"))
            Image.fromarray(labels).save(out)
            photo["segment_seconds"] = round(time.monotonic() - started, 3)
        meta.write_text(json.dumps(data, indent=1) + "\n")
        print(meta.parent.name, "segmented")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
