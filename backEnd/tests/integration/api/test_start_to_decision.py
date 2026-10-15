"""Start -> decision -> relay -> workflow, end to end on a real Temporal dev server.

The real API (``TemporalWorkflowService`` on the dev server's client), the real outbox
relay, PostgreSQL, and ``fake_study_workflow.FakeStudyWorkflow`` on a worker. The one
stand-in: after the workflow reports ``appraisal_review``, the test moves the study row
there itself, which the real workflow's ``transition`` activity does (S2_SPEC section 3).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.client import Client
from workflowtest_support import applied_decisions, confirmed_document, wait_for_stage

from basis.db.enums import Stage
from basis.db.models import Study
from basis.relay.delivery import Relay, TemporalWorkflowGateway
from basis.storage import LocalStorage
from basis.temporal.service import TemporalWorkflowService

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(
    database_url: str,
    sm: async_sessionmaker[AsyncSession],
    fake_workflows: Client,
    task_queue: str,
    tmp_path: Path,
) -> AsyncIterator[Api]:
    settings = api_settings(database_url, temporal_task_queue=task_queue)
    service = TemporalWorkflowService(fake_workflows, task_queue=settings.temporal_task_queue)
    app = build_api_app(settings, sm, LocalStorage(tmp_path), FakeVerifier(), service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
        yield Api(http)


async def test_start_decide_relay_cancel(
    api: Api, sm: async_sessionmaker[AsyncSession], fake_workflows: Client
) -> None:
    sid = (await api.create_study("alice"))["id"]
    await confirmed_document(sm, uuid.UUID(sid))

    started = await api.post("alice", f"/v1/studies/{sid}/start")
    assert started.status_code == 202, started.text
    workflow_id, run_id = started.json()["workflow_id"], started.json()["run_id"]
    assert workflow_id == f"study-{sid}"
    handle = fake_workflows.get_workflow_handle(workflow_id)
    await wait_for_stage(handle, "appraisal_review")
    assert (await handle.describe()).run_id == run_id

    status = (await api.get("alice", f"/v1/studies/{sid}/workflow")).json()
    assert (status["run_id"], status["execution_status"]) == (run_id, "RUNNING")
    assert status["status"]["stage"] == "appraisal_review"

    # Starting again returns the running execution (USE_EXISTING).
    again = await api.post("alice", f"/v1/studies/{sid}/start")
    assert again.status_code == 202 and again.json() == started.json()

    # The real workflow's transition activity would move the row; the fake does not.
    await Seed(sm).stage(sid, Stage.APPRAISAL_REVIEW)
    # A correction must name a row of the decided revision (the API checks it).
    entity = (await Seed(sm).outputs(sid, revision=1))["extraction_field"]
    decision = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        {
            "stage": "appraisal_review",
            "revision": 1,
            "decision": "approve",
            "corrections": [
                {
                    "entity_type": "extraction_field",
                    "entity_id": entity,
                    "after": {"year_built": 1998},
                }
            ],
        },
    )
    assert decision.status_code == 202, decision.text
    review_id = decision.json()["review"]["id"]
    # Accepted by the API, not yet seen by the workflow.
    assert (await wait_for_stage(handle, "appraisal_review")).decisions_applied == ()

    report = await Relay(sm, TemporalWorkflowGateway(fake_workflows)).run_once()

    assert (report.claimed, report.delivered) == (1, 1)
    status = (await api.get("alice", f"/v1/studies/{sid}/workflow")).json()
    assert status["status"]["stage"] == "rooms_review"
    assert status["status"]["decisions_applied"] == [review_id]
    (applied,) = await applied_decisions(handle)
    assert applied.review_id == review_id
    assert applied.corrections[0].entity_id == entity
    assert applied.corrections[0].after == {"year_built": 1998}

    cancelled = await api.post("alice", f"/v1/studies/{sid}/cancel", {"reason": "withdrawn"})
    assert cancelled.status_code == 202, cancelled.text
    assert await handle.result() == "cancelled: withdrawn"
    status = (await api.get("alice", f"/v1/studies/{sid}/workflow")).json()
    assert status["execution_status"] == "COMPLETED"

    closed = await api.post("alice", f"/v1/studies/{sid}/cancel")
    assert closed.status_code == 409 and closed.json()["type"].endswith("/workflow-closed")
    # Past its first stage, start only describes the (closed) execution.
    restarted = await api.post("alice", f"/v1/studies/{sid}/start")
    assert restarted.status_code == 202 and restarted.json() == started.json()


async def test_a_closed_workflow_is_never_started_again(
    api: Api, sm: async_sessionmaker[AsyncSession], fake_workflows: Client
) -> None:
    """Cancelled while still extracting: start hits REJECT_DUPLICATE and answers with the
    closed execution instead of running the study again."""
    sid = (await api.create_study("alice"))["id"]
    await confirmed_document(sm, uuid.UUID(sid))
    started = await api.post("alice", f"/v1/studies/{sid}/start")
    assert started.status_code == 202, started.text
    handle = fake_workflows.get_workflow_handle(started.json()["workflow_id"])
    assert (await api.post("alice", f"/v1/studies/{sid}/cancel")).status_code == 202
    assert await handle.result() == "cancelled: cancelled by owner"

    again = await api.post("alice", f"/v1/studies/{sid}/start")

    assert again.status_code == 202, again.text
    assert again.json() == started.json()
    assert (await handle.describe()).run_id == started.json()["run_id"]


async def test_health_checks_temporal(api: Api) -> None:
    response = await api.client.get("/health")
    assert response.status_code == 200
    assert response.json()["checks"]["temporal"]["status"] == "ok"


async def test_status_of_a_workflow_temporal_does_not_know_is_404(
    api: Api, sm: async_sessionmaker[AsyncSession]
) -> None:
    sid = (await api.create_study("alice"))["id"]
    await Seed(sm).stage(sid, Stage.EXTRACTING)
    async with sm() as session, session.begin():
        study = await session.get(Study, uuid.UUID(sid))
        assert study is not None
        study.workflow_id = f"study-{sid}"

    response = await api.get("alice", f"/v1/studies/{sid}/workflow")
    assert response.status_code == 404
    cancel = await api.post("alice", f"/v1/studies/{sid}/cancel")
    assert cancel.status_code == 404
