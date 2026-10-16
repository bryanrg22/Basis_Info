"""The pipeline's room step: one room's inventory from its photos (EVAL_REPORT section 16.5).

Per room, in the configuration section 16.5 measured best (the ``ROOM_*`` settings):

1. a one-photo room (``single_photo="per_photo"``): the per-photo analysis is the inventory,
   merged by label (:func:`basis.vision.reconcile.heuristic_merge`), no call: there is
   nothing to de-duplicate (``step="per_photo"``);
2. otherwise one :func:`basis.vision.reconcile.reconcile_room` call per slice of at most ten
   photos with the released ``room_inventory`` prompt (``step="model"``, or
   ``"heuristic"`` when the call stayed unusable and the room fell back to the heuristic
   merge, flagged);
3. the surface material second look (:mod:`basis.vision.surface_material`) on the
   inventory's wainscoting, wall finish and tile (``materials="sensitive"``), on the same
   model.

The same code path as ``evals.predict_rooms`` with ``--single-photo-rooms per-photo
--materials sensitive``, so what the evaluation scored is what runs. Transient provider
errors and an exhausted budget propagate (the activity's retry and budget handling apply);
an unusable answer is retried once by the call itself.

:func:`analysis_from_rows` rebuilds a photo's analysis from its stored ``components``
rows (corrections applied), which is what the room step reconciles in the pipeline.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.prompts import Prompt
from basis.llm.provider import ChatProvider
from basis.vision import surface_material
from basis.vision.reconcile import (
    PROMPT_NAME,
    ReconcileContext,
    RoomInventory,
    RoomPhoto,
    RoomResult,
    heuristic_merge,
    reconcile_room,
)
from basis.vision.schemas import (
    MAX_NOTE_CHARS,
    Attachment,
    Component,
    Material,
    PhotoAnalysis,
    normalize_room_type,
)

SinglePhoto = Literal["per_photo", "reconcile"]
Materials = Literal["none", "disagree", "sensitive"]
Step = Literal["per_photo", "model", "heuristic"]
# The released second-look prompt (EVAL_REPORT section 16.5: v3 adds painted masonry).
MATERIALS_PROMPT_VERSION: Final = "v3"


def _no_heartbeat(_: str) -> None:
    return None


@dataclass(frozen=True)
class RoomStepConfig:
    room_prompt: Prompt
    model: str
    reasoning_effort: str | None = None
    single_photo: SinglePhoto = "per_photo"
    materials: Materials = "sensitive"
    materials_prompt: Prompt | None = None


def load_config(
    *,
    prompt_version: str,
    model: str,
    reasoning_effort: str | None,
    single_photo: SinglePhoto,
    materials: Materials,
) -> RoomStepConfig:
    """The step's configuration with its prompts loaded and checked against the manifest
    (the worker calls this at startup, so a missing release fails there)."""
    return RoomStepConfig(
        room_prompt=prompts.load(PROMPT_NAME, prompt_version),
        model=model,
        reasoning_effort=reasoning_effort,
        single_photo=single_photo,
        materials=materials,
        materials_prompt=(
            prompts.load(surface_material.PROMPT_NAME, MATERIALS_PROMPT_VERSION)
            if materials != "none"
            else None
        ),
    )


@dataclass
class RoomStepResult:
    result: RoomResult
    step: Step
    looks: list[surface_material.SecondLook] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.result.needs_review


async def inventory_room(
    room_id: str,
    photos: Sequence[RoomPhoto],
    config: RoomStepConfig,
    *,
    call: CallContext,
    provider: ChatProvider,
    heartbeat: Callable[[str], None] = _no_heartbeat,
) -> RoomStepResult:
    """The room's inventory (module doc). ``photos`` are in the room's order."""
    if not photos:
        raise ValueError(f"room {room_id} has no photos")
    if len(photos) == 1 and config.single_photo == "per_photo":
        result = RoomResult(
            room_id=room_id,
            files=[photos[0].file],
            inventory=RoomInventory(objects=heuristic_merge([photos[0].analysis])),
            source="heuristic",
            review_reason="single_photo",
        )
        step: Step = "per_photo"
    else:
        ctx = ReconcileContext(
            call=call,
            provider=provider,
            prompt=config.room_prompt,
            model=config.model,
            heartbeat=heartbeat,
            reasoning_effort=config.reasoning_effort,
        )
        result = await reconcile_room(room_id, photos, ctx)
        step = "model" if result.source == "model" else "heuristic"
    looks: list[surface_material.SecondLook] = []
    if config.materials != "none" and config.materials_prompt is not None:
        heartbeat(f"room {room_id} material second look")
        objects, looks = await surface_material.second_look(
            result.inventory.objects,
            photos,
            surface_material.MaterialContext(
                call=call,
                provider=provider,
                prompt=config.materials_prompt,
                model=config.model,
                reasoning_effort=config.reasoning_effort,
            ),
            config.materials,
        )
        result.inventory = RoomInventory(objects=objects)
    return RoomStepResult(result=result, step=step, looks=looks)


def _enum_or[E: (Material, Attachment)](kind: type[E], value: Any, default: E) -> E:
    try:
        return kind(str(value))
    except ValueError:
        return default


def analysis_from_rows(
    room_type: str, rows: Sequence[Mapping[str, Any]], room_confidence: float = 1.0
) -> PhotoAnalysis:
    """A photo's analysis rebuilt from its ``components`` rows.

    Each row is ``{"label", "material", "attachment", "confidence", "raw"}``; the count
    and note come from ``raw`` (count 1 when missing). A label outside the vocabulary
    becomes ``other`` with the text as its note (the schema's rule), and a material or
    attachment outside its vocabulary (a reviewer's free text) is ``other``/``unknown``.
    """
    components = []
    for row in rows:
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        assert isinstance(raw, dict)
        count = raw.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            count = 1
        note = raw.get("note")
        material = row.get("material") or Material.UNKNOWN.value
        attachment = row.get("attachment") or Attachment.UNKNOWN.value
        components.append(
            Component.model_validate(
                {
                    "label": str(row["label"]),
                    "note": note[:MAX_NOTE_CHARS] if isinstance(note, str) else None,
                    "count": count,
                    "material": _enum_or(Material, material, Material.OTHER),
                    "attachment": _enum_or(Attachment, attachment, Attachment.UNKNOWN),
                    "confidence": float(row.get("confidence") or 0.0),
                }
            )
        )
    return PhotoAnalysis(
        room_type=normalize_room_type(room_type),
        room_confidence=room_confidence,
        components=components,
    )


__all__ = [
    "MATERIALS_PROMPT_VERSION",
    "RoomStepConfig",
    "RoomStepResult",
    "analysis_from_rows",
    "inventory_room",
    "load_config",
]
