"""Detector and language model together: numbered marks, keep/reject/add answers, points.

Three orderings of SAM 3 (:mod:`basis.vision.localize`) and a vision-language model are
measured in EVAL_REPORT section 21; this module holds the parts they share, so the eval
runners (``evals.predict_marks``, ``evals.predict_points``) stay thin and the rules are
unit-tested.

* Set-of-Mark prompting (Yang et al. 2023, arXiv 2310.11441): the detector proposes regions,
  each is drawn on the photo with a number, and the model answers by number.
  :func:`select_marks` picks the proposals (countable labels, a recall-oriented score
  threshold, one mark per object across labels by mask overlap, reading order),
  :func:`render_marks` draws them (``box`` or mask ``outline``, a numbered tag outside the
  object so a small outlet is not covered), :class:`MarksReview` is the answer (a verdict
  per mark plus objects the detector missed, each with an approximate point), and
  :func:`tally_review` turns it into counts (kept + added).
* The loop (the SAM 3 Agent pattern of the SAM 3 paper, arXiv 2511.16719: propose, filter,
  prompt again): :func:`resolve_points` gives each added point a mask, from the saved
  low-threshold SAM 3 candidates under the point (:func:`basis.vision.localize.pick_prompted`)
  or, for the runner, a fresh point prompt; a point inside an object already counted, or
  whose mask overlaps one by more than ``overlap``, is a duplicate.
* Point-then-count (arXiv 2603.21746; "locate, label, then count", arXiv 2512.15254):
  :class:`PointAnswer` is a model's answer that points at every instance before it counts;
  :func:`parse_molmo_points` reads Molmo's ``<point>``/``<points>`` tags (coordinates in
  percent of the image).

Coordinates are fractions of the image (0 = left/top, 1 = right/bottom) everywhere.
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, field_validator
from scipy import ndimage

from basis.vision.localize import Instance, Point, box_iou, mask_iou, pick_prompted

# The seven labels behind the six count classes (doors are scored pooled).
MarkLabel = Literal[
    "window",
    "interior_door",
    "exterior_door",
    "light_fixture",
    "outdoor_lighting",
    "electrical_outlet",
    "kitchen_cabinet",
]
MARK_LABELS: Final[tuple[str, ...]] = (
    "window",
    "interior_door",
    "exterior_door",
    "light_fixture",
    "outdoor_lighting",
    "electrical_outlet",
    "kitchen_cabinet",
)
Rejection = Literal["duplicate", "part_or_group", "not_countable"]
REJECTIONS: Final[tuple[str, ...]] = ("duplicate", "part_or_group", "not_countable")
# Labels a detector confuses with each other: one object, one mark, and a point may take the
# mask of either. A door and a window are not grouped (a glazed door is a door).
FAMILY: Final[dict[str, str]] = {
    "interior_door": "door",
    "exterior_door": "door",
    "light_fixture": "light",
    "outdoor_lighting": "light",
}
Style = Literal["box", "outline"]

DEFAULT_MIN_SCORE: Final = 0.3
DEFAULT_MARK_NMS: Final = 0.6
DEFAULT_MAX_MARKS: Final = 60
DEFAULT_OVERLAP: Final = 0.5
RENDER_LONG_SIDE: Final = 1024
PALETTE: Final[tuple[tuple[int, int, int], ...]] = (
    (230, 25, 75),
    (60, 180, 75),
    (0, 130, 200),
    (245, 130, 48),
    (145, 30, 180),
    (0, 160, 160),
    (240, 50, 230),
    (128, 128, 0),
    (170, 110, 40),
    (0, 0, 128),
)


def family(label: str) -> str:
    return FAMILY.get(label, label)


# --------------------------------------------------------------------------- marks


@dataclass(frozen=True, slots=True)
class Mark:
    """One numbered proposal (``number`` is 1-based, as drawn)."""

    number: int
    instance: Instance

    @property
    def label(self) -> str:
        return self.instance.label.value


def _overlap(a: Instance, b: Instance, decoded: dict[int, Any]) -> float:
    if a.mask is not None and b.mask is not None and a.mask.counts and b.mask.counts:
        ma = decoded.setdefault(id(a), a.mask.decode())
        mb = decoded.setdefault(id(b), b.mask.decode())
        if ma.shape == mb.shape:
            return mask_iou(ma, mb)
    return box_iou(a.box, b.box)


def select_marks(
    instances: Sequence[Instance],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    nms: float = DEFAULT_MARK_NMS,
    max_marks: int = DEFAULT_MAX_MARKS,
    labels: Sequence[str] = MARK_LABELS,
) -> list[Mark]:
    """The proposals to draw: instances of ``labels`` scoring at least ``min_score``, one per
    object (greedy NMS across labels, highest score first: an instance overlapping a kept one
    by more than ``nms`` mask IoU, box IoU without masks, is the same object found by another
    phrase or label), at most ``max_marks`` (the highest scoring), numbered in reading order
    (rows of a tenth of the image, then left to right)."""
    wanted = set(labels)
    ranked = sorted(
        (i for i in instances if i.label.value in wanted and i.score >= min_score),
        key=lambda i: -i.score,
    )
    decoded: dict[int, Any] = {}
    kept: list[Instance] = []
    for candidate in ranked:
        if all(_overlap(candidate, k, decoded) <= nms for k in kept):
            kept.append(candidate)
        if len(kept) >= max_marks:
            break

    def reading(i: Instance) -> tuple[int, float]:
        cy = (i.box.y1 + i.box.y2) / 2
        return int(cy * 10), (i.box.x1 + i.box.x2) / 2

    return [Mark(number=n, instance=i) for n, i in enumerate(sorted(kept, key=reading), start=1)]


def mark_list_text(marks: Sequence[Mark]) -> str:
    """The marks as the model reads them: ``1: window`` one per line."""
    return "\n".join(f"{m.number}: {m.label.replace('_', ' ')}" for m in marks)


# --------------------------------------------------------------------------- drawing


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _outline(mask: np.ndarray[Any, Any], width: int) -> np.ndarray[Any, Any]:
    mask = ndimage.binary_fill_holes(mask)
    eroded = ndimage.binary_erosion(mask, iterations=width, border_value=0)
    edge: np.ndarray[Any, Any] = np.logical_and(mask, np.logical_not(eroded))
    return edge


def _tag_spots(
    box: tuple[float, float, float, float], tw: int, th: int, w: int, h: int
) -> list[tuple[int, int]]:
    x1, y1, x2, y2 = (round(v) for v in box)
    spots = [
        (x1, y1 - th),  # above, left-aligned
        (x1, y2),  # below
        (x2 - tw, y1 - th),  # above, right-aligned
        (x2 - tw, y2),
        (x1 - tw, y1),  # left of the box
        (x2, y1),  # right of the box
        (x1, y1),  # inside, top-left: last resort
    ]
    return [(min(max(0, x), w - tw), min(max(0, y), h - th)) for x, y in spots]


def _hits(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def render_marks(
    jpeg: bytes, marks: Sequence[Mark], *, style: Style = "box", long_side: int = RENDER_LONG_SIDE
) -> bytes:
    """The photo with every mark drawn and numbered, as a JPEG.

    The photo is enlarged (Lanczos) so its longer side is ``long_side`` (lines and digits
    then cover fewer of the photo's own pixels); each mark is its box (``box``) or the
    outline of its mask (``outline``; the box when the mask is missing), in one of ten
    colours, with its number on a filled tag of the same colour placed outside the object
    where there is room, avoiding tags already placed."""
    with Image.open(io.BytesIO(jpeg)) as source:
        image = source.convert("RGB")
    scale = long_side / max(image.size)
    if scale != 1:
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS
        )
    w, h = image.size
    draw = ImageDraw.Draw(image)
    line = max(2, round(long_side / 400))
    font = _font(max(12, round(long_side / 60)))
    overlay = np.asarray(image).copy()
    for mark in marks:
        colour = PALETTE[(mark.number - 1) % len(PALETTE)]
        rle = mark.instance.mask
        if style == "outline" and rle is not None and rle.counts and rle.area() > 0:
            mask = Image.fromarray(rle.decode().astype(np.uint8) * 255).resize(
                (w, h), Image.Resampling.NEAREST
            )
            edge = _outline(np.asarray(mask) > 0, line)
            overlay[edge] = colour
    if style == "outline":
        image = Image.fromarray(overlay)
        draw = ImageDraw.Draw(image)
    placed: list[tuple[int, int, int, int]] = []
    for mark in marks:
        colour = PALETTE[(mark.number - 1) % len(PALETTE)]
        b = mark.instance.box
        box = (b.x1 * w, b.y1 * h, b.x2 * w, b.y2 * h)
        rle = mark.instance.mask
        if style == "box" or rle is None or not rle.counts or rle.area() == 0:
            draw.rectangle(box, outline=colour, width=line)
        text = str(mark.number)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        tw, th = round(right - left) + 6, round(bottom - top) + 4
        spots = _tag_spots(box, tw, th, w, h)
        x, y = next(
            (
                s
                for s in spots
                if not any(_hits((s[0], s[1], s[0] + tw, s[1] + th), p) for p in placed)
            ),
            spots[0],
        )
        placed.append((x, y, x + tw, y + th))
        draw.rectangle((x, y, x + tw, y + th), fill=colour)
        brightness = 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2]
        ink = (0, 0, 0) if brightness > 150 else (255, 255, 255)
        draw.text((x + 3 - left, y + 2 - top), text, fill=ink, font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


# --------------------------------------------------------------------------- answers


def _fraction(value: Any) -> Any:
    """A coordinate as a fraction: values in (1, 100] are read as percent (a common slip),
    anything else is clamped to [0, 1]."""
    if isinstance(value, int | float):
        number = float(value)
        if 1.0 < number <= 100.0:
            number /= 100.0
        return min(1.0, max(0.0, number))
    return value


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MarkVerdict(_Answer):
    mark: int = Field(ge=1, description="The number drawn on the mark")
    verdict: Literal[MarkLabel, Rejection] = Field(
        description="The object's label when the mark is one countable object; otherwise "
        "duplicate, part_or_group or not_countable"
    )


class AddedObject(_Answer):
    label: MarkLabel
    x: float = Field(ge=0.0, le=1.0, description="Centre, fraction of the image width")
    y: float = Field(ge=0.0, le=1.0, description="Centre, fraction of the image height")

    _x = field_validator("x", "y", mode="before")(_fraction)


class MarksReview(_Answer):
    """The answer to ``photo_marks``: a verdict for every mark, then the missed objects."""

    marks: list[MarkVerdict] = Field(max_length=200)
    added: list[AddedObject] = Field(max_length=100)


@dataclass
class Tally:
    """Counts from a review, and what went into them."""

    counts: dict[str, int]
    kept: list[tuple[Mark, str]]
    added: list[AddedObject]
    rejected: dict[str, int]
    unanswered: list[int]
    unknown: list[int]
    repeated: list[int]


def tally_review(review: MarksReview, marks: Sequence[Mark]) -> Tally:
    """Count = kept marks (by the label the model gave them) + added objects.

    A mark the model did not mention is not counted (and listed in ``unanswered``); a
    verdict for a number that was not drawn is ignored (``unknown``); a mark answered twice
    keeps its first verdict (``repeated``)."""
    by_number = {m.number: m for m in marks}
    seen: dict[int, str] = {}
    unknown: list[int] = []
    repeated: list[int] = []
    for verdict in review.marks:
        if verdict.mark not in by_number:
            unknown.append(verdict.mark)
        elif verdict.mark in seen:
            repeated.append(verdict.mark)
        else:
            seen[verdict.mark] = verdict.verdict
    counts: dict[str, int] = {}
    kept: list[tuple[Mark, str]] = []
    rejected: dict[str, int] = {}
    for number, label in seen.items():
        if label in MARK_LABELS:
            kept.append((by_number[number], label))
            counts[label] = counts.get(label, 0) + 1
        else:
            rejected[label] = rejected.get(label, 0) + 1
    for obj in review.added:
        counts[obj.label] = counts.get(obj.label, 0) + 1
    return Tally(
        counts=dict(sorted(counts.items())),
        kept=sorted(kept, key=lambda k: k[0].number),
        added=list(review.added),
        rejected=dict(sorted(rejected.items())),
        unanswered=sorted(set(by_number) - set(seen)),
        unknown=unknown,
        repeated=repeated,
    )


# --------------------------------------------------------------------------- points


class PointedObject(_Answer):
    label: MarkLabel
    x: float = Field(ge=0.0, le=1.0, description="Centre, fraction of the image width")
    y: float = Field(ge=0.0, le=1.0, description="Centre, fraction of the image height")

    _x = field_validator("x", "y", mode="before")(_fraction)


class StatedCounts(_Answer):
    window: int = Field(ge=0, le=200)
    interior_door: int = Field(ge=0, le=200)
    exterior_door: int = Field(ge=0, le=200)
    light_fixture: int = Field(ge=0, le=200)
    outdoor_lighting: int = Field(ge=0, le=200)
    electrical_outlet: int = Field(ge=0, le=200)
    kitchen_cabinet: int = Field(ge=0, le=200)


class PointAnswer(_Answer):
    """The answer to ``photo_points``: one point per object, then the counts."""

    objects: list[PointedObject] = Field(max_length=200)
    counts: StatedCounts


def point_counts(points: Sequence[PointedObject | AddedObject]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in points:
        counts[p.label] = counts.get(p.label, 0) + 1
    return dict(sorted(counts.items()))


def stated_counts(answer: PointAnswer) -> dict[str, int]:
    return {k: v for k, v in answer.counts.model_dump().items() if v}


_TAG = re.compile(r"<points?\b([^>]*)>", re.IGNORECASE)
_ATTR = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def parse_molmo_points(text: str) -> list[tuple[float, float]]:
    """Molmo's points as fractions: ``<point x="10.5" y="20" ...>`` and ``<points x1=".."
    y1=".." x2=".." y2=".." ...>``, coordinates in percent of the image. Malformed or
    out-of-range coordinates are skipped."""
    found: list[tuple[float, float]] = []
    for tag in _TAG.finditer(text):
        attrs = dict(_ATTR.findall(tag.group(1)))
        pairs: list[tuple[str, str]] = []
        if "x" in attrs and "y" in attrs:
            pairs.append((attrs["x"], attrs["y"]))
        n = 1
        while f"x{n}" in attrs and f"y{n}" in attrs:
            pairs.append((attrs[f"x{n}"], attrs[f"y{n}"]))
            n += 1
        for xs, ys in pairs:
            try:
                x, y = float(xs), float(ys)
            except ValueError:
                continue
            if 0.0 <= x <= 100.0 and 0.0 <= y <= 100.0:
                found.append((x / 100.0, y / 100.0))
    return found


# --------------------------------------------------------------------------- masks for points

Status = Literal["kept_inside", "pool", "prompted", "unmasked", "duplicate"]


@dataclass
class Resolved:
    """One point and what it became: a masked instance, a duplicate, or unmasked."""

    label: str
    point: Point
    status: Status
    instance: Instance | None = None
    duplicate_of: int | None = field(default=None)


def _inside(instance: Instance, point: Point) -> bool:
    return bool(pick_prompted([instance], [point], []))


def resolve_points(
    points: Sequence[tuple[str, Point]],
    candidates: Sequence[Instance],
    *,
    counted: Sequence[Instance] = (),
    overlap: float = DEFAULT_OVERLAP,
    prompted: Mapping[int, Instance | None] | None = None,
) -> list[Resolved]:
    """A mask for each ``(label, point)``, de-duplicated by mask overlap.

    In order: a point inside an object already ``counted`` (same label family) is a
    ``duplicate`` (``kept_inside``); otherwise it takes the highest-scoring candidate of its
    family under the point (``pool``), or the instance a fresh point prompt returned for it
    (``prompted[i]``, from the runner), or stays ``unmasked``. A masked point whose mask
    overlaps a counted object or an earlier point's mask (same family) by more than
    ``overlap`` mask IoU is a ``duplicate`` of it; two points under one mask are one object.
    """
    prompted = prompted or {}
    decoded: dict[int, Any] = {}
    out: list[Resolved] = []
    taken: list[tuple[int | None, Instance]] = [(None, c) for c in counted]
    for index, (label, point) in enumerate(points):
        fam = family(label)
        if any(family(c.label.value) == fam and _inside(c, point) for c in counted):
            out.append(Resolved(label, point, "kept_inside"))
            continue
        pool = [c for c in candidates if family(c.label.value) == fam]
        chosen = pick_prompted(pool, [point], [])
        status: Status
        if chosen:
            instance, status = chosen[0], "pool"
        elif prompted.get(index) is not None:
            instance, status = prompted[index], "prompted"  # type: ignore[assignment]
        else:
            out.append(Resolved(label, point, "unmasked"))
            continue
        assert instance is not None
        duplicate = next(
            (
                owner
                for owner, other in taken
                if family(other.label.value) == fam
                and (other is instance or _overlap(instance, other, decoded) > overlap)
            ),
            -1,
        )
        if duplicate != -1:
            out.append(Resolved(label, point, "duplicate", instance, duplicate))
            continue
        taken.append((index, instance))
        out.append(Resolved(label, point, status, instance))
    return out


def resolved_counts(
    resolved: Sequence[Resolved], *, include_unmasked: bool = True
) -> dict[str, int]:
    """Objects counted from resolved points: masked ones, and unmasked ones when asked."""
    counts: dict[str, int] = {}
    for r in resolved:
        if r.status in ("pool", "prompted") or (include_unmasked and r.status == "unmasked"):
            counts[r.label] = counts.get(r.label, 0) + 1
    return dict(sorted(counts.items()))


def add_counts(*parts: Mapping[str, int]) -> dict[str, int]:
    total: dict[str, int] = {}
    for part in parts:
        for k, v in part.items():
            total[k] = total.get(k, 0) + v
    return dict(sorted(total.items()))


__all__ = [
    "MARK_LABELS",
    "AddedObject",
    "Mark",
    "MarkVerdict",
    "MarksReview",
    "PointAnswer",
    "PointedObject",
    "Resolved",
    "StatedCounts",
    "Tally",
    "add_counts",
    "family",
    "mark_list_text",
    "parse_molmo_points",
    "point_counts",
    "render_marks",
    "resolve_points",
    "resolved_counts",
    "select_marks",
    "stated_counts",
    "tally_review",
]
