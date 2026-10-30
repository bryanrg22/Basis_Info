"""The firm's conventions on real runs of the workflow (patch ``firm-conventions-v1``).

docs/review_2026-10-15/CLASSIFICATION_CONVENTIONS.md section 5:

* an engineer's asset correction, recorded and approved as the API records them, becomes
  the firm's convention when the assets review is approved (``promote_conventions`` runs
  between the approval and the export), and the firm's next study is classified from it:
  the same component gets the engineer's bucket from the convention, with its provenance,
  and that study's approval adds an approval to the convention;
* the hierarchy of section 9: in the firm's next study the engineer reads the component's
  material differently at the components gate, so its context key is new; the ruling still
  serves it (``matched_tier`` ``label_room`` on the asset and in the export), and the
  approval counts on the ruling that served;
* a ``promote_conventions`` that fails does not fail the study: the workflow logs it with
  BASIS-ASSETS-012 and exports.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from lifecycle_support import Harness
from lifecycletest_support import SeededStudy
from sqlalchemy import select, update
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.exceptions import ApplicationError

from basis.classify.cache import cache_key
from basis.db.enums import Bucket, ClassificationMethod, Stage, StudyStatus
from basis.db.enums import Decision as DecisionKind
from basis.db.models import Asset, ClassificationCacheEntry, Component, Room, Study, StudyEvent
from basis.db.repositories import CorrectionInput as RepoCorrection
from basis.db.repositories import ReviewRepository
from basis.db.session import session_scope
from basis.workflow.contracts import (
    CorrectionInput,
    Decision,
    PromoteConventionsInput,
    PromoteConventionsOutput,
    ReviewStage,
)
from basis.workflow.study_workflow import StudyWorkflow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

REASON = "the firm treats it as building"


async def decide_assets_like_the_api(
    harness: Harness,
    handle: Any,
    study: SeededStudy,
    revision: int,
    decision: str,
    corrections: tuple[RepoCorrection, ...] = (),
    stage: ReviewStage = "assets_review",
) -> None:
    """Record the decision as ``POST .../decisions`` does (the review row the promotion
    requires, the corrections), then deliver it as the relay does."""
    await harness.wait_awaiting(handle, stage, revision)
    async with session_scope(harness.sessionmaker) as session:
        record = await ReviewRepository(session).record_decision(
            study_id=study.study_id,
            stage=Stage(stage),
            revision=revision,
            decision=DecisionKind(decision),
            reviewer_id=study.owner_id,
            idempotency_key=str(uuid.uuid4()),
            comment=None,
            corrections=corrections,
        )
        review_id, submitted = record.review.id, record.review.created_at
    delivered = Decision(
        review_id=str(review_id),
        stage=stage,
        revision=revision,
        decision=decision,  # type: ignore[arg-type]
        reviewer_id=str(study.owner_id),
        corrections=tuple(
            CorrectionInput(
                entity_type=c.entity_type,
                entity_id=str(c.entity_id),
                after=c.after,
                reason=c.reason,
            )
            for c in corrections
        ),
        submitted_at=submitted,
    )
    await handle.execute_update(StudyWorkflow.decide, delivered, id=f"decision-{review_id}")


async def labelled_assets(
    harness: Harness, study_id: uuid.UUID, revision: int
) -> list[tuple[Component, Room | None, Asset]]:
    async with session_scope(harness.sessionmaker) as session:
        rows = await session.execute(
            select(Component, Room, Asset)
            .join(Asset, Asset.component_id == Component.id)
            .outerjoin(Room, Room.id == Component.room_id)
            .where(Asset.study_id == study_id, Asset.revision == revision)
            .order_by(Component.label, Asset.id)
        )
        return [(c, r, a) for c, r, a in rows.all()]


def key_of(component: Component, room: Room | None) -> str:
    return cache_key(
        component.label,
        component.material,
        component.attachment,
        "residential_rental",
        room.room_type if room else None,
    )


async def test_an_assets_correction_classifies_the_firms_next_study(harness: Harness) -> None:
    first = await harness.seed()
    async with harness.worker():
        started = await harness.start(first)
        handle = started.handle
        for stage in ("appraisal_review", "rooms_review"):
            await harness.approve(handle, stage)  # type: ignore[arg-type]
        await harness.approve(handle, "components_review")
        await harness.wait_awaiting(handle, "assets_review", 1)
        # The engineer rules a 5-year asset building, with a reason.
        found = await labelled_assets(harness, first.study_id, 1)
        component, room, target = next(
            (c, r, a) for c, r, a in found if a.bucket is Bucket.Y5 and r is not None
        )
        after = {"bucket": "y27_5", "recovery_period": "27.5", "irs_section": "1250"}
        await decide_assets_like_the_api(
            harness,
            handle,
            first,
            1,
            "reject",
            (RepoCorrection("asset", target.id, after, before={"bucket": "y5"}, reason=REASON),),
        )
        await decide_assets_like_the_api(harness, handle, first, 2, "approve")
        result = await harness.result(handle)
    assert (result.outcome, result.revision) == ("completed", 2)
    sequence = await harness.activity_sequence(handle)
    assert sequence.index("promote_conventions") < sequence.index("export_report")

    key = key_of(component, room)
    async with session_scope(harness.sessionmaker) as session:
        rows = (await session.scalars(select(ClassificationCacheEntry))).all()
        promoted = (
            await session.scalars(
                select(StudyEvent).where(
                    StudyEvent.study_id == first.study_id,
                    StudyEvent.event_type == "convention.promoted",
                )
            )
        ).all()
    assert [(r.org_id, r.key, r.result["bucket"]) for r in rows] == [(first.org_id, key, "y27_5")]
    [row] = rows
    assert (row.study_id, row.reviewer_id, row.approvals) == (first.study_id, first.owner_id, 1)
    assert row.result["rationale"] == REASON
    assert [e.detail["key"] for e in promoted] == [key]

    # The firm's next study (the same photos, so the same component in the same room).
    second = await harness.seed()
    async with session_scope(harness.sessionmaker) as session:
        await session.execute(
            update(Study).where(Study.id == second.study_id).values(org_id=first.org_id)
        )
    async with harness.worker():
        started = await harness.start(second)
        for stage in ("appraisal_review", "rooms_review"):
            await harness.approve(started.handle, stage)  # type: ignore[arg-type]
        # At the components gate the engineer reads one of the matching components'
        # material differently, so its context key is one the firm never ruled on
        # (CLASSIFICATION_CONVENTIONS.md section 9); the others keep the exact key.
        await harness.wait_awaiting(started.handle, "components_review", 1)
        async with session_scope(harness.sessionmaker) as session:
            pairs = await session.execute(
                select(Component, Room)
                .outerjoin(Room, Room.id == Component.room_id)
                .where(Component.study_id == second.study_id, Component.revision == 1)
            )
            reread = next(c for c, r in pairs.all() if key_of(c, r) == key)
        new_material = "metal" if reread.material == "laminate" else "laminate"
        await decide_assets_like_the_api(
            harness,
            started.handle,
            second,
            1,
            "reject",
            (
                RepoCorrection(
                    "component",
                    reread.id,
                    {"material": new_material},
                    before={"material": reread.material},
                ),
            ),
            stage="components_review",
        )
        await decide_assets_like_the_api(
            harness, started.handle, second, 2, "approve", stage="components_review"
        )
        await decide_assets_like_the_api(harness, started.handle, second, 2, "approve")
        second_result = await harness.result(started.handle)
        assert (second_result.outcome, second_result.revision) == ("completed", 2)
    labelled = await labelled_assets(harness, second.study_id, 2)
    exact = [a for c, r, a in labelled if key_of(c, r) == key]
    [reread_asset] = [
        a for c, r, a in labelled if c.material == new_material and c.label == component.label
    ]
    assert exact and all(
        (a.method, a.bucket) == (ClassificationMethod.CACHE, Bucket.Y27_5) for a in exact
    )
    assert all(a.convention is not None and a.convention["key"] == key for a in exact)
    assert all(a.convention["matched_tier"] == "exact" for a in exact)  # type: ignore[index]
    assert all(a.convention["study_id"] == str(first.study_id) for a in exact)  # type: ignore[index]
    # The re-read component: served by the same ruling at the label-and-room tier.
    assert (reread_asset.method, reread_asset.bucket) == (ClassificationMethod.CACHE, Bucket.Y27_5)
    assert reread_asset.convention is not None
    assert (reread_asset.convention["key"], reread_asset.convention["matched_tier"]) == (
        key,
        "label_room",
    )
    exported = json.loads(await harness.deps.storage.read(second_result.export_uri or ""))
    by_id = {a["id"]: a for a in exported["assets"]}
    assert by_id[str(reread_asset.id)]["convention"]["matched_tier"] == "label_room"
    assert by_id[str(exact[0].id)]["convention"]["matched_tier"] == "exact"
    # The second study's approval let the convention stand (at both tiers, one approval
    # per study): one more approval of it, and still one row.
    async with session_scope(harness.sessionmaker) as session:
        [row] = (await session.scalars(select(ClassificationCacheEntry))).all()
    assert (row.approvals, row.study_id) == (2, first.study_id)


class FailingPromotion:
    @activity.defn(name="promote_conventions")
    async def run(self, input: PromoteConventionsInput) -> PromoteConventionsOutput:
        raise ApplicationError(
            "promote_conventions: database unavailable [BASIS-ASSETS-012]",
            type="ValidationError",
            non_retryable=True,
        )


async def test_a_failed_promotion_does_not_fail_the_study(harness: Harness) -> None:
    async with harness.worker(promote_conventions=FailingPromotion().run):
        started = await harness.start()
        await harness.approve_all(started.handle)
        result = await harness.result(started.handle)
        history = (await started.handle.fetch_history()).events
    assert result.outcome == "completed" and result.export_uri is not None
    assert await harness.deps.storage.read(result.export_uri)
    study = await harness.study_row(started.study_id)
    assert (study.current_stage, study.status, study.failure_code) == (
        Stage.COMPLETED,
        StudyStatus.COMPLETED,
        None,
    )
    scheduled = {
        e.event_id: e.activity_task_scheduled_event_attributes.activity_type.name
        for e in history
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    }
    failed = [
        scheduled[e.activity_task_failed_event_attributes.scheduled_event_id]
        for e in history
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED
    ]
    assert failed == ["promote_conventions"]
    sequence = await harness.activity_sequence(started.handle)
    assert sequence[-5:] == [
        "promote_conventions",
        "transition",
        "export_report",
        "transition",
        "record_run_end",
    ]
