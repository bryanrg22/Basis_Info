"""Rejections at each gate: corrections applied, outputs recomputed, the gate asked again.

BUILD_PLAN 6.3 ``lifecycle/test_reject_paths.py`` and the W3 regression ("a rejection is
never recorded as an approval"): after a reject the workflow never runs the approve
transition for that revision, the study moves to the reject target with revision + 1, the
rejected revision's rows are untouched, and the same gate waits again at the new revision.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lifecycle_support import Harness
from sqlalchemy import select
from temporalio.client import WorkflowHandle

from basis.db.enums import Bucket, ClassificationMethod, Stage, StudyStatus
from basis.db.models import Asset, Component, Extraction, ExtractionField, PhotoRoom, Room
from basis.db.session import session_scope
from basis.workflow.contracts import CorrectionInput

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def scheduled_activity_ids(handle: WorkflowHandle[Any, Any]) -> list[str]:
    history = await handle.fetch_history()
    return [
        e.activity_task_scheduled_event_attributes.activity_id
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    ]


async def rows(harness: Harness, model: Any, study_id: uuid.UUID, **where: Any) -> list[Any]:
    async with session_scope(harness.sessionmaker) as session:
        stmt = select(model).where(model.study_id == study_id)
        for name, value in where.items():
            stmt = stmt.where(getattr(model, name) == value)
        return list((await session.scalars(stmt)).all())


async def test_appraisal_reject_reextracts_with_corrections(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        [field] = await rows(harness, ExtractionField, sid, field="property_address")
        correction = CorrectionInput(
            entity_type="extraction_field",
            entity_id=str(field.id),
            after={"value_text": "12 Main Street"},
            reason="typo",
        )
        ack = await harness.decide(
            handle,
            harness.decision("appraisal_review", 1, "reject", corrections=[correction]),
        )
        assert ack.accepted and ack.applied_revision == 1

        status = await harness.wait_awaiting(handle, "appraisal_review", 2)
        assert status.revision == 2
        study = await harness.study_row(sid)
        assert (study.current_stage, study.revision, study.status) == (
            Stage.APPRAISAL_REVIEW,
            2,
            StudyStatus.WAITING_REVIEW,
        )
        ids = await scheduled_activity_ids(handle)
        assert "transition:rooms_review:r1" not in ids  # never treated as an approval
        assert "transition:extracting:r2" in ids
        assert "apply_corrections:appraisal_review:r1" in ids
        assert "extract_fields:r2" in ids

        [old] = await rows(harness, Extraction, sid, revision=1)
        [new] = await rows(harness, Extraction, sid, revision=2)
        old_fields = {
            f.field: f
            for f in await rows(harness, ExtractionField, sid)
            if f.extraction_id == old.id
        }
        new_fields = {
            f.field: f
            for f in await rows(harness, ExtractionField, sid)
            if f.extraction_id == new.id
        }
        assert old_fields["property_address"].value_text == "1290 Example Way"
        assert old_fields["property_address"].source == "azure"
        assert new_fields["property_address"].value_text == "12 Main Street"
        assert new_fields["property_address"].source == "manual"
        assert len(new_fields) == len(old_fields)
        # Photo outputs were carried into the new revision unchanged.
        assert len(await rows(harness, PhotoRoom, sid, revision=2)) == 3
        assert len(await rows(harness, Component, sid, revision=2)) == 6

        await harness.approve_all(handle, revision=2)
        result = await harness.result(handle)
    assert (result.outcome, result.revision) == ("completed", 2)
    assert len(await rows(harness, Asset, sid, revision=2)) == 6
    assert await rows(harness, Asset, sid, revision=1) == []


async def test_rooms_reject_renames_rooms_and_reanalyzes_only_flagged_photos(
    harness: Harness,
) -> None:
    """The single rooms review (an execution without the components gate). With the gate
    the same corrections split over the two reviews: ``test_components_gate.py``."""
    async with harness.worker():
        started = await harness.start(components_gate=False)
        handle, sid = started.handle, started.study_id
        await harness.approve(handle, "appraisal_review")
        await harness.wait_awaiting(handle, "rooms_review", 1)

        rooms_r1 = {r.name: r for r in await rows(harness, Room, sid, revision=1)}
        flagged_photo = started.study.photo_ids[2]  # bathroom_1.jpg
        corrections = [
            CorrectionInput(
                entity_type="room",
                entity_id=str(rooms_r1["Kitchen"].id),
                after={"name": "Main Kitchen", "floor": "1"},
            ),
            CorrectionInput(entity_type="photo", entity_id=str(flagged_photo), after={}),
        ]
        await harness.decide(
            handle, harness.decision("rooms_review", 1, "reject", corrections=corrections)
        )
        await harness.wait_awaiting(handle, "rooms_review", 2)

        ids = await scheduled_activity_ids(handle)
        assert "transition:assets_processing:r1" not in ids
        reanalyzed = [i for i in ids if i.startswith("analyze_photo:") and i.endswith(":r2")]
        assert reanalyzed == [f"analyze_photo:{flagged_photo}:r2"]

        rooms_r2 = {r.name: r for r in await rows(harness, Room, sid, revision=2)}
        assert set(rooms_r2) == {"Main Kitchen", "Bathroom"}
        assert rooms_r2["Main Kitchen"].floor == "1"
        assert rooms_r2["Main Kitchen"].source == "manual"
        assert {r.name for r in await rows(harness, Room, sid, revision=1)} == {
            "Kitchen",
            "Bathroom",
        }
        photo_rooms = await rows(harness, PhotoRoom, sid, revision=2)
        assert len(photo_rooms) == 3
        assert len(await rows(harness, Component, sid, revision=2)) == 6
        study = await harness.study_row(sid)
        assert (study.current_stage, study.revision) == (Stage.ROOMS_REVIEW, 2)

        await harness.approve(handle, "rooms_review", 2)
        await harness.approve(handle, "assets_review", 2)
        result = await harness.result(handle)
    assert (result.outcome, result.revision) == ("completed", 2)


async def test_assets_reject_reprocesses_with_the_corrected_bucket(harness: Harness) -> None:
    async with harness.worker():
        # The living room's unlisted item is a residual: the scripted model calls it a
        # building component (y27_5), the guess the engineer corrects below.
        study = await harness.seed(photos=("kitchen_1.jpg", "living_1.jpg", "bathroom_1.jpg"))
        started = await harness.start(study)
        handle, sid = started.handle, started.study_id
        await harness.approve(handle, "appraisal_review")
        await harness.approve(handle, "rooms_review")
        await harness.approve(handle, "components_review")
        await harness.wait_awaiting(handle, "assets_review", 1)

        assets_r1 = await rows(harness, Asset, sid, revision=1)
        target = next(a for a in assets_r1 if a.bucket is Bucket.Y27_5)  # a building guess
        correction = CorrectionInput(
            entity_type="asset",
            entity_id=str(target.id),
            after={"bucket": "y5", "recovery_period": "5.0", "irs_section": "1245"},
            reason="decorative, not structural",
        )
        await harness.decide(
            handle, harness.decision("assets_review", 1, "reject", corrections=[correction])
        )
        await harness.wait_awaiting(handle, "assets_review", 2)
        ids = await scheduled_activity_ids(handle)
        assert "transition:exporting:r1" not in ids
        assert "process_assets:r2" in ids

        assets_r2 = await rows(harness, Asset, sid, revision=2)
        assert len(assets_r2) == len(assets_r1) == 7
        manual = [a for a in assets_r2 if a.method is ClassificationMethod.MANUAL]
        assert len(manual) == 1
        assert (manual[0].bucket, str(manual[0].recovery_period)) == (Bucket.Y5, "5.0")
        # The corrected asset is the one for the same (carried) component.
        components_r2 = {c.id: c for c in await rows(harness, Component, sid, revision=2)}
        carried = components_r2[manual[0].component_id]
        assert carried.raw["carried_from"] == str(target.component_id)
        # Revision 1 is history: unchanged.
        assert {a.id: a.bucket for a in await rows(harness, Asset, sid, revision=1)} == {
            a.id: a.bucket for a in assets_r1
        }

        await harness.approve(handle, "assets_review", 2)
        result = await harness.result(handle)
    assert (result.outcome, result.revision) == ("completed", 2)


async def test_two_rejections_in_a_row_keep_counting_revisions(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        handle = started.handle
        for revision in (1, 2):
            await harness.wait_awaiting(handle, "appraisal_review", revision)
            await harness.decide(handle, harness.decision("appraisal_review", revision, "reject"))
        status = await harness.wait_awaiting(handle, "appraisal_review", 3)
        assert len(status.decisions_applied) == 2
        study = await harness.study_row(started.study_id)
        assert (study.current_stage, study.revision) == (Stage.APPRAISAL_REVIEW, 3)


async def test_refused_corrections_start_the_revision_uncorrected_and_flag_the_stage(
    harness: Harness,
) -> None:
    """A correction target the database does not know must not fail the study."""
    async with harness.worker():
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        ghost = CorrectionInput(
            entity_type="extraction_field", entity_id=str(uuid.uuid4()), after={"value_text": "x"}
        )
        await harness.decide(
            handle, harness.decision("appraisal_review", 1, "reject", corrections=[ghost])
        )
        status = await harness.wait_awaiting(handle, "appraisal_review", 2)
        assert status.needs_review_stages == ("appraisal_review",)
        ids = await scheduled_activity_ids(handle)
        assert "apply_corrections:appraisal_review:r1" in ids
        assert "apply_corrections:appraisal_review:r1:uncorrected" in ids
        [new] = await rows(harness, Extraction, sid, revision=2)
        fields = [f for f in await rows(harness, ExtractionField, sid) if f.extraction_id == new.id]
        assert fields and "manual" not in {f.source for f in fields}  # without the correction
        await harness.approve_all(handle, revision=2)
        result = await harness.result(handle)
    assert result.outcome == "completed"
