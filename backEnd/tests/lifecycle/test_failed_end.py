"""An error no stage holds for a retry ends the execution failed, and the study says so
(EVAL_REPORT 59; patch ``failed-end-records-failure-v1``).

``stage-failure-retry-v1`` holds the extraction, the assets and the export for a retry. An
activity outside those (here the photo listing, failing permanently) propagates out of the
lifecycle and the execution ends ``FAILED``. Before this patch the run ended ``failed`` while
the study row stayed ``processing`` at ``extracting`` with no reason: a client polling
``GET /v1/studies/{id}`` saw a study in progress forever. Now the workflow records the
failure on the study first (``status = failed``, ``failure_reason``, ``failure_code``, a
``stage.failed`` event), then the run's end. ``POST .../retry`` cannot reach the closed
execution (409 ``workflow-closed``); ``POST .../restart`` runs the study again
(docs/api/WORKFLOW.md).
"""

from __future__ import annotations

import pytest
from lifecycle_support import Harness
from sqlalchemy import select
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer

from basis.db.enums import StudyStatus
from basis.db.models import StudyEvent
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.temporal.service import TemporalWorkflowService, WorkflowNotRunningError
from basis.workflow.contracts import ListPhotosInput, ListPhotosOutput
from basis.workflow.study_workflow import StudyWorkflow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

BROKEN = "the photo listing is not available"


@activity.defn(name="list_photos")
async def broken_listing(input: ListPhotosInput) -> ListPhotosOutput:
    raise ApplicationError(f"list_photos: {BROKEN}", type="SimulatedFailure", non_retryable=True)


async def test_an_unheld_failure_ends_the_execution_and_marks_the_study_failed(
    harness: Harness,
) -> None:
    async with harness.worker(list_photos=broken_listing):
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        with pytest.raises(WorkflowFailureError):
            await harness.result(handle)
        assert (await handle.describe()).status is WorkflowExecutionStatus.FAILED
        # The study row: failed, at the stage it was, with the reason and its code.
        row = await harness.study_row(sid)
        assert (row.status, row.current_stage.value) == (StudyStatus.FAILED, "extracting")
        assert row.failure_reason is not None
        assert row.failure_reason.startswith("list_photos: SimulatedFailure:")
        assert BROKEN in row.failure_reason and E.WF_SIMULATED_FAILURE in row.failure_reason
        assert row.failure_code == E.WF_SIMULATED_FAILURE
        # The closed workflow's final state answers the status query the same way.
        status = await harness.status(handle)
        assert (status.failed_stage, status.failure_code) == ("extracting", E.WF_SIMULATED_FAILURE)
        assert status.failure == row.failure_reason
        # A retry cannot reach a closed execution: the API answers 409 workflow-closed.
        service = TemporalWorkflowService(harness.client, task_queue=harness.task_queue)
        with pytest.raises(WorkflowNotRunningError, match="failed"):
            await service.retry(handle.id)
    [run] = await harness.runs(sid)
    assert run.outcome == "failed" and run.ended_at is not None
    sequence = await harness.activity_sequence(handle)
    assert sequence[-3:] == ["list_photos", "record_failure", "record_run_end"]
    # The timeline: the failure (marked on the row), then the run's end.
    async with session_scope(harness.sessionmaker) as session:
        rows = await session.scalars(
            select(StudyEvent)
            .where(
                StudyEvent.study_id == sid,
                StudyEvent.event_type.in_(["stage.failed", "run.ended"]),
            )
            .order_by(StudyEvent.seq)
        )
        timeline = [(e.event_type, e.code, e.detail) for e in rows.all()]
    assert [(t, c) for t, c, _ in timeline] == [
        ("stage.failed", E.WF_SIMULATED_FAILURE),
        ("run.ended", None),
    ]
    assert timeline[0][2]["marked"] is True and timeline[1][2] == {"outcome": "failed"}
    history = await handle.fetch_history()
    replayed = await Replayer(
        workflows=[StudyWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)
    assert replayed.replay_failure is None
