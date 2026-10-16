"""The whole StudyWorkflow at the production defaults of every model step.

The suite's baseline (tests/conftest.py) pins the pre-12.1 photo step with fusion and the
room step off, so the other lifecycle tests keep their arithmetic. Here the settings are
the defaults ``Settings`` ships (checked field by field against the class defaults):
gpt-6.1-sol decomposed photos with per-class count fusion on claude-opus-5-5 (EVAL_REPORT
15), the room step on claude-sonnet-5-5 with room_inventory/v3 (16), and the text steps on
gpt-6-luna and claude-sonnet-5-5 (17). Providers are the offline fixtures; rows, storage,
the Temporal server and the activities are real.

* happy path: three calls per photo, one room call (the Kitchen's two photos), assets built
  from the room inventories (four, not the six per-photo rows), nothing flagged;
* a rooms rejection rebuilds the Kitchen's inventory in revision 2 and carries the
  Bathroom's;
* a room step that fails flags the rooms review and the study completes on the per-photo
  rows (the behaviour before the room step).
"""

from __future__ import annotations

import dataclasses
import uuid
from collections import Counter
from typing import Any

import pytest
from lifecycle_support import Harness
from lifecycletest_support import make_deps
from llm_fakes import FixtureVisionProvider
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from basis.config import Settings
from basis.db.models import Asset, Component
from basis.db.session import session_scope
from basis.vision.rooms import ROOM_INVENTORY_SOURCE
from basis.workflow.contracts import CorrectionInput, ReconcileRoomsInput, ReconcileRoomsOutput

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

# Every setting of the three wirings and the photo step, at the class default.
STEP_FIELDS = (
    "vision_model",
    "vision_photo_mode",
    "vision_reasoning_effort",
    "vision_count_fusion",
    "vision_fusion_model",
    "vision_fusion_reasoning_effort",
    "room_reconcile",
    "room_prompt_version",
    "room_model",
    "room_reasoning_effort",
    "room_single_photo",
    "room_materials",
    "text_model",
    "extraction_reasoning_effort",
    "extraction_fallback_max_tokens",
    "classify_model",
    "classify_reasoning_effort",
)


def production(harness: Harness, provider: FixtureVisionProvider) -> Harness:
    defaults = {name: Settings.model_fields[name].default for name in STEP_FIELDS}
    settings = harness.settings.model_copy(update=defaults)
    assert settings.vision_count_fusion_active and settings.room_reconcile
    return dataclasses.replace(
        harness,
        deps=make_deps(
            harness.sessionmaker,
            harness.deps.storage,
            settings,
            llm=provider,
            chunk_store=harness.deps.chunk_store,
        ),
        settings=settings,
        task_queue=f"lifecycle-prod-{uuid.uuid4().hex[:12]}",
        handles=[],
    )


async def rows(harness: Harness, model: Any, study_id: uuid.UUID, revision: int) -> list[Any]:
    async with session_scope(harness.sessionmaker) as session:
        stmt = select(model).where(model.study_id == study_id, model.revision == revision)
        return list((await session.scalars(stmt)).all())


def is_inventory(component: Component) -> bool:
    return (
        component.document_id is None
        and (component.raw or {}).get("source") == ROOM_INVENTORY_SOURCE
    )


async def test_the_production_defaults_run_end_to_end(harness: Harness) -> None:
    provider = FixtureVisionProvider()
    run = production(harness, provider)
    try:
        async with run.worker():
            started = await run.start()
            handle, sid = started.handle, started.study_id
            status = await run.wait_awaiting(handle, "appraisal_review", 1)
            assert status.flagged_photos == () and status.needs_review_stages == ()
            await run.approve_all(handle)
            result = await run.result(handle)
        sequence = await run.activity_sequence(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed" and result.flagged_photos == ()
    assert Counter(sequence)["reconcile_rooms"] == 1
    assert sequence.index("reconcile_rooms") > max(
        i for i, name in enumerate(sequence) if name == "analyze_photo"
    )
    by_prompt = Counter((r.model, r.prompt_version) for r in provider.requests)
    assert by_prompt == {
        ("gpt-6.1-sol", "photo_countables/v3"): 3,
        ("gpt-6.1-sol", "photo_surfaces/v2"): 3,
        ("claude-opus-5-5", "photo_countables/v3"): 3,
        ("claude-sonnet-5-5", "room_inventory/v3"): 1,  # the two-photo Kitchen only
    }
    components = await rows(run, Component, sid, 1)
    per_photo = [c for c in components if not is_inventory(c)]
    assert {c.raw["fusion"]["status"] for c in per_photo} == {"fused"}
    inventory = [c for c in components if is_inventory(c)]
    assert sorted((c.label, c.raw["count"]) for c in inventory) == [
        ("bathtub", 1),
        ("countertop", 1),
        ("kitchen_cabinet", 6),
        ("toilet", 1),
    ]
    assets = await rows(run, Asset, sid, 1)
    by_id = {c.id: c for c in components}
    assert len(assets) == 4 and all(is_inventory(by_id[a.component_id]) for a in assets)


async def test_a_rooms_rejection_rebuilds_the_changed_room(harness: Harness) -> None:
    provider = FixtureVisionProvider()
    run = production(harness, provider)
    try:
        async with run.worker():
            started = await run.start()
            handle, sid = started.handle, started.study_id
            await run.approve(handle, "appraisal_review")
            await run.wait_awaiting(handle, "rooms_review", 1)
            flagged = started.study.photo_ids[0]  # a kitchen photo
            await run.decide(
                handle,
                run.decision(
                    "rooms_review",
                    1,
                    "reject",
                    corrections=[
                        CorrectionInput(entity_type="photo", entity_id=str(flagged), after={})
                    ],
                ),
            )
            await run.wait_awaiting(handle, "rooms_review", 2)
            room_calls = [r for r in provider.requests if r.prompt_version.startswith("room_")]
            assert len(room_calls) == 2  # revision 1 and the rebuilt Kitchen of revision 2
            await run.approve(handle, "rooms_review", 2)
            await run.approve(handle, "assets_review", 2)
            result = await run.result(handle)
        sequence = await run.activity_sequence(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed" and result.revision == 2
    assert Counter(sequence)["reconcile_rooms"] == 2
    inventory = [c for c in await rows(run, Component, sid, 2) if is_inventory(c)]
    kitchen = [c for c in inventory if c.label == "kitchen_cabinet"]
    assert len(kitchen) == 1 and kitchen[0].raw["count"] == 6
    assert len(kitchen[0].raw["photos"]) == 2
    bathroom = [c for c in inventory if c.label == "toilet"]
    assert len(bathroom) == 1 and "carried_from" in bathroom[0].raw
    assert len(await rows(run, Asset, sid, 2)) == 4


@activity.defn(name="reconcile_rooms")
async def failing_room_step(input: ReconcileRoomsInput) -> ReconcileRoomsOutput:
    raise ApplicationError("room step broke", type="ProviderNotConfigured", non_retryable=True)


async def test_a_failed_room_step_flags_the_review_and_the_study_completes(
    harness: Harness,
) -> None:
    provider = FixtureVisionProvider()
    run = production(harness, provider)
    try:
        async with run.worker(reconcile_rooms=failing_room_step):
            started = await run.start()
            handle, sid = started.handle, started.study_id
            status = await run.wait_awaiting(handle, "appraisal_review", 1)
            assert "rooms_review" in status.needs_review_stages
            assert status.flagged_photos == ()
            await run.approve_all(handle)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed"
    # No inventory: the assets are the per-photo rows, as before the room step.
    assert len(await rows(run, Asset, sid, 1)) == 6
