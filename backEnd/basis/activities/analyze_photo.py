"""``analyze_photo`` (Stage 2 stub): one photo -> its room and two components.

Stage 2 body: infer the room from the file name, create the room for this revision the
first time any photo names it, assign the photo to it (``photo_rooms``) and add two
template components. Charges a fixed stub cost against the remaining budget and raises the
non-retryable ``BudgetExceededError`` when the budget cannot cover it; the workflow flags
the photo and continues. Stage 3 replaces the body with Grounding DINO, SAM2 and the VLM.

Test hooks (``SimulateFault``) are honoured only when ``BASIS_ENV`` is dev or test:
``fail_attempts`` fails the first N attempts (retryable), ``fail_permanently`` fails with
a non-retryable error, and ``sleep_seconds`` sleeps inside the work transaction, after the
room is written, heartbeating every second (to test cancellation and worker loss).
"""

from __future__ import annotations

import asyncio
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.exceptions import ApplicationError

from basis.activities._support import (
    budget_exceeded,
    heartbeat,
    parse_uuid,
    run_once,
    validation_error,
)
from basis.activities.deps import Deps
from basis.activities.stub_rules import PHOTO_COST_USD, components_for, room_for_filename
from basis.db.enums import DocumentKind
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.room import RoomRepository
from basis.workflow.contracts import AnalyzePhotoInput, AnalyzePhotoOutput, SimulateFault

SIMULATED_FAILURE: Final = "SimulatedFailure"
SOURCE: Final = "stub"


def check_faults(simulate: SimulateFault | None, attempt: int) -> None:
    """Raise the failure a ``SimulateFault`` asks for on this attempt, if any."""
    if simulate is None:
        return
    if simulate.fail_permanently:
        raise ApplicationError(
            "simulated permanent failure", type=SIMULATED_FAILURE, non_retryable=True
        )
    if attempt <= simulate.fail_attempts:
        raise ApplicationError(f"simulated failure on attempt {attempt}", type=SIMULATED_FAILURE)


async def sleep_heartbeating(seconds: float) -> None:
    remaining = seconds
    while remaining > 0:
        heartbeat("sleeping")
        step = min(1.0, remaining)
        await asyncio.sleep(step)
        remaining -= step


class AnalyzePhoto:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="analyze_photo")
    async def run(self, input: AnalyzePhotoInput) -> AnalyzePhotoOutput:
        study_id = parse_uuid(input.study_id, "study_id")
        document_id = parse_uuid(input.document_id, "document_id")
        simulate = input.simulate if self._deps.faults_enabled else None
        heartbeat("start")
        check_faults(simulate, activity.info().attempt)
        if input.budget_remaining_usd < PHOTO_COST_USD:
            raise budget_exceeded(
                f"photo {document_id} needs ${PHOTO_COST_USD}; "
                f"${input.budget_remaining_usd:.4f} remains"
            )

        async def work(session: AsyncSession) -> AnalyzePhotoOutput:
            document = await DocumentRepository(session).get(study_id, document_id)
            if document is None or document.kind is not DocumentKind.PHOTO:
                raise validation_error(f"photo {document_id} not found in study {study_id}")
            rooms = RoomRepository(session)
            existing = await rooms.photo_room(document_id, input.revision)
            if existing is not None:
                # Written for this revision already (carried over, or an earlier execution).
                count = await rooms.count_components(study_id, input.revision, document_id)
                return AnalyzePhotoOutput(
                    document_id=str(document_id), room_id=str(existing.room_id), components=count
                )
            name, room_type = room_for_filename(document.filename)
            room, _ = await rooms.get_or_create(
                study_id=study_id,
                revision=input.revision,
                name=name,
                room_type=room_type,
                source=SOURCE,
            )
            if simulate is not None and simulate.sleep_seconds > 0:
                await sleep_heartbeating(simulate.sleep_seconds)
            await rooms.assign_photo(
                study_id=study_id, document_id=document_id, room_id=room.id, revision=input.revision
            )
            labels = components_for(room_type)
            for label in labels:
                heartbeat(label)
                await rooms.add_component(
                    study_id=study_id,
                    revision=input.revision,
                    document_id=document_id,
                    room_id=room.id,
                    label=label,
                    material=None,
                    attachment=None,
                    confidence=0.5,
                    raw={"source": SOURCE},
                )
            return AnalyzePhotoOutput(
                document_id=str(document_id),
                room_id=str(room.id),
                components=len(labels),
                cost_usd=PHOTO_COST_USD,
            )

        return await run_once(
            self._deps,
            name="analyze_photo",
            key=input.idempotency_key,
            output_type=AnalyzePhotoOutput,
            work=work,
        )
