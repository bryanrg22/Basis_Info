"""``process_assets`` (stub): asset, takeoff, cost and citation per component."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lifecycletest_support import SeededStudy, count_rows
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.ingest_appraisal import IngestAppraisal
from basis.activities.process_assets import ProcessAssets
from basis.activities.stub_rules import COMPONENT_COST_USD
from basis.db.enums import Bucket, ClassificationMethod
from basis.db.models import Asset, Citation, Component, CostEstimate, Takeoff
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.workflow.contracts import (
    AnalyzePhotoInput,
    ApplyCorrectionsInput,
    CorrectionInput,
    IngestAppraisalInput,
    ProcessAssetsInput,
)

from .conftest import Env

pytestmark = pytest.mark.integration


@pytest.fixture
async def analyzed(deps: Deps, seeded: SeededStudy, activity_env: Env) -> SeededStudy:
    await activity_env.run(
        IngestAppraisal(deps).run,
        IngestAppraisalInput(study_id=str(seeded.study_id), revision=1, idempotency_key="k-i"),
    )
    for photo in seeded.photo_ids:
        await activity_env.run(
            AnalyzePhoto(deps).run,
            AnalyzePhotoInput(
                study_id=str(seeded.study_id),
                document_id=str(photo),
                revision=1,
                idempotency_key=f"k-p-{photo}",
                budget_remaining_usd=1.0,
            ),
        )
    activity_env.heartbeats.clear()
    return seeded


def process(
    seeded: SeededStudy,
    revision: int = 1,
    budget: float = 1.0,
    corrections: tuple[CorrectionInput, ...] = (),
    key: str | None = None,
) -> ProcessAssetsInput:
    return ProcessAssetsInput(
        study_id=str(seeded.study_id),
        revision=revision,
        idempotency_key=key or f"k-assets-{revision}",
        budget_remaining_usd=budget,
        corrections=corrections,
    )


async def assets(deps: Deps, study_id: uuid.UUID, revision: int) -> list[Asset]:
    async with session_scope(deps.sessionmaker) as session:
        stmt = select(Asset).where(Asset.study_id == study_id, Asset.revision == revision)
        return list((await session.scalars(stmt)).all())


async def test_every_component_gets_asset_takeoff_cost_and_citation(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    assert output.assets == 6 and output.needs_review == 0
    assert output.cost_usd == pytest.approx(6 * COMPONENT_COST_USD)
    rows = await assets(deps, analyzed.study_id, 1)
    assert {a.method for a in rows} == {ClassificationMethod.RULE}
    assert len({a.component_id for a in rows}) == 6
    for model in (Takeoff, CostEstimate, Citation):
        assert await count_rows(deps.sessionmaker, model, study_id=analyzed.study_id) == 6
    assert len(activity_env.heartbeats) == 6


async def test_unknown_label_is_flagged_for_review(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    async with session_scope(deps.sessionmaker) as session:
        await RoomRepository(session).add_component(
            study_id=analyzed.study_id,
            revision=1,
            document_id=None,
            room_id=None,
            label="mystery_box",
            material=None,
            attachment=None,
            confidence=0.2,
        )
    output = await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    assert (output.assets, output.needs_review) == (7, 1)
    [flagged] = [a for a in await assets(deps, analyzed.study_id, 1) if a.needs_review]
    assert flagged.review_reason and "mystery_box" in flagged.review_reason


async def test_budget_too_small_writes_nothing(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ProcessAssets(deps).run, process(analyzed, budget=0.005))
    assert caught.value.type == "BudgetExceededError" and caught.value.non_retryable
    assert await count_rows(deps.sessionmaker, Asset, study_id=analyzed.study_id) == 0


async def test_replay_and_reset_do_not_duplicate(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    activity = ProcessAssets(deps)
    first = await activity_env.run(activity.run, process(analyzed))
    assert await activity_env.run(activity.run, process(analyzed)) == first
    reset = await activity_env.run(activity.run, process(analyzed, key="k-reset"))
    assert reset.assets == 6
    assert await count_rows(deps.sessionmaker, Asset, study_id=analyzed.study_id) == 6


async def test_asset_correction_applies_to_the_carried_component(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    sid = analyzed.study_id
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    target = next(a for a in await assets(deps, sid, 1) if a.bucket is Bucket.Y39)
    await activity_env.run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(sid),
            stage="assets_review",
            from_revision=1,
            to_revision=2,
            corrections=(),
        ),
    )
    correction = CorrectionInput(
        entity_type="asset",
        entity_id=str(target.id),
        after={"bucket": "y15", "recovery_period": "15", "category": "Land improvement"},
    )
    output = await activity_env.run(
        ProcessAssets(deps).run, process(analyzed, revision=2, corrections=(correction,))
    )
    assert output.assets == 6
    [manual] = [a for a in await assets(deps, sid, 2) if a.method is ClassificationMethod.MANUAL]
    assert (manual.bucket, str(manual.recovery_period), manual.category) == (
        Bucket.Y15,
        "15.0",
        "Land improvement",
    )
    async with session_scope(deps.sessionmaker) as session:
        component = await session.get(Component, manual.component_id)
    assert component is not None and component.raw["carried_from"] == str(target.component_id)


@pytest.mark.parametrize(
    "after", [{"bucket": "y99"}, {"recovery_period": "soon"}, {"colour": "red"}]
)
async def test_bad_asset_correction_is_not_retried(
    deps: Deps, analyzed: SeededStudy, activity_env: Env, after: dict[str, Any]
) -> None:
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    target = (await assets(deps, analyzed.study_id, 1))[0]
    await activity_env.run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(analyzed.study_id),
            stage="assets_review",
            from_revision=1,
            to_revision=2,
            corrections=(),
        ),
    )
    bad = (CorrectionInput(entity_type="asset", entity_id=str(target.id), after=after),)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ProcessAssets(deps).run, process(analyzed, 2, corrections=bad))
    assert caught.value.non_retryable
    assert await assets(deps, analyzed.study_id, 2) == []
