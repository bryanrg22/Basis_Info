"""``python -m evals.render_masks``: draw the localizer's instances on their photos.

For a reviewer to look at results (and as the first sketch of the engineer's highlight):
each instance's mask outline is drawn as a soft glow (a wide, blurred, translucent band)
under a crisp outline, in one colour per label, with ``label score`` at the box corner.
Instances come from ``evals.predict_boxes``' ``instances_raw.jsonl``; the photo is prepared
again with :func:`basis.vision.prep.prepare_image` (deterministic), so it has the size
the masks were made at. Writes ``<out>/<photo stem>.r<repeat>.png``.

Usage (from ``backEnd/``)::

    python -m evals.render_masks --raw out/sam3/instances_raw.jsonl \\
        --photos ~/basis-eval-data/openimages/images --out out/sam3/render --min-score 0.5
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from basis.vision.localize import Instance
from basis.vision.prep import ImageRejected, prepare_image
from basis.vision.schemas import ComponentLabel

# Colour-blind-safe qualitative palette (Okabe-Ito), then a few extra hues.
PALETTE: Final = (
    (230, 159, 0),
    (86, 180, 233),
    (0, 158, 115),
    (240, 228, 66),
    (0, 114, 178),
    (213, 94, 0),
    (204, 121, 167),
    (255, 255, 255),
    (153, 255, 153),
    (255, 102, 102),
)
GLOW_WIDTH: Final = 9
GLOW_ALPHA: Final = 0.55
LINE_ALPHA: Final = 235
_ORDER: Final = {label: i for i, label in enumerate(ComponentLabel)}


def colour(label: ComponentLabel) -> tuple[int, int, int]:
    return PALETTE[_ORDER[label] % len(PALETTE)]


def _mask_image(instance: Instance, size: tuple[int, int]) -> Image.Image:
    width, height = size
    if instance.mask is not None:
        array = instance.mask.decode().astype(np.uint8) * 255
        mask = Image.fromarray(array, mode="L")
        return mask if mask.size == size else mask.resize(size, Image.Resampling.NEAREST)
    mask = Image.new("L", size, 0)
    box = instance.box
    ImageDraw.Draw(mask).rectangle(
        (box.x1 * width, box.y1 * height, box.x2 * width - 1, box.y2 * height - 1), fill=255
    )
    return mask


def render(
    photo: Image.Image, instances: Sequence[Instance], min_score: float = 0.0
) -> Image.Image:
    """``photo`` with every instance at or above ``min_score`` highlighted."""
    base = photo.convert("RGBA")
    font = ImageFont.load_default()
    labels: list[tuple[Instance, tuple[int, int, int]]] = []
    for instance in sorted(instances, key=lambda i: i.score):
        if instance.score < min_score:
            continue
        rgb = colour(instance.label)
        mask = _mask_image(instance, base.size)
        edge = ImageChops.subtract(
            mask.filter(ImageFilter.MaxFilter(3)), mask.filter(ImageFilter.MinFilter(3))
        )
        glow = edge.filter(ImageFilter.MaxFilter(GLOW_WIDTH)).filter(
            ImageFilter.GaussianBlur(GLOW_WIDTH / 2)
        )
        glow = glow.point(lambda v: int(v * GLOW_ALPHA))
        for alpha in (glow, edge.point(lambda v: LINE_ALPHA if v else 0)):
            layer = Image.new("RGBA", base.size, (*rgb, 0))
            layer.putalpha(alpha)
            base = Image.alpha_composite(base, layer)
        labels.append((instance, rgb))
    draw = ImageDraw.Draw(base)
    for instance, rgb in labels:
        text = f"{instance.label.value} {instance.score:.2f}"
        x, y = instance.box.x1 * base.width + 2, instance.box.y1 * base.height + 2
        left, top, right, bottom = draw.textbbox((x, y), text, font=font)
        draw.rectangle((left - 2, top - 1, right + 2, bottom + 1), fill=(0, 0, 0, 170))
        draw.text((x, y), text, fill=(*rgb, 255), font=font)
    return base.convert("RGB")


def render_record(photo_bytes: bytes, record: dict[str, Any], min_score: float) -> Image.Image:
    image = prepare_image(photo_bytes)
    expected = record.get("image") or {}
    if (expected.get("width"), expected.get("height")) not in (
        (None, None),
        (image.width, image.height),
    ):
        raise ValueError(
            f"{record['file']}: the masks are {expected['width']} x {expected['height']}, "
            f"the prepared photo is {image.width} x {image.height}"
        )
    instances = [Instance.model_validate(i) for i in record.get("instances", [])]
    return render(Image.open(io.BytesIO(image.jpeg)), instances, min_score)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.render_masks", description=__doc__)
    parser.add_argument("--raw", type=Path, required=True, help="predict_boxes instances_raw.jsonl")
    parser.add_argument("--photos", type=Path, required=True, help="directory of the photos")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--min-score", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    written = 0
    for line in args.raw.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("repeat", 1) != args.repeat or "instances" not in record:
            continue
        if args.limit is not None and written >= args.limit:
            break
        candidates = [args.photos / record["file"], args.photos / "images" / record["file"]]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            print(f"photo not found: {record['file']}", file=sys.stderr)
            continue
        try:
            picture = render_record(path.read_bytes(), record, args.min_score)
        except (ImageRejected, ValueError) as exc:
            print(f"skipped {record['file']}: {exc}", file=sys.stderr)
            continue
        picture.save(args.out / f"{Path(record['file']).stem}.r{args.repeat}.png")
        written += 1
    print(f"wrote {written} images to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
