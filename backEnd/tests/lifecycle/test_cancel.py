"""Cancelling a study: in-flight activities are cancelled, the run records ``cancelled``.

S2_SPEC section 3: ``cancel_study(reason)`` cancels in-flight activities and records the
outcome ``cancelled``. A Temporal-level cancellation (``handle.cancel()``) is handled the
same way and ends the execution as cancelled. A cancelled activity leaves no rows.
"""

from __future__ import annotations

import asyncio

import pytest
from lifecycle_support import Harness
from sqlalchemy import func, select
from temporalio import activity
from temporalio.client import (
    WorkflowExecutionStatus,
    WorkflowFailureError,
    WorkflowUpdateFailedError,
)
from temporalio.exceptions import CancelledError
from temporalio.service import RPCError

from basis.db.models import PhotoRoom
from basis.db.repositories.activity_result import COMPLETED
from basis.db.session import session_scope
from basis.workflow.contracts import ListPhotosInput, ListPhotosOutput, SimulateFault
from basis.workflow.study_workflow import CANCEL_SIGNAL, StudyWorkflow, workflow_id_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_cancel_during_an_activity_that_ignores_it_stops_the_lifecycle(
    harness: Harness,
) -> None:
    """An activity that completes despite the cancel request must not let the study go on."""
    listing = asyncio.Event()

    @activity.defn(name="list_photos")
    async def slow_listing(input: ListPhotosInput) -> ListPhotosOutput:
        listing.set()
        await asyncio.sleep(2)  # no heartbeat: the cancellation never reaches it
        return ListPhotosOutput(document_ids=())

    async with harness.worker(list_photos=slow_listing):
        started = await harness.start()
        await asyncio.wait_for(listing.wait(), 10)
        await started.handle.signal(CANCEL_SIGNAL, "stop")
        result = await harness.result(started.handle)
    assert result.outcome == "cancelled"
    sequence = await harness.activity_sequence(started.handle)
    assert "ingest_appraisal" not in sequence
    assert sequence[-1] == "record_run_end"


async def test_cancel_while_waiting_for_review(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        handle = started.handle
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        await handle.signal(StudyWorkflow.cancel_study, "client withdrew")
        result = await harness.result(handle)

    assert (result.outcome, result.cancel_reason) == ("cancelled", "client withdrew")
    assert result.export_uri is None
    [run] = await harness.runs(started.study_id)
    assert run.outcome == "cancelled" and run.ended_at is not None
    assert (await handle.describe()).status is WorkflowExecutionStatus.COMPLETED


async def test_cancel_stops_an_in_flight_photo_activity(harness: Harness) -> None:
    study = await harness.seed(photos=["kitchen_1.jpg"])
    slow = str(study.photo_ids[0])
    loop = asyncio.get_running_loop()
    async with harness.worker():
        started = await harness.start(
            study, simulate_photos={slow: SimulateFault(sleep_seconds=30)}
        )
        handle = started.handle
        await harness.wait_for(handle, lambda s: s.pending_photos == 1, "photo in flight")
        await asyncio.sleep(1.5)  # inside the activity's transaction, after the room insert
        began = loop.time()
        await handle.signal(CANCEL_SIGNAL, "stop")
        result = await harness.result(handle)
        # Leaving the block shuts the worker down; it would wait up to the 8 s grace for
        # an activity that ignored the cancellation.
    elapsed = loop.time() - began

    assert result.outcome == "cancelled"
    assert elapsed < 7, f"the activity did not stop promptly ({elapsed:.1f} s)"
    history = await handle.fetch_history()
    assert any(
        e.HasField("activity_task_cancel_requested_event_attributes") for e in history.events
    )
    async with session_scope(harness.sessionmaker) as session:
        assigned = await session.scalar(
            select(func.count()).select_from(PhotoRoom).where(PhotoRoom.study_id == study.study_id)
        )
    assert assigned == 0  # the activity's transaction rolled back
    results = await harness.activity_results(workflow_id_for(str(study.study_id)))
    [photo] = [r for r in results if r.activity == "analyze_photo"]
    assert photo.status != COMPLETED
    [run] = await harness.runs(study.study_id)
    assert run.outcome == "cancelled"


async def test_temporal_cancellation_records_the_outcome(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        handle = started.handle
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as caught:
            await harness.result(handle)
    assert isinstance(caught.value.cause, CancelledError)
    assert (await handle.describe()).status is WorkflowExecutionStatus.CANCELED
    [run] = await harness.runs(started.study_id)
    assert run.outcome == "cancelled"


async def test_decisions_after_cancel_are_rejected(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start(await harness.seed(photos=["kitchen_1.jpg"]))
        handle = started.handle
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        await handle.signal(CANCEL_SIGNAL, "stop")
        with pytest.raises((WorkflowUpdateFailedError, RPCError)) as caught:
            await harness.decide(handle, harness.decision("appraisal_review", 1))
        if isinstance(caught.value, WorkflowUpdateFailedError):
            # The validator ran after the signal: rejected because the study is cancelled.
            assert "cancelled" in str(caught.value.cause)
        else:
            # The workflow had already finished: the server refuses the update.
            assert "completed" in str(caught.value).lower()
        result = await harness.result(handle)
    assert (result.outcome, result.cancel_reason) == ("cancelled", "stop")
