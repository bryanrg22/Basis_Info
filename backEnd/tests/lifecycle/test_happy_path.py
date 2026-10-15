"""Happy path: start -> extraction and photos -> three approvals -> assets -> export -> done.

BUILD_PLAN 6.3 ``lifecycle/test_happy_path.py``: the activity sequence, the final state in
Temporal and in Postgres, the export in storage, and one idempotency row per activity.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest
from lifecycle_support import Harness
from sqlalchemy import select

from basis.db.enums import Stage, StudyStatus
from basis.db.models import Asset, Citation, Component, Extraction, PhotoRoom, Room
from basis.db.repositories.activity_result import COMPLETED
from basis.db.session import session_scope
from basis.workflow.study_workflow import workflow_id_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_three_approvals_complete_the_study(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        handle = started.handle

        status = await harness.wait_awaiting(handle, "appraisal_review", 1)
        assert status.completed_photos == 3 and status.pending_photos == 0
        assert status.flagged_photos == ()
        study = await harness.study_row(started.study_id)
        assert (study.current_stage, study.status) == (
            Stage.APPRAISAL_REVIEW,
            StudyStatus.WAITING_REVIEW,
        )

        await harness.approve_all(handle)
        result = await harness.result(handle)

    assert result.outcome == "completed"
    assert (result.stage, result.revision) == ("completed", 1)
    workflow_id = workflow_id_for(str(started.study_id))
    assert result.export_uri == f"gs://basis-test/studies/{started.study_id}/exports/rev-1.json"

    sequence = await harness.activity_sequence(handle)
    assert sequence[:3] == ["record_run_start", "transition", "list_photos"]
    assert Counter(sequence) == Counter(
        {
            "record_run_start": 1,
            "list_photos": 1,
            "ingest_appraisal": 1,
            "extract_fields": 1,
            "analyze_photo": 3,
            # extracting, appraisal_review, rooms_review, assets_processing, assets_review,
            # exporting, completed
            "transition": 7,
            "process_assets": 1,
            "export_report": 1,
            "record_run_end": 1,
        }
    )
    assert sequence.index("extract_fields") > sequence.index("ingest_appraisal")
    assert sequence.index("process_assets") > sequence.index("extract_fields")
    assert sequence[-3:] == ["export_report", "transition", "record_run_end"]

    study = await harness.study_row(started.study_id)
    assert (study.current_stage, study.status, study.revision) == (
        Stage.COMPLETED,
        StudyStatus.COMPLETED,
        1,
    )
    [run] = await harness.runs(started.study_id)
    assert (run.workflow_id, run.outcome) == (workflow_id, "completed")
    assert run.ended_at is not None

    results = await harness.activity_results(workflow_id)
    assert len(results) == len(sequence)
    assert {r.status for r in results} == {COMPLETED}
    assert len({r.idempotency_key for r in results}) == len(results)

    async with session_scope(harness.sessionmaker) as session:
        sid = started.study_id
        assert (
            len((await session.scalars(select(Extraction).where(Extraction.study_id == sid))).all())
            == 1
        )
        rooms = (await session.scalars(select(Room).where(Room.study_id == sid))).all()
        assert sorted(r.name for r in rooms) == ["Bathroom", "Kitchen"]
        assert (
            len((await session.scalars(select(PhotoRoom).where(PhotoRoom.study_id == sid))).all())
            == 3
        )
        components = (
            await session.scalars(select(Component).where(Component.study_id == sid))
        ).all()
        assert len(components) == 6
        assets = (await session.scalars(select(Asset).where(Asset.study_id == sid))).all()
        assert len(assets) == 6
        citations = (await session.scalars(select(Citation).where(Citation.study_id == sid))).all()
        # Every asset here is a rules-table hit whose citation resolved against the
        # seeded reference corpus (one verified citation each, none unverified). Review is
        # asked only for the contested plumbing rows (Treas. Reg. 1.48-1(e)(2)) and for
        # the countertops, which only cross-validation flags (a synthetic takeoff too large
        # for section 1245, and no cost row).
        labels = {c.id: c.label for c in components}
        assert {c.asset_id for c in citations} == {a.id for a in assets}
        assert all(c.verified_at is not None for c in citations)
        assert not any("citation_unverified" in (a.review_reason or "") for a in assets)
        flagged = sorted(
            (labels[a.component_id], a.review_reason or "") for a in assets if a.needs_review
        )
        assert [label for label, _ in flagged] == ["bathtub", "countertop", "countertop", "toilet"]
        assert flagged[0][1].startswith("rule_contested:bathtub")
        assert flagged[3][1].startswith("rule_contested:toilet")
        assert all(
            reason.startswith("cross:") for label, reason in flagged if label == "countertop"
        )

    exported = json.loads(await harness.deps.storage.read(result.export_uri or ""))
    assert exported["revision"] == 1 and len(exported["assets"]) == 6
    assert set(exported["checksums"]) == {
        "extraction",
        "rooms",
        "photos",
        "components",
        "assets",
        "evidence",
        "summary",
    }


async def test_status_query_reports_the_gate_and_the_budget(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start(budget_usd=1.0)
        status = await harness.wait_awaiting(started.handle, "appraisal_review", 1)
        assert status.waiting_since is not None
        assert status.decisions_applied == ()
        assert status.budget_remaining_usd == pytest.approx(1.0 - 3 * 0.01)
        assert status.stalled is False

        ack = await harness.approve(started.handle, "appraisal_review")
        status = await harness.wait_awaiting(started.handle, "rooms_review", 1)
        assert ack.accepted and ack.applied_revision == 1
        assert len(status.decisions_applied) == 1


async def test_starting_twice_returns_the_same_execution(harness: Harness) -> None:
    async with harness.worker():
        first = await harness.start()
        second = await harness.start(first.study)
        assert second.handle.id == first.handle.id
        first_run = (await first.handle.describe()).run_id
        assert (await second.handle.describe()).run_id == first_run
        await harness.wait_awaiting(first.handle, "appraisal_review", 1)
