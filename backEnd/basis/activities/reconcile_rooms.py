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
room) and rooms whose photos list nothing are skipped: their per-photo rows stand.

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
    heartbeating,
    provider_failure,
    run_pk,
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
from basis.db.session import session_scope
from basis.llm.call import CallContext
from basis.llm.errors import ProviderError
from basis.llm.provider import Budget, BudgetExceededError
from basis.llm.recorder import LlmCallRecorder
from basis.storage import ObjectNotFound
from basis.vision import room_step
from basis.vision.prep import ImageRejected, prepare_image
from basis.vision.reconcile import RoomPhoto
from basis.vision.rooms import ROOM_INVENTORY_SOURCE, is_room_inventory, optional_value
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


async def plan_rooms(session: AsyncSession, study_id: uuid.UUID, revision: int) -> Plan:
    """Which rooms of the revision need an inventory built (module doc)."""
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
    plan = Plan()
    for room in rooms:
        if room.room_type == RoomType.AMBIGUOUS.value or room.id not in photos_of:
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
        if self._deps.llm is None:
            raise ApplicationError(
                "no model provider is configured for the room step",
                type=PROVIDER_NOT_CONFIGURED,
                non_retryable=True,
            )
        config = room_step.load_config(
            prompt_version=settings.room_prompt_version,
            model=settings.room_model,
            reasoning_effort=settings.room_reasoning_effort,
            single_photo=settings.room_single_photo,
            materials=settings.room_materials,
        )
        async with session_scope(self._deps.sessionmaker) as session:
            plan = await plan_rooms(session, study_id, input.revision)
            run_id = await run_pk(session)
        budget = Budget(remaining_usd=max(input.budget_remaining_usd, 0.0))
        call = CallContext(
            study_id=study_id,
            run_id=run_id,
            activity=ACTIVITY,
            budget=budget,
            recorder=LlmCallRecorder(self._deps.sessionmaker),
        )
        gate = asyncio.Semaphore(settings.photo_concurrency)

        async def one(planned: PlannedRoom) -> room_step.RoomStepResult:
            async with gate:
                return await self._room(study_id, input.revision, planned, config, call)

        try:
            async with heartbeating("reconciling rooms"):
                results = await _all_or_none([one(p) for p in plan.todo])
        except BudgetExceededError as exc:
            raise budget_exceeded(f"room step of study {study_id}: {exc}") from exc
        except ProviderError as exc:
            raise provider_failure(exc, f"room step of study {study_id}") from exc
        output = ReconcileRoomsOutput(
            rooms=plan.rooms,
            built=len(results),
            current=plan.current,
            needs_review=sum(r.needs_review for r in results),
            cost_usd=round(budget.spent_usd, 6),
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
    ) -> room_step.RoomStepResult:
        assert self._deps.llm is not None
        heartbeat(f"room {planned.room.id}")
        photos = []
        for photo in planned.photos:
            try:
                image = prepare_image(await self._deps.storage.read(photo.gcs_uri))
            except ObjectNotFound:
                raise validation_error(f"photo file {photo.gcs_uri} is missing") from None
            except ImageRejected as exc:  # pragma: no cover - it was analyzed before
                raise validation_error(f"photo {photo.document_id}: {exc}") from exc
            analysis = room_step.analysis_from_rows(
                planned.room.room_type, photo.rows, _room_confidence(photo.rows)
            )
            photos.append(RoomPhoto(file=str(photo.document_id), image=image, analysis=analysis))
        outcome = await room_step.inventory_room(
            str(planned.room.id),
            photos,
            config,
            call=call,
            provider=self._deps.llm,
            heartbeat=heartbeat,
        )
        uri = room_uri(self._deps.bucket, study_id, planned.room.id, revision)
        await self._deps.storage.put(
            uri, _record_json(planned, outcome, config, revision), "application/json"
        )
        async with session_scope(self._deps.sessionmaker) as session:
            await _write(session, study_id, revision, planned, outcome, config, uri)
        heartbeat(f"room {planned.room.id} written")
        return outcome


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


async def _write(
    session: AsyncSession,
    study_id: uuid.UUID,
    revision: int,
    planned: PlannedRoom,
    outcome: room_step.RoomStepResult,
    config: room_step.RoomStepConfig,
    uri: str,
) -> None:
    """Replace the room's inventory rows with ``outcome``'s objects (one transaction)."""
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
    looks: dict[str, list[dict[str, Any]]] = {}
    for look in outcome.looks:
        looks.setdefault(look.label, []).append(look.record())
    repo = RoomRepository(session)
    for obj in result.inventory.objects:
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
                "prompt_version": (
                    config.room_prompt.version_id if outcome.step != "per_photo" else None
                ),
                "materials_prompt_version": (
                    config.materials_prompt.version_id if config.materials_prompt else None
                ),
                "model": _model_of(outcome),
                "room_needs_review": result.needs_review,
                "room_review_reason": result.review_reason,
                "record_uri": uri,
                **({"second_look": looks[obj.label.value]} if obj.label.value in looks else {}),
            },
        )


def _record_json(
    planned: PlannedRoom,
    outcome: room_step.RoomStepResult,
    config: room_step.RoomStepConfig,
    revision: int,
) -> bytes:
    result = outcome.result
    payload = {
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
    return json.dumps(payload, sort_keys=True, default=str).encode("utf-8")


__all__ = ["ACTIVITY", "Plan", "ReconcileRooms", "plan_rooms", "room_uri"]
