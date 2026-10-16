"""``/v1/studies/{id}/start``, ``/workflow`` and ``/cancel`` with a fake Temporal (S2_SPEC 7).

The application is the real one with the real database; Temporal is
``workflowtest_support.FakeWorkflowService``. The same routes run against a real Temporal
dev server in ``tests/integration/api/test_start_to_decision.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from apitest_support import Api, FakeVerifier, Seed, api_settings, build_api_app
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from workflowtest_support import FakeWorkflowService

from basis.api.errors import PROBLEM_JSON
from basis.api.idempotency import REPLAYED_HEADER
from basis.db.enums import OrgRole, Stage, StudyStatus
from basis.db.models import Run, Study
from basis.db.session import session_scope
from basis.storage import LocalStorage
from basis.workflow.contracts import StudyStatusView

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n" + b"appraisal body " * 50


@pytest.fixture
def workflows() -> FakeWorkflowService:
    return FakeWorkflowService()


@pytest.fixture
def api_app(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    workflows: FakeWorkflowService,
) -> FastAPI:
    """Overrides tests/api/conftest.py: the same app, with the fake Temporal injected."""
    return build_api_app(api_settings(database_url), sessionmaker, storage, verifier, workflows)


async def upload(
    api: Api,
    storage: LocalStorage,
    uid: str,
    study_id: str,
    *,
    kind: str = "appraisal",
    confirm: bool = True,
    data: bytes = PDF,
) -> None:
    extra = {"content_type": "image/jpeg", "filename": "room.jpg"} if kind == "photo" else {}
    response = await api.request_upload(uid, study_id, data, kind=kind, **extra)
    assert response.status_code == 201, response.text
    ticket = response.json()
    storage.put_signed(ticket["upload"]["url"], data, ticket["upload"]["headers"])
    if confirm:
        path = f"/v1/studies/{study_id}/documents/{ticket['document']['id']}/complete"
        assert (await api.post(uid, path)).status_code == 200


async def ready_study(api: Api, storage: LocalStorage, uid: str = "alice") -> str:
    study = await api.create_study(uid)
    await upload(api, storage, uid, study["id"])
    return str(study["id"])


async def load(sm: async_sessionmaker[AsyncSession], study_id: str) -> Study:
    async with sm() as session:
        study = await session.get(Study, uuid.UUID(study_id))
    assert study is not None
    return study


def problem(response: Any, status: int, suffix: str | None = None) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body: dict[str, Any] = response.json()
    if suffix is not None:
        assert body["type"].endswith(f"/{suffix}"), body
    return body


# --------------------------------------------------------------------------- start


async def test_start_moves_the_study_and_starts_the_workflow(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    sid = await ready_study(api, storage)

    response = await api.post("alice", f"/v1/studies/{sid}/start")

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["workflow_id"] == f"study-{sid}"
    assert body["run_id"] == workflows.executions[f"study-{sid}"].run_id
    study = await load(sessionmaker, sid)
    assert (study.current_stage, study.status, study.workflow_id) == (
        Stage.EXTRACTING,
        StudyStatus.PROCESSING,
        f"study-{sid}",
    )
    (started,) = workflows.start_calls
    assert started.study_id == sid and started.org_id == str(study.org_id)
    assert started.input_revision == 1
    assert started.workflow_version == "test-sha"  # settings.git_sha
    assert started.budget_usd == 25.0  # settings.study_budget_usd
    assert (started.photo_concurrency, started.review_reminder_days) == (5, 7)
    assert started.review_deadline_days == 30


async def test_start_is_idempotent_with_any_key(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    sid = await ready_study(api, storage)
    first = await api.post("alice", f"/v1/studies/{sid}/start", key="k-1")
    replayed = await api.post("alice", f"/v1/studies/{sid}/start", key="k-1")
    again = await api.post("alice", f"/v1/studies/{sid}/start", key="k-2")

    assert first.status_code == replayed.status_code == again.status_code == 202
    assert first.json() == replayed.json() == again.json()
    assert replayed.headers[REPLAYED_HEADER] == "true"
    assert len(workflows.start_calls) == 2  # the replay never reached the handler
    assert len(workflows.executions) == 1
    assert (await load(sessionmaker, sid)).current_stage is Stage.EXTRACTING


async def test_concurrent_starts_converge(
    api: Api, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    sid = await ready_study(api, storage)
    responses = await asyncio.gather(
        *(api.post("alice", f"/v1/studies/{sid}/start") for _ in range(4))
    )
    assert [r.status_code for r in responses] == [202] * 4
    assert len({r.json()["run_id"] for r in responses}) == 1
    assert len(workflows.executions) == 1


@pytest.mark.parametrize(
    "documents",
    [[], [("photo", True)], [("other", True)], [("appraisal", False)]],
    ids=["none", "photo-only", "other-only", "appraisal-unconfirmed"],
)
async def test_start_requires_a_confirmed_appraisal(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
    documents: list[tuple[str, bool]],
) -> None:
    study = await api.create_study("alice")
    for n, (kind, confirm) in enumerate(documents):
        await upload(
            api, storage, "alice", study["id"], kind=kind, confirm=confirm, data=PDF + bytes([n])
        )

    body = problem(
        await api.post("alice", f"/v1/studies/{study['id']}/start"), 409, "appraisal-required"
    )

    assert "appraisal" in body["detail"]
    assert (await load(sessionmaker, study["id"])).current_stage is Stage.UPLOADING
    assert workflows.start_calls == []


async def test_start_roles(api: Api, seed: Seed, storage: LocalStorage) -> None:
    sid = await ready_study(api, storage)
    org = (await api.get("alice", f"/v1/studies/{sid}")).json()["org_id"]
    await seed.member(uuid.UUID(org), "victor", OrgRole.VIEWER)
    await seed.member(uuid.UUID(org), "erin", OrgRole.ENGINEER)

    problem(await api.post("mallory", f"/v1/studies/{sid}/start"), 404, "not-found")
    problem(await api.post("victor", f"/v1/studies/{sid}/start"), 403, "forbidden")
    assert (await api.post("erin", f"/v1/studies/{sid}/start")).status_code == 202


async def test_start_an_archived_study_is_409(
    api: Api, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    sid = await ready_study(api, storage)
    assert (await api.delete("alice", f"/v1/studies/{sid}")).status_code == 200
    problem(await api.post("alice", f"/v1/studies/{sid}/start"), 409, "study-archived")
    assert workflows.start_calls == []


async def test_start_a_study_past_uploading_without_a_workflow_is_stale(
    api: Api, seed: Seed, storage: LocalStorage
) -> None:
    sid = await ready_study(api, storage)
    await seed.stage(sid, Stage.APPRAISAL_REVIEW)
    problem(await api.post("alice", f"/v1/studies/{sid}/start"), 409, "stale-state")


async def test_temporal_down_at_start_then_a_retry_converges(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    sid = await ready_study(api, storage)
    workflows.unavailable = True

    body = problem(
        await api.post("alice", f"/v1/studies/{sid}/start", key="k-1"),
        503,
        "service-unavailable",
    )
    assert "unavailable" in body["detail"]
    # Committed before the Temporal call: the study is extracting, the workflow is not.
    study = await load(sessionmaker, sid)
    assert (study.current_stage, study.workflow_id) == (Stage.EXTRACTING, f"study-{sid}")
    assert workflows.executions == {}

    workflows.unavailable = False
    retried = await api.post("alice", f"/v1/studies/{sid}/start", key="k-1")  # 5xx not stored

    assert retried.status_code == 202, retried.text
    assert retried.json()["workflow_id"] == f"study-{sid}"
    assert REPLAYED_HEADER not in retried.headers
    assert (await load(sessionmaker, sid)).current_stage is Stage.EXTRACTING


async def test_start_past_the_first_stage_never_starts_again(
    api: Api, seed: Seed, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    sid = await ready_study(api, storage)
    first = await api.post("alice", f"/v1/studies/{sid}/start")
    await seed.stage(sid, Stage.ROOMS_REVIEW)  # the workflow has moved the study on

    again = await api.post("alice", f"/v1/studies/{sid}/start")
    assert again.status_code == 202 and again.json() == first.json()
    assert len(workflows.start_calls) == 1  # described, not started

    workflows.executions.clear()  # Temporal lost it (e.g. namespace retention)
    body = problem(await api.post("alice", f"/v1/studies/{sid}/start"), 409, "workflow-missing")
    assert "not started again" in body["detail"]
    assert len(workflows.start_calls) == 1


@pytest.mark.parametrize("stage", [Stage.EXTRACTING, Stage.ROOMS_REVIEW])
async def test_start_of_a_failed_workflow_is_409_not_202(
    api: Api, seed: Seed, storage: LocalStorage, workflows: FakeWorkflowService, stage: Stage
) -> None:
    """A study whose workflow failed is not running: start must not answer 202 with the
    dead run (the live run of EVAL_REPORT section 22 got 202 and stayed "processing")."""
    sid = await ready_study(api, storage)
    assert (await api.post("alice", f"/v1/studies/{sid}/start")).status_code == 202
    await seed.stage(sid, stage)
    workflows.executions[f"study-{sid}"].status = "FAILED"

    body = problem(await api.post("alice", f"/v1/studies/{sid}/start"), 409, "workflow-closed")
    assert "ended failed" in body["detail"]

    workflows.executions[f"study-{sid}"].status = "COMPLETED"  # a completed one stays 202
    assert (await api.post("alice", f"/v1/studies/{sid}/start")).status_code == 202


async def test_unknown_study_is_404(api: Api) -> None:
    problem(await api.post("alice", f"/v1/studies/{uuid.uuid4()}/start"), 404)


# --------------------------------------------------------------------------- status


async def test_status_before_start_is_404(api: Api, storage: LocalStorage) -> None:
    sid = await ready_study(api, storage)
    body = problem(await api.get("alice", f"/v1/studies/{sid}/workflow"), 404, "not-found")
    assert "start it first" in body["detail"]


async def test_status_after_start(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    sid = await ready_study(api, storage)
    run_id = (await api.post("alice", f"/v1/studies/{sid}/start")).json()["run_id"]
    workflows.executions[f"study-{sid}"].view = StudyStatusView(
        stage="appraisal_review",
        revision=1,
        pending_photos=2,
        completed_photos=3,
        decisions_applied=("r-1",),
        budget_remaining_usd=24.5,
    )
    async with session_scope(sessionmaker) as session:
        session.add(
            Run(
                study_id=uuid.UUID(sid),
                workflow_id=f"study-{sid}",
                run_id=run_id,
                workflow_version="test-sha",
                input_revision=1,
            )
        )

    response = await api.get("alice", f"/v1/studies/{sid}/workflow")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["workflow_id"], body["run_id"], body["execution_status"]) == (
        f"study-{sid}",
        run_id,
        "RUNNING",
    )
    assert body["status"] == {
        "stage": "appraisal_review",
        "revision": 1,
        "waiting_since": None,
        "pending_photos": 2,
        "completed_photos": 3,
        "decisions_applied": ["r-1"],
        "stalled": False,
        "budget_remaining_usd": 24.5,
        "awaiting": None,
        "flagged_photos": [],
        "needs_review_stages": [],
    }
    (run,) = body["runs"]
    assert (run["run_id"], run["workflow_version"], run["input_revision"]) == (
        run_id,
        "test-sha",
        1,
    )
    assert run["ended_at"] is None and run["outcome"] is None


async def test_status_when_the_query_is_unanswered_is_still_200(
    api: Api, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    sid = await ready_study(api, storage)
    assert (await api.post("alice", f"/v1/studies/{sid}/start")).status_code == 202
    workflows.query_fails = True

    response = await api.get("alice", f"/v1/studies/{sid}/workflow")

    assert response.status_code == 200
    assert response.json()["status"] is None
    assert response.json()["execution_status"] == "RUNNING"


async def test_status_errors(
    api: Api, seed: Seed, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    sid = await ready_study(api, storage)
    assert (await api.post("alice", f"/v1/studies/{sid}/start")).status_code == 202
    org = (await api.get("alice", f"/v1/studies/{sid}")).json()["org_id"]
    await seed.member(uuid.UUID(org), "victor", OrgRole.VIEWER)

    assert (await api.get("victor", f"/v1/studies/{sid}/workflow")).status_code == 200
    problem(await api.get("mallory", f"/v1/studies/{sid}/workflow"), 404)
    workflows.unavailable = True
    problem(await api.get("alice", f"/v1/studies/{sid}/workflow"), 503, "service-unavailable")
    workflows.unavailable = False
    workflows.executions.clear()  # the database says started, Temporal has no record
    problem(await api.get("alice", f"/v1/studies/{sid}/workflow"), 404, "not-found")


# --------------------------------------------------------------------------- cancel


async def test_cancel_signals_the_workflow(
    api: Api, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    sid = await ready_study(api, storage)
    assert (await api.post("alice", f"/v1/studies/{sid}/start")).status_code == 202

    response = await api.post("alice", f"/v1/studies/{sid}/cancel", {"reason": "client withdrew"})
    default = await api.post("alice", f"/v1/studies/{sid}/cancel")

    assert response.status_code == default.status_code == 202, response.text
    assert response.json() == {"workflow_id": f"study-{sid}"}
    assert workflows.executions[f"study-{sid}"].cancel_reasons == [
        "client withdrew",
        "cancelled by owner",
    ]


async def test_cancel_errors(
    api: Api, seed: Seed, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    sid = await ready_study(api, storage)
    org = (await api.get("alice", f"/v1/studies/{sid}")).json()["org_id"]
    await seed.member(uuid.UUID(org), "erin", OrgRole.ENGINEER)

    problem(await api.post("alice", f"/v1/studies/{sid}/cancel"), 404, "not-found")  # not started
    assert (await api.post("alice", f"/v1/studies/{sid}/start")).status_code == 202
    problem(await api.post("erin", f"/v1/studies/{sid}/cancel"), 403, "forbidden")  # owner only
    problem(await api.post("mallory", f"/v1/studies/{sid}/cancel"), 404)
    problem(await api.post("alice", f"/v1/studies/{sid}/cancel", {"reason": ""}), 422)
    problem(await api.post("alice", f"/v1/studies/{sid}/cancel", {"why": "x"}), 422)

    workflows.executions[f"study-{sid}"].status = "COMPLETED"
    body = problem(await api.post("alice", f"/v1/studies/{sid}/cancel"), 409, "workflow-closed")
    assert "completed" in body["detail"]
    workflows.unavailable = True
    problem(await api.post("alice", f"/v1/studies/{sid}/cancel"), 503, "service-unavailable")


# --------------------------------------------------------------------------- wiring


async def test_routes_are_503_when_temporal_is_not_configured(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    verifier: FakeVerifier,
) -> None:
    import httpx
    from apitest_support import BASE_URL

    app = build_api_app(api_settings(database_url), sessionmaker, LocalStorage(tmp_path), verifier)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as http:
        api = Api(http)
        sid = (await api.create_study("alice"))["id"]
        for response in (
            await api.post("alice", f"/v1/studies/{sid}/start"),
            await api.get("alice", f"/v1/studies/{sid}/workflow"),
            await api.post("alice", f"/v1/studies/{sid}/cancel"),
        ):
            body = problem(response, 503, "service-unavailable")
            assert body["detail"] == "Temporal is not configured."
            assert response.headers["Retry-After"] == "5"
        # Decisions do not need Temporal: they are queued in the outbox.
        await Seed(sessionmaker).stage(sid, Stage.APPRAISAL_REVIEW)
        decision = {"stage": "appraisal_review", "revision": 1, "decision": "approve"}
        assert (
            await api.post("alice", f"/v1/studies/{sid}/decisions", decision)
        ).status_code == 202


async def test_health_reports_temporal_without_failing_on_it(
    api: Api, workflows: FakeWorkflowService
) -> None:
    ok = await api.client.get("/health")
    assert ok.status_code == 200
    assert ok.json()["checks"]["temporal"]["status"] == "ok"
    assert ok.json()["checks"]["db"]["status"] == "ok"

    workflows.unavailable = True
    down = await api.client.get("/health")
    assert down.status_code == 200  # a Temporal outage does not take the API out of service
    assert down.json()["checks"]["temporal"]["status"] == "fail"
    assert down.json()["checks"]["db"]["status"] == "ok"
