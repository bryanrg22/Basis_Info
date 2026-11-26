"""``measure_rooms``: each measured room's quantities drafted from its photos (EVAL_REPORT 110).

Patch ``measure-drafts-v1`` runs it once before an execution's first ``process_assets`` when
``MEASURE_DRAFTS_INTERIOR`` is on (the API's setting puts it on the workflow input; the worker's
own must be on too, else the activity drafts nothing). For each room of the revision whose type
EVAL_REPORT 107 measured (:data:`basis.domain.measure_drafts.INTERIOR_ROOM_TYPES`) and that has
photos (``photo_rooms``, in file-name order): its photos read from storage and prepared on a
thread as production prepares any photo, then one call on ``MEASURE_INTERIOR_MODEL`` at
``MEASURE_INTERIOR_REASONING_EFFORT`` with ``MEASURE_INTERIOR_MAX_TOKENS`` and the prompt
``measure_interior_reference/v1`` (:func:`basis.vision.measure_interior.build_request`, the
setup section 107 measured). Every call goes through :func:`basis.llm.call.call_model` with the
study's budget and the ledger (``llm_calls``, about the room).

Failures (the money rule, EVAL_REPORT 78: a failed call never becomes a number). A room whose
photos the provider refuses is ``refused``; one with no usable answer after a second attempt,
or a provider error that outlasted the provider pool's own retries, ``failed``. Either keeps the
room's rows on today's formulas (``code`` ``E.MEASURE_ROOM_NOT_DRAFTED``); the workflow flags
the assets review. The worst case of every call (two attempts a room) must fit the remaining
budget before the first call, else the non-retryable ``BudgetExceededError``
(``E.MEASURE_INTERIOR_BUDGET``). An account or configuration error stops the step
(``ProviderAccountError``). The workflow treats
any failure of the step as "no drafts": the assets are built on today's formulas.

Writes nothing but its completion (the drafts are its output, passed to ``process_assets`` by
the workflow and recorded on each drafted row's takeoff). Idempotent: a completed key returns
its stored output without a call.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.exceptions import ApplicationError

from basis.activities._model_calls import (
    PROVIDER_NOT_CONFIGURED,
    call_origin,
    heartbeating,
    provider_failure,
)
from basis.activities._support import budget_exceeded, heartbeat, parse_uuid, run_once
from basis.activities.deps import Deps
from basis.db.repositories.activity_result import COMPLETED, ActivityResultRepository
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.domain.measure_drafts import INTERIOR_ROOM_TYPES
from basis.domain.rooms import normalize_room_type
from basis.errors.codes import E, tag
from basis.llm.call import CallContext, call_model, estimate_request_usd
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderError,
    ProviderRequestRejected,
)
from basis.llm.provider import Budget, BudgetExceededError, ChatRequest
from basis.storage import ObjectNotFound
from basis.vision import measure_interior
from basis.vision.measure_interior import InteriorAnswer
from basis.vision.prep import ImageRejected, prepare_image_in_thread
from basis.workflow.contracts import MeasureRoomsInput, MeasureRoomsOutput, RoomDraftOut

ACTIVITY: Final = "measure_rooms"
ATTEMPTS: Final = 2  # an unusable answer is asked once more (the photo runner's rule)
# The message's word for each measured type (basis.vision.measure_interior.ROOM_NAMES).
MESSAGE_TYPE: Final = {
    "kitchen": "kitchen",
    "bedroom": "bedroom",
    "bathroom": "bathroom",
    "living_room": "living",
    "family_room": "living",
}


@dataclass(frozen=True, slots=True)
class PlannedRoom:
    room_id: uuid.UUID
    room_type: str  # normalized
    photos: tuple[uuid.UUID, ...]
    uris: tuple[str, ...]


async def plan(session: AsyncSession, study_id: uuid.UUID, revision: int) -> list[PlannedRoom]:
    """The revision's rooms of a measured type with photos, each with its photos in file-name
    order (as the room step orders them)."""
    repo = RoomRepository(session)
    rooms = await repo.list_for_revision(study_id, revision)
    documents = {d.id: d for d in await DocumentRepository(session).list_for_study(study_id)}
    photos_of: dict[uuid.UUID, list[uuid.UUID]] = {}
    for assignment in await repo.photo_rooms(study_id, revision):
        if assignment.document_id in documents:
            photos_of.setdefault(assignment.room_id, []).append(assignment.document_id)
    out = []
    for room in sorted(rooms, key=lambda r: (r.name, str(r.id))):
        kind = normalize_room_type(room.room_type)
        ids = photos_of.get(room.id)
        if kind not in INTERIOR_ROOM_TYPES or not ids:
            continue
        ordered = sorted(ids, key=lambda d: (documents[d].filename, str(d)))
        out.append(
            PlannedRoom(
                room.id,
                kind,
                tuple(ordered),
                tuple(documents[d].gcs_uri for d in ordered),
            )
        )
    return out


class MeasureRooms:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="measure_rooms")
    async def run(self, input: MeasureRoomsInput) -> MeasureRoomsOutput:
        study_id = parse_uuid(input.study_id, "study_id")
        settings = self._deps.settings
        heartbeat("start")
        async with session_scope(self._deps.sessionmaker) as session:
            claimed = await ActivityResultRepository(session).get(input.idempotency_key)
            done = claimed is not None and claimed.status == COMPLETED
            planned = [] if done else await plan(session, study_id, input.revision)
            origin = await call_origin(session, study_id)
        if done or not settings.measure_drafts_interior:
            return await self._complete(input, MeasureRoomsOutput())
        if not planned:
            return await self._complete(input, MeasureRoomsOutput())
        if self._deps.llm is None:
            raise ApplicationError(
                tag(
                    E.MEASURE_INTERIOR_FAILED,
                    "no model provider is configured for room measurements",
                ),
                type=PROVIDER_NOT_CONFIGURED,
                non_retryable=True,
            )
        budget = Budget(remaining_usd=max(input.budget_remaining_usd, 0.0))
        call = origin.context(
            study_id, ACTIVITY, budget, self._deps.ledger(), revision=input.revision
        )
        prepared = [await self._prepare(room) for room in planned]
        requests = {
            room.room_id: self._request(room, jpegs, study_id)
            for room, jpegs in zip(planned, prepared, strict=True)
            if jpegs
        }
        worst = round(sum(estimate_request_usd(r) for r in requests.values()) * ATTEMPTS, 6)
        if worst > input.budget_remaining_usd:
            raise budget_exceeded(
                f"room measurements of study {study_id}: {len(requests)} rooms need up to "
                f"${worst}; ${input.budget_remaining_usd:.4f} remains",
                E.MEASURE_INTERIOR_BUDGET,
            )
        gate = asyncio.Semaphore(settings.photo_concurrency)

        async def one(room: PlannedRoom, jpegs: list[bytes]) -> RoomDraftOut:
            if not jpegs:
                return _not_drafted(room, "failed", "no photo of the room could be read")
            async with gate:
                return await self._measure(room, requests[room.room_id], call)

        try:
            async with heartbeating("measuring rooms"):
                drafts = await asyncio.gather(
                    *(one(r, j) for r, j in zip(planned, prepared, strict=True))
                )
        except BudgetExceededError as exc:
            raise budget_exceeded(
                f"room measurements of study {study_id}: {exc}", E.MEASURE_INTERIOR_BUDGET
            ) from exc
        except ProviderAccountError as exc:
            raise provider_failure(exc, f"room measurements of study {study_id}") from exc
        output = MeasureRoomsOutput(
            rooms=len(planned),
            drafted=sum(d.status == "drafted" for d in drafts),
            not_drafted=sum(d.status != "drafted" for d in drafts),
            cost_usd=round(budget.spent_usd, 6),
            drafts=tuple(drafts),
        )
        return await self._complete(input, output)

    async def _prepare(self, room: PlannedRoom) -> list[bytes]:
        """The room's photos prepared (decode, orient, resize, re-encode) on a thread; a photo
        that cannot be read is left out (it was analyzed before, so this is rare)."""
        out = []
        for uri in room.uris:
            heartbeat(f"room {room.room_id} photo")
            try:
                data = await self._deps.storage.read(uri)
                image = await prepare_image_in_thread(data)
            except (ObjectNotFound, ImageRejected):
                continue
            out.append(image.jpeg)
        return out

    def _request(self, room: PlannedRoom, jpegs: list[bytes], study_id: uuid.UUID) -> ChatRequest:
        settings = self._deps.settings
        return measure_interior.build_request(
            model=settings.measure_interior_model,
            reasoning_effort=settings.measure_interior_reasoning_effort,
            max_tokens=settings.measure_interior_max_tokens,
            room_type=MESSAGE_TYPE[room.room_type],
            jpegs=jpegs,
            ref=f"room:{room.room_id}",
            study_id=str(study_id),
            activity=ACTIVITY,
        )

    async def _measure(
        self, room: PlannedRoom, request: ChatRequest, call: CallContext
    ) -> RoomDraftOut:
        """One room's call, asked once more on an unusable answer (module doc)."""
        assert self._deps.llm is not None
        ctx = call.about("room", room.room_id)
        spent = ctx.budget.spent_usd
        reason = "no usable answer"
        for _ in range(ATTEMPTS):
            heartbeat(f"room {room.room_id}")
            try:
                response = await call_model(self._deps.llm, request, ctx)
            except ProviderRequestRejected as exc:
                return _not_drafted(room, "refused", f"the provider refused the photos: {exc}")
            except ProviderBadResponse as exc:
                reason = f"unusable answer: {exc}"
                continue
            except ProviderAccountError:
                raise
            except ProviderError as exc:
                return _not_drafted(room, "failed", f"the provider failed: {exc}")
            answer = response.parsed
            if not isinstance(answer, InteriorAnswer):
                reason = "no structured answer"
                continue
            return RoomDraftOut(
                room_id=str(room.room_id),
                room_type=room.room_type,
                photos=tuple(str(p) for p in room.photos),
                status="drafted",
                quantities={
                    k: float(v)
                    for k in measure_interior.QUANTITIES
                    if (v := getattr(answer, k)) is not None
                },
                model=response.model,
                prompt_version=request.prompt_version,
                confidence=answer.confidence,
                room_fully_seen=answer.room_fully_seen,
                notes=answer.notes or None,
                cost_usd=round(ctx.budget.spent_usd - spent, 6),
            )
        return _not_drafted(room, "failed", reason, cost=round(ctx.budget.spent_usd - spent, 6))

    async def _complete(
        self, input: MeasureRoomsInput, output: MeasureRoomsOutput
    ) -> MeasureRoomsOutput:
        async def work(_: AsyncSession) -> MeasureRoomsOutput:
            return output

        return await run_once(
            self._deps,
            name=ACTIVITY,
            key=input.idempotency_key,
            output_type=MeasureRoomsOutput,
            work=work,
        )


def _not_drafted(room: PlannedRoom, status: str, reason: str, *, cost: float = 0.0) -> RoomDraftOut:
    return RoomDraftOut(
        room_id=str(room.room_id),
        room_type=room.room_type,
        photos=tuple(str(p) for p in room.photos),
        status="refused" if status == "refused" else "failed",
        code=E.MEASURE_ROOM_NOT_DRAFTED,
        reason=reason[:500],
        cost_usd=cost,
    )


__all__ = ["ACTIVITY", "MeasureRooms", "PlannedRoom", "plan"]
