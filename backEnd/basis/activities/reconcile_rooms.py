"""``reconcile_rooms``: one inventory per room of a revision (the room step, EVAL_REPORT 16.5).

The per-photo step counts each photo alone, so two photos of one kitchen give two sets of
cabinets (EVAL_REPORT section 6). After a revision's photos are analyzed, this activity
builds each room's inventory with :func:`basis.vision.room_step.inventory_room` in the
configuration section 16.5 chose (``ROOM_*`` settings): one-photo rooms take their photo's
analysis, multi-photo rooms one ``room_inventory`` call, then the material second look on
wainscoting, wall finish and tile.

Input of a room: its photos (``photo_rooms``, in file-name order), each photo's analysis
rebuilt from its ``components`` rows (so a reviewer's corrections carried into the revision
are what is reconciled), and the photos' prepared images (read from storage again;
preparation is deterministic). The Unassigned room (ambiguous photos, which are not one
room) and rooms whose photos list nothing are skipped: their per-photo rows stand. A room
grouped by file name (``rooms.group_key``) first gets its type and floor re-voted from its
photos' answers (:func:`vote_room`, ``group_photos``'s rules), and is reconciled even when
every photo answered ``ambiguous`` (its photos are one room by name).

Output rows: one ``components`` row per inventory object with no document and
``raw.source = "room_inventory"`` (:data:`basis.vision.rooms.ROOM_INVENTORY_SOURCE`); its
``raw`` keeps the count, the room's photos, the photos the object was seen in, the step
(``per_photo``, ``model`` or ``heuristic``), the prompt and model, and the second look's
record. The full record of each room (every call's attempts, response and raw provider
answers, dropped objects, second looks) is stored at :func:`room_uri`. ``process_assets``
builds a room's assets from these rows instead of its per-photo rows.

Idempotent per room: a room whose inventory rows name exactly the room's current photos is
current and skipped (carried over by a rejection, or written by an earlier attempt). A room
whose photos changed (a rooms rejection re-analyzed one into or out of it) has its old
inventory rows deleted and rebuilt. Each room is written in its own transaction as soon as
it is done, so a retry after a transient error only redoes the rooms not yet written.

Errors: as ``analyze_photo``. A transient provider error is retryable (Temporal retries
the activity); the budget exhausted is non-retryable ``BudgetExceededError``; no provider
is non-retryable ``ProviderNotConfigured``. The workflow treats any failure of this
activity as "flag the rooms review and continue": rooms without an inventory keep their
per-photo rows, the behaviour before the room step. An unusable reconciliation answer is
not an error: the room falls back to the heuristic merge and is flagged.

``ROOM_RECONCILE=false`` writes nothing but its completion (``enabled=False``).

``vote_only`` (patch ``components-gate-v1``): only the re-vote of the file-name rooms'
type and floor (:func:`plan_rooms`), committed, and no inventory built and no model call:
the photo stage and a rooms rejection run it so the rooms review shows the voted rooms; the
room step itself runs once the rooms review approves the grouping. A room whose photos a
rooms rejection moved is planned again then (its photo list changed).

Measured but off by default (EVAL_REPORT 26.10; flip after the second client package):

* ``ROOM_STEP_MODE=room_call``: each room :func:`basis.vision.room_step.room_path` sends
  down the ``room_call`` path (an interior room by its voted type) gets one
  ``room_photos_inventory/v2`` call with all its photos on ``ROOM_CALL_MODEL`` instead
  (``step="room_call"``); exterior, parking and mechanical rooms keep the reconcile path. An
  unusable room-call answer falls back to the reconcile path (``room_call_fallback``). Rows
  and the record say which ``path`` each room took and what it cost.
* ``MATERIALS_SECOND_LOOK=surface_crops``: after the inventory, each photo of the room is
  segmented (``Deps.segmenter``) and its wall, floor and ceiling crops are asked about
  (:func:`basis.vision.surface_crops.photo_crops`); the room's wall finish, wainscoting,
  tile and ceiling finish take the crops' majority material
  (:func:`basis.vision.surface_crops.apply_crops`), the rest keep theirs. A failure leaves
  that photo on its per-photo material and flags the room (``surface_crops_flags``), never
  fails it.

Both share the activity's budget (the study's remaining budget, every call reserved and
charged) and its error handling: an exhausted budget or a provider account error stops the
activity as before.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.exceptions import ApplicationError

from basis.activities._model_calls import (
    PROVIDER_NOT_CONFIGURED,
    call_origin,
    heartbeating,
    provider_failure,
)
from basis.activities._support import (
    budget_exceeded,
    heartbeat,
    parse_uuid,
    run_once,
    validation_error,
)
from basis.activities.deps import Deps
from basis.db.models import Component, Room
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.room import RoomRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import session_scope
from basis.errors.codes import E, tag
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.errors import ProviderError
from basis.llm.prompts import Prompt
from basis.llm.provider import Budget, BudgetExceededError
from basis.storage import ObjectNotFound
from basis.vision import room_step
from basis.vision import surface_crops as crops
from basis.vision.prep import ImageRejected, prepare_image
from basis.vision.reconcile import RoomInventory, RoomPhoto
from basis.vision.rooms import (
    ROOM_INVENTORY_SOURCE,
    SOURCE,
    is_room_inventory,
    name_hint,
    optional_value,
    vote_identity,
)
from basis.vision.schemas import RoomType
from basis.workflow.contracts import ReconcileRoomsInput, ReconcileRoomsOutput

ACTIVITY: Final = "reconcile_rooms"


def room_uri(bucket: str, study_id: uuid.UUID, room_id: uuid.UUID, revision: int) -> str:
    return f"gs://{bucket}/studies/{study_id}/rooms/{room_id}/r{revision}.json"


@dataclass(frozen=True)
class PlannedPhoto:
    document_id: uuid.UUID
    gcs_uri: str
    rows: tuple[dict[str, Any], ...]


@dataclass
class PlannedRoom:
    room: Room
    photos: list[PlannedPhoto]
    stale: list[uuid.UUID] = field(default_factory=list)

    @property
    def photo_ids(self) -> list[str]:
        return [str(p.document_id) for p in self.photos]


@dataclass
class RoomOutcome:
    """One built room: its inventory and, with surface crops on, the crops."""

    step: room_step.RoomStepResult
    crops: crops.RoomCrops | None = None

    @property
    def cost_usd(self) -> float:
        return self.step.cost_usd + (self.crops.cost_usd if self.crops is not None else 0.0)


@dataclass
class Plan:
    rooms: int = 0
    current: int = 0
    todo: list[PlannedRoom] = field(default_factory=list)


def _row(component: Component) -> dict[str, Any]:
    return {
        "label": component.label,
        "material": component.material,
        "attachment": component.attachment,
        "confidence": component.confidence,
        "raw": component.raw if isinstance(component.raw, dict) else {},
    }


def vote_room(room: Room, files: list[str], rows: list[list[dict[str, Any]]]) -> bool:
    """Re-vote a file-name room's type and floor from its photos (``group_photos``'s rules:
    :func:`basis.vision.rooms.vote_identity`; a floor a file name gives wins). ``files`` are
    the room's photos in file-name order, ``rows`` each photo's per-photo component rows
    (their ``raw`` keeps the photo's answer). A room a reviewer corrected (``source`` is not
    ``vision``) or keyed by the model's identity is left alone. True when it changed."""
    if room.group_key is None or room.source != SOURCE:
        return False
    answered = [a for a in map(_answer, rows) if a is not None]
    if not answered:
        return False
    room_type, model_floor = vote_identity([t for t, _ in answered], [f for _, f in answered])
    hint_floor = next((h.floor for h in map(name_hint, files) if h.floor is not None), None)
    floor = hint_floor if hint_floor is not None else model_floor
    new = (room_type.value, None if floor is None else str(floor))
    if (room.room_type, room.floor) == new:
        return False
    room.room_type, room.floor = new
    return True


def _answer(rows: list[dict[str, Any]]) -> tuple[str, int | None] | None:
    """The photo's room type and floor as its component rows recorded them."""
    for row in rows:
        raw = row["raw"]
        if isinstance(raw.get("room_type"), str):
            floor = raw.get("floor")
            ok = isinstance(floor, int) and not isinstance(floor, bool)
            return raw["room_type"], floor if ok else None
    return None


async def plan_rooms(session: AsyncSession, study_id: uuid.UUID, revision: int) -> Plan:
    """Which rooms of the revision need an inventory built (module doc). File-name rooms
    are re-voted first (:func:`vote_room`)."""
    repo = RoomRepository(session)
    rooms = await repo.list_for_revision(study_id, revision)
    assignments = await repo.photo_rooms(study_id, revision)
    components = await repo.components(study_id, revision)
    documents = {d.id: d for d in await DocumentRepository(session).list_for_study(study_id)}
    photos_of: dict[uuid.UUID, list[uuid.UUID]] = {}
    for assignment in assignments:
        photos_of.setdefault(assignment.room_id, []).append(assignment.document_id)
    rows_of: dict[uuid.UUID, list[Component]] = {}
    inventory_of: dict[uuid.UUID, list[Component]] = {}
    for component in components:
        if is_room_inventory(component.raw, component.document_id):
            if component.room_id is not None:
                inventory_of.setdefault(component.room_id, []).append(component)
        elif component.document_id is not None:
            rows_of.setdefault(component.document_id, []).append(component)
    for room in rooms:
        members = sorted(photos_of.get(room.id, []), key=lambda d: (documents[d].filename, str(d)))
        vote_room(
            room,
            [documents[d].filename for d in members],
            [[_row(c) for c in rows_of.get(d, [])] for d in members],
        )
    await session.flush()
    plan = Plan()
    for room in rooms:
        if room.id not in photos_of:
            continue
        if room.room_type == RoomType.AMBIGUOUS.value and room.group_key is None:
            continue
        ids = sorted(photos_of[room.id], key=lambda d: (documents[d].filename, str(d)))
        if not any(rows_of.get(d) for d in ids):
            continue
        plan.rooms += 1
        existing = inventory_of.get(room.id, [])
        wanted = [str(d) for d in ids]
        if existing and all(
            isinstance(c.raw, dict) and c.raw.get("photos") == wanted for c in existing
        ):
            plan.current += 1
            continue
        plan.todo.append(
            PlannedRoom(
                room=room,
                photos=[
                    PlannedPhoto(
                        document_id=d,
                        gcs_uri=documents[d].gcs_uri,
                        rows=tuple(_row(c) for c in rows_of.get(d, [])),
                    )
                    for d in ids
                ],
                stale=[c.id for c in existing],
            )
        )
    return plan


def _room_confidence(rows: tuple[dict[str, Any], ...]) -> float:
    for row in rows:
        value = row["raw"].get("room_confidence")
        if isinstance(value, int | float) and 0.0 <= value <= 1.0:
            return float(value)
    return 1.0


class ReconcileRooms:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="reconcile_rooms")
    async def run(self, input: ReconcileRoomsInput) -> ReconcileRoomsOutput:
        study_id = parse_uuid(input.study_id, "study_id")
        settings = self._deps.settings
        heartbeat("start")
        if not settings.room_reconcile:
            return await self._complete(input, ReconcileRoomsOutput(enabled=False))
        if input.vote_only:
            async with session_scope(self._deps.sessionmaker) as session:
                plan = await plan_rooms(session, study_id, input.revision)
            return await self._complete(
                input, ReconcileRoomsOutput(rooms=plan.rooms, current=plan.current)
            )
        if self._deps.llm is None:
            raise ApplicationError(
                tag(
                    E.PROVIDER_NOT_CONFIGURED_ROOMS,
                    "no model provider is configured for the room step",
                ),
                type=PROVIDER_NOT_CONFIGURED,
                non_retryable=True,
            )
        config = room_step.load_config(
            prompt_version=settings.room_prompt_version,
            model=settings.room_model,
            reasoning_effort=settings.room_reasoning_effort,
            single_photo=settings.room_single_photo,
            materials=settings.room_materials,
            mode=settings.room_step_mode,
            room_call_model=settings.room_call_model,
            room_call_reasoning_effort=settings.room_call_reasoning_effort,
            materials_model=settings.room_materials_model,
            materials_reasoning_effort=settings.room_materials_reasoning_effort,
        )
        crop_prompt = (
            prompts.load(crops.PROMPT_NAME, crops.PROMPT_VERSION)
            if settings.materials_second_look == "surface_crops"
            else None
        )
        async with session_scope(self._deps.sessionmaker) as session:
            plan = await plan_rooms(session, study_id, input.revision)
            origin = await call_origin(session, study_id)
        budget = Budget(remaining_usd=max(input.budget_remaining_usd, 0.0))
        call = origin.context(
            study_id, ACTIVITY, budget, self._deps.ledger(), revision=input.revision
        )
        gate = asyncio.Semaphore(settings.photo_concurrency)

        async def one(planned: PlannedRoom) -> RoomOutcome:
            async with gate:
                return await self._room(
                    study_id, input.revision, planned, config, call, crop_prompt
                )

        try:
            async with heartbeating("reconciling rooms"):
                results = await _all_or_none([one(p) for p in plan.todo])
        except BudgetExceededError as exc:
            raise budget_exceeded(f"room step of study {study_id}: {exc}", E.BUDGET_ROOMS) from exc
        except ProviderError as exc:
            raise provider_failure(exc, f"room step of study {study_id}") from exc
        output = ReconcileRoomsOutput(
            rooms=plan.rooms,
            built=len(results),
            current=plan.current,
            needs_review=sum(r.step.needs_review for r in results),
            cost_usd=round(budget.spent_usd, 6),
            room_calls=sum(r.step.step == "room_call" for r in results),
            room_call_fallbacks=sum(r.step.room_call_fallback is not None for r in results),
            surface_crop_rooms=sum(r.crops is not None for r in results),
            surface_crop_fallbacks=sum(bool(r.crops and r.crops.flags) for r in results),
        )

        return await self._complete(input, output)

    async def _complete(
        self, input: ReconcileRoomsInput, output: ReconcileRoomsOutput
    ) -> ReconcileRoomsOutput:
        """Record the activity's completion under its key (the stored output wins on a
        re-execution, like every activity's)."""

        async def work(_: AsyncSession) -> ReconcileRoomsOutput:
            return output

        return await run_once(
            self._deps,
            name=ACTIVITY,
            key=input.idempotency_key,
            output_type=ReconcileRoomsOutput,
            work=work,
        )

    async def _room(
        self,
        study_id: uuid.UUID,
        revision: int,
        planned: PlannedRoom,
        config: room_step.RoomStepConfig,
        call: CallContext,
        crop_prompt: Prompt | None = None,
    ) -> RoomOutcome:
        assert self._deps.llm is not None
        heartbeat(f"room {planned.room.id}")
        call = call.about("room", planned.room.id)  # every call below is about this room
        photos = []
        originals: list[bytes] = []
        for photo in planned.photos:
            try:
                original = await self._deps.storage.read(photo.gcs_uri)
                originals.append(original)
                image = prepare_image(original)
            except ObjectNotFound:
                raise validation_error(
                    f"photo file {photo.gcs_uri} is missing", E.ROOMS_FILE_MISSING
                ) from None
            except ImageRejected as exc:  # pragma: no cover - it was analyzed before
                raise validation_error(
                    f"photo {photo.document_id}: {exc}", E.ROOMS_IMAGE_REJECTED
                ) from exc
            analysis = room_step.analysis_from_rows(
                planned.room.room_type, photo.rows, _room_confidence(photo.rows)
            )
            photos.append(RoomPhoto(file=str(photo.document_id), image=image, analysis=analysis))
        step = await room_step.inventory_room(
            str(planned.room.id),
            photos,
            config,
            call=call,
            provider=self._deps.llm,
            heartbeat=heartbeat,
            room_type=planned.room.room_type,
        )
        outcome = RoomOutcome(step=step)
        if crop_prompt is not None:
            outcome.crops = await self._crops(planned, originals, step, call, crop_prompt)
        uri = room_uri(self._deps.bucket, study_id, planned.room.id, revision)
        await self._deps.storage.put(
            uri, _record_json(planned, outcome, config, revision), "application/json"
        )
        async with session_scope(self._deps.sessionmaker) as session:
            await _write(session, study_id, revision, planned, outcome, config, uri)
        heartbeat(f"room {planned.room.id} written")
        return outcome

    async def _crops(
        self,
        planned: PlannedRoom,
        originals: list[bytes],
        step: room_step.RoomStepResult,
        call: CallContext,
        prompt: Prompt,
    ) -> crops.RoomCrops:
        """The surface-crop material look on the room's photos (module doc); replaces the
        inventory's surface materials in place."""
        assert self._deps.llm is not None
        settings = self._deps.settings
        ctx = crops.CropContext(
            call=call,
            provider=self._deps.llm,
            prompt=prompt,
            model=settings.surface_crop_model,
            reasoning_effort=settings.surface_crop_reasoning_effort,
            heartbeat=heartbeat,
        )
        objects, found = await crops.look_at_room(
            [(str(p.document_id), o) for p, o in zip(planned.photos, originals, strict=True)],
            step.result.inventory.objects,
            self._deps.segmenter,
            ctx,
        )
        step.result.inventory = RoomInventory(objects=objects)
        return found


async def _all_or_none[T](
    coroutines: list[Coroutine[Any, Any, T]],
) -> list[T]:
    """Run concurrently; the first exception cancels the rest and propagates as itself."""
    tasks = [asyncio.ensure_future(c) for c in coroutines]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _model_of(outcome: room_step.RoomStepResult) -> str | None:
    """The models that answered the room's calls and second looks, ``+``-joined."""
    models = {c.model for c in outcome.result.calls if c.model}
    models |= {x.model for x in outcome.looks if x.model}
    return "+".join(sorted(models)) if models else None


def _prompt_of(outcome: room_step.RoomStepResult, config: room_step.RoomStepConfig) -> str | None:
    """The inventory prompt of the path the room took (none for a one-photo room)."""
    if outcome.step == "per_photo":
        return None
    if outcome.step == "room_call" and config.room_call is not None:
        return config.room_call.prompt.version_id
    return config.room_prompt.version_id


def _path_raw(outcome: RoomOutcome, config: room_step.RoomStepConfig) -> dict[str, Any]:
    """What a row records of the path and the measured-but-off options (absent when they
    are off, so ROOM_STEP_MODE=reconcile rows are exactly the rows before them)."""
    raw: dict[str, Any] = {}
    step = outcome.step
    if config.room_call is not None:
        raw["path"] = step.path
        raw["room_call_fallback"] = step.room_call_fallback
    if outcome.crops is not None:
        raw["materials_second_look"] = "surface_crops"
        raw["surface_crops_flags"] = outcome.crops.flags
    return raw


async def _write(
    session: AsyncSession,
    study_id: uuid.UUID,
    revision: int,
    planned: PlannedRoom,
    built: RoomOutcome,
    config: room_step.RoomStepConfig,
    uri: str,
) -> None:
    """Replace the room's inventory rows with ``built``'s objects (one transaction)."""
    outcome = built.step
    changed = {c["index"]: c for c in (built.crops.changes if built.crops is not None else [])}
    extra = _path_raw(built, config)
    current = (
        await session.scalars(
            select(Component).where(
                Component.study_id == study_id,
                Component.revision == revision,
                Component.room_id == planned.room.id,
                Component.document_id.is_(None),
            )
        )
    ).all()
    stale = [c.id for c in current if is_room_inventory(c.raw, c.document_id)]
    if stale:
        await session.execute(delete(Component).where(Component.id.in_(stale)))
    result = outcome.result
    await StudyEventRepository(session).record(
        study_id,
        "inventory.rebuilt",
        actor="model",
        revision=revision,
        entity_type="room",
        entity_id=planned.room.id,
        detail={
            "deleted": [str(c) for c in stale],
            "written": len(result.inventory.objects),
            "step": outcome.step,
            "model": _model_of(outcome),
            "record_uri": uri,
        },
    )
    looks: dict[str, list[dict[str, Any]]] = {}
    for look in outcome.looks:
        looks.setdefault(look.label, []).append(look.record())
    repo = RoomRepository(session)
    for index, obj in enumerate(result.inventory.objects):
        await repo.add_component(
            study_id=study_id,
            revision=revision,
            document_id=None,
            room_id=planned.room.id,
            label=obj.label.value,
            material=optional_value(obj.material.value),
            attachment=optional_value(obj.attachment.value),
            confidence=obj.confidence,
            raw={
                "source": ROOM_INVENTORY_SOURCE,
                "count": obj.count,
                "note": obj.note,
                "material": obj.material.value,
                "attachment": obj.attachment.value,
                "photos": planned.photo_ids,
                "seen_in": result.files_for(obj),
                "step": outcome.step,
                "prompt_version": _prompt_of(outcome, config),
                "materials_prompt_version": (
                    config.materials_prompt.version_id
                    if config.materials_prompt and outcome.step != "room_call"
                    else None
                ),
                "model": _model_of(outcome),
                "room_needs_review": result.needs_review,
                "room_review_reason": result.review_reason,
                "record_uri": uri,
                **({"second_look": looks[obj.label.value]} if obj.label.value in looks else {}),
                **extra,
                **({"material_before_crops": changed[index]["before"]} if index in changed else {}),
            },
        )


def _record_json(
    planned: PlannedRoom,
    built: RoomOutcome,
    config: room_step.RoomStepConfig,
    revision: int,
) -> bytes:
    outcome = built.step
    result = outcome.result
    payload: dict[str, Any] = {
        "room_id": str(planned.room.id),
        "revision": revision,
        "photos": planned.photo_ids,
        "step": outcome.step,
        "prompt_version": config.room_prompt.version_id,
        "materials": config.materials,
        "materials_prompt_version": (
            config.materials_prompt.version_id if config.materials_prompt else None
        ),
        "model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "needs_review": result.needs_review,
        "review_reason": result.review_reason,
        "objects": [
            {**obj.model_dump(mode="json"), "files": result.files_for(obj)}
            for obj in result.inventory.objects
        ],
        "dropped": [
            {"reason": d.reason, "object": d.object, "call": d.call} for d in result.dropped
        ],
        "calls": [
            {
                "files": c.files,
                "first_index": c.first_index,
                "attempts": c.attempts,
                "provider": c.provider,
                "model": c.model,
                "cost_usd": round(c.cost_usd, 6),
                "errors": c.errors,
                "response": c.response,
                "raw": c.raw,
            }
            for c in result.calls
        ],
        "second_looks": [look.record() for look in outcome.looks],
    }
    if config.room_call is not None:
        # Only when the room call is on, so ROOM_STEP_MODE=reconcile records are unchanged.
        payload |= {
            "path": outcome.path,
            "room_call_fallback": outcome.room_call_fallback,
            "room_call_prompt_version": config.room_call.prompt.version_id,
            "room_call_model": config.room_call.model,
            "room_call_reasoning_effort": config.room_call.reasoning_effort,
            "cost_usd": round(built.cost_usd, 6),
        }
    if built.crops is not None:
        payload["surface_crops"] = built.crops.record()
        payload["cost_usd"] = round(built.cost_usd, 6)
    return json.dumps(payload, sort_keys=True, default=str).encode("utf-8")


__all__ = ["ACTIVITY", "Plan", "ReconcileRooms", "plan_rooms", "room_uri"]
