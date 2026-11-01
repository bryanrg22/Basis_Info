"""Reopening a completed study and restoring a revision, on the Temporal test server
(docs/api/REVISIONS.md).

A study is completed and its revision signed; then it is reopened at each gate the way the
API does it (``Harness.resume``: the row moved under its lock, a new execution started under
the study's id over the completed one). The second execution runs no intake: it copies the
revision (``carry_revision``), opens the gate, and reruns exactly the steps after it; the
first revision's export bytes and signature row are untouched and the new revision gets its
own export. The restore round trip: revision 1 restored after a rename in revision 3 gives
revision 4 with revision 1's rooms (the diff 1 -> 4 has no change), and the history lists
every revision with what made it. Two executions started at once over the completed one:
Temporal admits exactly one.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from datetime import UTC, datetime

import pytest
from lifecycle_support import Harness, Started
from lifecycletest_support import SeededStudy
from sqlalchemy import select
from temporalio.client import WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from test_timeline_audit import decide_like_the_api

from basis.db.enums import Stage, StudyStatus
from basis.db.models import Asset, Component, PhotoRoom, ReportSignature, Room, StudyEvent
from basis.db.repositories import CorrectionInput as RepoCorrection
from basis.db.repositories.report import ReportRepository
from basis.db.repositories.revision import RevisionRepository
from basis.db.session import session_scope
from basis.workflow.contracts import ResumeInput, ReviewStage
from basis.workflow.study_workflow import StudyWorkflow, workflow_id_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

GATES: tuple[ReviewStage, ...] = (
    "appraisal_review",
    "rooms_review",
    "components_review",
    "assets_review",
)
# The activities of the second execution after ``record_run_start``, ``carry_revision`` and
# the gate's transition, per gate reopened; every gate ends with the same tail.
TAIL = ["promote_conventions", "transition", "export_report", "transition", "record_run_end"]
AFTER_GATE: dict[ReviewStage, list[str]] = {
    "appraisal_review": [
        "transition",  # rooms_review
        "transition",  # components_processing
        "reconcile_rooms",  # the room step (a no-op here: ROOM_RECONCILE off)
        "transition",  # components_review
        "transition",  # assets_processing
        "process_assets",
        "transition",  # assets_review
    ],
    "rooms_review": [
        "transition",
        "reconcile_rooms",
        "transition",
        "transition",
        "process_assets",
        "transition",
    ],
    "components_review": ["transition", "process_assets", "transition"],
    "assets_review": [],
}


async def complete(harness: Harness) -> Started:
    started = await harness.start()
    await harness.approve_all(started.handle)
    result = await harness.result(started.handle)
    assert result.outcome == "completed" and result.revision == 1
    return started


async def sign(harness: Harness, study: SeededStudy, revision: int) -> ReportSignature:
    """A signature row for the revision (synthetic values; the route's checks are its own)."""
    async with session_scope(harness.sessionmaker) as session:
        repo = ReportRepository(session)
        await repo.lock_study(study.study_id)
        return await repo.add_signature(
            study.study_id,
            revision,
            signature_id=uuid.uuid4(),
            signed_by=study.owner_id,
            signed_at=datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
            engineer={"name": "A. Engineer"},
            statement_version=1,
            export_sha256="a" * 64,
            reviewed_sha256="b" * 64,
            pdf_uri=f"gs://basis-test/studies/{study.study_id}/reports/rev-{revision}.pdf",
            pdf_sha256="c" * 64,
        )


async def export_bytes(harness: Harness, study_id: uuid.UUID, revision: int) -> bytes:
    return await harness.deps.storage.read(
        f"gs://basis-test/studies/{study_id}/exports/rev-{revision}.json"
    )


async def rows(harness: Harness, model: type, study_id: uuid.UUID, revision: int) -> list:  # type: ignore[type-arg]
    async with session_scope(harness.sessionmaker) as session:
        stmt = select(model).where(model.study_id == study_id, model.revision == revision)
        return list((await session.scalars(stmt)).all())


async def events(harness: Harness, study_id: uuid.UUID, *types: str) -> list[StudyEvent]:
    async with session_scope(harness.sessionmaker) as session:
        stmt = (
            select(StudyEvent)
            .where(StudyEvent.study_id == study_id, StudyEvent.event_type.in_(types))
            .order_by(StudyEvent.seq)
        )
        return list((await session.scalars(stmt)).all())


async def approve_from(
    harness: Harness, handle: WorkflowHandle, stage: ReviewStage, revision: int
) -> None:  # type: ignore[type-arg]
    """Approve every gate from ``stage`` on at ``revision``."""
    for gate in GATES[GATES.index(stage) :]:
        await harness.approve(handle, gate, revision)


@pytest.mark.parametrize("stage", GATES)
async def test_reopen_at_a_gate_reruns_only_what_follows_it(
    harness: Harness, stage: ReviewStage
) -> None:
    async with harness.worker():
        started = await complete(harness)
        sid = started.study_id
        signature = await sign(harness, started.study, 1)
        first_export = await export_bytes(harness, sid, 1)
        before = {
            model: len(await rows(harness, model, sid, 1))
            for model in (Room, PhotoRoom, Component, Asset)
        }

        resumed = await harness.resume(started.study, stage)
        status = await harness.wait_awaiting(resumed.handle, stage, 2)
        assert status.revision == 2 and status.awaiting == stage
        study = await harness.study_row(sid)
        assert (study.current_stage.value, study.status, study.revision) == (
            stage,
            StudyStatus.WAITING_REVIEW,
            2,
        )
        # The copy: everything the gate shows, the assets only at the assets review.
        copied = {model: len(await rows(harness, model, sid, 2)) for model in before}
        assert copied[Room] == before[Room] and copied[PhotoRoom] == before[PhotoRoom]
        assert copied[Component] == before[Component]
        assert copied[Asset] == (before[Asset] if stage == "assets_review" else 0)
        assert all(r.carried_from is not None for r in await rows(harness, Room, sid, 2))

        await approve_from(harness, resumed.handle, stage, 2)
        result = await harness.result(resumed.handle)

    assert result.outcome == "completed" and result.revision == 2
    assert result.export_uri == f"gs://basis-test/studies/{sid}/exports/rev-2.json"
    sequence = await harness.activity_sequence(resumed.handle)
    assert sequence == [
        "record_run_start",
        "carry_revision",
        "transition",
        *AFTER_GATE[stage],
        *TAIL,
    ]
    assert "ingest_appraisal" not in sequence and "analyze_photo" not in sequence
    # The first revision is exactly as it was: its export bytes, its signature, its rows.
    assert await export_bytes(harness, sid, 1) == first_export
    async with session_scope(harness.sessionmaker) as session:
        kept = await ReportRepository(session).signature(sid, 1)
        assert kept is not None and (kept.id, kept.pdf_sha256) == (signature.id, "c" * 64)
        assert await ReportRepository(session).signature(sid, 2) is None  # its own, unsigned
    assert {model: len(await rows(harness, model, sid, 1)) for model in before} == before
    second_export = await export_bytes(harness, sid, 2)
    assert second_export and second_export != first_export
    assert len(await rows(harness, Asset, sid, 2)) == before[Asset]
    study = await harness.study_row(sid)
    assert (study.current_stage, study.status, study.revision) == (
        Stage.COMPLETED,
        StudyStatus.COMPLETED,
        2,
    )
    runs = sorted(await harness.runs(sid), key=lambda r: r.started_at)
    assert [r.outcome for r in runs] == ["completed", "completed"]
    assert runs[1].input_revision == 2 and runs[0].workflow_id == runs[1].workflow_id
    reopened = await events(harness, sid, "study.reopened")
    assert len(reopened) == 1 and reopened[0].stage is Stage(stage) and reopened[0].revision == 2
    carried = await events(harness, sid, "revision.carried")
    assert [e.revision for e in carried] == [2]
    assert carried[0].detail["through"] == stage and carried[0].detail["reason"] == "reopen"
    opened = await events(harness, sid, "gate.opened")
    assert [(e.stage.value, e.revision) for e in opened if e.revision == 2][0] == (stage, 2)
    exported = await events(harness, sid, "export.written")
    assert [e.revision for e in exported] == [1, 2]


async def test_reopen_then_reject_then_restore_round_trip(harness: Harness) -> None:
    async with harness.worker():
        started = await complete(harness)
        sid = started.study_id
        await sign(harness, started.study, 1)
        names_1 = sorted(r.name for r in await rows(harness, Room, sid, 1))

        # Revision 2: reopened at the rooms review; rejected with a rename -> revision 3.
        reopened = await harness.resume(started.study, "rooms_review")
        await harness.wait_awaiting(reopened.handle, "rooms_review", 2)
        kitchen = next(r for r in await rows(harness, Room, sid, 2) if r.name == "Kitchen")
        # Recorded as the API records it (the review, the correction, the events), so the
        # history can say who made revision 3 and with what.
        await decide_like_the_api(
            harness,
            reopened.handle,
            started.study,
            "rooms_review",
            2,
            "reject",
            (RepoCorrection("room", kitchen.id, {"name": "Galley"}, before={"name": "Kitchen"}),),
        )
        await approve_from(harness, reopened.handle, "rooms_review", 3)
        third = await harness.result(reopened.handle)
        assert third.revision == 3
        assert sorted(r.name for r in await rows(harness, Room, sid, 3)) == ["Bathroom", "Galley"]

        # Revision 4: revision 1 restored; it completed, so it opens at the assets review.
        restored = await harness.resume(started.study, "assets_review", source_revision=1)
        status = await harness.wait_awaiting(restored.handle, "assets_review", 4)
        assert status.revision == 4
        assert sorted(r.name for r in await rows(harness, Room, sid, 4)) == names_1
        await harness.approve(restored.handle, "assets_review", 4)
        fourth = await harness.result(restored.handle)

    assert fourth.outcome == "completed" and fourth.revision == 4
    sequence = await harness.activity_sequence(restored.handle)
    assert sequence == ["record_run_start", "carry_revision", "transition", *TAIL]
    async with session_scope(harness.sessionmaker) as session:
        study = await harness.study_row(sid)
        repo = RevisionRepository(session)
        diff = await repo.diff(sid, 1, 4)
        assert (diff.added, diff.removed, diff.changed) == ([], [], [])
        assert diff.unchanged["room"] == 2 and diff.unchanged["asset"] > 0
        back = await repo.diff(sid, 3, 4)
        assert [
            (c.kind, c.changes["name"].before, c.changes["name"].after)
            for c in back.changed
            if c.kind == "room"
        ] == [("room", "Galley", "Kitchen")]
        history = await repo.history(study)
    assert [(i.revision, i.origin.kind, i.origin.stage) for i in history] == [
        (1, "start", None),
        (2, "reopen", Stage.ROOMS_REVIEW),
        (3, "rejection", Stage.ROOMS_REVIEW),
        (4, "restore", Stage.ASSETS_REVIEW),
    ]
    assert history[1].origin.from_revision == 1 and history[3].origin.from_revision == 1
    assert history[2].corrections == {"room": 1}
    assert history[0].signature is not None and history[0].export is not None
    assert history[1].export is None and history[2].export is not None
    assert history[3].export is not None and history[3].current
    assert [i.stage_reached for i in history] == [
        Stage.COMPLETED,
        Stage.ROOMS_REVIEW,
        Stage.COMPLETED,
        Stage.COMPLETED,
    ]
    assert all(i.started_at is not None for i in history)
    assert [i.ended_at is None for i in history] == [False, False, False, True]
    # Every revision's export is still in storage, byte for byte distinct.
    exports = {n: await export_bytes(harness, sid, n) for n in (1, 3, 4)}
    assert len(set(exports.values())) == 3


async def test_two_starts_over_the_completed_execution_admit_exactly_one(
    harness: Harness,
) -> None:
    """What makes two concurrent reopens safe: with id reuse ``ALLOW_DUPLICATE`` and conflict
    ``FAIL``, Temporal admits one new execution under the completed study's id and refuses
    the other (``WorkflowAlreadyStartedError``), whichever arrives first."""
    async with harness.worker():
        started = await complete(harness)
    study = started.study
    resume = ResumeInput(stage="assets_review", from_revision=1, reason="reopen")

    async def start() -> WorkflowHandle[StudyWorkflow, object] | WorkflowAlreadyStartedError:
        try:
            return await harness.client.start_workflow(
                StudyWorkflow.run,
                harness.workflow_input(study, input_revision=2, resume=resume),
                id=workflow_id_for(str(study.study_id)),
                task_queue=harness.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            )
        except WorkflowAlreadyStartedError as exc:
            return exc

    outcomes = await asyncio.gather(start(), start())

    admitted = [o for o in outcomes if not isinstance(o, WorkflowAlreadyStartedError)]
    refused = [o for o in outcomes if isinstance(o, WorkflowAlreadyStartedError)]
    assert len(admitted) == 1 and len(refused) == 1
    harness.handles.extend(admitted)
    described = await admitted[0].describe()
    assert described.run_id != started.handle.result_run_id
    assert Counter(type(o).__name__ for o in outcomes)[WorkflowAlreadyStartedError.__name__] == 1
