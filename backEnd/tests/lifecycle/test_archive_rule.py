"""Archiving a study whose workflow runs, on the real workflow (EVAL_REPORT 62).

The rule (docs/api/STUDY_LIBRARY.md): a study is archived only while nothing runs for it.
``DELETE /v1/studies/{id}`` asks Temporal whether the execution is running and refuses with
409 ``workflow-running`` (BASIS-STUDY-035) while it is: in progress, waiting at a review
gate, or holding a failed stage for a retry. The owner cancels first; once the execution has
closed, the archive proceeds and is recorded once. The API here is the real one over the test
server's client (``TemporalWorkflowService``), as ``tests/integration/api`` runs it.

Why refuse rather than cancel for the owner: every transition activity refuses an archived
study (``StaleStateError``), so an execution left running would fail at its next step and an
execution left waiting at a gate would wait for a decision the API refuses; and a cancel is
the explicit, recorded act (``study.cancel_requested`` with its reason) that a soft delete
should not perform on the owner's behalf.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from apitest_support import BASE_URL, Api, FakeVerifier, api_settings, build_api_app
from lifecycle_support import Harness
from lifecycletest_support import SeededStudy
from sqlalchemy import select
from temporalio.client import (
    WorkflowExecutionStatus,
    WorkflowFailureError,
    WorkflowUpdateFailedError,
)
from temporalio.exceptions import CancelledError
from temporalio.service import RPCError

from basis.db.enums import StudyStatus
from basis.db.models import StudyEvent, User
from basis.db.repositories import StudyRepository
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.temporal.service import TemporalWorkflowService
from basis.workflow.study_workflow import workflow_id_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def api(harness: Harness, database_url: str) -> AsyncIterator[Api]:
    """The real API over the harness's Temporal client and database."""
    settings = api_settings(database_url, temporal_task_queue=harness.task_queue)
    service = TemporalWorkflowService(harness.client, task_queue=harness.task_queue)
    app = build_api_app(
        settings, harness.sessionmaker, harness.deps.storage, FakeVerifier(), service
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
        yield Api(http)


async def owner_uid(harness: Harness, study: SeededStudy) -> str:
    async with session_scope(harness.sessionmaker) as session:
        user = await session.get(User, study.owner_id)
        assert user is not None
        return user.firebase_uid


async def events(harness: Harness, study_id: uuid.UUID, event_type: str) -> list[StudyEvent]:
    async with session_scope(harness.sessionmaker) as session:
        rows = await session.scalars(
            select(StudyEvent)
            .where(StudyEvent.study_id == study_id, StudyEvent.event_type == event_type)
            .order_by(StudyEvent.seq)
        )
        return list(rows.all())


def problem(response: httpx.Response, status: int, suffix: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    assert body["type"].endswith(f"/{suffix}"), body
    return body


async def test_archive_waits_for_the_execution_to_end_and_a_decision_after_it_is_refused(
    harness: Harness, api: Api
) -> None:
    study = await harness.seed(photos=["kitchen_1.jpg"])
    uid = await owner_uid(harness, study)
    sid = str(study.study_id)
    path = f"/v1/studies/{sid}"
    # As ``POST .../start`` does before it starts the execution: the row moves to
    # ``extracting`` and records its workflow id (the harness alone starts the execution
    # with the row untouched, and a study with no workflow id is a draft to the archive).
    async with session_scope(harness.sessionmaker) as session:
        await StudyRepository(session).mark_started(
            study.study_id, workflow_id_for(str(study.study_id)), 1, actor_id=study.owner_id
        )
    async with harness.worker():
        started = await harness.start(study)
        handle = started.handle
        # In progress (the first stage), then waiting at the appraisal gate: refused both
        # times, nothing changes, nothing is recorded.
        body = problem(await api.delete(uid, path), 409, "workflow-running")
        assert body["code"] == E.STUDY_ARCHIVE_WHILE_RUNNING
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        body = problem(await api.delete(uid, path), 409, "workflow-running")
        assert "cancel" in body["detail"]
        row = await harness.study_row(study.study_id)
        assert (row.status, row.current_stage.value) == (
            StudyStatus.WAITING_REVIEW,
            "appraisal_review",
        )
        assert await events(harness, study.study_id, "study.archived") == []
        assert (await handle.describe()).status is WorkflowExecutionStatus.RUNNING

        # The owner cancels; the execution closes; the archive proceeds, recorded once.
        cancelled = await api.post(uid, f"{path}/cancel", {"reason": "client withdrew"})
        assert cancelled.status_code == 202, cancelled.text
        with pytest.raises(WorkflowFailureError) as caught:
            await harness.result(handle)
        assert isinstance(caught.value.cause, CancelledError)
        history = await handle.fetch_history()
        [request] = [
            e.workflow_execution_cancel_requested_event_attributes
            for e in history.events
            if e.HasField("workflow_execution_cancel_requested_event_attributes")
        ]
        assert request.cause == "client withdrew"
        archived = await api.delete(uid, path)
        assert archived.status_code == 200 and archived.json()["status"] == "archived"
        assert (await api.delete(uid, path)).status_code == 200
        assert len(await events(harness, study.study_id, "study.archived")) == 1

        # A decision after the archive: refused by the API (the study is archived) and by
        # the workflow (closed), so nothing can move the study.
        decision = {"stage": "appraisal_review", "revision": 1, "decision": "approve"}
        refused = await api.post(uid, f"{path}/decisions", decision)
        assert refused.status_code == 409, refused.text
        with pytest.raises((WorkflowUpdateFailedError, RPCError)):
            await harness.decide(handle, harness.decision("appraisal_review", 1))
    [run] = await harness.runs(study.study_id)
    assert run.outcome == "cancelled"
    # Unarchived, the study reads as it was (waiting at the gate) with a closed execution:
    # ``POST .../cancel`` is a Temporal cancellation request, so it closed ``CANCELED``
    # (EVAL_REPORT 76; the signal it sent before closed it ``COMPLETED``, which restart
    # refused), and ``POST .../restart`` runs the study again on revision 2.
    restored = await api.post(uid, f"{path}/unarchive")
    assert restored.status_code == 200 and restored.json()["status"] == "waiting_review"
    workflow = (await api.get(uid, f"{path}/workflow")).json()
    assert workflow["execution_status"] == "CANCELED"
    assert [r["outcome"] for r in workflow["runs"]] == ["cancelled"]
    restarted = await api.post(uid, f"{path}/restart")
    assert restarted.status_code == 202, restarted.text
    assert (restarted.json()["from_revision"], restarted.json()["revision"]) == (1, 2)
    rerun = harness.client.get_workflow_handle(restarted.json()["workflow_id"])
    assert (await rerun.describe()).status is WorkflowExecutionStatus.RUNNING
