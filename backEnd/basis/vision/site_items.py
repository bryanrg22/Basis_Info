"""AI-drafted outlines of site items on a straight-down view (EVAL_REPORT 106;
docs/review_2026-10-15/AI_MEASUREMENT_PLAN.md methods M2 and M2v).

A *draft* is a measurement an engineer then checks in the measure tool (EVAL_REPORT 104): a
lon/lat outline in the body :func:`basis.domain.traced.measure` takes, with
``drawn_by: "model:sam3@<pipeline version>"`` and ``verified: false``, the view it was drawn
on, and an expected error. Nothing here prices anything or writes a takeoff: the caller decides
what a draft is for (``SITE_DRAFTS`` is off by default and nothing in the pipeline calls this
module yet; the wiring is a later step of the plan).

**The method** (each choice is the one EVAL_REPORT 106 measured best, or the standard):

1. A *prompt* per item: a box (an engineer's drag, or a vision model's proposal) or one point
   inside it, sent to SAM 3 **alone, with no phrase**. EVAL 106.3 measured box, point, phrase and
   phrase-plus-box side by side on 35 parcels: a box is unbiased on driveways and the phrase
   adds nothing within the noise (median 13.6 against 13.3 %, the phrase pulling driveways 16 %
   low); one click is far worse (median 34 %, a quarter missed); a phrase alone misses 55 of 90
   items. The research expected the phrase to hurt on overhead imagery (arXiv 2607.09583).
2. SAM 3 (:meth:`basis.vision.localize.ReplicateSam3Localizer.run_prompts`, or the self-hosted
   Cog server) returns every instance like the prompt (concept mode: a box is an exemplar, not a
   selection); the one overlapping the box best at box IoU >= 0.5 (PASCAL VOC), or the one under
   the point, is the item (:func:`basis.vision.localize.best_box_match`, ``under_point``).
3. The mask becomes a ring (:func:`mask_to_ring`): the largest 4-connected part, holes filled
   (a draft has no cut-outs), its pixel-edge boundary traced and simplified by Douglas-Peucker
   (:func:`basis.geo.lidar.trace_outline`, ``simplify_ring``: the code that outlines the
   lidar roof, EVAL 104.2) at :data:`TOLERANCE_M` on the ground (EVAL 106.4); a simplified
   ring that crosses itself falls back to the traced one.
4. Pixels to lon/lat through the view's georeference (:class:`MercatorGeoref` for a public
   ortho's Web Mercator box; :class:`CentredGeoref` for a property capture's centre and pixel
   size, heading 0, times ``scale``: the capture's measured pixel-size correction, EVAL 106.2).
5. The body is checked and measured by :func:`basis.domain.traced.measure`; the expected error is
   the larger of the one-pixel bound ``traced`` computes and the method's measured error for the
   family (:data:`METHOD_ERROR_PCT`, EVAL 106.3).

**Refusals** (one per prompt, with a code from the catalogue; never a guessed outline):
``SITE-001`` the view has no pixel size (not a top-down view); ``-002`` its pixel is coarser
than ``max_pixel_m`` (on the 0.6 m federal view drafts were off by a median 30 %: EVAL 106.3);
``-003`` SAM 3 returned nothing at the prompt; ``-004`` the outline touches the picture's
edge (the item runs out of the picture, so its area would be short); ``-005`` the outline
covers less ground than ``min_area_m2`` (a stoop); ``-006`` the outline is not a measurable
shape (:func:`traced.measure` refused it); ``-007`` the call to SAM 3 failed; ``-008`` the
family is not drafted (a fence: EVAL 106.6 found no method that sees one from above).

The vision model's half (M2v) is not shipped: :class:`Proposal` and :func:`proposal_prompts`
read a ``site_items/v1`` answer into prompts, but the two models measured (EVAL 106.5) put
their boxes too far off the items for SAM 3 to take them (3 to 5 of 90 items drafted).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any, Final, Literal, Protocol

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field

from basis.config import Settings
from basis.domain import traced
from basis.errors.codes import E
from basis.geo.geometry import pixel_to_lonlat
from basis.vision.localize import (
    Box,
    Instance,
    LocalizeCall,
    LocalizerError,
    Point,
    VisualPrompt,
    best_box_match,
    under_point,
)
from basis.vision.prep import PreparedImage
from basis.vision.schemas import ComponentLabel

PIPELINE_VERSION: Final = "site-v1"
# Douglas-Peucker tolerance on the ground (EVAL_REPORT 106.4: from 0 to 4 pixels at 7.8 cm the
# area and the overlap do not move and the corners fall from 299 to 17 a draft; the overlap is
# best at 4 pixels, about 0.3 m).
TOLERANCE_M: Final = 0.3
# The coarsest pixel a draft is made on: the registry's sharp class (EVAL_REPORT 101: 15.25 cm or
# finer). EVAL 106.3 measured 7.8 and 8.4 cm (usable) and 0.6 m (not); between them is untested.
MAX_PIXEL_M: Final = 0.16
# The smallest outline drafted, on the ground: a small stoop (about 32 SF).
MIN_AREA_M2: Final = 3.0
# SAM 3's confidence floor for a prompted instance (the localizer's default).
THRESHOLD: Final = 0.5

Unit = Literal["SF", "LF"]
PromptKind = Literal["box", "point"]


@dataclass(frozen=True, slots=True)
class Family:
    label: ComponentLabel
    unit: Unit
    phrase: str  # what a phrase prompt and the vision model call it


FAMILIES: Final[dict[str, Family]] = {
    "driveway": Family(ComponentLabel.DRIVEWAY, "SF", "driveway"),
    "parking": Family(ComponentLabel.DRIVEWAY, "SF", "parking lot"),
    "walk": Family(ComponentLabel.SIDEWALK, "SF", "sidewalk"),
    "patio": Family(ComponentLabel.PATIO, "SF", "patio"),
    "pool_deck": Family(ComponentLabel.PATIO, "SF", "pool deck"),
    "pool": Family(ComponentLabel.POOL, "SF", "swimming pool"),
    "deck": Family(ComponentLabel.DECK, "SF", "deck"),
    "fence": Family(ComponentLabel.FENCE, "LF", "fence"),
}
# Families drafted as outlines; a fence is refused (E.SITE_NOT_DRAFTED, EVAL_REPORT 106.6).
DRAFTED: Final = frozenset(f for f, v in FAMILIES.items() if v.unit == "SF")
# The method's own measured error per family: the 90th percentile of the absolute area error
# against the county truth (EVAL_REPORT 106.3: a box alone, the 2023 county ortho at 7.8 cm, 35
# driveways, 21 patios, 25 decks, 8 pools; the truth's own edge error is in it). The expected
# error of a draft is never below it; a family not measured (one walk, no parking or pool deck)
# carries the largest.
METHOD_ERROR_PCT: Final[dict[str, Decimal]] = {
    "driveway": Decimal("33"),
    "patio": Decimal("46"),
    "deck": Decimal("28"),
    "pool": Decimal("53"),
}
UNMEASURED_ERROR_PCT: Final = max(METHOD_ERROR_PCT.values())


# ------------------------------------------------------------------ georeference


class Georef(Protocol):
    def to_lonlat(self, x: float, y: float) -> tuple[float, float]: ...


@dataclass(frozen=True, slots=True)
class MercatorGeoref:
    """A north-up Web Mercator view: its box (xmin, ymin, xmax, ymax) and size in pixels
    (a public ortho, :class:`basis.geo.geometry.ViewBox`)."""

    bbox: tuple[float, float, float, float]
    width: int
    height: int

    def to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        return pixel_to_lonlat(self.bbox, self.width, self.height, x, y)


@dataclass(frozen=True, slots=True)
class CentredGeoref:
    """A property capture's top-down view: its centre, size and recorded pixel size, heading 0
    (docs/api/PROPERTY_CAPTURE.md section 3), with ``scale`` the measured correction to the
    recorded pixel size (1: none)."""

    lat: float
    lon: float
    width: int
    height: int
    pixel_size_m: float
    scale: float = 1.0

    def to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        """On the WGS84 ellipsoid's radii of curvature at the centre (the inverse of the tangent
        plane :func:`basis.domain.traced.measure` measures on, so a pixel's ground size comes
        back exactly; a mean-radius sphere would read east-west lengths 0.1 % and north-south
        0.3 % off at 39 degrees)."""
        size = self.pixel_size_m * self.scale
        east = (x - self.width / 2) * size
        north = (self.height / 2 - y) * size
        m, n = traced.radii(self.lat)
        lat = self.lat + math.degrees(north / m)
        lon = self.lon + math.degrees(east / (n * math.cos(math.radians(self.lat))))
        return lon, lat


@dataclass(frozen=True, slots=True)
class Offset:
    """A crop of another view: pixel ``(x, y)`` of the crop is ``(x + x0, y + y0)`` there."""

    inner: Georef
    x0: float
    y0: float

    def to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        return self.inner.to_lonlat(x + self.x0, y + self.y0)


@dataclass(frozen=True, slots=True)
class TopDownView:
    """The picture a draft is made on: its bytes, size, pixel size, georeference and what it
    is (the measurement's ``image`` and ``view``)."""

    data: bytes
    width: int
    height: int
    pixel_size_m: float | None
    georef: Georef
    kind: Literal["capture", "public"]
    source: str
    id: str | None = None
    label: str | None = None
    captured_on: date | None = None


def crop(view: TopDownView, box: tuple[int, int, int, int]) -> TopDownView:
    """The part ``(left, top, right, bottom)`` of the view, georeferenced (CPU: a thread)."""
    import io

    from PIL import Image

    left, top, right, bottom = box
    left, top = max(0, left), max(0, top)
    right, bottom = min(view.width, right), min(view.height, bottom)
    if right - left < 16 or bottom - top < 16:
        raise ValueError(f"crop {box} leaves too little of a {view.width} x {view.height} view")
    with Image.open(io.BytesIO(view.data)) as im:
        part = im.convert("RGB").crop((left, top, right, bottom))
    out = io.BytesIO()
    part.save(out, format="PNG")
    return TopDownView(
        data=out.getvalue(),
        width=right - left,
        height=bottom - top,
        pixel_size_m=view.pixel_size_m,
        georef=Offset(view.georef, left, top),
        kind=view.kind,
        source=view.source,
        id=view.id,
        label=view.label,
        captured_on=view.captured_on,
    )


# ------------------------------------------------------------------ mask to ring


def largest_part(mask: npt.NDArray[np.bool_]) -> npt.NDArray[np.bool_]:
    """The largest 4-connected part of the mask with its holes filled (lowest-indexed part on
    a tie)."""
    from scipy import ndimage

    labels, count = ndimage.label(mask)
    if count == 0:
        return np.zeros_like(mask, dtype=bool)
    sizes = np.bincount(labels.ravel())[1:]
    part = labels == (int(np.argmax(sizes)) + 1)
    return np.asarray(ndimage.binary_fill_holes(part), dtype=bool)


def mask_to_ring(
    mask: npt.NDArray[np.bool_], tolerance_px: float = 1.0
) -> list[tuple[float, float]]:
    """The outline of the mask's largest part as pixel-edge corners ``(x, y)`` (x right, y
    down), simplified at ``tolerance_px``; empty when the mask is empty. A simplified ring that
    is not simple (an edge meets another) is replaced by the traced one; a ring over
    :data:`basis.domain.traced.MAX_RING` corners is simplified harder until it fits."""
    from basis.geo.lidar import simplify_ring, trace_outline

    part = largest_part(np.asarray(mask, dtype=bool))
    if not part.any():
        return []
    # trace_outline reads mask[i, j] with i the first axis: transposing makes i = x, j = y.
    corners = [(float(x), float(y)) for x, y in trace_outline(part.T)]
    tolerance = tolerance_px
    while True:
        simple = simplify_ring(corners, tolerance) if tolerance > 0 else list(corners)
        if len(simple) < 3 or traced.ring_problem(simple) is not None:
            simple = corners
        if len(simple) <= traced.MAX_RING:
            return simple
        tolerance = max(tolerance * 2, 0.5)


def touches_edge(mask: npt.NDArray[np.bool_]) -> bool:
    return bool(mask[0, :].any() or mask[-1, :].any() or mask[:, 0].any() or mask[:, -1].any())


# ------------------------------------------------------------------ drafts


@dataclass(frozen=True, slots=True)
class SitePrompt:
    """What to outline: a family and a box or a point, normalized to the view (0-1)."""

    family: str
    box: Box | None = None
    point: Point | None = None
    by: str = "engineer"  # who placed it: the engineer, or a model's proposal

    @property
    def kind(self) -> PromptKind:
        return "box" if self.box is not None else "point"


@dataclass(frozen=True, slots=True)
class Draft:
    family: str
    prompt: SitePrompt
    measurement: dict[str, Any]  # the body traced.measure accepted
    quantity: Decimal
    unit: Unit
    expected_error_pct: Decimal
    one_pixel_error_pct: Decimal | None
    score: float
    mask_pixels: int
    ring_px: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class Refused:
    family: str
    prompt: SitePrompt | None
    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class Drafted:
    """Every prompt's draft or refusal, in the prompts' order, and the SAM 3 call (None when no
    call was made)."""

    results: tuple[Draft | Refused, ...]
    call: LocalizeCall | None = None
    instances: tuple[tuple[Instance, ...], ...] = ()


class Segmenter(Protocol):
    async def run_prompts(
        self, image: PreparedImage, prompts: Sequence[VisualPrompt], *, threshold: float = ...
    ) -> tuple[list[list[Instance]], LocalizeCall]: ...


@dataclass(frozen=True, slots=True)
class DraftSettings:
    model: str = "sam3"
    tolerance_m: float = TOLERANCE_M
    max_pixel_m: float = MAX_PIXEL_M
    min_area_m2: float = MIN_AREA_M2
    threshold: float = THRESHOLD
    capture_scale: float = 1.0
    method_error_pct: Mapping[str, Decimal] = field(default_factory=lambda: dict(METHOD_ERROR_PCT))

    @property
    def drawn_by(self) -> str:
        return f"model:{self.model}@{PIPELINE_VERSION}"

    @classmethod
    def from_settings(cls, settings: Settings) -> DraftSettings:
        return cls(
            max_pixel_m=settings.site_draft_max_pixel_m,
            capture_scale=settings.site_draft_capture_scale,
        )


def apply_capture_scale(view: TopDownView, scale: float) -> TopDownView:
    """A property capture's view with its pixel size corrected by ``scale``
    (``SITE_DRAFT_CAPTURE_SCALE``); any other view as it is."""
    if view.kind != "capture" or scale == 1.0 or view.pixel_size_m is None:
        return view
    return replace(
        view,
        pixel_size_m=view.pixel_size_m * scale,
        georef=_scaled(view.georef, scale),
    )


def _scaled(georef: Georef, scale: float) -> Georef:
    if isinstance(georef, CentredGeoref):
        return replace(georef, scale=georef.scale * scale)
    if isinstance(georef, Offset):
        return replace(georef, inner=_scaled(georef.inner, scale))
    raise ValueError(f"a capture's georeference cannot be scaled: {type(georef).__name__}")


def view_refusal(view: TopDownView, settings: DraftSettings) -> tuple[str, str] | None:
    if view.pixel_size_m is None or not view.pixel_size_m > 0:
        return E.SITE_NOT_TOP_DOWN, "the view has no pixel size: it is not a top-down view"
    if view.pixel_size_m > settings.max_pixel_m:
        return (
            E.SITE_VIEW_TOO_COARSE,
            f"the view's pixel is {view.pixel_size_m:.2f} m, coarser than "
            f"{settings.max_pixel_m:g} m",
        )
    return None


def sam_prompt(prompt: SitePrompt) -> VisualPrompt:
    """The visual prompt (no phrase) for one site prompt."""
    label = FAMILIES[prompt.family].label
    if prompt.box is not None:
        return VisualPrompt(label=label, boxes=(prompt.box,))
    if prompt.point is not None:
        return VisualPrompt(label=label, points=(prompt.point,))
    raise ValueError("a site prompt needs a box or a point")


def pick(instances: Sequence[Instance], prompt: SitePrompt) -> Instance | None:
    if prompt.box is not None:
        index = best_box_match(instances, prompt.box)
    else:
        assert prompt.point is not None
        index = under_point(instances, prompt.point)
    return None if index is None else instances[index]


def measurement_body(
    view: TopDownView, ring_lonlat: Sequence[tuple[float, float]], unit: Unit, drawn_by: str
) -> dict[str, Any]:
    image: dict[str, Any] = {"source": view.source, "pixel_size_m": view.pixel_size_m}
    if view.captured_on is not None:
        image["captured_on"] = view.captured_on.isoformat()
    shown: dict[str, Any] = {"kind": view.kind, "width": view.width, "height": view.height}
    if view.id is not None:
        shown["id"] = view.id
    if view.label is not None:
        shown["label"] = view.label
    if "id" not in shown and "label" not in shown:
        shown["label"] = view.source[: traced.MAX_VIEW_TEXT]
    return {
        "unit": unit,
        "space": "lonlat",
        "shapes": [{"kind": "area", "ring": [[lon, lat] for lon, lat in ring_lonlat]}],
        "image": image,
        "drawn_by": drawn_by,
        "verified": False,
        "view": shown,
    }


def draft_from_instance(
    view: TopDownView,
    prepared: PreparedImage,
    prompt: SitePrompt,
    instance: Instance,
    settings: DraftSettings,
) -> Draft | Refused:
    """One picked instance -> a draft (or the reason it is not one). CPU: call on a thread."""
    family = FAMILIES[prompt.family]
    if instance.mask is None:
        return Refused(prompt.family, prompt, E.SITE_NOTHING_FOUND, "SAM 3 returned no mask")
    mask = instance.mask.decode().astype(bool)
    pixels = int(mask.sum())
    if touches_edge(mask):
        return Refused(
            prompt.family,
            prompt,
            E.SITE_AT_EDGE,
            "the outline reaches the picture's edge: the item runs out of the picture",
        )
    # The view's pixel at the size SAM 3 saw the picture (the mask's pixels).
    mask_pixel_m = (view.pixel_size_m or 0.0) * view.width / prepared.width
    ground_m2 = pixels * mask_pixel_m * mask_pixel_m
    if ground_m2 < settings.min_area_m2:
        return Refused(
            prompt.family,
            prompt,
            E.SITE_TOO_SMALL,
            f"the outline covers {ground_m2:.1f} m2 ({pixels} pixels), under "
            f"{settings.min_area_m2:g} m2",
        )
    ring = mask_to_ring(mask, settings.tolerance_m / mask_pixel_m if mask_pixel_m > 0 else 0.0)
    sx, sy = view.width / prepared.width, view.height / prepared.height
    ring_view = tuple((x * sx, y * sy) for x, y in ring)
    lonlat = [view.georef.to_lonlat(x, y) for x, y in ring_view]
    body = measurement_body(view, lonlat, family.unit, settings.drawn_by)
    try:
        result = traced.measure(body)
    except traced.MeasurementError as exc:
        return Refused(prompt.family, prompt, E.SITE_SHAPE_INVALID, str(exc))
    one_pixel = result.expected_error_pct
    method = settings.method_error_pct.get(prompt.family)
    if method is None:
        method = max(settings.method_error_pct.values(), default=Decimal(0))
    expected = max(one_pixel or Decimal(0), method or Decimal(0))
    return Draft(
        family=prompt.family,
        prompt=prompt,
        measurement=result.record(),
        quantity=result.quantity,
        unit=family.unit,
        expected_error_pct=expected,
        one_pixel_error_pct=one_pixel,
        score=instance.score,
        mask_pixels=pixels,
        ring_px=ring_view,
    )


async def draft_site_items(
    view: TopDownView,
    prompts: Sequence[SitePrompt],
    segmenter: Segmenter,
    settings: DraftSettings | None = None,
    *,
    prepare: Callable[[bytes], PreparedImage] | None = None,
) -> Drafted:
    """A draft or a refusal for every prompt on one view (module doc), with one SAM 3 call
    for all of them."""
    import asyncio

    from basis.vision.prep import prepare_image

    settings = settings or DraftSettings()
    refusal = view_refusal(view, settings)
    if refusal is not None:
        return Drafted(tuple(Refused(p.family, p, *refusal) for p in prompts))
    results: list[Draft | Refused | None] = [None] * len(prompts)
    asked: list[int] = []
    for k, p in enumerate(prompts):
        if p.family not in FAMILIES:
            raise ValueError(f"unknown site family {p.family!r}")
        if p.family not in DRAFTED:
            results[k] = Refused(
                p.family, p, E.SITE_NOT_DRAFTED, f"a {p.family} is not drafted from above"
            )
        else:
            asked.append(k)
    if not asked:
        return Drafted(tuple(r for r in results if r is not None))
    prepared = await asyncio.to_thread(prepare or prepare_image, view.data)
    try:
        found, call = await segmenter.run_prompts(
            prepared, [sam_prompt(prompts[k]) for k in asked], threshold=settings.threshold
        )
    except LocalizerError as exc:
        for k in asked:
            results[k] = Refused(prompts[k].family, prompts[k], E.SITE_SEGMENTER_FAILED, str(exc))
        return Drafted(tuple(r for r in results if r is not None))
    for k, instances in zip(asked, found, strict=True):
        chosen = pick(instances, prompts[k])
        if chosen is None:
            results[k] = Refused(
                prompts[k].family,
                prompts[k],
                E.SITE_NOTHING_FOUND,
                f"SAM 3 found nothing at the {prompts[k].kind} ({len(instances)} returned)",
            )
            continue
        results[k] = await asyncio.to_thread(
            draft_from_instance, view, prepared, prompts[k], chosen, settings
        )
    return Drafted(
        tuple(r for r in results if r is not None),
        call=call,
        instances=tuple(tuple(i) for i in found),
    )


async def draft_if_enabled(
    settings: Settings,
    view: TopDownView,
    prompts: Sequence[SitePrompt],
    segmenter: Segmenter,
    *,
    prepare: Callable[[bytes], PreparedImage] | None = None,
) -> Drafted | None:
    """:func:`draft_site_items` when ``SITE_DRAFTS=on`` (with the capture scale and the pixel
    limit of the settings); None, with no call, when it is off (the default)."""
    if settings.site_drafts != "on":
        return None
    draft_settings = DraftSettings.from_settings(settings)
    scaled = apply_capture_scale(view, draft_settings.capture_scale)
    return await draft_site_items(scaled, prompts, segmenter, draft_settings, prepare=prepare)


# ------------------------------------------------------------------ M2v: the model proposes


class ProposedItem(BaseModel):
    """One site item the model sees: its family and a box in thousandths of the picture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    family: Literal["driveway", "parking", "walk", "patio", "pool_deck", "pool", "deck", "fence"]
    box: list[int] = Field(description="[x1, y1, x2, y2], 0 to 1000, x right, y down")
    confidence: Literal["high", "medium", "low"]


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[ProposedItem]


def proposal_prompts(proposal: Proposal, by: str) -> list[SitePrompt]:
    """The proposal's boxes as prompts (a box that is not four ordered numbers in 0-1000 is
    dropped: the model's answer is untrusted input)."""
    out: list[SitePrompt] = []
    for item in proposal.items:
        if len(item.box) != 4:
            continue
        x1, y1, x2, y2 = (min(1000, max(0, v)) / 1000 for v in item.box)
        if x2 <= x1 or y2 <= y1:
            continue
        out.append(SitePrompt(item.family, box=Box(x1=x1, y1=y1, x2=x2, y2=y2), by=by))
    return out


USER_TEXT: Final = (
    "This is a straight-down aerial picture of one residential property, north up. List every "
    "site item you can see on the property in the middle of the picture, with a tight box "
    "around each."
)


__all__ = [
    "DRAFTED",
    "FAMILIES",
    "METHOD_ERROR_PCT",
    "PIPELINE_VERSION",
    "TOLERANCE_M",
    "USER_TEXT",
    "CentredGeoref",
    "Draft",
    "DraftSettings",
    "Drafted",
    "Family",
    "MercatorGeoref",
    "Offset",
    "Proposal",
    "ProposedItem",
    "Refused",
    "Segmenter",
    "SitePrompt",
    "TopDownView",
    "apply_capture_scale",
    "crop",
    "draft_if_enabled",
    "draft_from_instance",
    "draft_site_items",
    "largest_part",
    "mask_to_ring",
    "measurement_body",
    "pick",
    "proposal_prompts",
    "sam_prompt",
    "touches_edge",
    "view_refusal",
]
