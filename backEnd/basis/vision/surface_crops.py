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
"""

from __future__ import annotations

import io
from enum import StrEnum
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator

from basis.vision.reconcile import NOTE_MAX, clip_text

PROMPT_NAME: Final = "surface_crop_material"
Surface = Literal["floor", "wall", "ceiling"]
SURFACES: Final[tuple[Surface, ...]] = ("floor", "wall", "ceiling")
MIN_SHARE: Final = 0.02  # surfaces smaller than this share of the photo are not asked about
MARGIN: Final = 0.04  # of the image side, around the mask's box
CROP_SIDE: Final = 768  # longest side of the enlarged crop
GREY: Final = 0.75  # how far non-surface pixels are blended toward grey
MAX_TOKENS: Final = 500


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
