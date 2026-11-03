"""Exhibit A: each photograph with every counted object outlined and numbered, captioned
with the report lines it supports (STUDY_REPORT_SPEC section 6 and 11.3; ATG D.6, D.9).

The outline is drawn here, on the image, before the PDF is rendered (the renderer only
places JPEGs, which keeps the PDF engine out of the geometry): the mask's edge when the
object has a mask (:class:`basis.vision.localize.Rle`), else its box. Images are scaled to
at most 1,200 pixels on the long side and saved as JPEG at quality 80, so a 50-photo study
stays a few megabytes. The same inputs give the same bytes.

The caption links each numbered object to the report's lines: the lines whose category is
the object's label in the photograph's room.
"""

from __future__ import annotations

import io
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from basis.report.model import StudyReport, category_name
from basis.report.render import ExhibitPhoto
from basis.vision import heif

heif.register()  # the photographs are read as uploaded, and an iPhone uploads HEIC

LONG_SIDE: Final = 1200
QUALITY: Final = 80
WIDTH: Final = 3  # outline pixels
PALETTE: Final = (
    (29, 78, 137),
    (196, 69, 54),
    (42, 140, 120),
    (214, 150, 40),
    (110, 70, 160),
    (60, 120, 180),
)


@dataclass(frozen=True, slots=True)
class PhotoObjectView:
    label: str
    box: tuple[float, float, float, float]  # normalized x1, y1, x2, y2
    mask: Mapping[str, Any] | None


def _edge(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1)
    inner = (
        padded[1:-1, 1:-1]
        & padded[:-2, 1:-1]
        & padded[2:, 1:-1]
        & padded[1:-1, :-2]
        & padded[1:-1, 2:]
    )
    edge: np.ndarray = mask & ~inner
    thick: np.ndarray = edge.copy()
    for dy in range(-(WIDTH // 2), WIDTH // 2 + 1):
        for dx in range(-(WIDTH // 2), WIDTH // 2 + 1):
            thick |= np.roll(np.roll(edge, dy, 0), dx, 1)
    return thick


def compose(jpeg: bytes, objects: Sequence[PhotoObjectView]) -> bytes:
    """The photograph, upright and scaled, with each object outlined and numbered 1..n in
    the given order."""
    from basis.vision.localize import Rle

    image = ImageOps.exif_transpose(Image.open(io.BytesIO(jpeg))).convert("RGB")
    image.thumbnail((LONG_SIDE, LONG_SIDE))
    w, h = image.size
    pixels = np.array(image)
    marks: list[tuple[int, int, tuple[int, int, int]]] = []
    for n, obj in enumerate(objects, start=1):
        color = PALETTE[(n - 1) % len(PALETTE)]
        x1, y1, x2, y2 = obj.box
        drawn = False
        if obj.mask:
            try:
                rle = Rle.model_validate(obj.mask)
                mask = (
                    np.asarray(
                        Image.fromarray(rle.decode().astype(np.uint8) * 255).resize(
                            (w, h), Image.Resampling.NEAREST
                        )
                    )
                    > 0
                )
                pixels[_edge(mask)] = color
                drawn = True
            except ValueError:
                drawn = False
        if not drawn:
            box = np.zeros((h, w), dtype=bool)
            box[
                int(y1 * h) : max(int(y2 * h), int(y1 * h) + 1),
                int(x1 * w) : max(int(x2 * w), int(x1 * w) + 1),
            ] = True
            pixels[_edge(box)] = color
        marks.append((int(x1 * w), int(y1 * h), color))
    out = Image.fromarray(pixels)
    draw = ImageDraw.Draw(out)
    font = ImageFont.load_default(size=max(14, h // 34))
    for n, (x, y, color) in enumerate(marks, start=1):
        text = str(n)
        right, bottom = draw.textbbox((0, 0), text, font=font)[2:]
        draw.rectangle((x, y, x + right + 8, y + bottom + 6), fill=color)
        draw.text((x + 4, y + 2), text, fill=(255, 255, 255), font=font)
    buffer = io.BytesIO()
    out.save(buffer, "JPEG", quality=QUALITY, optimize=True)
    return buffer.getvalue()


def line_index(
    report: StudyReport, room_of: Mapping[str, str | None]
) -> dict[tuple[str | None, str], list[int]]:
    """``(room id, category key) -> report lines``; ``room_of`` maps asset id to room id."""
    index: dict[tuple[str | None, str], list[int]] = defaultdict(list)
    for item in report.items:
        for aid in item.asset_ids:
            index[(room_of.get(aid), item.category_key)].append(item.line)
    return {k: sorted(set(v)) for k, v in index.items()}


def caption(
    title: str,
    room_id: str | None,
    objects: Sequence[PhotoObjectView],
    index: Mapping[tuple[str | None, str], list[int]],
) -> str:
    # Consecutive numbers with the same label read as one range: "1-9 outdoor lighting".
    runs: list[tuple[int, int, str]] = []
    for n, obj in enumerate(objects, start=1):
        if runs and runs[-1][2] == obj.label and runs[-1][1] == n - 1:
            runs[-1] = (runs[-1][0], n, obj.label)
        else:
            runs.append((n, n, obj.label))
    parts = []
    for first, last, label in runs:
        key = category_name(label)[0]
        lines = index.get((room_id, key)) or index.get((None, key)) or []
        shown = ", ".join(map(str, lines[:4])) + ("..." if len(lines) > 4 else "")
        where = f" (line{'s' if len(lines) > 1 else ''} {shown})" if lines else ""
        numbers = str(first) if first == last else f"{first}-{last}"
        parts.append(f"{numbers} {label.replace('_', ' ')}{where}")
    return f"{title}: " + ("; ".join(parts) if parts else "no counted components")


def exhibit_photo(
    index_name: int,
    title: str,
    jpeg: bytes,
    room_id: str | None,
    objects: Sequence[PhotoObjectView],
    lines: Mapping[tuple[str | None, str], list[int]],
) -> ExhibitPhoto:
    return ExhibitPhoto(
        name=f"photo-{index_name:03d}.jpg",
        jpeg=compose(jpeg, objects),
        caption=caption(title, room_id, objects, lines),
    )


__all__ = ["PhotoObjectView", "caption", "compose", "exhibit_photo", "line_index"]
