"""The real ordering: the API moves the study row first, the relay delivers afterwards.

``POST /start`` sets ``current_stage = extracting`` before the workflow runs, and each
decision moves the row through ``DECISION_TRANSITIONS`` (with ``revision + 1`` on reject)
in the same transaction that records the review and the outbox row. The relay delivers
the ``decide`` update (id ``decision-<review_id>``) only when the workflow's ``status``
reports the decision's stage and revision. This test drives the workflow exactly that way,
using the S1 ``ReviewRepository.record_decision`` as the API, and checks that every
``transition`` converges (already at the target is success) and the study completes.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lifecycle_support import Harness
from sqlalchemy import select, update
from temporalio.client import WorkflowHandle

from basis.db.enums import Decision as DecisionKind
from basis.db.enums import Stage, StudyStatus
from basis.db.models import PhotoRoom, Review, Room, Study
from basis.db.repositories.review import CorrectionInput as ApiCorrection
from basis.db.repositories.review import ReviewRepository
from basis.db.session import session_scope
from basis.workflow.contracts import CorrectionInput, StudyStatusView

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

STAGES = {stage.value for stage in Stage}


async def api_start(harness: Harness, study_id: uuid.UUID) -> None:
    """What ``POST /v1/studies/{id}/start`` does to the row before starting the workflow."""
    async with session_scope(harness.sessionmaker) as session:
        await session.execute(
            update(Study)
            .where(Study.id == study_id)
            .values(current_stage=Stage.EXTRACTING, status=StudyStatus.PROCESSING)
        )


async def api_decide(
    harness: Harness,
    study_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    stage: str,
    revision: int,
    decision: str,
    corrections: tuple[CorrectionInput, ...] = (),
) -> str:
    """The decision endpoint's transaction: row moved, review and outbox row written."""
    async with session_scope(harness.sessionmaker) as session:
        record = await ReviewRepository(session).record_decision(
            study_id=study_id,
            stage=Stage(stage),
            revision=revision,
            decision=DecisionKind(decision),
            reviewer_id=reviewer_id,
            idempotency_key=f"{stage}-{revision}",
            corrections=[
                ApiCorrection(
                    entity_type=c.entity_type, entity_id=uuid.UUID(c.entity_id), after=c.after
                )
                for c in corrections
            ],
        )
        return str(record.review.id)


async def relay_deliver(
    harness: Harness,
    handle: WorkflowHandle[Any, Any],
    review_id: str,
    stage: str,
    revision: int,
    decision: str,
    corrections: tuple[CorrectionInput, ...] = (),
) -> None:
    """The relay: wait until ``status`` shows the decision's stage and revision, then send."""
    status = await harness.wait_for(
        handle,
        lambda s: s.stage == stage and s.revision == revision,
        f"status at {stage} revision {revision}",
    )
    assert status.stage in STAGES
    command = harness.decision(
        stage,  # type: ignore[arg-type]
        revision,
        decision,
        corrections=corrections,
        review_id=review_id,
    )
    ack = await harness.decide(handle, command)
    assert ack.accepted and ack.reason is None and ack.applied_revision == revision


async def row(harness: Harness, study_id: uuid.UUID) -> tuple[Stage, int, StudyStatus]:
    study = await harness.study_row(study_id)
    return study.current_stage, study.revision, study.status


async def rooms_at(harness: Harness, study_id: uuid.UUID, revision: int) -> dict[str, Room]:
    async with session_scope(harness.sessionmaker) as session:
        found = await session.scalars(
            select(Room).where(Room.study_id == study_id, Room.revision == revision)
        )
        return {room.name: room for room in found.all()}


async def test_workflow_follows_a_row_the_api_already_moved(harness: Harness) -> None:
    """Patch components-gate-v1: the rooms review (a move, then an approval that sends the
    row to ``components_processing``), the components review (a flagged photo, then an
    approval), the assets review; every transition converges on the row the API moved."""
    study = await harness.seed()
    sid, reviewer = study.study_id, study.owner_id
    await api_start(harness, sid)
    async with harness.worker():
        started = await harness.start(study)
        handle = started.handle

        await harness.wait_awaiting(handle, "appraisal_review", 1)
        review = await api_decide(harness, sid, reviewer, "appraisal_review", 1, "approve")
        assert await row(harness, sid) == (Stage.ROOMS_REVIEW, 1, StudyStatus.PROCESSING)
        await relay_deliver(harness, handle, review, "appraisal_review", 1, "approve")

        # Rooms review: move the second kitchen photo to the bathroom (extracting r2).
        await harness.wait_awaiting(handle, "rooms_review", 1)
        assert await row(harness, sid) == (Stage.ROOMS_REVIEW, 1, StudyStatus.WAITING_REVIEW)
        assert (await harness.study_row(sid)).components_gate is True
        bathroom = (await rooms_at(harness, sid, 1))["Bathroom"]
        moved = study.photo_ids[1]
        move = (
            CorrectionInput(
                entity_type="photo_room", entity_id=str(moved), after={"room_id": str(bathroom.id)}
            ),
        )
        review = await api_decide(harness, sid, reviewer, "rooms_review", 1, "reject", move)
        assert await row(harness, sid) == (Stage.EXTRACTING, 2, StudyStatus.PROCESSING)
        await relay_deliver(harness, handle, review, "rooms_review", 1, "reject", move)
        await harness.wait_awaiting(handle, "rooms_review", 2)
        assert await row(harness, sid) == (Stage.ROOMS_REVIEW, 2, StudyStatus.WAITING_REVIEW)

        # Approve the grouping: the API moves the row to components_processing; the
        # workflow runs the room step and opens the components review on it.
        review = await api_decide(harness, sid, reviewer, "rooms_review", 2, "approve")
        assert await row(harness, sid) == (
            Stage.COMPONENTS_PROCESSING,
            2,
            StudyStatus.PROCESSING,
        )
        await relay_deliver(harness, handle, review, "rooms_review", 2, "approve")
        await harness.wait_awaiting(handle, "components_review", 2)
        assert await row(harness, sid) == (Stage.COMPONENTS_REVIEW, 2, StudyStatus.WAITING_REVIEW)

        # Components review: flag the moved photo (components_processing r3), then approve.
        flag = (CorrectionInput(entity_type="photo", entity_id=str(moved), after={}),)
        review = await api_decide(harness, sid, reviewer, "components_review", 2, "reject", flag)
        assert await row(harness, sid) == (Stage.COMPONENTS_PROCESSING, 3, StudyStatus.PROCESSING)
        await relay_deliver(harness, handle, review, "components_review", 2, "reject", flag)
        await harness.wait_awaiting(handle, "components_review", 3)
        review = await api_decide(harness, sid, reviewer, "components_review", 3, "approve")
        assert await row(harness, sid) == (Stage.ASSETS_PROCESSING, 3, StudyStatus.PROCESSING)
        await relay_deliver(harness, handle, review, "components_review", 3, "approve")

        await harness.wait_awaiting(handle, "assets_review", 3)
        review = await api_decide(harness, sid, reviewer, "assets_review", 3, "approve")
        assert await row(harness, sid) == (Stage.EXPORTING, 3, StudyStatus.PROCESSING)
        await relay_deliver(harness, handle, review, "assets_review", 3, "approve")
        result = await harness.result(handle)

    assert (result.outcome, result.revision) == ("completed", 3)
    assert await row(harness, sid) == (Stage.COMPLETED, 3, StudyStatus.COMPLETED)
    history = await handle.fetch_history()
    failures = [e for e in history.events if e.HasField("activity_task_failed_event_attributes")]
    assert failures == []  # every converging transition succeeded on its first attempt
    # The re-analyzed photo stayed in the room the rooms review approved.
    async with session_scope(harness.sessionmaker) as session:
        placed = await session.get(PhotoRoom, (moved, 3))
        assert placed is not None
        reviews = (await session.scalars(select(Review).where(Review.study_id == sid))).all()
    assert placed.room_id == (await rooms_at(harness, sid, 3))["Bathroom"].id
    assert sorted((r.stage.value, r.revision, r.decision.value) for r in reviews) == [
        ("appraisal_review", 1, "approve"),
        ("assets_review", 3, "approve"),
        ("components_review", 2, "reject"),
        ("components_review", 3, "approve"),
        ("rooms_review", 1, "reject"),
        ("rooms_review", 2, "approve"),
    ]


async def test_workflow_follows_a_row_the_api_already_moved_single_rooms_gate(
    harness: Harness,
) -> None:
    """An execution without the components gate (``components_gate=False``, as one recorded
    before the patch): the single rooms review re-analyzes a flagged photo and an approval
    goes to the assets."""
    study = await harness.seed()
    sid, reviewer = study.study_id, study.owner_id
    await api_start(harness, sid)
    async with harness.worker():
        started = await harness.start(study, components_gate=False)
        handle = started.handle

        # Gate 1: approve. The workflow put the row at appraisal_review; the API moves it on.
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        review = await api_decide(harness, sid, reviewer, "appraisal_review", 1, "approve")
        # v0.9: the rooms gate is not open until the workflow opens it (the photo stage
        # may still be running behind the appraisal review), so the row is processing.
        assert await row(harness, sid) == (Stage.ROOMS_REVIEW, 1, StudyStatus.PROCESSING)
        await relay_deliver(harness, handle, review, "appraisal_review", 1, "approve")

        # Gate 2: reject with a flagged photo. The API moves the row to extracting r2.
        await harness.wait_awaiting(handle, "rooms_review", 1)
        assert await row(harness, sid) == (Stage.ROOMS_REVIEW, 1, StudyStatus.WAITING_REVIEW)
        flag = (CorrectionInput(entity_type="photo", entity_id=str(study.photo_ids[0]), after={}),)
        review = await api_decide(harness, sid, reviewer, "rooms_review", 1, "reject", flag)
        assert await row(harness, sid) == (Stage.EXTRACTING, 2, StudyStatus.PROCESSING)
        await relay_deliver(harness, handle, review, "rooms_review", 1, "reject", flag)

        # The workflow re-analyzes the flagged photo, then asks rooms_review r2 again; the
        # relay holding the next decision waits until status says exactly that.
        status: StudyStatusView = await harness.wait_awaiting(handle, "rooms_review", 2)
        assert (status.stage, status.revision) == ("rooms_review", 2)
        assert await row(harness, sid) == (Stage.ROOMS_REVIEW, 2, StudyStatus.WAITING_REVIEW)
        review = await api_decide(harness, sid, reviewer, "rooms_review", 2, "approve")
        await relay_deliver(harness, handle, review, "rooms_review", 2, "approve")

        # Gate 3: approve; the API moves the row to exporting before the workflow does.
        await harness.wait_awaiting(handle, "assets_review", 2)
        review = await api_decide(harness, sid, reviewer, "assets_review", 2, "approve")
        assert await row(harness, sid) == (Stage.EXPORTING, 2, StudyStatus.PROCESSING)
        await relay_deliver(harness, handle, review, "assets_review", 2, "approve")
        result = await harness.result(handle)

    assert (result.outcome, result.revision) == ("completed", 2)
    assert await row(harness, sid) == (Stage.COMPLETED, 2, StudyStatus.COMPLETED)
    history = await handle.fetch_history()
    failures = [e for e in history.events if e.HasField("activity_task_failed_event_attributes")]
    assert failures == []  # every converging transition succeeded on its first attempt
    async with session_scope(harness.sessionmaker) as session:
        reviews = (await session.scalars(select(Review).where(Review.study_id == sid))).all()
    assert sorted((r.stage.value, r.revision, r.decision.value) for r in reviews) == [
        ("appraisal_review", 1, "approve"),
        ("assets_review", 2, "approve"),
        ("rooms_review", 1, "reject"),
        ("rooms_review", 2, "approve"),
    ]


async def test_status_reports_the_wait_point_exactly(harness: Harness) -> None:
    """The relay's gate: stage and revision equal the open gate, named like ``Stage``."""
    async with harness.worker():
        started = await harness.start()
        status = await harness.wait_awaiting(started.handle, "appraisal_review", 1)
        assert (status.stage, status.revision, status.awaiting) == (
            "appraisal_review",
            1,
            "appraisal_review",
        )
        assert Stage(status.stage) is Stage.APPRAISAL_REVIEW
        await harness.decide(started.handle, harness.decision("appraisal_review", 1, "reject"))
        status = await harness.wait_awaiting(started.handle, "appraisal_review", 2)
        assert (status.stage, status.revision) == ("appraisal_review", 2)
