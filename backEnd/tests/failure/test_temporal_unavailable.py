"""Temporal is unreachable: no decision is lost (BUILD_PLAN 6.9, S2_SPEC 9).

The API and the relay are pointed at a port where nothing listens (their lazy clients,
built exactly as in production with ``basis.temporal.client.connect(..., lazy=True)``).
While Temporal is down:

* the API still records decisions (202) and ``/health`` stays 200, reporting
  ``checks.temporal = fail``; ``start`` is 503 and converges once Temporal is back;
* the relay keeps the decision ``pending``, counting attempts with backoff.

Then a dev server is started **on that same port**, with a worker running the fake
workflow, and the same, never re-created clients deliver: the relay delivers the decision
and the API's start and health recover.

The dev server keeps its state in memory, so the workflow the decision is for is started
after the server comes up; with a persistent Temporal it would have existed throughout.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from apitest_support import (
    BASE_URL,
    COMPLETE_INTAKE,
    Api,
    FakeVerifier,
    Seed,
    api_settings,
    build_api_app,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from workflowtest_support import (
    applied_decisions,
    confirmed_document,
    fake_worker,
    free_port,
    local_temporal,
    make_due,
    outbox_rows,
    start_fake,
    wait_for_stage,
)

from basis.db.enums import OutboxStatus, Stage
from basis.db.models import Study
from basis.db.session import session_scope
from basis.relay.delivery import Relay, TemporalWorkflowGateway
from basis.storage import LocalStorage
from basis.temporal.client import connect
from basis.temporal.names import study_workflow_id
from basis.temporal.service import TemporalWorkflowService

pytestmark = pytest.mark.integration


@pytest.fixture
def port() -> int:
    return free_port()


@pytest.fixture
async def api_and_relay(
    database_url: str,
    sm: async_sessionmaker[AsyncSession],
    port: int,
    task_queue: str,
    tmp_path: Path,
) -> AsyncIterator[tuple[Api, Relay]]:
    settings = api_settings(
        database_url, temporal_address=f"127.0.0.1:{port}", temporal_task_queue=task_queue
    )
    api_client = await connect(settings, lazy=True)
    relay_client = await connect(settings, lazy=True)  # separate processes in production
    service = TemporalWorkflowService(api_client, task_queue=task_queue, call_timeout=2.0)
    app = build_api_app(settings, sm, LocalStorage(tmp_path), FakeVerifier(), service)
    relay = Relay(sm, TemporalWorkflowGateway(relay_client), delivery_timeout=5.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
        yield Api(http), relay


async def test_no_decision_is_lost_while_temporal_is_down(
    api_and_relay: tuple[Api, Relay],
    sm: async_sessionmaker[AsyncSession],
    port: int,
    task_queue: str,
) -> None:
    api, relay = api_and_relay
    seed = Seed(sm)
    # A study that was started before the outage and now waits for the appraisal review.
    sid = (await api.create_study("alice"))["id"]
    await seed.stage(sid, Stage.APPRAISAL_REVIEW)
    async with session_scope(sm) as session:
        study = await session.get(Study, uuid.UUID(sid))
        assert study is not None
        study.workflow_id = study_workflow_id(sid)
    # A second study that is ready to start.
    other = (await api.create_study("alice", intake=COMPLETE_INTAKE))["id"]
    await confirmed_document(sm, uuid.UUID(other))

    # ---- Temporal down -------------------------------------------------------------
    decision = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        {"stage": "appraisal_review", "revision": 1, "decision": "approve"},
    )
    assert decision.status_code == 202, decision.text
    review_id = decision.json()["review"]["id"]

    health = await api.client.get("/health")
    assert health.status_code == 200
    assert health.json()["checks"] == {
        "db": health.json()["checks"]["db"],
        "temporal": health.json()["checks"]["temporal"],
    }
    assert health.json()["checks"]["db"]["status"] == "ok"
    assert health.json()["checks"]["temporal"]["status"] == "fail", health.text

    start = await api.post("alice", f"/v1/studies/{other}/start", key="start-other")
    assert start.status_code == 503, start.text
    assert start.json()["type"].endswith("/service-unavailable")

    for attempt in (1, 2, 3):
        report = await relay.run_once()
        (row,) = await outbox_rows(sm)
        assert (report.claimed, report.retried) == (1, 1), (report, row.last_error)
        assert (row.status, row.attempts) == (OutboxStatus.PENDING, attempt)
        assert row.last_error is not None and "connect" in row.last_error.lower()
        await make_due(sm)

    # ---- Temporal back on the same address ------------------------------------------
    async with local_temporal(port=port) as env, fake_worker(env.client, task_queue):
        study_row = await _load(sm, sid)
        handle = await start_fake(env.client, study_row, task_queue)
        await wait_for_stage(handle, "appraisal_review")

        report = await relay.run_once()

        (row,) = await outbox_rows(sm)
        assert (report.claimed, report.delivered) == (1, 1), (report, row.last_error)
        assert row.status is OutboxStatus.DELIVERED and row.attempts == 3
        assert row.last_error is None  # cleared by the successful delivery
        assert (await wait_for_stage(handle, "rooms_review")).decisions_applied == (review_id,)
        assert [d.review_id for d in await applied_decisions(handle)] == [review_id]

        # The API's lazy client recovered too.
        health = await api.client.get("/health")
        assert health.json()["checks"]["temporal"]["status"] == "ok", health.text
        retried = await api.post("alice", f"/v1/studies/{other}/start", key="start-other")
        assert retried.status_code == 202, retried.text
        other_handle = env.client.get_workflow_handle(study_workflow_id(other))
        assert (await other_handle.describe()).run_id == retried.json()["run_id"]


async def _load(sm: async_sessionmaker[AsyncSession], study_id: str) -> Study:
    async with sm() as session:
        study = await session.get(Study, uuid.UUID(study_id))
    assert study is not None
    return study
