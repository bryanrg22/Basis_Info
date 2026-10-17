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
   inventory's wainscoting, wall finish and tile (``materials="sensitive"``), on
   ``materials_model`` at ``materials_reasoning_effort`` (``ROOM_MATERIALS_MODEL``) when
   given, else on the reconciliation call's model and effort.

The same code path as ``evals.predict_rooms`` with ``--single-photo-rooms per-photo
--materials sensitive``, so what the evaluation scored is what runs. Transient provider
errors and an exhausted budget propagate (the activity's retry and budget handling apply);
an unusable answer is retried once by the call itself.

With ``ROOM_STEP_MODE=room_call`` (EVAL_REPORT 26.10, measured but off by default) the
configuration carries a :class:`RoomCallConfig`, and a room :func:`room_path` sends down the
``room_call`` path (every interior room, one-photo rooms included, as section 26.4 measured)
gets instead one :func:`room_call`: all the room's photos in one
``room_photos_inventory/v2`` request (:func:`basis.vision.multiview.build_request`, scope
``room``), no per-photo lists, the answer checked like the reconciliation call's and stored
the same way (``step="room_call"``). No material second look follows it: section 26.7's
combination measured the room call's own materials (37 of 38 report rows). A room call whose
answer stays unusable falls back to the reconcile path above, and the result says so
(``room_call_fallback``; the failed call's record and cost are kept). Exterior, parking and
mechanical rooms always take the reconcile path. ``evals.replay_rooms`` runs
:func:`inventory_room` exactly as the activity does, on stored per-photo outputs.

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
from basis.vision import multiview, surface_material
from basis.vision.reconcile import (
    PROMPT_NAME,
    CallRecord,
    ReconcileContext,
    RoomInventory,
    RoomPhoto,
    RoomResult,
    attempt_inventory,
    check_objects,
    heuristic_merge,
    reconcile_room,
)
from basis.vision.schemas import (
    MAX_NOTE_CHARS,
    Attachment,
    Component,
    Material,
    PhotoAnalysis,
    RoomType,
    normalize_room_type,
)

SinglePhoto = Literal["per_photo", "reconcile"]
Materials = Literal["none", "disagree", "sensitive"]
Step = Literal["per_photo", "model", "heuristic", "room_call"]
StepMode = Literal["reconcile", "room_call"]
RoomPath = Literal["reconcile", "room_call"]
# The released second-look prompt (EVAL_REPORT section 16.5: v3 adds painted masonry).
MATERIALS_PROMPT_VERSION: Final = "v3"
# The released room-call prompt (EVAL_REPORT 26.4: v2 carries room_inventory/v3's counting
# rules and the per-photo prompts' label definitions).
ROOM_CALL_PROMPT_VERSION: Final = "v2"
# Rooms that keep the reconcile path in ROOM_STEP_MODE=room_call (EVAL_REPORT 26.7). The
# study's rule was the file names' no-floor groups (x_property_front, x_shed,
# x_mechanicals: the room calls dropped site items there); their voted types are exterior,
# exterior and mechanical, so the rule is fixed on the room type, which a package named
# without floors (or not named at all) still has. Parking is site work.
RECONCILE_ROOM_TYPES: Final = frozenset(
    {RoomType.EXTERIOR.value, RoomType.PARKING.value, RoomType.MECHANICAL.value}
)


def _no_heartbeat(_: str) -> None:
    return None


@dataclass(frozen=True)
class RoomCallConfig:
    """The room call of ``ROOM_STEP_MODE=room_call`` (EVAL_REPORT 26.10)."""

    prompt: Prompt
    model: str
    reasoning_effort: str | None = None
    max_tokens: int = multiview.MAX_TOKENS["room"]


@dataclass(frozen=True)
class RoomStepConfig:
    room_prompt: Prompt
    model: str
    reasoning_effort: str | None = None
    single_photo: SinglePhoto = "per_photo"
    materials: Materials = "sensitive"
    materials_prompt: Prompt | None = None
    # None: ROOM_STEP_MODE=reconcile, every room takes the reconcile path.
    room_call: RoomCallConfig | None = None
    # The second look's model and effort; None: the reconciliation call's.
    materials_model: str | None = None
    materials_reasoning_effort: str | None = None

    @property
    def look_model(self) -> str:
        """The model of the material second look."""
        return self.model if self.materials_model is None else self.materials_model

    @property
    def look_reasoning_effort(self) -> str | None:
        """The effort of the material second look (with its model)."""
        if self.materials_model is None:
            return self.reasoning_effort
        return self.materials_reasoning_effort

    @property
    def mode(self) -> StepMode:
        return "reconcile" if self.room_call is None else "room_call"


def load_config(
    *,
    prompt_version: str,
    model: str,
    reasoning_effort: str | None,
    single_photo: SinglePhoto,
    materials: Materials,
    mode: StepMode = "reconcile",
    room_call_model: str = "claude-opus-5-5",
    room_call_reasoning_effort: str | None = "high",
    materials_model: str | None = None,
    materials_reasoning_effort: str | None = None,
) -> RoomStepConfig:
    """The step's configuration with its prompts loaded and checked against the manifest
    (the worker calls this at startup, so a missing release fails there). ``mode``
    ``room_call`` adds the room call on ``room_call_model`` (``ROOM_CALL_*``);
    ``materials_model`` (``ROOM_MATERIALS_MODEL``) puts the second look on its own model."""
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
        room_call=(
            RoomCallConfig(
                prompt=prompts.load(multiview.PROMPTS["room"], ROOM_CALL_PROMPT_VERSION),
                model=room_call_model,
                reasoning_effort=room_call_reasoning_effort,
            )
            if mode == "room_call"
            else None
        ),
        materials_model=materials_model,
        materials_reasoning_effort=materials_reasoning_effort,
    )


def room_path(config: RoomStepConfig, room_type: str) -> RoomPath:
    """Which path a room of ``room_type`` (its voted type) takes: ``room_call`` for every
    room not of :data:`RECONCILE_ROOM_TYPES` when the room call is configured, else
    ``reconcile``."""
    if config.room_call is not None and room_type not in RECONCILE_ROOM_TYPES:
        return "room_call"
    return "reconcile"


@dataclass
class RoomStepResult:
    result: RoomResult
    step: Step
    looks: list[surface_material.SecondLook] = field(default_factory=list)
    path: RoomPath = "reconcile"
    # Why a room sent down the room_call path was built on the reconcile path instead.
    room_call_fallback: str | None = None

    @property
    def needs_review(self) -> bool:
        return self.result.needs_review

    @property
    def cost_usd(self) -> float:
        """Every call of the room: inventory calls (a failed room call included) and the
        second looks."""
        return sum(c.cost_usd for c in self.result.calls) + sum(x.cost_usd for x in self.looks)


async def room_call(
    room_id: str,
    photos: Sequence[RoomPhoto],
    config: RoomCallConfig,
    *,
    call: CallContext,
    provider: ChatProvider,
    heartbeat: Callable[[str], None] = _no_heartbeat,
) -> tuple[CallRecord, RoomResult | None]:
    """One call with every photo of the room (``room_photos_inventory``): its record, and
    the room's result, or None when the answer stayed unusable after one retry (or the
    room has more photos than one request may carry). Objects pointing at no image or at
    an image outside the request are dropped with their reason and the room is flagged,
    as in the reconciliation call. Per-photo analyses are not sent."""
    files = [p.file for p in photos]
    record = CallRecord(files=files, first_index=1)
    if len(photos) > multiview.MAX_IMAGES:
        record.errors.append(f"{len(photos)} photos, more than {multiview.MAX_IMAGES} per call")
        return record, None
    request = multiview.build_request(
        "room",
        [multiview.Photo(file=p.file, room_id=room_id, image=p.image) for p in photos],
        multiview.CallSettings(
            model=config.model,
            prompt=config.prompt,
            study_id=str(call.study_id),
            activity=call.activity,
            max_tokens=config.max_tokens,
            reasoning_effort=config.reasoning_effort,
        ),
    )
    ctx = ReconcileContext(
        call=call,
        provider=provider,
        prompt=config.prompt,
        model=config.model,
        max_tokens=config.max_tokens,
        heartbeat=heartbeat,
        reasoning_effort=config.reasoning_effort,
    )
    parsed = await attempt_inventory(request, record, ctx, f"room {room_id} room call")
    if parsed is None:
        return record, None
    kept, dropped = check_objects(parsed, len(photos), 0, 1)
    result = RoomResult(
        room_id=room_id,
        files=files,
        inventory=RoomInventory(objects=kept),
        source="model",
        dropped=dropped,
        calls=[record],
    )
    if dropped:
        result.needs_review = True
        result.review_reason = f"dropped_objects:{len(dropped)}"
    return record, result


async def inventory_room(
    room_id: str,
    photos: Sequence[RoomPhoto],
    config: RoomStepConfig,
    *,
    call: CallContext,
    provider: ChatProvider,
    heartbeat: Callable[[str], None] = _no_heartbeat,
    room_type: str | None = None,
) -> RoomStepResult:
    """The room's inventory (module doc). ``photos`` are in the room's order;
    ``room_type`` (the room's voted type) chooses the path (:func:`room_path`) when the
    room call is configured; without it the room takes the reconcile path."""
    if not photos:
        raise ValueError(f"room {room_id} has no photos")
    fallback: str | None = None
    failed: CallRecord | None = None
    if (
        config.room_call is not None
        and room_type is not None
        and room_path(config, room_type) == "room_call"
    ):
        heartbeat(f"room {room_id} room call")
        record, outcome = await room_call(
            room_id, photos, config.room_call, call=call, provider=provider, heartbeat=heartbeat
        )
        if outcome is not None:
            return RoomStepResult(result=outcome, step="room_call", path="room_call")
        fallback = (
            "too_many_photos" if len(photos) > multiview.MAX_IMAGES else "invalid_model_output"
        )
        failed = record
    built = await _reconcile_path(
        room_id, photos, config, call=call, provider=provider, heartbeat=heartbeat
    )
    if failed is not None:
        built.result.calls.insert(0, failed)
        built.path, built.room_call_fallback = "room_call", fallback
    return built


async def _reconcile_path(
    room_id: str,
    photos: Sequence[RoomPhoto],
    config: RoomStepConfig,
    *,
    call: CallContext,
    provider: ChatProvider,
    heartbeat: Callable[[str], None],
) -> RoomStepResult:
    """Steps 1 to 3 of the module doc (the section 16.5 room step)."""
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
                model=config.look_model,
                reasoning_effort=config.look_reasoning_effort,
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
    "RECONCILE_ROOM_TYPES",
    "ROOM_CALL_PROMPT_VERSION",
    "RoomCallConfig",
    "RoomPath",
    "RoomStepConfig",
    "RoomStepResult",
    "analysis_from_rows",
    "inventory_room",
    "load_config",
    "room_call",
    "room_path",
]
