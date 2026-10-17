"""The whole StudyWorkflow with the measured-but-off room-stage options switched on
(EVAL_REPORT 26.10): ``ROOM_STEP_MODE=room_call``, alone and with
``MATERIALS_SECOND_LOOK=surface_crops``.

Every other setting is the production default (as ``test_production_defaults``); providers
are the offline fixtures and the segmenter a fake; rows, storage, the Temporal server and the
activities are real. Confirming the options on a second package is a settings flip: these
tests are that flip end to end.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections import Counter

import pytest
from lifecycle_support import Harness
from lifecycletest_support import make_deps
from llm_fakes import FakeSegmenter, FixtureVisionProvider
from test_production_defaults import STEP_FIELDS, is_inventory, rows

from basis.config import Settings
from basis.db.models import Asset, Component

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

PHOTO_CALLS = {
    ("gpt-6.1-sol", "photo_countables/v3"): 3,
    ("gpt-6.1-sol", "photo_surfaces/v2"): 3,
    ("claude-opus-5-5", "photo_countables/v3"): 3,
    ("gpt-6-luna", "appraisal_field_fallback/v1"): 1,
}


def with_options(
    harness: Harness,
    provider: FixtureVisionProvider,
    segmenter: FakeSegmenter | None,
    **options: str,
) -> Harness:
    defaults = {name: Settings.model_fields[name].default for name in STEP_FIELDS}
    settings = harness.settings.model_copy(update={**defaults, **options})
    return dataclasses.replace(
        harness,
        deps=make_deps(
            harness.sessionmaker,
            harness.deps.storage,
            settings,
            llm=provider,
            chunk_store=harness.deps.chunk_store,
            segmenter=segmenter,
        ),
        settings=settings,
        task_queue=f"lifecycle-room-stage-{uuid.uuid4().hex[:12]}",
        handles=[],
    )


async def run_study(run: Harness) -> tuple[uuid.UUID, list[str]]:
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
    return sid, sequence


async def test_room_call_mode_runs_end_to_end(harness: Harness) -> None:
    provider = FixtureVisionProvider()
    run = with_options(harness, provider, None, room_step_mode="room_call")
    sid, sequence = await run_study(run)
    assert Counter(sequence)["reconcile_rooms"] == 1
    by_prompt = Counter((r.model, r.prompt_version) for r in provider.requests)
    # The Kitchen (two photos) and the Bathroom (one) each get the room call; no
    # reconciliation call, no second look.
    assert by_prompt == {**PHOTO_CALLS, ("claude-opus-5-5", "room_photos_inventory/v2"): 2}
    room_calls = [r for r in provider.requests if r.prompt_version.startswith("room_photos")]
    assert {r.reasoning_effort for r in room_calls} == {"high"}
    components = await rows(run, Component, sid, 1)
    inventory = [c for c in components if is_inventory(c)]
    assert sorted((c.label, c.raw["count"]) for c in inventory) == [
        ("bathtub", 1),
        ("countertop", 1),
        ("kitchen_cabinet", 6),
        ("toilet", 1),
    ]
    assert {(c.raw["step"], c.raw["path"]) for c in inventory} == {("room_call", "room_call")}
    assets = await rows(run, Asset, sid, 1)
    by_id = {c.id: c for c in components}
    assert len(assets) == 4 and all(is_inventory(by_id[a.component_id]) for a in assets)


async def test_room_call_and_surface_crops_run_end_to_end(harness: Harness) -> None:
    provider = FixtureVisionProvider()
    segmenter = FakeSegmenter()
    run = with_options(
        harness,
        provider,
        segmenter,
        room_step_mode="room_call",
        materials_second_look="surface_crops",
    )
    sid, _ = await run_study(run)
    by_prompt = Counter((r.model, r.prompt_version) for r in provider.requests)
    assert by_prompt == {
        **PHOTO_CALLS,
        ("claude-opus-5-5", "room_photos_inventory/v2"): 2,
        # three photos x floor, wall and ceiling
        ("claude-sonnet-5-5", "surface_crop_material/v1"): 9,
    }
    assert segmenter.calls == 3
    inventory = [c for c in await rows(run, Component, sid, 1) if is_inventory(c)]
    assert {c.raw["materials_second_look"] for c in inventory} == {"surface_crops"}
    assert {tuple(c.raw["surface_crops_flags"]) for c in inventory} == {()}
    assert len(await rows(run, Asset, sid, 1)) == 4
