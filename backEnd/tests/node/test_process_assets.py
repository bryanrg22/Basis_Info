"""``process_assets`` end to end on Postgres: rule, model and cache paths with citations.

BUILD_PLAN 6.2 (``node/test_classify_component.py``, ``test_compute_takeoffs.py``,
``test_estimate_costs.py``): a scripted model with search results produces an asset
with a verified citation; a fabricated citation is rejected; takeoffs and costs are
deterministic; an engineer-approved asset seeds the cache and the next study is
classified from it without a model call.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from classifytest_support import (
    ASSET_CLASS_CHUNK,
    CARPET_CHUNK,
    GARAGE_EXCERPT,
    STRUCTURAL_CHUNK,
    answer_turn,
    call,
    classification,
    tools_turn,
)
from lifecycletest_support import SeededStudy, seed_run, seed_study
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.activities.process_assets import ProcessAssets
from basis.classify.cache import NotApprovedError, cache_key, promote_to_cache
from basis.classify.store_protocol import InMemoryChunkStore
from basis.config import Settings
from basis.db.enums import Bucket, ClassificationMethod, Decision, Stage
from basis.db.models import (
    Asset,
    Citation,
    ClassificationCacheEntry,
    Component,
    CostEstimate,
    LlmCall,
    Review,
    Takeoff,
)
from basis.db.session import session_scope
from basis.llm.cost import cost_usd
from basis.llm.provider import Usage
from basis.storage import LocalStorage
from basis.workflow.contracts import ProcessAssetsInput

from .conftest import Env, add_room_with, node_deps

pytestmark = pytest.mark.integration

SEARCH = {"query": "garage door structural component", "k": 3}
FAKE_CHUNK = "00000000-0000-4000-8000-00000000beef"


def scripted_turns() -> list:  # type: ignore[type-arg]
    # Components are processed in label order: carpet (rule), flooring (model),
    # garage_door (model), refrigerator (rule).
    return [
        tools_turn(call("search_reference", {"query": "vinyl flooring"})),
        answer_turn(
            classification(
                category="Flooring",
                section="1245",
                bucket="y5",
                citations=((FAKE_CHUNK, "Floor coverings of every kind are 5-year"),),
            )
        ),
        tools_turn(call("search_reference", SEARCH)),
        answer_turn(),  # cites STRUCTURAL_CHUNK with an excerpt that is in it
    ]


async def seed_components(sm: async_sessionmaker[AsyncSession], study_id: uuid.UUID) -> None:
    await add_room_with(sm, study_id, "Bedroom 2", "bedroom", [("carpet", None)])
    await add_room_with(
        sm, study_id, "Kitchen", "kitchen", [("flooring", None), ("refrigerator", None)]
    )
    await add_room_with(sm, study_id, "Garage", "garage", [("garage_door", None)])


def process(study: SeededStudy, key: str = "k-assets") -> ProcessAssetsInput:
    return ProcessAssetsInput(
        study_id=str(study.study_id), revision=1, idempotency_key=key, budget_remaining_usd=1.0
    )


async def by_label(sm: async_sessionmaker[AsyncSession], study_id: uuid.UUID) -> dict[str, Asset]:
    async with session_scope(sm) as session:
        rows = await session.execute(
            select(Component.label, Asset)
            .join(Asset, Asset.component_id == Component.id)
            .where(Asset.study_id == study_id)
        )
        return {label: asset for label, asset in rows.all()}


async def citations_of(sm: async_sessionmaker[AsyncSession], asset_id: uuid.UUID) -> list[Citation]:
    async with session_scope(sm) as session:
        stmt = select(Citation).where(Citation.asset_id == asset_id).order_by(Citation.chunk_id)
        return list((await session.scalars(stmt)).all())


async def test_rule_model_and_fabricated_paths(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
    reference_store: InMemoryChunkStore,
    study: SeededStudy,
    activity_env: Env,
) -> None:
    deps = node_deps(sessionmaker, storage, db_test_settings, reference_store, scripted_turns())
    await seed_components(sessionmaker, study.study_id)
    output = await activity_env.run(ProcessAssets(deps).run, process(study))
    assert output.assets == 4
    assets = await by_label(sessionmaker, study.study_id)

    carpet = assets["carpet"]
    assert (carpet.method, carpet.bucket, carpet.needs_review) == (
        ClassificationMethod.RULE,
        Bucket.Y5,
        False,
    )
    cited = await citations_of(sessionmaker, carpet.id)
    assert {c.chunk_id for c in cited} == {CARPET_CHUNK, ASSET_CLASS_CHUNK}
    assert all(c.verified_at is not None and c.verification_method for c in cited)

    door = assets["garage_door"]
    assert (door.method, door.bucket, door.irs_section) == (
        ClassificationMethod.LLM,
        Bucket.Y27_5,
        "1250",
    )
    [door_citation] = await citations_of(sessionmaker, door.id)
    assert (door_citation.chunk_id, door_citation.excerpt) == (STRUCTURAL_CHUNK, GARAGE_EXCERPT)
    # The classification stands; only the missing cost row needs the engineer.
    assert door.review_reason == "cross:COST_ROW_MISSING"

    floor = assets["flooring"]
    assert floor.method is ClassificationMethod.LLM
    assert await citations_of(sessionmaker, floor.id) == []  # fabricated chunk id dropped
    assert floor.review_reason and floor.review_reason.startswith("citation_unverified")

    fridge = assets["refrigerator"]
    assert fridge.method is ClassificationMethod.RULE and fridge.needs_review
    assert fridge.review_reason and "citation_unverified" in fridge.review_reason

    async with session_scope(sessionmaker) as session:
        calls = (await session.scalars(select(LlmCall))).all()
        carpet_takeoff = (
            await session.scalars(select(Takeoff).where(Takeoff.asset_id == carpet.id))
        ).one()
        carpet_cost = (
            await session.scalars(select(CostEstimate).where(CostEstimate.asset_id == carpet.id))
        ).one()
    assert len(calls) == 4 and {c.prompt_version for c in calls} == {"classify/v1"}
    assert all(c.run_id is not None and c.activity == "process_assets" for c in calls)
    # Charged from the cost table for the model the provider reports it ran; the scripted
    # provider's name has no price row, so the conservative unknown-model row applies.
    assert output.cost_usd == pytest.approx(
        4 * cost_usd("scripted-model", Usage(input_tokens=100, output_tokens=50))
    )
    # Bedroom: 150 SF typical -> 150 / 9 x 1.10 = 18.33 SY; 61.20/SY x 1.22 = 74.66.
    assert (carpet_takeoff.quantity, carpet_takeoff.unit) == (Decimal("18.33"), "SY")
    assert carpet_takeoff.inputs["room_area_source"] == "typical_for_room_type"
    assert (carpet_cost.unit_cost, carpet_cost.total, carpet_cost.cost_year) == (
        Decimal("74.66"),
        Decimal("1368.52"),
        2026,
    )
    assert output.needs_review == 3


async def approve_assets(sm: async_sessionmaker[AsyncSession], study: SeededStudy) -> None:
    async with session_scope(sm) as session:
        session.add(
            Review(
                study_id=study.study_id,
                stage=Stage.ASSETS_REVIEW,
                revision=1,
                decision=Decision.APPROVE,
                reviewer_id=study.owner_id,
                idempotency_key=f"approve-{study.study_id}",
            )
        )


async def test_approved_asset_seeds_the_cache_for_the_next_study(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
    reference_store: InMemoryChunkStore,
    study: SeededStudy,
    activity_env: Env,
) -> None:
    deps = node_deps(sessionmaker, storage, db_test_settings, reference_store, scripted_turns())
    await seed_components(sessionmaker, study.study_id)
    await activity_env.run(ProcessAssets(deps).run, process(study))
    door = (await by_label(sessionmaker, study.study_id))["garage_door"]

    async with session_scope(sessionmaker) as session:
        with pytest.raises(NotApprovedError):  # not approved yet
            await promote_to_cache(
                session,
                study_id=study.study_id,
                asset_id=door.id,
                property_class="residential_rental",
            )
    await approve_assets(sessionmaker, study)
    async with session_scope(sessionmaker) as session:
        key = await promote_to_cache(
            session, study_id=study.study_id, asset_id=door.id, property_class="residential_rental"
        )
    assert key == cache_key("garage_door", None, None, "residential_rental")

    # A second study with a garage door: classified from the cache, no model call.
    second = await seed_study(sessionmaker, photos=())
    await seed_run(sessionmaker, second.study_id, workflow_id="second", run_id="second-run")
    await add_room_with(sessionmaker, second.study_id, "Garage", "garage", [("garage_door", None)])
    no_model = node_deps(sessionmaker, storage, db_test_settings, reference_store, [])
    env = Env().with_info(workflow_id="second", workflow_run_id="second-run")
    output = await env.run(ProcessAssets(no_model).run, process(second, key="k-second"))
    assert (output.assets, output.cost_usd) == (1, 0.0)
    cached = (await by_label(sessionmaker, second.study_id))["garage_door"]
    assert (cached.method, cached.bucket, cached.confidence) == (
        ClassificationMethod.CACHE,
        Bucket.Y27_5,
        pytest.approx(0.95),
    )
    [citation] = await citations_of(sessionmaker, cached.id)
    assert citation.chunk_id == STRUCTURAL_CHUNK
    async with session_scope(sessionmaker) as session:
        entry = await session.get(ClassificationCacheEntry, key)
    assert entry is not None and entry.hits == 1 and entry.source == f"approved_asset:{door.id}"


async def test_asset_without_reference_citation_cannot_seed_the_cache(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
    reference_store: InMemoryChunkStore,
    study: SeededStudy,
    activity_env: Env,
) -> None:
    deps = node_deps(sessionmaker, storage, db_test_settings, reference_store, scripted_turns())
    await seed_components(sessionmaker, study.study_id)
    await activity_env.run(ProcessAssets(deps).run, process(study))
    await approve_assets(sessionmaker, study)
    floor = (await by_label(sessionmaker, study.study_id))["flooring"]
    async with session_scope(sessionmaker) as session:
        with pytest.raises(NotApprovedError, match="no reference citation"):
            await promote_to_cache(
                session,
                study_id=study.study_id,
                asset_id=floor.id,
                property_class="residential_rental",
            )
        with pytest.raises(NotApprovedError):
            await promote_to_cache(
                session,
                study_id=study.study_id,
                asset_id=uuid.uuid4(),
                property_class="residential_rental",
            )
