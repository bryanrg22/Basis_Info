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

import httpx
import pytest
from apitest_support import (
    BASE_URL,
    COMPLETE_INTAKE,
    Api,
    FakeVerifier,
    Seed,
    api_settings,
    bearer,
    build_api_app,
)
from dbtest_support import queued_behind_a_lock
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from workflowtest_support import FakeWorkflowService

from basis.api.errors import PROBLEM_JSON
from basis.api.idempotency import REPLAYED_HEADER
from basis.db.enums import OrgRole, Stage, StudyStatus
from basis.db.models import Run, Study, StudyEvent
from basis.db.session import session_scope
from basis.errors.codes import E
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
    """A study that may start: a complete intake and a confirmed appraisal."""
    study = await api.create_study(uid, intake=COMPLETE_INTAKE)
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
    study = await api.create_study("alice", intake=COMPLETE_INTAKE)
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


REQUIRED_INTAKE_FIELDS = [
    "purchase_price",
    "acquisition_costs",
    "land_method",
    "property_use",
    "placed_in_service_date",
    "state",
]


async def test_start_without_an_intake_is_422_naming_every_required_field(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    """EVAL_REPORT 37: a study cannot start without the intake the output needs. The
    appraisal is there; the problem is the intake, and it is checked first."""
    study = await api.create_study("alice")
    await upload(api, storage, "alice", study["id"])

    body = problem(
        await api.post("alice", f"/v1/studies/{study['id']}/start"), 422, "intake-required"
    )

    assert "PUT /v1/studies/{id}/intake" in body["detail"]
    assert [e["loc"] for e in body["errors"]] == [["intake", f] for f in REQUIRED_INTAKE_FIELDS]
    assert {e["type"] for e in body["errors"]} == {"missing"}
    assert all(e["detail"] for e in body["errors"])
    assert (await load(sessionmaker, study["id"])).current_stage is Stage.UPLOADING
    assert workflows.start_calls == []
    # No appraisal either: the intake is still the first answer.
    bare = await api.create_study("alice")
    problem(await api.post("alice", f"/v1/studies/{bare['id']}/start"), 422, "intake-required")


async def test_start_with_a_partial_intake_is_422_naming_the_missing_field(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    """The document's own rules make every other field present, so the one a stored intake
    can lack is the state (the appraisal fills it only after the start; a ZIP alone does
    not locate the cost index). Supplying it with a PUT lets the study start."""
    partial = {k: v for k, v in COMPLETE_INTAKE.items() if k != "state"}
    study = await api.create_study("alice", intake=partial)
    await upload(api, storage, "alice", study["id"])
    assert study["intake"]["state"] is None and study["intake"]["zip"] == "12345"

    body = problem(
        await api.post("alice", f"/v1/studies/{study['id']}/start"), 422, "intake-required"
    )
    assert "lacks 1 field(s)" in body["detail"] and "state" in body["detail"]
    assert body["errors"] == [
        {
            "loc": ["intake", "state"],
            "detail": next(e["detail"] for e in body["errors"] if e["loc"] == ["intake", "state"]),
            "type": "missing",
        }
    ]
    assert "location cost index" in body["errors"][0]["detail"]
    assert (await load(sessionmaker, study["id"])).current_stage is Stage.UPLOADING
    assert workflows.start_calls == []

    put = await api.client.put(
        f"/v1/studies/{study['id']}/intake", json=COMPLETE_INTAKE, headers=bearer("alice")
    )
    assert put.status_code == 200, put.text
    started = await api.post("alice", f"/v1/studies/{study['id']}/start")
    assert started.status_code == 202, started.text
    assert len(workflows.start_calls) == 1


async def test_start_with_a_complete_intake_set_later_is_202(
    api: Api, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    """The intake may come after creation and after the upload; a start then proceeds."""
    study = await api.create_study("alice")
    await upload(api, storage, "alice", study["id"])
    put = await api.client.put(
        f"/v1/studies/{study['id']}/intake", json=COMPLETE_INTAKE, headers=bearer("alice")
    )
    assert put.status_code == 200, put.text
    response = await api.post("alice", f"/v1/studies/{study['id']}/start")
    assert response.status_code == 202, response.text
    assert response.json()["workflow_id"] == f"study-{study['id']}"
    assert len(workflows.start_calls) == 1


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
    dead run (the live run of EVAL_REPORT section 23 got 202 and stayed "processing")."""
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
        "failed_stage": None,
        "failure": None,
        "failure_code": None,
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


# --------------------------------------------------------------------------- retry (EVAL_REPORT 27)


def failed_view(stage: str = "extracting") -> StudyStatusView:
    return StudyStatusView(
        stage=stage,  # type: ignore[arg-type]
        revision=1,
        failed_stage=stage,  # type: ignore[arg-type]
        failure="extract_fields: ProviderUnavailable: no module named aiohttp",
    )


async def test_retry_runs_the_failed_stage_again(
    api: Api, seed: Seed, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    sid = await ready_study(api, storage)
    org = (await api.get("alice", f"/v1/studies/{sid}")).json()["org_id"]
    await seed.member(uuid.UUID(org), "erin", OrgRole.ENGINEER)
    assert (await api.post("alice", f"/v1/studies/{sid}/start")).status_code == 202
    workflows.executions[f"study-{sid}"].view = failed_view()

    first = await api.post("erin", f"/v1/studies/{sid}/retry")  # an engineer may retry
    again = await api.post("alice", f"/v1/studies/{sid}/retry")

    assert first.status_code == again.status_code == 202, first.text
    assert first.json() == {
        "workflow_id": f"study-{sid}",
        "stage": "extracting",
        "revision": 1,
        "already_requested": False,
    }
    assert again.json()["already_requested"] is True
    status = (await api.get("alice", f"/v1/studies/{sid}/workflow")).json()["status"]
    assert status["failed_stage"] == "extracting"
    assert status["failure"].startswith("extract_fields:")


async def test_retry_errors(
    api: Api, seed: Seed, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    sid = await ready_study(api, storage)
    org = (await api.get("alice", f"/v1/studies/{sid}")).json()["org_id"]
    await seed.member(uuid.UUID(org), "vera", OrgRole.VIEWER)

    problem(await api.post("alice", f"/v1/studies/{sid}/retry"), 404, "not-found")  # not started
    assert (await api.post("alice", f"/v1/studies/{sid}/start")).status_code == 202
    body = problem(await api.post("alice", f"/v1/studies/{sid}/retry"), 409, "study-not-failed")
    assert "no stage has failed" in body["detail"]
    workflows.executions[f"study-{sid}"].view = failed_view()
    problem(await api.post("vera", f"/v1/studies/{sid}/retry"), 403, "forbidden")
    problem(await api.post("mallory", f"/v1/studies/{sid}/retry"), 404)
    workflows.executions[f"study-{sid}"].status = "FAILED"  # an execution that ended
    problem(await api.post("alice", f"/v1/studies/{sid}/retry"), 409, "workflow-closed")
    workflows.unavailable = True
    problem(await api.post("alice", f"/v1/studies/{sid}/retry"), 503, "service-unavailable")


async def test_a_failed_study_shows_its_status_and_reason(
    api: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await ready_study(api, storage)
    assert (await api.post("alice", f"/v1/studies/{sid}/start")).status_code == 202
    async with session_scope(sessionmaker) as session:
        study = await session.get(Study, uuid.UUID(sid))
        assert study is not None
        study.status, study.failure_reason = StudyStatus.FAILED, "extract_fields: boom"
    body = (await api.get("alice", f"/v1/studies/{sid}")).json()
    assert (body["status"], body["current_stage"], body["failure_reason"]) == (
        "failed",
        "extracting",
        "extract_fields: boom",
    )


# --------------------------------------------------------------------------- wiring


# ------------------------------------------------------------------- archive (EVAL_REPORT 60)


async def archived_events(sm: async_sessionmaker[AsyncSession], sid: str) -> int:
    async with sm() as session:
        rows = await session.scalars(
            select(StudyEvent).where(
                StudyEvent.study_id == uuid.UUID(sid), StudyEvent.event_type == "study.archived"
            )
        )
        return len(rows.all())


async def test_archive_is_refused_while_the_execution_runs(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    """A started study is archived only once its execution has closed. While Temporal says
    ``RUNNING`` (in progress, at a gate, or holding a failure for a retry) the answer is
    409 ``workflow-running`` BASIS-STUDY-035 and nothing changes; after a cancel (or any
    other end: failed, terminated, timed out, completed) the archive proceeds and is
    recorded once. Temporal unreachable: 503, nothing changes. Temporal that has forgotten
    the execution (retention): it is over, the study archives."""
    sid = await ready_study(api, storage)
    started = await api.post("alice", f"/v1/studies/{sid}/start")
    assert started.status_code == 202
    execution = workflows.executions[started.json()["workflow_id"]]
    for view in (None, failed_view()):
        execution.view = view
        body = problem(await api.delete("alice", f"/v1/studies/{sid}"), 409, "workflow-running")
        assert body["code"] == E.STUDY_ARCHIVE_WHILE_RUNNING and "cancel" in body["detail"]
    assert (await load(sessionmaker, sid)).status is StudyStatus.PROCESSING
    assert await archived_events(sessionmaker, sid) == 0

    workflows.unavailable = True
    problem(await api.delete("alice", f"/v1/studies/{sid}"), 503, "service-unavailable")
    workflows.unavailable = False
    assert (await load(sessionmaker, sid)).status is StudyStatus.PROCESSING

    assert (await api.post("alice", f"/v1/studies/{sid}/cancel")).status_code == 202
    execution.status = "CANCELED"  # the workflow ended: what the signal leads to
    archived = await api.delete("alice", f"/v1/studies/{sid}")
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert await archived_events(sessionmaker, sid) == 1
    # Archived already: a repeat does not ask Temporal (even unreachable) and records nothing.
    workflows.unavailable = True
    assert (await api.delete("alice", f"/v1/studies/{sid}")).status_code == 200
    assert await archived_events(sessionmaker, sid) == 1
    workflows.unavailable = False

    for ended in ("COMPLETED", "FAILED", "TERMINATED", "TIMED_OUT"):
        other = await ready_study(api, storage)
        run = await api.post("alice", f"/v1/studies/{other}/start")
        workflows.executions[run.json()["workflow_id"]].status = ended
        assert (await api.delete("alice", f"/v1/studies/{other}")).status_code == 200, ended
    forgotten = await ready_study(api, storage)
    run = await api.post("alice", f"/v1/studies/{forgotten}/start")
    del workflows.executions[run.json()["workflow_id"]]
    assert (await api.delete("alice", f"/v1/studies/{forgotten}")).status_code == 200


async def test_archive_of_a_started_study_needs_temporal(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    verifier: FakeVerifier,
) -> None:
    """With Temporal not configured a draft still archives (nothing can be running), but a
    study with a workflow is 503: whether its execution runs cannot be known."""
    app = build_api_app(api_settings(database_url), sessionmaker, LocalStorage(tmp_path), verifier)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as http:
        api = Api(http)
        draft = (await api.create_study("alice"))["id"]
        assert (await api.delete("alice", f"/v1/studies/{draft}")).status_code == 200
        started = (await api.create_study("alice"))["id"]
        async with sessionmaker() as session, session.begin():
            await session.execute(
                update(Study)
                .where(Study.id == uuid.UUID(started))
                .values(workflow_id=f"study-{started}", current_stage=Stage.EXTRACTING)
            )
        body = problem(await api.delete("alice", f"/v1/studies/{started}"), 503)
        assert body["detail"] == "Temporal is not configured."
        assert (await load(sessionmaker, started)).status is not StudyStatus.ARCHIVED


async def test_a_start_and_an_archive_at_once_leave_the_study_running(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    """The owner archives a draft while an engineer starts it. Both read the study as a
    draft with no workflow; held on the row lock with the start first in the queue, the
    start moves the study and records its workflow, and the archive's compare-and-set
    (``workflow_id`` still none) finds the study started and refuses: 202 and 409
    ``workflow-running``, the study ``processing`` with its execution, no ``study.archived``
    event. Without the compare-and-set (verified by dropping ``expected_workflow_id``) the
    archive lands on a study whose execution has just begun."""
    sid = await ready_study(api, storage)
    lock = select(Study).where(Study.id == uuid.UUID(sid)).with_for_update()
    started, archived = await queued_behind_a_lock(
        sessionmaker,
        lock,
        lambda: api.post("alice", f"/v1/studies/{sid}/start"),
        lambda: api.delete("alice", f"/v1/studies/{sid}"),
    )
    assert started.status_code == 202, started.text
    body = problem(archived, 409, "workflow-running")
    assert body["code"] == E.STUDY_ARCHIVE_WHILE_RUNNING
    row = await load(sessionmaker, sid)
    assert (row.status, row.current_stage, row.workflow_id) == (
        StudyStatus.PROCESSING,
        Stage.EXTRACTING,
        started.json()["workflow_id"],
    )
    assert len(workflows.executions) == 1
    assert await archived_events(sessionmaker, sid) == 0


async def test_a_start_and_an_intake_write_at_once_leave_the_intake_the_run_read(
    api: Api,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    """An engineer saves the intake while another starts the study. Both read a draft; with
    the start first on the row lock, the write finds the study started under the lock and is
    409 ``intake-locked``: the run prices from the intake it read, and the intake the
    engineer saw is the one on the row (version unchanged, no event)."""
    sid = await ready_study(api, storage)
    lock = select(Study).where(Study.id == uuid.UUID(sid)).with_for_update()
    changed = {**COMPLETE_INTAKE, "purchase_price": "700000.00"}
    started, written = await queued_behind_a_lock(
        sessionmaker,
        lock,
        lambda: api.post("alice", f"/v1/studies/{sid}/start"),
        lambda: api.client.put(f"/v1/studies/{sid}/intake", json=changed, headers=bearer("alice")),
    )
    assert started.status_code == 202, started.text
    body = problem(written, 409, "intake-locked")
    assert body["code"] == E.STUDY_INTAKE_LOCKED
    read = (await api.get("alice", f"/v1/studies/{sid}/intake")).json()
    assert read["intake"]["purchase_price"] == COMPLETE_INTAKE["purchase_price"]
    assert read["intake_version"] == 1
    assert len(workflows.start_calls) == 1


async def test_routes_are_503_when_temporal_is_not_configured(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    verifier: FakeVerifier,
) -> None:
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


# --------------------------------------------------------------------------- restart
# docs/api/GATE_DRAFTS_AND_RESUME.md: a cancelled study runs again from its uploads.


async def events_of(sm: async_sessionmaker[AsyncSession], sid: str, *types: str) -> list[Any]:
    from basis.db.models import StudyEvent

    async with sm() as session:
        rows = (
            await session.scalars(
                select(StudyEvent)
                .where(StudyEvent.study_id == uuid.UUID(sid), StudyEvent.event_type.in_(types))
                .order_by(StudyEvent.seq)
            )
        ).all()
    return list(rows)


async def cancelled_study(
    api: Api,
    seed: Seed,
    storage: LocalStorage,
    workflows: FakeWorkflowService,
    *,
    stage: Stage = Stage.ASSETS_REVIEW,
    revision: int = 2,
    outcome: str = "CANCELED",
) -> str:
    """A started study the workflow moved to ``stage``/``revision`` and then closed with
    ``outcome`` (as the owner's cancel leaves it: the row stays where it was)."""
    sid = await ready_study(api, storage)
    assert (await api.post("alice", f"/v1/studies/{sid}/start")).status_code == 202
    await seed.stage(sid, stage, revision, components_gate=True)
    workflows.executions[f"study-{sid}"].status = outcome
    return sid


async def test_restart_runs_a_cancelled_study_again_on_a_new_revision(
    api: Api,
    seed: Seed,
    storage: LocalStorage,
    workflows: FakeWorkflowService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sid = await cancelled_study(api, seed, storage, workflows)
    old_run = workflows.executions[f"study-{sid}"].run_id
    problem(await api.post("alice", f"/v1/studies/{sid}/start"), 409, "workflow-closed")  # dead end

    response = await api.post("alice", f"/v1/studies/{sid}/restart")

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["workflow_id"] == f"study-{sid}" and body["run_id"] != old_run
    assert (body["revision"], body["from_stage"], body["from_revision"]) == (3, "assets_review", 2)
    study = await load(sessionmaker, sid)
    assert (study.current_stage, study.status, study.revision) == (
        Stage.EXTRACTING,
        StudyStatus.PROCESSING,
        3,
    )
    assert study.workflow_id == f"study-{sid}"  # the id is unchanged: cancel and retry still route
    [started] = workflows.restart_calls
    assert (started.study_id, started.input_revision) == (sid, 3)
    execution = workflows.executions[f"study-{sid}"]
    assert execution.status == "RUNNING" and execution.run_id == body["run_id"]
    assert [e.status for e in workflows.history[f"study-{sid}"]] == ["CANCELED"]
    # The timeline: the move and the restart, by the owner.
    [moved] = [
        e
        for e in await events_of(sessionmaker, sid, "stage.entered")
        if e.before and e.before["stage"] == "assets_review"
    ]
    assert moved.after == {"stage": "extracting", "revision": 3} and moved.actor_type == "engineer"
    [restarted] = await events_of(sessionmaker, sid, "study.restarted")
    assert restarted.before == {"stage": "assets_review", "revision": 2, "status": "waiting_review"}
    assert restarted.after == {"stage": "extracting", "revision": 3}
    assert restarted.detail == {"workflow_id": f"study-{sid}"} and restarted.revision == 3
    # The workflow route follows the new execution.
    view = (await api.get("alice", f"/v1/studies/{sid}/workflow")).json()
    assert (view["run_id"], view["execution_status"]) == (body["run_id"], "RUNNING")
    assert view["status"]["revision"] == 3
    # A second restart now: the new execution runs.
    problem(await api.post("alice", f"/v1/studies/{sid}/restart"), 409, "workflow-running")


@pytest.mark.parametrize("outcome", ["TERMINATED", "TIMED_OUT", "FAILED"])
async def test_restart_after_any_unsuccessful_close(
    api: Api, seed: Seed, storage: LocalStorage, workflows: FakeWorkflowService, outcome: str
) -> None:
    sid = await cancelled_study(api, seed, storage, workflows, outcome=outcome)
    assert (await api.post("alice", f"/v1/studies/{sid}/restart")).status_code == 202


async def test_restart_clears_a_held_failure(
    api: Api,
    seed: Seed,
    storage: LocalStorage,
    workflows: FakeWorkflowService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sid = await cancelled_study(api, seed, storage, workflows, stage=Stage.EXTRACTING, revision=1)
    async with session_scope(sessionmaker) as session:
        await session.execute(
            update(Study)
            .where(Study.id == uuid.UUID(sid))
            .values(status=StudyStatus.FAILED, failure_reason="ingest: timeout", failure_code="X")
        )
    response = await api.post("alice", f"/v1/studies/{sid}/restart")
    assert response.status_code == 202 and response.json()["revision"] == 2
    study = await load(sessionmaker, sid)
    assert (study.status, study.failure_reason, study.failure_code) == (
        StudyStatus.PROCESSING,
        None,
        None,
    )


async def test_restart_refusals(
    api: Api, seed: Seed, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    sid = await ready_study(api, storage)
    org = (await api.get("alice", f"/v1/studies/{sid}")).json()["org_id"]
    await seed.member(uuid.UUID(org), "erin", OrgRole.ENGINEER)
    problem(await api.post("alice", f"/v1/studies/{sid}/restart"), 404, "not-found")  # not started
    assert (await api.post("alice", f"/v1/studies/{sid}/start")).status_code == 202

    running = problem(
        await api.post("alice", f"/v1/studies/{sid}/restart"), 409, "workflow-running"
    )
    assert running["code"] == E.STUDY_RESTART_RUNNING and "running" in running["detail"]
    workflows.executions[f"study-{sid}"].status = "COMPLETED"
    done = problem(await api.post("alice", f"/v1/studies/{sid}/restart"), 409, "study-completed")
    assert done["code"] == E.STUDY_RESTART_COMPLETED
    workflows.executions[f"study-{sid}"].status = "CANCELED"
    problem(await api.post("erin", f"/v1/studies/{sid}/restart"), 403, "forbidden")  # owner only
    problem(await api.post("mallory", f"/v1/studies/{sid}/restart"), 404)
    problem(await api.post("alice", f"/v1/studies/{uuid.uuid4()}/restart"), 404)
    workflows.unavailable = True
    problem(await api.post("alice", f"/v1/studies/{sid}/restart"), 503, "service-unavailable")
    workflows.unavailable = False
    assert (await api.delete("alice", f"/v1/studies/{sid}")).status_code == 200
    problem(await api.post("alice", f"/v1/studies/{sid}/restart"), 409, "study-archived")
    assert workflows.restart_calls == []  # nothing was started by any refusal


async def test_restart_after_a_failed_start_reuses_the_reset(
    api: Api,
    seed: Seed,
    storage: LocalStorage,
    workflows: FakeWorkflowService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Temporal down between the reset and the start: the row is reset (503); the repeat
    does not bump the revision again, and two owners clicking at once end on one revision."""
    from basis.temporal.service import WorkflowUnavailableError

    sid = await cancelled_study(api, seed, storage, workflows)
    workflows.fail_restart = WorkflowUnavailableError("Temporal is unavailable (fake).")
    problem(await api.post("alice", f"/v1/studies/{sid}/restart"), 503, "service-unavailable")
    study = await load(sessionmaker, sid)
    assert (study.current_stage, study.revision) == (Stage.EXTRACTING, 3)  # reset, not started
    assert workflows.executions[f"study-{sid}"].status == "CANCELED"

    workflows.fail_restart = None
    response = await api.post("alice", f"/v1/studies/{sid}/restart")
    assert response.status_code == 202, response.text
    assert (response.json()["revision"], response.json()["from_stage"]) == (3, "assets_review")
    assert (await load(sessionmaker, sid)).revision == 3  # reused, not 4
    assert len(await events_of(sessionmaker, sid, "study.restarted")) == 1
    assert workflows.restart_calls[-1].input_revision == 3

    # Two restarts at once on another cancelled study: one execution, one revision bump.
    other = await cancelled_study(api, seed, storage, workflows)
    first, second = await asyncio.gather(
        api.post("alice", f"/v1/studies/{other}/restart"),
        api.post("alice", f"/v1/studies/{other}/restart"),
    )
    assert sorted([first.status_code, second.status_code]) == [202, 409]
    assert (await load(sessionmaker, other)).revision == 3
    # The loser is refused at the describe (the winner's execution already runs) or at the
    # start (both passed the describe; the fake refuses the second start): 409 either way,
    # and never a second reset or a second execution.
    assert 1 <= len([c for c in workflows.restart_calls if c.study_id == other]) <= 2
    assert workflows.executions[f"study-{other}"].workflow_input.input_revision == 3
    assert len(await events_of(sessionmaker, other, "study.restarted")) == 1


async def test_restart_when_temporal_has_no_record_of_the_execution(
    api: Api, seed: Seed, storage: LocalStorage, workflows: FakeWorkflowService
) -> None:
    """After namespace retention the execution is gone; the owner's explicit restart is
    allowed (``start`` keeps refusing with ``workflow-missing``)."""
    sid = await cancelled_study(api, seed, storage, workflows)
    del workflows.executions[f"study-{sid}"]
    problem(await api.post("alice", f"/v1/studies/{sid}/start"), 409, "workflow-missing")
    response = await api.post("alice", f"/v1/studies/{sid}/restart")
    assert response.status_code == 202 and response.json()["revision"] == 3
    assert workflows.executions[f"study-{sid}"].status == "RUNNING"
