"""Cancelling a study: in-flight activities are cancelled, the run records ``cancelled``.

S2_SPEC section 3: ``cancel_study(reason)`` cancels in-flight activities and records the
outcome ``cancelled``. A Temporal-level cancellation (``handle.cancel()``) is handled the
same way and ends the execution as cancelled. A cancelled activity leaves no rows.
"""

from __future__ import annotations

import asyncio

import pytest
from lifecycle_support import Harness
from sqlalchemy import func, select, update
from temporalio import activity
from temporalio.client import (
    WorkflowExecutionStatus,
    WorkflowFailureError,
    WorkflowUpdateFailedError,
)
from temporalio.exceptions import CancelledError
from temporalio.service import RPCError

from basis.db.models import PhotoRoom, Study
from basis.db.repositories.activity_result import COMPLETED
from basis.db.repositories.study import StudyRepository
from basis.db.session import session_scope
from basis.temporal.service import TemporalWorkflowService, WorkflowNotRunningError
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


# ------------------------------------------- POST .../cancel: a Temporal cancellation request
# What the API sends since EVAL_REPORT 76 (``TemporalWorkflowService.cancel``): the execution
# closes CANCELED, so ``restart`` can run the study again. The signal tests above stay: the
# workflow still handles the signal, and recorded histories carry it.


async def test_a_cancellation_request_stops_an_in_flight_photo_activity(
    harness: Harness,
) -> None:
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
        await TemporalWorkflowService(harness.client, task_queue=harness.task_queue).cancel(
            handle.id, "withdrawn"
        )
        with pytest.raises(WorkflowFailureError) as caught:
            await harness.result(handle)
    elapsed = loop.time() - began

    assert isinstance(caught.value.cause, CancelledError)
    assert (await handle.describe()).status is WorkflowExecutionStatus.CANCELED
    assert elapsed < 7, f"the activity did not stop promptly ({elapsed:.1f} s)"
    history = await handle.fetch_history()
    assert any(
        e.HasField("activity_task_cancel_requested_event_attributes") for e in history.events
    )
    [request] = [
        e.workflow_execution_cancel_requested_event_attributes
        for e in history.events
        if e.HasField("workflow_execution_cancel_requested_event_attributes")
    ]
    assert request.cause == "withdrawn"
    async with session_scope(harness.sessionmaker) as session:
        assigned = await session.scalar(
            select(func.count()).select_from(PhotoRoom).where(PhotoRoom.study_id == study.study_id)
        )
    assert assigned == 0  # the activity's transaction rolled back
    [run] = await harness.runs(study.study_id)
    assert run.outcome == "cancelled"


async def test_a_cancellation_request_during_an_activity_that_ignores_it_stops_the_lifecycle(
    harness: Harness,
) -> None:
    """The activity completes despite the request; the study must not go on, and a second
    request sent while the first is handled is accepted (the API's cancel pressed twice)."""
    listing = asyncio.Event()

    @activity.defn(name="list_photos")
    async def slow_listing(input: ListPhotosInput) -> ListPhotosOutput:
        listing.set()
        await asyncio.sleep(2)  # no heartbeat: the cancellation never reaches it
        return ListPhotosOutput(document_ids=())

    service = TemporalWorkflowService(harness.client, task_queue=harness.task_queue)
    async with harness.worker(list_photos=slow_listing):
        started = await harness.start()
        await asyncio.wait_for(listing.wait(), 10)
        await service.cancel(started.handle.id, "stop")
        await service.cancel(started.handle.id, "stop again")
        with pytest.raises(WorkflowFailureError):
            await harness.result(started.handle)
    assert (await started.handle.describe()).status is WorkflowExecutionStatus.CANCELED
    sequence = await harness.activity_sequence(started.handle)
    assert "ingest_appraisal" not in sequence
    assert sequence[-1] == "record_run_end"
    with pytest.raises(WorkflowNotRunningError):
        await service.cancel(started.handle.id, "after it closed")


async def test_decisions_and_retries_after_a_cancellation_request_are_rejected(
    harness: Harness,
) -> None:
    async with harness.worker():
        started = await harness.start()
        handle = started.handle
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        await handle.cancel(reason="stop")
        with pytest.raises((WorkflowUpdateFailedError, RPCError)) as caught:
            await harness.decide(handle, harness.decision("appraisal_review", 1))
        if isinstance(caught.value, WorkflowUpdateFailedError):
            assert "cancelled" in str(caught.value.cause)  # refused while the work stopped
        else:
            assert "completed" in str(caught.value).lower()  # the execution had closed
        with pytest.raises(WorkflowFailureError):
            await harness.result(handle)
    [run] = await harness.runs(started.study_id)
    assert run.outcome == "cancelled"


async def test_a_study_cancelled_through_the_api_path_restarts_and_completes(
    harness: Harness,
) -> None:
    """The bug of EVAL_REPORT 76, end to end on the real workflow: cancelled as
    ``POST .../cancel`` does, restarted as ``POST .../restart`` does (the row reset under its
    lock, then a new execution under the same id with ``ALLOW_DUPLICATE_FAILED_ONLY``), and the
    new execution runs every gate to a completed study on revision 2."""
    service = TemporalWorkflowService(harness.client, task_queue=harness.task_queue)
    study = await harness.seed()
    async with harness.worker():
        started = await harness.start(study)
        await harness.wait_awaiting(started.handle, "appraisal_review", 1)
        await service.cancel(started.handle.id, "withdrawn")
        with pytest.raises(WorkflowFailureError):
            await harness.result(started.handle)
        described = await service.describe(started.handle.id)
        assert described.status == "CANCELED"  # what restart accepts

        async with session_scope(harness.sessionmaker) as session:
            # The harness bypasses ``POST .../start``, which records the workflow id.
            await session.execute(
                update(Study)
                .where(Study.id == study.study_id)
                .values(workflow_id=started.handle.id)
            )
            record = await StudyRepository(session).restart(study.study_id, actor_id=study.owner_id)
        assert record.study.revision == 2
        ref = await service.restart_study(
            harness.workflow_input(study, input_revision=record.study.revision)
        )
        assert (ref.workflow_id, ref.run_id != started.handle.result_run_id) == (
            started.handle.id,
            True,
        )
        rerun = harness.client.get_workflow_handle_for(
            StudyWorkflow.run, ref.workflow_id, run_id=ref.run_id
        )
        await harness.wait_awaiting(rerun, "appraisal_review", 2)
        for stage in ("appraisal_review", "rooms_review", "components_review", "assets_review"):
            await harness.approve(rerun, stage, revision=2)  # type: ignore[arg-type]
        result = await harness.result(rerun)
    assert (result.outcome, result.revision) == ("completed", 2)
    runs = await harness.runs(study.study_id)
    assert sorted(r.outcome for r in runs) == ["cancelled", "completed"]
