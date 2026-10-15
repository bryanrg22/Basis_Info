"""``process_assets``: classify, measure, price and cross-check every component.

Activity mechanics with the Stage 2 photo stub's components, a scripted classifier and an
empty in-memory store (no reference corpus): rows per component, the rule/model split,
budget refusal, idempotency, corrections, and the "no provider" path. The full pipeline
with a reference corpus and verified citations is ``tests/node/test_process_assets.py``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from lifecycletest_support import SeededStudy, count_rows, make_deps
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.ingest_appraisal import IngestAppraisal
from basis.activities.process_assets import ProcessAssets
from basis.db.enums import Bucket, ClassificationMethod
from basis.db.models import (
    Asset,
    Citation,
    Component,
    CostEstimate,
    LlmCall,
    Takeoff,
)
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.llm.provider import ScriptedProvider
from basis.workflow.contracts import (
    AnalyzePhotoInput,
    ApplyCorrectionsInput,
    CorrectionInput,
    IngestAppraisalInput,
    ProcessAssetsInput,
)

from .conftest import Env

pytestmark = pytest.mark.integration

# Stub components: kitchen_1/kitchen_2 -> cabinetry, flooring; bathroom_1 ->
# plumbing_fixtures, tile_flooring. cabinetry and tile_flooring hit rules; flooring
# (no material) and plumbing_fixtures go to the model.
MODEL_CALLS = 3
CALL_COST = (100 * 0.15 + 50 * 0.60) / 1_000_000


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


async def labelled(deps: Deps, study_id: uuid.UUID, revision: int) -> list[tuple[str, Asset]]:
    async with session_scope(deps.sessionmaker) as session:
        stmt = (
            select(Component.label, Asset)
            .join(Asset, Asset.component_id == Component.id)
            .where(Asset.study_id == study_id, Asset.revision == revision)
            .order_by(Component.label, Asset.id)
        )
        return [(label, asset) for label, asset in (await session.execute(stmt)).all()]


async def test_every_component_gets_asset_takeoff_and_cost(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    assert output.assets == 6
    assert output.cost_usd == pytest.approx(MODEL_CALLS * CALL_COST)
    rows = await labelled(deps, analyzed.study_id, 1)
    methods = {label: a.method for label, a in rows}
    assert methods == {
        "cabinetry": ClassificationMethod.RULE,
        "tile_flooring": ClassificationMethod.RULE,
        "flooring": ClassificationMethod.LLM,
        "plumbing_fixtures": ClassificationMethod.LLM,
    }
    for model in (Takeoff, CostEstimate):
        assert await count_rows(deps.sessionmaker, model, study_id=analyzed.study_id) == 6
    # No reference corpus: no citation can be verified, so nothing is cited and
    # every asset is in front of the engineer.
    assert await count_rows(deps.sessionmaker, Citation, study_id=analyzed.study_id) == 0
    assert output.needs_review == 6
    for label, asset in rows:
        assert asset.review_reason and "citation_unverified" in asset.review_reason, label
    assert await count_rows(deps.sessionmaker, LlmCall, study_id=analyzed.study_id) == MODEL_CALLS
    assert len(activity_env.heartbeats) == 12  # one per classification, one per write


async def test_rule_and_model_values_and_takeoffs(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    rows = await labelled(deps, analyzed.study_id, 1)
    cabinet = next(a for label, a in rows if label == "cabinetry")
    assert (cabinet.bucket, cabinet.irs_section, cabinet.recovery_period) == (
        Bucket.Y5,
        "1245",
        Decimal("5.0"),
    )
    tile = next(a for label, a in rows if label == "tile_flooring")
    assert tile.review_reason and "rule_contested:tile" in tile.review_reason
    floor = next(a for label, a in rows if label == "flooring")
    assert (floor.bucket, floor.confidence) == (Bucket.Y27_5, pytest.approx(0.3))
    assert floor.review_reason and floor.review_reason.startswith("missing_evidence")
    async with session_scope(deps.sessionmaker) as session:
        takeoffs = {t.asset_id: t for t in (await session.scalars(select(Takeoff))).all()}
    cabinets = sorted(
        (takeoffs[a.id] for label, a in rows if label == "cabinetry"), key=lambda t: t.quantity
    )
    # Both kitchen photos show the cabinets; the run is measured once for the room.
    assert [t.quantity for t in cabinets] == [Decimal("0"), Decimal("48.99")]
    assert cabinets[0].inputs["measured_with_asset"]
    assert cabinets[1].inputs["room_area_source"] == "typical_for_room_type"
    assert cabinets[1].formula == "4 x sqrt(150 SF)"


async def test_unknown_label_goes_to_the_model_and_is_flagged(
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
    assert output.assets == 7
    rows = await labelled(deps, analyzed.study_id, 1)
    [mystery] = [asset for label, asset in rows if label == "mystery_box"]
    assert mystery.method is ClassificationMethod.LLM and mystery.needs_review


async def test_budget_too_small_writes_nothing(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    # 3 components need the model; worst case 3 x 4 calls x $0.002 = $0.024.
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ProcessAssets(deps).run, process(analyzed, budget=0.02))
    assert caught.value.type == "BudgetExceededError" and caught.value.non_retryable
    assert await count_rows(deps.sessionmaker, Asset, study_id=analyzed.study_id) == 0
    assert isinstance(deps.chat_provider, ScriptedProvider)
    assert deps.chat_provider.requests == []


async def test_without_a_provider_nothing_is_reserved_and_residuals_are_flagged(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    bare = make_deps(deps.sessionmaker, deps.storage, deps.settings, chat_provider=None)
    output = await activity_env.run(ProcessAssets(bare).run, process(analyzed, budget=0.0001))
    assert (output.assets, output.cost_usd) == (6, 0.0)
    rows = await labelled(deps, analyzed.study_id, 1)
    floor = next(a for label, a in rows if label == "flooring")
    assert floor.review_reason and floor.review_reason.startswith("classifier_unavailable")
    assert floor.confidence == 0.0


async def test_replay_and_reset_do_not_duplicate(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    activity = ProcessAssets(deps)
    first = await activity_env.run(activity.run, process(analyzed))
    assert await activity_env.run(activity.run, process(analyzed)) == first
    reset = await activity_env.run(activity.run, process(analyzed, key="k-reset"))
    assert (reset.assets, reset.needs_review, reset.cost_usd) == (6, first.needs_review, 0.0)
    assert await count_rows(deps.sessionmaker, Asset, study_id=analyzed.study_id) == 6
    assert await count_rows(deps.sessionmaker, LlmCall, study_id=analyzed.study_id) == MODEL_CALLS


async def start_revision_two(deps: Deps, seeded: SeededStudy, activity_env: Env) -> None:
    await activity_env.run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(seeded.study_id),
            stage="assets_review",
            from_revision=1,
            to_revision=2,
            corrections=(),
        ),
    )


async def test_asset_correction_applies_to_the_carried_component(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    sid = analyzed.study_id
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    target = next(a for label, a in await labelled(deps, sid, 1) if label == "plumbing_fixtures")
    await start_revision_two(deps, analyzed, activity_env)
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
    assert manual.irs_section == "1250"  # kept from the corrected asset
    async with session_scope(deps.sessionmaker) as session:
        component = await session.get(Component, manual.component_id)
        takeoff = (
            await session.scalars(select(Takeoff).where(Takeoff.asset_id == manual.id))
        ).one()
    assert component is not None and component.raw["carried_from"] == str(target.component_id)
    assert takeoff.inputs["asset_correction"] == {
        "from_asset_id": str(target.id),
        "after": correction.after,
    }


async def test_bucket_only_correction_derives_the_recovery_period(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    sid = analyzed.study_id
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    target = next(a for label, a in await labelled(deps, sid, 1) if label == "flooring")
    await start_revision_two(deps, analyzed, activity_env)
    correction = CorrectionInput(
        entity_type="asset",
        entity_id=str(target.id),
        after={"bucket": "y5", "irs_section": "1245", "category": "Vinyl flooring"},
    )
    await activity_env.run(
        ProcessAssets(deps).run, process(analyzed, revision=2, corrections=(correction,))
    )
    [manual] = [a for a in await assets(deps, sid, 2) if a.method is ClassificationMethod.MANUAL]
    assert (manual.bucket, manual.recovery_period, manual.irs_section) == (
        Bucket.Y5,
        Decimal("5.0"),
        "1245",
    )
    # The classification is the engineer's; the missing cost row still needs a number.
    assert manual.review_reason is not None
    assert "validation" not in manual.review_reason
    assert "cross:COST_ROW_MISSING" in manual.review_reason


@pytest.mark.parametrize(
    "after",
    [
        {"bucket": "y99"},
        {"recovery_period": "soon"},
        {"colour": "red"},
        {"bucket": "y5", "recovery_period": "39"},
        {"bucket": "y5", "irs_section": "1250"},
        {"irs_section": "section 1245"},
    ],
)
async def test_bad_asset_correction_is_not_retried(
    deps: Deps, analyzed: SeededStudy, activity_env: Env, after: dict[str, Any]
) -> None:
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    target = (await assets(deps, analyzed.study_id, 1))[0]
    await start_revision_two(deps, analyzed, activity_env)
    bad = (CorrectionInput(entity_type="asset", entity_id=str(target.id), after=after),)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ProcessAssets(deps).run, process(analyzed, 2, corrections=bad))
    assert caught.value.non_retryable
    assert await assets(deps, analyzed.study_id, 2) == []
