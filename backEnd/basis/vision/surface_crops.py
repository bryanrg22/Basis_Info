"""Surface materials from segmentation-guided crops (EVAL_REPORT section 26, experiment c).

The per-photo surfaces call reads every surface's material while listing everything else
in the photo; the room step's second look (:mod:`basis.vision.surface_material`) asks one
surface at a time but on the whole photo. This module asks about a region a trained
segmenter (ADE20K ``wall`` / ``floor`` / ``ceiling``, :mod:`basis.vision.ade20k`) says is
the surface: the whole photo for context, then the surface cropped to its box, enlarged,
with everything else greyed out. Cropping to the region of interest is what "MLLMs know
where to look" (Zhang et al., ICLR 2025) found restores small details; greying the rest
out follows the masked-region prompts of Set-of-Mark (Yang et al. 2023) without covering
the surface with marks.

The answer is a closed choice (:class:`CropMaterial`). :func:`canonical` maps both the
answers and the labels' free-text materials onto the classes the report rows
distinguish, so one table scores every source.

The pipeline's use (``MATERIALS_SECOND_LOOK=surface_crops``, measured but off by default;
``reconcile_rooms``): :func:`photo_crops` segments one photo
(:mod:`basis.vision.surface_segmenter`) and asks one :func:`build_request` per surface of at
least :data:`MIN_SHARE` of the photo; :func:`apply_crops` then sets the material of the
room's ``wall_finish``, ``wainscoting``, ``tile`` and ``ceiling_finish`` objects to the
crops' majority class over the room's photos (:data:`LABEL_KIND`, :data:`TO_MATERIAL`),
exactly ``evals.predict_surface_crops apply``; where no crop answered the object keeps its
material (the per-photo or room-call one), which is section 26.3's "crops, else per-photo".
Failures never fail the room: a segmenter error, an answer unusable twice, a transient
provider error or a crop the provider refuses (``request_rejected``) leave that photo or
surface unanswered and are listed in :attr:`RoomCrops.flags`. An exhausted budget, a
provider account or configuration error and a missing provider propagate (the activity's
budget guard and account-error stop apply).
"""

from __future__ import annotations

import io
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Literal

import numpy as np
import numpy.typing as npt
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator

from basis.llm.call import CallContext, call_model
from basis.llm.cost import cost_usd
from basis.llm.errors import (
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderRequestRejected,
    ProviderUnavailable,
)
from basis.llm.prompts import Prompt
from basis.llm.provider import ChatProvider, ChatRequest, ImagePart, Message
from basis.vision.prep import prepare_image
from basis.vision.reconcile import NOTE_MAX, InventoryObject, clip_text
from basis.vision.schemas import Material

if TYPE_CHECKING:
    from basis.vision.surface_segmenter import SurfaceSegmenter

PROMPT_NAME: Final = "surface_crop_material"
Surface = Literal["floor", "wall", "ceiling"]
SURFACES: Final[tuple[Surface, ...]] = ("floor", "wall", "ceiling")
MIN_SHARE: Final = 0.02  # surfaces smaller than this share of the photo are not asked about
MARGIN: Final = 0.04  # of the image side, around the mask's box
CROP_SIDE: Final = 768  # longest side of the enlarged crop
GREY: Final = 0.75  # how far non-surface pixels are blended toward grey
MAX_TOKENS: Final = 500
MAX_ATTEMPTS: Final = 2  # an unusable answer is asked once more, as in evals.predict_multiview
# Why a crop ended unanswered without an unusable answer (error prefixes, also flag kinds).
_NOT_ANSWERED: Final = ("provider_unavailable", "request_rejected")
# The released prompt the study measured (EVAL_REPORT 26.3).
PROMPT_VERSION: Final = "v1"


class CropMaterial(StrEnum):
    CARPET = "carpet"
    HARDWOOD = "hardwood"
    LAMINATE = "laminate"
    VINYL = "vinyl"
    CERAMIC_TILE = "ceramic_tile"
    STONE = "stone"
    CONCRETE = "concrete"
    PAINTED_DRYWALL = "painted_drywall"
    PLASTER = "plaster"
    WALLPAPER = "wallpaper"
    WOOD = "wood"
    PAINTED_WOOD = "painted_wood"
    PAINTED_MASONRY = "painted_masonry"
    BRICK = "brick"
    FIBERGLASS = "fiberglass"
    FABRIC = "fabric"
    ACOUSTIC_TILE = "acoustic_tile"
    EXPOSED_STRUCTURE = "exposed_structure"
    OTHER = "other"
    UNKNOWN = "unknown"


class LowerFinish(StrEnum):
    NONE = "none"
    WALLPAPER = "wallpaper"
    WOOD = "wood"
    PAINTED_WOOD = "painted_wood"
    CERAMIC_TILE = "ceramic_tile"
    FIBERGLASS = "fiberglass"
    FABRIC = "fabric"
    OTHER = "other"
    UNKNOWN = "unknown"


class CropAnswer(BaseModel):
    """What the model fills."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    material: CropMaterial
    lower_finish: LowerFinish = Field(
        description="walls: the finish of the lower part when it differs; otherwise none"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(max_length=NOTE_MAX)

    @field_validator("evidence", mode="before")
    @classmethod
    def _clip(cls, value: Any) -> Any:
        return clip_text(value)


# --------------------------------------------------------------------------- crops


def mask_box(
    mask: npt.NDArray[np.bool_], margin: float = MARGIN
) -> tuple[int, int, int, int] | None:
    """Pixel box ``(x1, y1, x2, y2)`` (exclusive ends) around the mask, widened by
    ``margin`` of the image's longer side and clamped; None for an empty mask."""
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if rows.size == 0:
        return None
    height, width = mask.shape
    pad = int(round(margin * max(height, width)))
    return (
        max(0, int(cols[0]) - pad),
        max(0, int(rows[0]) - pad),
        min(width, int(cols[-1]) + 1 + pad),
        min(height, int(rows[-1]) + 1 + pad),
    )


def crop_image(
    image: Image.Image, mask: npt.NDArray[np.bool_], side: int = CROP_SIDE
) -> Image.Image | None:
    """The surface cropped to its box, non-surface pixels blended toward grey, the
    longer side scaled to ``side`` (up or down)."""
    if mask.shape != (image.height, image.width):
        raise ValueError(f"mask {mask.shape} does not match image {image.size}")
    box = mask_box(mask)
    if box is None:
        return None
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    grey = np.full_like(pixels, 128.0)
    keep = mask[..., None]
    blended = np.where(keep, pixels, pixels * (1 - GREY) + grey * GREY)
    x1, y1, x2, y2 = box
    crop = Image.fromarray(blended[y1:y2, x1:x2].astype(np.uint8))
    scale = side / max(crop.width, crop.height)
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    return crop.resize(size, Image.Resampling.LANCZOS)


def jpeg_bytes(image: Image.Image, quality: int = 90) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


# --------------------------------------------------------------------------- classes

# The classes the report rows distinguish, per surface; "wall_lower" is the wainscot band.
Kind = Literal["floor", "wall", "wall_lower", "ceiling"]

_FLOOR_LABELS: Final[dict[str, str]] = {
    "carpet": "carpet",
    "hardwood_floor": "hardwood",
    "laminate_flooring": "laminate",
    "vinyl_flooring": "vinyl",
    "concrete_floor": "concrete",
}
_EQUIVALENT: Final[dict[str, str]] = {
    # painted drywall and painted plaster are both the "Painting" row
    "plaster": "painted_drywall",
    "drywall": "painted_drywall",
    "gypsum": "painted_drywall",
    "paint": "painted_drywall",
    "plaster_or_poly": "painted_drywall",
    "gypsum_unfinished": "painted_drywall",
    # painted and stained wood paneling are both "Raised Wood Paneling"
    "painted_wood": "wood",
    "stained_wood": "wood",
    "reclaimed_wood_plank": "wood",
    "embossed_wallpaper": "wallpaper",
    "painted_block": "painted_masonry",
    "ceramic": "ceramic_tile",
    "porcelain": "ceramic_tile",
    "ceramic_tile": "ceramic_tile",
    "ceramic_subway_tile": "ceramic_tile",
    "tile": "ceramic_tile",
    "plastic": "fiberglass",
    "hardwood": "hardwood",
}


def canonical(material: str | None) -> str | None:
    """A material (an answer, a component material or a label's free text) as the class
    the report rows distinguish; None when it says nothing (unknown, empty)."""
    if material is None:
        return None
    key = material.strip().lower().replace(" ", "_")
    if key in ("", "unknown", "none", "null", "other"):
        return None
    return _EQUIVALENT.get(key, key)


def floor_class(label: str, material: str | None) -> str | None:
    """A floor component (label + material) as a floor class."""
    if label in _FLOOR_LABELS:
        return _FLOOR_LABELS[label]
    if label == "tile":
        m = canonical(material)
        return m if m in ("stone", "vinyl") else "ceramic_tile"
    return None


def component_classes(components: list[dict[str, Any]]) -> dict[Kind, set[str]]:
    """Per surface kind, the classes a list of components (``label``, ``material``) gives:
    floor labels, ``wall_finish`` / ``wainscoting`` / ``ceiling_finish`` materials."""
    out: dict[Kind, set[str]] = {
        "floor": set(),
        "wall": set(),
        "wall_lower": set(),
        "ceiling": set(),
    }
    for c in components:
        label, material = str(c.get("label")), c.get("material")
        floor = floor_class(label, material)
        if floor is not None:
            out["floor"].add(floor)
        elif label == "wall_finish" and canonical(material):
            out["wall"].add(str(canonical(material)))
        elif label == "wainscoting" and canonical(material):
            out["wall_lower"].add(str(canonical(material)))
        elif label == "ceiling_finish" and canonical(material):
            out["ceiling"].add(str(canonical(material)))
    return out


def answer_classes(surface: Surface, answer: CropAnswer) -> dict[Kind, set[str]]:
    """A crop answer as the same per-kind classes."""
    out: dict[Kind, set[str]] = {}
    main = canonical(answer.material.value)
    if main is not None:
        out[surface] = {main}
    if surface == "wall":
        lower = canonical(answer.lower_finish.value)
        if lower is not None:
            out["wall_lower"] = {lower}
    return out


# --------------------------------------------------------------------------- pipeline

# crop class -> the component Material the room objects carry
TO_MATERIAL: Final[dict[str, str]] = {
    "painted_drywall": "drywall",
    "wallpaper": "wallpaper",
    "wood": "wood",
    "painted_masonry": "painted_masonry",
    "ceramic_tile": "ceramic",
    "stone": "stone",
    "vinyl": "vinyl",
    "fiberglass": "fiberglass",
    "brick": "brick",
    "fabric": "fabric",
    "concrete": "concrete",
}
# room object label -> the crop kind its material comes from
LABEL_KIND: Final[dict[str, Kind]] = {
    "wall_finish": "wall",
    "wainscoting": "wall_lower",
    "tile": "floor",
    "ceiling_finish": "ceiling",
}
# A tile object's material: only a tile class may replace it (a carpet crop says nothing
# about the bathroom's tile).
FLOOR_TILE_CLASSES: Final = frozenset({"ceramic_tile", "stone", "vinyl"})


def majority(
    classes: Sequence[str], allowed: set[str] | frozenset[str] | None = None
) -> str | None:
    """The most common class (ties: the first seen), among ``allowed`` when given."""
    pool = [c for c in classes if allowed is None or c in allowed]
    if not pool:
        return None
    counts = Counter(pool)
    return sorted(counts, key=lambda c: (-counts[c], pool.index(c)))[0]


def build_request(
    surface: Surface,
    photo: bytes,
    crop: bytes,
    *,
    model: str,
    prompt: Prompt,
    study_id: str,
    activity: str,
    effort: str | None,
) -> ChatRequest:
    """The whole photo, then the crop, then the question (closed choice)."""
    images = (
        Message(
            role="user",
            content="Image 1 (the whole photo):",
            images=(
                ImagePart(
                    media_type="image/jpeg",
                    data_base64=prepare_image(photo).base64,
                    detail="high",
                ),
            ),
        ),
        Message(
            role="user",
            content=f"Image 2 (the {surface}, cropped and enlarged, the rest greyed out):",
            images=(
                ImagePart(
                    media_type="image/jpeg", data_base64=prepare_image(crop).base64, detail="high"
                ),
            ),
        ),
    )
    text = f"The surface is the {surface}. What is it made of" + (
        ", and what covers its lower part when that differs?" if surface == "wall" else "?"
    )
    return ChatRequest(
        model=model,
        system=prompt.text,
        messages=(*images, Message(role="user", content=text)),
        response_model=CropAnswer,
        max_tokens=MAX_TOKENS,
        temperature=0.0,
        reasoning_effort=effort,
        prompt_version=prompt.version_id,
        study_id=study_id,
        activity=activity,
    )


def _no_heartbeat(_: str) -> None:
    return None


@dataclass
class CropContext:
    call: CallContext
    provider: ChatProvider
    prompt: Prompt
    model: str
    reasoning_effort: str | None = None
    heartbeat: Callable[[str], None] = field(default=_no_heartbeat)


@dataclass
class CropRecord:
    """One surface of one photo: its crop box, every attempt, and the answer (or None)."""

    file: str
    surface: Surface
    box: tuple[int, int, int, int]
    share: float
    answer: CropAnswer | None = None
    attempts: int = 0
    provider: str | None = None
    model: str | None = None
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)
    raw: list[dict[str, Any]] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "surface": self.surface,
            "box": list(self.box),
            "share": round(self.share, 4),
            "answer": None if self.answer is None else self.answer.model_dump(mode="json"),
            "attempts": self.attempts,
            "provider": self.provider,
            "model": self.model,
            "cost_usd": round(self.cost_usd, 6),
            "errors": self.errors,
            "raw": self.raw,
        }


@dataclass
class PhotoCrops:
    file: str
    crops: list[CropRecord] = field(default_factory=list)
    # The photo could not be segmented, or a crop call failed: per-photo material there.
    errors: list[str] = field(default_factory=list)

    def classes(self) -> dict[Kind, set[str]]:
        """The answered crops as per-kind classes (``evals.predict_surface_crops``)."""
        out: dict[Kind, set[str]] = {}
        for crop in self.crops:
            if crop.answer is None:
                continue
            for kind, found in answer_classes(crop.surface, crop.answer).items():
                out.setdefault(kind, set()).update(found)
        return out


@dataclass
class RoomCrops:
    """The room's crops, the materials they changed, and why any photo fell back."""

    photos: list[PhotoCrops]
    changes: list[dict[str, Any]] = field(default_factory=list)
    segmenter: str = ""

    @property
    def flags(self) -> list[str]:
        """``segmenter``, ``provider_unavailable``, ``request_rejected`` or
        ``invalid_model_output`` (sorted, distinct) when some photo or surface fell back to
        the per-photo material."""
        kinds = {e.split(":", 1)[0] for p in self.photos for e in p.errors}
        kinds |= {
            "invalid_model_output"
            for p in self.photos
            for c in p.crops
            if c.answer is None and not any(e.startswith(_NOT_ANSWERED) for e in c.errors)
        }
        return sorted(kinds)

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for p in self.photos for c in p.crops)

    def record(self) -> dict[str, Any]:
        return {
            "segmenter": self.segmenter,
            "flags": self.flags,
            "changes": self.changes,
            "cost_usd": round(self.cost_usd, 6),
            "photos": [
                {
                    "file": p.file,
                    "errors": p.errors,
                    "crops": [c.record() for c in p.crops],
                }
                for p in self.photos
            ],
        }


async def ask_crop(
    surface: Surface, photo: bytes, crop: bytes, record: CropRecord, ctx: CropContext
) -> None:
    """One surface's question, an unusable answer asked once more; fills ``record``. A
    transient provider error (``provider_unavailable``) or a refused crop
    (``request_rejected``) is recorded and ends the crop unanswered; budget, account and
    configuration errors propagate."""
    request = build_request(
        surface,
        photo,
        crop,
        model=ctx.model,
        prompt=ctx.prompt,
        study_id=str(ctx.call.study_id),
        activity=ctx.call.activity,
        effort=ctx.reasoning_effort,
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        ctx.heartbeat(f"surface crop {record.file} {surface} attempt {attempt}")
        record.attempts = attempt
        try:
            response = await call_model(ctx.provider, request, ctx.call)
        except ProviderBadResponse as exc:
            record.errors.append(f"attempt {attempt}: {exc}")
            if exc.raw is not None:
                record.raw.append(exc.raw)
            if exc.usage is not None:
                record.cost_usd += cost_usd(exc.model or request.model, exc.usage)
            continue
        except (ProviderRateLimited, ProviderUnavailable) as exc:
            record.errors.append(f"provider_unavailable: attempt {attempt}: {exc}"[:500])
            return
        except ProviderRequestRejected as exc:
            # This crop refused (an image it cannot process, a content filter): unanswered
            # like an unusable one, not retried; the photo keeps its material.
            record.errors.append(f"request_rejected: attempt {attempt}: {exc}"[:500])
            return
        record.raw.append(response.raw)
        record.cost_usd += cost_usd(response.model, response.usage)
        record.provider, record.model = response.provider, response.model
        if not isinstance(response.parsed, CropAnswer):
            record.errors.append(f"attempt {attempt}: no structured answer")
            continue
        record.answer = response.parsed
        return


async def photo_crops(
    file: str, photo: bytes, segmenter: SurfaceSegmenter, ctx: CropContext
) -> PhotoCrops:
    """Segment one photo and ask about every surface large enough (module doc)."""
    from basis.vision.surface_segmenter import SegmenterError

    out = PhotoCrops(file=file)
    try:
        image = Image.open(io.BytesIO(photo)).convert("RGB")
    except Exception as exc:  # it was prepared before; a broken file is not this step's
        out.errors.append(f"segmenter: unreadable photo: {type(exc).__name__}")
        return out
    ctx.heartbeat(f"surface crops {file}: segmenting")
    try:
        masks = await segmenter.segment(image, budget=ctx.call.budget)
    except SegmenterError as exc:
        out.errors.append(f"segmenter: {exc}"[:500])
        return out
    for surface in SURFACES:
        mask = masks.get(surface)
        if mask is None or mask.shape != (image.height, image.width):
            continue
        share = float(mask.mean())
        if share < MIN_SHARE:
            continue
        crop = crop_image(image, mask)
        box = mask_box(mask)
        if crop is None or box is None:
            continue
        record = CropRecord(file=file, surface=surface, box=box, share=share)
        await ask_crop(surface, photo, jpeg_bytes(crop), record, ctx)
        for kind in _NOT_ANSWERED:
            if any(e.startswith(kind) for e in record.errors):
                out.errors.append(f"{kind}: {surface}")
        out.crops.append(record)
    return out


def apply_crops(
    objects: Sequence[InventoryObject], photos: Sequence[PhotoCrops]
) -> tuple[list[InventoryObject], list[dict[str, Any]]]:
    """The room's objects with each :data:`LABEL_KIND` object's material set to the crops'
    majority class over ``photos`` (in the room's photo order), and the changes made
    (``index`` is the object's position).
    Objects are never added or removed; an object no crop speaks to keeps its material."""
    per_photo = [(p.file, p.classes()) for p in photos]
    out: list[InventoryObject] = []
    changes: list[dict[str, Any]] = []
    for index, obj in enumerate(objects):
        kind = LABEL_KIND.get(obj.label.value)
        if kind is None:
            out.append(obj)
            continue
        seen = [c for _, classes in per_photo for c in sorted(classes.get(kind, set()))]
        choice = majority(seen, FLOOR_TILE_CLASSES if kind == "floor" else None)
        if choice is None or choice not in TO_MATERIAL or obj.material.value == TO_MATERIAL[choice]:
            out.append(obj)
            continue
        changes.append(
            {
                "index": index,
                "label": obj.label.value,
                "before": obj.material.value,
                "after": TO_MATERIAL[choice],
                "class": choice,
                "files": [f for f, classes in per_photo if choice in classes.get(kind, set())],
            }
        )
        out.append(obj.model_copy(update={"material": Material(TO_MATERIAL[choice])}))
    return out, changes


async def look_at_room(
    photos: Sequence[tuple[str, bytes]],
    objects: Sequence[InventoryObject],
    segmenter: SurfaceSegmenter | None,
    ctx: CropContext,
) -> tuple[list[InventoryObject], RoomCrops]:
    """The room's surface-crop look (module doc): ``photos`` are ``(file, original
    bytes)`` in the room's order. Without a segmenter every photo falls back, flagged."""
    if segmenter is None:
        found = [PhotoCrops(file=file, errors=["segmenter: not configured"]) for file, _ in photos]
        return list(objects), RoomCrops(photos=found)
    found = [await photo_crops(file, photo, segmenter, ctx) for file, photo in photos]
    changed, changes = apply_crops(objects, found)
    return changed, RoomCrops(photos=found, changes=changes, segmenter=segmenter.name)
