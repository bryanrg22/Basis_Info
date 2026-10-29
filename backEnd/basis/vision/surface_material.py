"""A second look at the material of a room's surfaces, one photo and one surface per call.

A surface's material decides its report row and recovery period: a wainscot of wood is
"Raised Wood Paneling", of wallpaper "Wall Coverings"; a painted drywall wall is
"Painting"; tile is "Ceramic Tile Flooring" only when ceramic, porcelain or stone
(COMPONENT_TAXONOMY, ``report_rows``). The per-photo and per-room calls read the material
while doing ten other things, and on this study every one of them called a hall's
wallpaper wainscot wood (EVAL_REPORT section 13). This step asks one narrow question per
(room, surface) with the released prompt ``surface_material``: the single photo where the
surface is seen best, the surface's label, and the materials the rules distinguish
(:class:`SurfaceMaterial`). ``wallpaper`` and ``painted_masonry`` are component
``Material`` values of their own (before section 16.5 they were stored as ``other`` with
the note ``wallpaper`` and as ``concrete`` with the note ``painted masonry``);
:data:`TO_MATERIAL` maps the rest onto the component vocabulary.

Which surfaces are looked at (:func:`needs_second_look`):

* ``disagree``: the photos of the room, or the room inventory and the photos, give
  different materials for the label, or the material is ``unknown``/``other``;
* ``sensitive``: every surface whose report row depends on its material
  (:data:`MATERIAL_SENSITIVE`), whatever the earlier calls said. This is the only trigger
  that reaches a material every call agreed on and got wrong.

The answer replaces the inventory object's material when it is not ``unknown`` and its
confidence is at least :data:`MIN_CONFIDENCE`; the earlier material is kept in the
record. An unusable answer is retried once, then the object keeps its material.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from basis.llm.call import CallContext, call_model
from basis.llm.cost import cost_usd
from basis.llm.errors import ProviderBadResponse, ProviderRequestRejected
from basis.llm.prompts import Prompt
from basis.llm.provider import ChatProvider, ChatRequest, ImagePart, Message
from basis.vision.reconcile import NOTE_MAX, InventoryObject, RoomPhoto, clip_text
from basis.vision.schemas import ComponentLabel, Material

PROMPT_NAME: Final = "surface_material"
MAX_ATTEMPTS: Final = 2
MAX_TOKENS: Final = 400
MIN_CONFIDENCE: Final = 0.5
# Labels whose report row depends on the material (report_rows rules: Raised Wood Paneling
# vs Wall Coverings vs Painting; Ceramic Tile Flooring).
MATERIAL_SENSITIVE: Final = frozenset(
    {ComponentLabel.WAINSCOTING, ComponentLabel.WALL_FINISH, ComponentLabel.TILE}
)
GENERIC: Final = frozenset({Material.UNKNOWN, Material.OTHER})
Trigger = Literal["disagree", "sensitive"]


class SurfaceMaterial(StrEnum):
    WALLPAPER = "wallpaper"
    FABRIC = "fabric"
    VINYL = "vinyl"
    WOOD = "wood"
    PAINTED_WOOD = "painted_wood"
    PAINTED_DRYWALL = "painted_drywall"
    PLASTER = "plaster"
    CERAMIC = "ceramic"
    PORCELAIN = "porcelain"
    STONE = "stone"
    BRICK = "brick"
    PAINTED_MASONRY = "painted_masonry"
    CONCRETE = "concrete"
    LAMINATE = "laminate"
    PLASTIC = "plastic"
    FIBERGLASS = "fiberglass"
    METAL = "metal"
    GLASS = "glass"
    OTHER = "other"
    UNKNOWN = "unknown"


# SurfaceMaterial -> (component Material, note). Painted drywall and plaster are both the
# painted-wall surface the Painting row looks for (``drywall``).
TO_MATERIAL: Final[dict[SurfaceMaterial, tuple[Material, str | None]]] = {
    SurfaceMaterial.WALLPAPER: (Material.WALLPAPER, None),
    SurfaceMaterial.FABRIC: (Material.FABRIC, None),
    SurfaceMaterial.VINYL: (Material.VINYL, None),
    SurfaceMaterial.WOOD: (Material.WOOD, None),
    SurfaceMaterial.PAINTED_WOOD: (Material.WOOD, "painted wood"),
    SurfaceMaterial.PAINTED_DRYWALL: (Material.DRYWALL, None),
    SurfaceMaterial.PLASTER: (Material.DRYWALL, "plaster"),
    SurfaceMaterial.CERAMIC: (Material.CERAMIC, None),
    SurfaceMaterial.PORCELAIN: (Material.PORCELAIN, None),
    SurfaceMaterial.STONE: (Material.STONE, None),
    SurfaceMaterial.BRICK: (Material.BRICK, None),
    SurfaceMaterial.PAINTED_MASONRY: (Material.PAINTED_MASONRY, None),
    SurfaceMaterial.CONCRETE: (Material.CONCRETE, None),
    SurfaceMaterial.LAMINATE: (Material.LAMINATE, None),
    SurfaceMaterial.PLASTIC: (Material.PLASTIC, None),
    SurfaceMaterial.FIBERGLASS: (Material.FIBERGLASS, None),
    SurfaceMaterial.METAL: (Material.METAL, None),
    SurfaceMaterial.GLASS: (Material.GLASS, None),
    SurfaceMaterial.OTHER: (Material.OTHER, None),
    SurfaceMaterial.UNKNOWN: (Material.UNKNOWN, None),
}


class ModelSurfaceMaterial(BaseModel):
    """What the model fills (strict JSON schema)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    material: SurfaceMaterial
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(max_length=NOTE_MAX, description="what in the image shows the material")

    @field_validator("evidence", mode="before")
    @classmethod
    def _clip_evidence(cls, value: Any) -> Any:
        return clip_text(value)


@dataclass
class MaterialContext:
    call: CallContext
    provider: ChatProvider
    prompt: Prompt
    model: str
    max_tokens: int = MAX_TOKENS
    reasoning_effort: str | None = None


@dataclass
class SecondLook:
    """One (room object, photo) second look and what it changed."""

    label: str
    file: str
    before: str
    answer: ModelSurfaceMaterial | None = None
    applied: bool = False
    attempts: int = 0
    model: str | None = None
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "file": self.file,
            "before": self.before,
            "answer": None if self.answer is None else self.answer.model_dump(mode="json"),
            "applied": self.applied,
            "attempts": self.attempts,
            "model": self.model,
            "cost_usd": round(self.cost_usd, 6),
            "errors": self.errors,
        }


def photo_materials(obj: InventoryObject, photos: Sequence[RoomPhoto]) -> list[Material]:
    """The materials the per-photo analyses gave ``obj``'s label in its photos."""
    found: list[Material] = []
    for index in obj.image_indices:
        analysis = photos[index - 1].analysis if index <= len(photos) else None
        for c in analysis.components if analysis is not None else []:
            if c.label is obj.label:
                found.append(c.material)
    return found


def needs_second_look(obj: InventoryObject, photos: Sequence[RoomPhoto], trigger: Trigger) -> bool:
    if obj.label not in MATERIAL_SENSITIVE:
        return False
    if trigger == "sensitive":
        return True
    seen = set(photo_materials(obj, photos)) | {obj.material}
    return len(seen) > 1 or bool(seen & GENERIC)


def best_photo(obj: InventoryObject, photos: Sequence[RoomPhoto]) -> int:
    """The 1-based index of the photo where the per-photo analysis was most confident of
    the label (largest count, then the earliest, on ties); the first index without one."""
    best: tuple[float, int, int] | None = None
    for index in obj.image_indices:
        analysis = photos[index - 1].analysis if index <= len(photos) else None
        for c in analysis.components if analysis is not None else []:
            if c.label is obj.label:
                key = (c.confidence, c.count, -index)
                if best is None or key > best:
                    best = key
    return obj.image_indices[0] if best is None else -best[2]


def build_request(obj: InventoryObject, photo: RoomPhoto, ctx: MaterialContext) -> ChatRequest:
    text = (
        f"Surface: {obj.label.value}. The earlier analysis said its material is "
        f"{obj.material.value}; check it against the image. What is this surface made of?"
    )
    return ChatRequest(
        model=ctx.model,
        system=ctx.prompt.text,
        messages=(
            Message(
                role="user",
                content="Image:",
                images=(
                    ImagePart(
                        media_type="image/jpeg",
                        data_base64=photo.image.base64,
                        detail="high",
                        ref=photo.file,
                    ),
                ),
            ),
            Message(role="user", content=text),
        ),
        response_model=ModelSurfaceMaterial,
        max_tokens=ctx.max_tokens,
        temperature=0.0,
        reasoning_effort=ctx.reasoning_effort,
        prompt_version=ctx.prompt.version_id,
        study_id=str(ctx.call.study_id),
        activity=ctx.call.activity,
    )


async def look(
    obj: InventoryObject, photos: Sequence[RoomPhoto], ctx: MaterialContext
) -> SecondLook:
    index = best_photo(obj, photos)
    result = SecondLook(
        label=obj.label.value, file=photos[index - 1].file, before=obj.material.value
    )
    request = build_request(obj, photos[index - 1], ctx)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result.attempts = attempt
        try:
            response = await call_model(ctx.provider, request, ctx.call)
        except ProviderBadResponse as exc:
            result.errors.append(f"attempt {attempt}: {exc}")
            if exc.usage is not None:
                result.cost_usd += cost_usd(exc.model or request.model, exc.usage)
            continue
        except ProviderRequestRejected as exc:
            # This photo crop refused (not retried: the same input gets the same answer);
            # the object keeps its material, as after an unusable answer. Configuration and
            # account errors propagate and stop the step.
            result.errors.append(f"attempt {attempt}: {exc}")
            return result
        result.cost_usd += cost_usd(response.model, response.usage)
        result.model = response.model
        if isinstance(response.parsed, ModelSurfaceMaterial):
            result.answer = response.parsed
            return result
        result.errors.append(f"attempt {attempt}: no structured material in the response")
    return result


def apply(obj: InventoryObject, result: SecondLook) -> InventoryObject:
    """``obj`` with the second look's material when it is usable (module doc)."""
    answer = result.answer
    if answer is None or answer.material is SurfaceMaterial.UNKNOWN:
        return obj
    if answer.confidence < MIN_CONFIDENCE:
        return obj
    material, note = TO_MATERIAL[answer.material]
    result.applied = True
    update: dict[str, Any] = {"material": material}
    if note is not None:
        update["note"] = note
    return obj.model_copy(update=update)


async def second_look(
    objects: Sequence[InventoryObject],
    photos: Sequence[RoomPhoto],
    ctx: MaterialContext,
    trigger: Trigger,
) -> tuple[list[InventoryObject], list[SecondLook]]:
    """The room's objects with the second look applied to the selected surfaces, and one
    record per look (in object order)."""
    selected = [i for i, obj in enumerate(objects) if needs_second_look(obj, photos, trigger)]
    looks = await asyncio.gather(*(look(objects[i], photos, ctx) for i in selected))
    updated = list(objects)
    for i, result in zip(selected, looks, strict=True):
        updated[i] = apply(objects[i], result)
    return updated, list(looks)
