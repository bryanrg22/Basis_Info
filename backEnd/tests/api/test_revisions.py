"""``POST .../reopen``, ``GET .../revisions``, ``GET .../revisions/{a}/diff/{b}`` and
``POST .../revisions/{n}/restore`` with the fake Temporal (docs/api/REVISIONS.md).

The application is the real one with the real database; Temporal is
``workflowtest_support.FakeWorkflowService`` (``resume_study`` admits a new execution over a
closed one and refuses while one runs, as ``ALLOW_DUPLICATE`` with conflict ``FAIL`` does).
The copy, the gates and the export of a resumed execution run on the Temporal test server in
``tests/lifecycle/test_reopen.py``.

The history and the diff are read on the two-revision study ``test_timeline.py`` seeds:
revision 1 as the pipeline wrote it, revision 2 as ``apply_corrections`` copied it after a
rejection with one correction of each kind, each copy naming its parent.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from apitest_support import COMPLETE_INTAKE, Api, Seed, api_settings, build_api_app
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_timeline import (
    record_other_corrections,
    reject_with_corrections,
    seed_revision_one,
    seed_revision_two,
)
from workflowtest_support import FakeWorkflowService

from basis.api.errors import PROBLEM_JSON
from basis.db.enums import Decision, Stage, StudyStatus
from basis.db.models import Correction, Review, Room, Study
from basis.db.repositories.report import ReportRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.storage import LocalStorage
from basis.temporal.service import WorkflowUnavailableError

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n" + b"appraisal body " * 50
GATES = ("appraisal_review", "rooms_review", "components_review", "assets_review")


@pytest.fixture
def workflows() -> FakeWorkflowService:
    return FakeWorkflowService()


@pytest.fixture
def api_app(database_url, sessionmaker, storage, verifier, workflows):  # type: ignore[no-untyped-def]
    return build_api_app(api_settings(database_url), sessionmaker, storage, verifier, workflows)


def problem(response: Any, status: int, suffix: str | None = None) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body: dict[str, Any] = response.json()
    if suffix is not None:
        assert body["type"].endswith(f"/{suffix}"), body
    return body


async def started_study(api: Api, storage: LocalStorage, uid: str = "alice") -> str:
    """A study started through the API (a complete intake, a confirmed appraisal), so the
    fake holds its execution and the row its workflow id."""
    study = await api.create_study(uid, intake=COMPLETE_INTAKE)
    sid = str(study["id"])
    response = await api.request_upload(uid, sid, PDF, kind="appraisal")
    assert response.status_code == 201, response.text
    ticket = response.json()
    storage.put_signed(ticket["upload"]["url"], PDF, ticket["upload"]["headers"])
    path = f"/v1/studies/{sid}/documents/{ticket['document']['id']}/complete"
    assert (await api.post(uid, path)).status_code == 200
    assert (await api.post(uid, f"/v1/studies/{sid}/start")).status_code == 202
    return sid


async def completed_study(
    api: Api,
    storage: LocalStorage,
    seed: Seed,
    workflows: FakeWorkflowService,
    *,
    revision: int = 1,
) -> str:
    """A started study whose execution completed at ``revision`` (rows of every kind)."""
    sid = await started_study(api, storage)
    await seed.stage(sid, Stage.COMPLETED, revision=revision, components_gate=True)
    await seed.outputs(sid, revision=revision)
    workflows.executions[f"study-{sid}"].status = "COMPLETED"
    return sid


async def load(sm: async_sessionmaker[AsyncSession], study_id: str) -> Study:
    async with sm() as session:
        study = await session.get(Study, uuid.UUID(study_id))
    assert study is not None
    return study


async def events(sm: async_sessionmaker[AsyncSession], study_id: str, *types: str) -> list[Any]:
    async with session_scope(sm) as session:
        page = await StudyEventRepository(session).list_for_study(
            uuid.UUID(study_id), event_types=list(types) or None
        )
        return list(page.items)


# --------------------------------------------------------------------------- reopen


@pytest.mark.parametrize("stage", GATES)
async def test_reopen_moves_the_study_to_the_gate_and_resumes_the_workflow(
    api: Api,
    storage: LocalStorage,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
    stage: str,
) -> None:
    sid = await completed_study(api, storage, seed, workflows)
    first_run = workflows.executions[f"study-{sid}"].run_id

    response = await api.post("alice", f"/v1/studies/{sid}/reopen", {"stage": stage})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["workflow_id"] == f"study-{sid}"
    assert body["run_id"] != first_run
    assert (body["stage"], body["from_revision"], body["revision"]) == (stage, 1, 2)
    assert body["reason"] == "reopen"
    assert body["reviews"] == list(GATES[GATES.index(stage) :])
    assert body["recomputed"][-1] == "exporting"
    assert ("assets" in body["carried"]) == (stage == "assets_review")
    study = await load(sessionmaker, sid)
    assert (study.current_stage.value, study.status, study.revision) == (
        stage,
        StudyStatus.PROCESSING,
        2,
    )
    (resumed,) = workflows.resume_calls
    assert resumed.resume is not None
    assert (resumed.resume.stage, resumed.resume.from_revision, resumed.resume.reason) == (
        stage,
        1,
        "reopen",
    )
    assert resumed.input_revision == 2
    assert workflows.history[f"study-{sid}"][0].run_id == first_run  # the completed one kept
    reopened = await events(sessionmaker, sid, "study.reopened")
    assert len(reopened) == 1
    assert reopened[0].stage.value == stage and reopened[0].revision == 2
    assert reopened[0].detail["from_revision"] == 1
    assert reopened[0].detail["recomputed"] == body["recomputed"]
    entered = await events(sessionmaker, sid, "stage.entered")
    assert entered[-1].before == {"stage": "completed", "revision": 1}
    assert entered[-1].after == {"stage": stage, "revision": 2}
    # The audit listing shows it as the engineer's action.
    audit = await api.get("alice", f"/v1/studies/{sid}/audit")
    assert [i["kind"] for i in audit.json()["items"]][-1] == "reopened"


async def test_reopen_dry_run_answers_the_plan_and_changes_nothing(
    api: Api,
    storage: LocalStorage,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    sid = await completed_study(api, storage, seed, workflows)

    response = await api.post(
        "alice", f"/v1/studies/{sid}/reopen", {"stage": "rooms_review", "dry_run": True}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "stage": "rooms_review",
        "from_revision": 1,
        "revision": 2,
        "recomputed": ["components_processing", "assets_processing", "exporting"],
        "reviews": ["rooms_review", "components_review", "assets_review"],
        "carried": ["extractions", "rooms", "photo_rooms", "components", "photo_objects"],
    }
    study = await load(sessionmaker, sid)
    assert (study.current_stage, study.revision) == (Stage.COMPLETED, 1)
    assert workflows.resume_calls == []
    assert await events(sessionmaker, sid, "study.reopened") == []


async def test_reopen_refuses_a_study_that_is_not_completed(
    api: Api, storage: LocalStorage, seed: Seed, workflows: FakeWorkflowService
) -> None:
    sid = await started_study(api, storage)
    await seed.stage(sid, Stage.ASSETS_REVIEW, revision=2)

    body = problem(
        await api.post("alice", f"/v1/studies/{sid}/reopen", {"stage": "rooms_review"}),
        409,
        "study-not-completed",
    )

    assert body["code"] == E.STUDY_NOT_COMPLETED
    assert "assets_review revision 2" in body["detail"]
    assert workflows.resume_calls == []


async def test_reopen_refuses_while_an_execution_runs(
    api: Api, storage: LocalStorage, seed: Seed, workflows: FakeWorkflowService
) -> None:
    sid = await started_study(api, storage)
    # The row says completed, the execution is still closing (record_run_end pending).
    await seed.stage(sid, Stage.COMPLETED, revision=1)

    body = problem(
        await api.post("alice", f"/v1/studies/{sid}/reopen", {"stage": "assets_review"}),
        409,
        "workflow-running",
    )

    assert body["code"] == E.STUDY_EXECUTION_RUNNING
    assert workflows.resume_calls == []


@pytest.mark.parametrize("stage", ["uploading", "extracting", "exporting", "completed", "x"])
async def test_reopen_refuses_a_stage_that_is_not_a_review_gate(
    api: Api, storage: LocalStorage, seed: Seed, workflows: FakeWorkflowService, stage: str
) -> None:
    sid = await completed_study(api, storage, seed, workflows)

    body = problem(
        await api.post("alice", f"/v1/studies/{sid}/reopen", {"stage": stage}), 422, "validation"
    )

    assert [e["loc"] for e in body["errors"]] == [["body", "stage"]]
    assert workflows.resume_calls == []


async def test_reopen_refuses_an_archived_study_and_one_without_a_workflow(
    api: Api, storage: LocalStorage, seed: Seed, workflows: FakeWorkflowService
) -> None:
    sid = await completed_study(api, storage, seed, workflows)
    assert (await api.delete("alice", f"/v1/studies/{sid}")).status_code == 200
    problem(
        await api.post("alice", f"/v1/studies/{sid}/reopen", {"stage": "rooms_review"}),
        409,
        "study-archived",
    )
    never_started = await api.create_study("alice", intake=COMPLETE_INTAKE)
    nid = never_started["id"]
    await seed.stage(nid, Stage.COMPLETED, revision=1)
    body = problem(
        await api.post("alice", f"/v1/studies/{nid}/reopen", {"stage": "rooms_review"}), 404
    )
    assert body["code"] == E.STUDY_NO_WORKFLOW
    assert workflows.resume_calls == []


async def test_reopen_needs_the_engineer_role(
    api: Api, storage: LocalStorage, seed: Seed, workflows: FakeWorkflowService
) -> None:
    sid = await completed_study(api, storage, seed, workflows)
    org_id = (await api.get("alice", f"/v1/studies/{sid}")).json()["org_id"]
    from basis.db.enums import OrgRole

    await seed.member(uuid.UUID(org_id), "vera", OrgRole.VIEWER)

    problem(await api.post("vera", f"/v1/studies/{sid}/reopen", {"stage": "rooms_review"}), 403)
    assert workflows.resume_calls == []


async def test_two_concurrent_reopens_admit_exactly_one(
    api: Api,
    storage: LocalStorage,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    """Two requests at once: one is 202, the other 409 (``workflow-running`` when it reached
    the start after the first was admitted, ``study-not-completed`` when it read the row
    after the first's reset and start were both done); one reset, one revision bump."""
    sid = await completed_study(api, storage, seed, workflows)

    responses = await asyncio.gather(
        *(
            api.post("alice", f"/v1/studies/{sid}/reopen", {"stage": "components_review"})
            for _ in range(2)
        )
    )

    assert sorted(r.status_code for r in responses) == [202, 409], [r.text for r in responses]
    refused = next(r for r in responses if r.status_code == 409)
    body = problem(refused, 409)
    assert body["code"] in (E.STUDY_EXECUTION_RUNNING, E.STUDY_NOT_COMPLETED)
    study = await load(sessionmaker, sid)
    assert (study.current_stage, study.revision) == (Stage.COMPONENTS_REVIEW, 2)
    assert len(await events(sessionmaker, sid, "study.reopened")) == 1
    assert workflows.executions[f"study-{sid}"].status == "RUNNING"


async def test_a_reopen_racing_a_start_in_flight_is_refused_as_running(
    api: Api,
    storage: LocalStorage,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    """The first request has reset the row and is inside the Temporal start; the second
    reads the row (no longer completed, but the reset is this same request's and no run
    has started from it), reuses the reset under the lock and reaches the start too. The
    start admits one, as Temporal's conflict policy does; the other is 409
    ``workflow-running``. One revision bump, one event."""
    sid = await completed_study(api, storage, seed, workflows)
    hold = asyncio.Event()
    workflows.hold_first_resume = hold
    first = asyncio.create_task(
        api.post("alice", f"/v1/studies/{sid}/reopen", {"stage": "rooms_review"})
    )
    for _ in range(200):
        if workflows.resume_calls:
            break
        await asyncio.sleep(0.01)
    assert len(workflows.resume_calls) == 1  # the first is inside the start

    second = await api.post("alice", f"/v1/studies/{sid}/reopen", {"stage": "rooms_review"})
    hold.set()
    first_response = await first

    assert second.status_code == 202, second.text
    assert problem(first_response, 409, "workflow-running")["code"] == E.STUDY_EXECUTION_RUNNING
    study = await load(sessionmaker, sid)
    assert (study.current_stage, study.revision) == (Stage.ROOMS_REVIEW, 2)
    assert len(await events(sessionmaker, sid, "study.reopened")) == 1
    assert workflows.executions[f"study-{sid}"].run_id == second.json()["run_id"]


async def test_a_reset_whose_start_failed_is_reused_by_the_repeat(
    api: Api,
    storage: LocalStorage,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    sid = await completed_study(api, storage, seed, workflows)
    workflows.fail_resume = WorkflowUnavailableError("Temporal is unavailable (fake).")

    first = await api.post("alice", f"/v1/studies/{sid}/reopen", {"stage": "assets_review"})
    assert first.status_code == 503, first.text
    study = await load(sessionmaker, sid)
    assert (study.current_stage, study.revision) == (Stage.ASSETS_REVIEW, 2)  # the row moved

    workflows.fail_resume = None
    again = await api.post("alice", f"/v1/studies/{sid}/reopen", {"stage": "assets_review"})
    assert again.status_code == 202, again.text
    assert again.json()["revision"] == 2  # no second bump
    assert len(await events(sessionmaker, sid, "study.reopened")) == 1
    # A repeat at another gate is not the same reset: the study is no longer completed.
    other = await api.post("alice", f"/v1/studies/{sid}/reopen", {"stage": "rooms_review"})
    problem(other, 409, "study-not-completed")


# --------------------------------------------------------------------------- restore


async def test_restore_copies_the_revision_and_opens_its_last_gate(
    api: Api,
    storage: LocalStorage,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    sid = await completed_study(api, storage, seed, workflows, revision=3)
    await seed.outputs(sid, revision=1)
    async with session_scope(sessionmaker) as session:
        # Revision 1 reached the rooms review (rejected there), per the timeline.
        repo = StudyEventRepository(session)
        for stage in (Stage.EXTRACTING, Stage.APPRAISAL_REVIEW, Stage.ROOMS_REVIEW):
            await repo.record(
                uuid.UUID(sid), "stage.entered", actor="system", stage=stage, revision=1
            )

    plan = await api.post("alice", f"/v1/studies/{sid}/revisions/1/restore", {"dry_run": True})
    assert plan.status_code == 200, plan.text
    assert (plan.json()["stage"], plan.json()["from_revision"], plan.json()["revision"]) == (
        "rooms_review",
        1,
        4,
    )

    response = await api.post("alice", f"/v1/studies/{sid}/revisions/1/restore")

    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["stage"], body["from_revision"], body["revision"], body["reason"]) == (
        "rooms_review",
        1,
        4,
        "restore",
    )
    study = await load(sessionmaker, sid)
    assert (study.current_stage, study.status, study.revision) == (
        Stage.ROOMS_REVIEW,
        StudyStatus.PROCESSING,
        4,
    )
    (resumed,) = workflows.resume_calls
    assert resumed.resume is not None
    assert (resumed.resume.stage, resumed.resume.from_revision, resumed.resume.reason) == (
        "rooms_review",
        1,
        "restore",
    )
    (restored,) = await events(sessionmaker, sid, "revision.restored")
    assert (restored.revision, restored.detail["from_revision"]) == (4, 1)
    assert restored.before == {"stage": "completed", "revision": 3}
    audit = await api.get("alice", f"/v1/studies/{sid}/audit")
    assert [i["kind"] for i in audit.json()["items"]][-1] == "restored"


async def test_restore_of_a_completed_revision_opens_the_assets_review(
    api: Api,
    storage: LocalStorage,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    """Revision 1 was exported (it entered every stage to `completed`), then reopened:
    restoring it opens the assets review, where its copied assets are reviewed."""
    sid = await completed_study(api, storage, seed, workflows, revision=2)
    await seed.outputs(sid, revision=1)
    async with session_scope(sessionmaker) as session:
        repo = StudyEventRepository(session)
        for stage in (Stage.ASSETS_REVIEW, Stage.EXPORTING, Stage.COMPLETED):
            await repo.record(
                uuid.UUID(sid), "stage.entered", actor="system", stage=stage, revision=1
            )

    response = await api.post("alice", f"/v1/studies/{sid}/revisions/1/restore")

    assert response.status_code == 202, response.text
    assert response.json()["stage"] == "assets_review"


async def test_restore_refusals(
    api: Api,
    storage: LocalStorage,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    workflows: FakeWorkflowService,
) -> None:
    sid = await completed_study(api, storage, seed, workflows, revision=2)
    base = f"/v1/studies/{sid}/revisions"

    missing = problem(await api.post("alice", f"{base}/3/restore"), 404, "revision-not-found")
    assert missing["code"] == E.STUDY_REVISION_NOT_FOUND
    current = problem(await api.post("alice", f"{base}/2/restore"), 409, "revision-current")
    assert current["code"] == E.STUDY_REVISION_CURRENT
    # Revision 1 has no rows and no stage.entered: it never reached a gate.
    bare = problem(await api.post("alice", f"{base}/1/restore"), 409, "revision-not-restorable")
    assert bare["code"] == E.STUDY_REVISION_NOT_RESTORABLE
    problem(await api.post("alice", f"{base}/0/restore"), 422)
    assert workflows.resume_calls == []

    await seed.outputs(sid, revision=1)
    async with session_scope(sessionmaker) as session:
        await StudyEventRepository(session).record(
            uuid.UUID(sid), "stage.entered", actor="system", stage=Stage.ROOMS_REVIEW, revision=1
        )
    await seed.stage(sid, Stage.ASSETS_REVIEW, revision=2)
    problem(await api.post("alice", f"{base}/1/restore"), 409, "study-not-completed")
    assert workflows.resume_calls == []


# --------------------------------------------------------------------------- history


async def seeded_two_revisions(
    api: Api, seed: Seed, sm: async_sessionmaker[AsyncSession]
) -> dict[str, Any]:
    ids = await seed_revision_one(api, seed, sm)
    decided = await reject_with_corrections(api, ids)
    reviewer = uuid.UUID(decided["review"]["reviewer_id"])
    await record_other_corrections(sm, ids, reviewer)
    await seed_revision_two(sm, ids)
    ids["reviewer"] = reviewer
    return ids


async def test_revisions_list_says_who_made_each_and_what_it_carries(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    ids = await seeded_two_revisions(api, seed, sessionmaker)
    sid = ids["study_id"]
    signed_at = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    async with session_scope(sessionmaker) as session:
        # Revision 1 exported and signed; revision 2 (the current one) at the assets review.
        await StudyEventRepository(session).record(
            sid,
            "export.written",
            actor="system",
            stage=Stage.EXPORTING,
            revision=1,
            detail={"uri": "gs://basis-local/x/rev-1.json", "sha256": "a" * 64},
        )
        signature = await ReportRepository(session).add_signature(
            sid,
            1,
            signature_id=uuid.uuid4(),
            signed_by=ids["reviewer"],
            signed_at=signed_at,
            engineer={"name": "A. Engineer"},
            statement_version=1,
            export_sha256="a" * 64,
            reviewed_sha256="b" * 64,
            pdf_uri="gs://basis-local/x/signed.pdf",
            pdf_sha256="c" * 64,
        )
        signature_id = signature.id

    response = await api.get("alice", f"/v1/studies/{sid}/revisions")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["current_revision"] == 2
    first, second = body["items"]
    assert (first["revision"], first["current"]) == (1, False)
    assert first["origin"]["kind"] == "start"
    assert first["decisions"] == [
        {
            "stage": "rooms_review",
            "decision": "reject",
            "reviewer_id": str(ids["reviewer"]),
            "review_id": first["decisions"][0]["review_id"],
            "at": first["decisions"][0]["at"],
        }
    ]
    assert first["corrections"] == {}
    # The seed put revision 1 at the gate directly (no stage.entered for it): from its rows.
    assert first["stage_reached"] == "assets_review"
    assert first["export"]["uri"] == "gs://basis-local/x/rev-1.json"
    assert first["signature"]["id"] == str(signature_id)
    assert first["signature"]["signed_by"] == str(ids["reviewer"])
    assert first["ended_at"] == second["started_at"]
    assert (second["revision"], second["current"]) == (2, True)
    assert second["origin"]["kind"] == "rejection"
    assert second["origin"]["stage"] == "rooms_review"
    assert second["origin"]["actor_id"] == str(ids["reviewer"])
    assert second["origin"]["review_id"] == first["decisions"][0]["review_id"]
    assert second["origin"]["from_revision"] == 1
    assert second["corrections"] == {
        "room": 2,
        "photo_room": 1,
        "component": 1,
        "room_component": 1,
        "extraction_field": 1,
        "asset": 1,
    }
    assert second["decisions"] == []
    assert second["export"] is None and second["signature"] is None
    assert second["ended_at"] is None


async def test_revisions_list_of_a_study_older_than_the_timeline(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Rows and a review written directly, no event for any revision (``study_events`` is
    append-only, so the seed never writes them): the rejection from ``reviews``, the start
    from the study, the stage and the start time from the rows."""
    study = await api.create_study("alice")
    sid = uuid.UUID(study["id"])
    await seed.stage(sid, Stage.ASSETS_REVIEW, revision=2)
    await seed.outputs(sid, revision=1)
    await seed.outputs(sid, revision=2)
    reviewer = (await seed.user("alice")).id
    async with session_scope(sessionmaker) as session:
        session.add(
            Review(
                study_id=sid,
                stage=Stage.ROOMS_REVIEW,
                revision=1,
                decision=Decision.REJECT,
                reviewer_id=reviewer,
                idempotency_key=f"{reviewer}:k",
            )
        )
        session.add(
            Correction(
                study_id=sid,
                entity_type="room",
                entity_id=uuid.uuid4(),
                revision=1,
                after={"name": "x"},
                reviewer_id=reviewer,
            )
        )

    body = (await api.get("alice", f"/v1/studies/{sid}/revisions")).json()

    first, second = body["items"]
    assert first["origin"]["kind"] == "start" and first["started_at"] is not None
    assert first["stage_reached"] == "assets_review"  # it has an asset
    assert first["decisions"][0]["decision"] == "reject"
    assert second["origin"]["kind"] == "rejection"
    assert second["origin"]["review_id"] == first["decisions"][0]["review_id"]
    assert second["origin"]["actor_id"] == str(reviewer)
    assert second["corrections"] == {"room": 1}
    assert second["stage_reached"] == "assets_review"
    assert second["started_at"] is not None and first["ended_at"] == second["started_at"]


# --------------------------------------------------------------------------- diff


async def test_diff_pairs_rows_through_their_lineage(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    ids = await seeded_two_revisions(api, seed, sessionmaker)
    sid = ids["study_id"]

    response = await api.get("alice", f"/v1/studies/{sid}/revisions/1/diff/2")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["from_revision"], body["to_revision"]) == (1, 2)
    assert body["counts"] == {
        "room": {"added": 0, "removed": 1, "changed": 1, "unchanged": 0},
        "component": {"added": 1, "removed": 0, "changed": 2, "unchanged": 0},
        "asset": {"added": 1, "removed": 0, "changed": 1, "unchanged": 0},
    }
    assert body["removed"] == [{"kind": "room", "id": str(ids["bath"]), "label": "Bath"}]
    assert body["added"] == [
        {"kind": "component", "id": str(ids["lamp_2"]), "label": "light_fixture"},
        {"kind": "asset", "id": str(ids["lamp_asset_2"]), "label": "light_fixture"},
    ]
    by_pair = {(c["from_id"], c["to_id"]): c for c in body["changed"]}
    kitchen = by_pair[(str(ids["kitchen"]), str(ids["kitchen_2"]))]
    assert kitchen["kind"] == "room" and kitchen["label"] == "Main Kitchen"
    assert kitchen["changes"] == {"name": {"before": "Kitchen", "after": "Main Kitchen"}}
    cabinet = by_pair[(str(ids["cabinet"]), str(ids["cabinet_2"]))]
    assert cabinet["changes"] == {
        "material": {"before": "wood", "after": "laminate"},
        "count": {"before": 6, "after": 8},
    }
    tub = by_pair[(str(ids["tub"]), str(ids["tub_2"]))]  # moved with its photo
    assert tub["changes"] == {
        "room": {
            "before": {"id": str(ids["bath"]), "name": "Bath"},
            "after": {"id": str(ids["kitchen_2"]), "name": "Main Kitchen"},
        }
    }
    asset = by_pair[(str(ids["cabinet_asset"]), str(ids["cabinet_asset_2"]))]
    assert asset["changes"] == {
        "bucket": {"before": "y5", "after": "y7"},
        "recovery_period": {"before": "5.0", "after": "7.0"},
        "method": {"before": "rule", "after": "manual"},
    }
    assert body["next_cursor"] is None
    assert [c["kind"] for c in body["changed"]] == ["room", "component", "component", "asset"]


async def test_diff_in_reverse_and_of_a_revision_with_itself(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    ids = await seeded_two_revisions(api, seed, sessionmaker)
    sid = ids["study_id"]
    forward = (await api.get("alice", f"/v1/studies/{sid}/revisions/1/diff/2")).json()

    reverse = (await api.get("alice", f"/v1/studies/{sid}/revisions/2/diff/1")).json()

    assert (reverse["from_revision"], reverse["to_revision"]) == (2, 1)
    assert reverse["added"] == forward["removed"] and reverse["removed"] == forward["added"]
    assert reverse["counts"]["room"] == {"added": 1, "removed": 0, "changed": 1, "unchanged": 0}
    kitchen = next(c for c in reverse["changed"] if c["kind"] == "room")
    assert (kitchen["from_id"], kitchen["to_id"]) == (str(ids["kitchen_2"]), str(ids["kitchen"]))
    assert kitchen["changes"] == {"name": {"before": "Main Kitchen", "after": "Kitchen"}}

    same = (await api.get("alice", f"/v1/studies/{sid}/revisions/2/diff/2")).json()
    assert same["added"] == same["removed"] == same["changed"] == []


async def test_diff_pages_the_changed_list(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    ids = await seeded_two_revisions(api, seed, sessionmaker)
    sid = ids["study_id"]
    base = f"/v1/studies/{sid}/revisions/1/diff/2"
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = "?limit=1" + (f"&cursor={cursor}" if cursor else "")
        page = (await api.get("alice", base + query)).json()
        pages.append(page)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    whole = (await api.get("alice", base)).json()
    assert [c for p in pages for c in p["changed"]] == whole["changed"]
    assert all(len(p["changed"]) == 1 for p in pages) and len(pages) == 4
    assert all(p["added"] == whole["added"] for p in pages)  # complete on every page

    foreign = await api.get(
        "alice", f"/v1/studies/{sid}/revisions/2/diff/1?cursor={pages[0]['next_cursor']}"
    )
    problem(foreign, 422, "invalid-cursor")
    problem(await api.get("alice", base + "?cursor=nope"), 422, "invalid-cursor")
    problem(await api.get("alice", base + "?limit=0"), 422)
    problem(await api.get("alice", base + "?limit=201"), 422)


async def test_diff_refuses_a_revision_the_study_does_not_have(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    ids = await seeded_two_revisions(api, seed, sessionmaker)
    sid = ids["study_id"]

    body = problem(
        await api.get("alice", f"/v1/studies/{sid}/revisions/1/diff/3"), 404, "revision-not-found"
    )

    assert body["code"] == E.STUDY_REVISION_NOT_FOUND
    problem(await api.get("alice", f"/v1/studies/{sid}/revisions/0/diff/1"), 422)
    problem(await api.get("alice", f"/v1/studies/{uuid.uuid4()}/revisions/1/diff/2"), 404)


async def test_diff_counts_a_carried_copy_as_unchanged(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """A room copied without a correction is the same room: not added, not removed."""
    ids = await seeded_two_revisions(api, seed, sessionmaker)
    sid = ids["study_id"]
    async with session_scope(sessionmaker) as session:
        await session.execute(
            update(Room).where(Room.id == ids["kitchen_2"]).values(name="Kitchen", source="vision")
        )

    body = (await api.get("alice", f"/v1/studies/{sid}/revisions/1/diff/2")).json()

    assert body["counts"]["room"] == {"added": 0, "removed": 1, "changed": 0, "unchanged": 1}
    assert [c["kind"] for c in body["changed"]] == ["component", "component", "asset"]
    async with session_scope(sessionmaker) as session:
        rooms = (await session.scalars(select(Room).where(Room.study_id == sid))).all()
    assert len(rooms) == 3  # nothing was written by the read
