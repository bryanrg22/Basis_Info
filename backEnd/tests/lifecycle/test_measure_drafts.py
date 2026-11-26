"""The StudyWorkflow with patch ``measure-drafts-v1`` (EVAL_REPORT 110).

With the input's ``measure_drafts_interior`` set and the worker's ``MEASURE_DRAFTS_INTERIOR``
on, the real ``measure_rooms`` runs once, before the first ``process_assets``, on a provider
that answers the photo prompts as the offline fixtures do and the room prompt with a scripted
answer; every ``process_assets`` of the execution (an assets rejection's too) receives the
drafts, and the Kitchen's floor rows are the draft's. A failed step flags the assets review and
the assets are today's. With the input unset nothing new is scheduled.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pytest
from lifecycle_support import Harness
from lifecycletest_support import make_deps
from llm_fakes import FixtureVisionProvider
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError
from test_versioning import patch_markers

from basis.activities.process_assets import ProcessAssets
from basis.db.models import Asset, Takeoff
from basis.db.session import session_scope
from basis.domain import measure_drafts as md
from basis.llm.provider import ChatRequest, ChatResponse, Usage
from basis.vision.measure_interior import PROMPT, InteriorAnswer
from basis.workflow.contracts import MeasureRoomsInput, MeasureRoomsOutput, ProcessAssetsInput
from basis.workflow.study_workflow import MEASURE_DRAFTS_PATCH

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

ANSWER = InteriorAnswer(
    length_ft=15.0,
    width_ft=12.0,
    floor_area_sf=180.0,
    ceiling_height_ft=8.0,
    perimeter_ft=54.0,
    wall_area_sf=432.0,
    countertop_sf=28.0,
    cabinet_lf=16.0,
    room_fully_seen=False,
    outline_shape="rectangle",
    confidence="medium",
    notes="against the door",
)


@dataclass
class RoomsAndPhotos:
    """The offline photo fixtures, and the room prompt's scripted answer."""

    photos: FixtureVisionProvider = field(default_factory=FixtureVisionProvider)
    room_requests: list[ChatRequest] = field(default_factory=list)
    name: str = "fixture"

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if request.prompt_version.split("/", 1)[0] != PROMPT:
            return await self.photos.complete(request)
        self.room_requests.append(request)
        return ChatResponse(
            parsed=ANSWER,
            usage=Usage(input_tokens=3200, output_tokens=220),
            model=request.model,
            provider=self.name,
            latency_ms=1,
        )


def drafting(harness: Harness, provider: Any) -> Harness:
    settings = harness.settings.model_copy(update={"measure_drafts_interior": True})
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
        task_queue=f"lifecycle-drafts-{uuid.uuid4().hex[:12]}",
        handles=[],
    )


def recording(run: Harness, seen: list[ProcessAssetsInput]) -> Any:
    real = ProcessAssets(run.deps).run

    @activity.defn(name="process_assets")
    async def process_assets(input: ProcessAssetsInput) -> Any:
        seen.append(input)
        return await real(input)

    return process_assets


async def takeoffs(run: Harness, study_id: uuid.UUID, revision: int) -> list[Takeoff]:
    async with session_scope(run.sessionmaker) as session:
        rows = await session.execute(
            select(Takeoff)
            .join(Asset, Asset.id == Takeoff.asset_id)
            .where(Asset.study_id == study_id, Asset.revision == revision)
        )
        return list(rows.scalars().all())


def drafted(rows: list[Takeoff]) -> list[Takeoff]:
    return [t for t in rows if md.DRAFT in t.inputs]


async def test_rooms_are_measured_once_and_every_assets_run_gets_the_drafts(
    harness: Harness,
) -> None:
    provider = RoomsAndPhotos()
    run = drafting(harness, provider)
    seen: list[ProcessAssetsInput] = []
    try:
        async with run.worker(process_assets=recording(run, seen)):
            started = await run.start(measure_drafts_interior=True)
            handle, sid = started.handle, started.study_id
            for stage in ("appraisal_review", "rooms_review"):
                await run.approve(handle, stage, 1)  # type: ignore[arg-type]
            status = await run.wait_for(
                handle,
                lambda s: s.awaiting in ("components_review", "assets_review"),
                "the gate after the rooms review",
            )
            if status.awaiting == "components_review":
                await run.approve(handle, "components_review", status.revision)
            revision = status.revision
            await run.wait_awaiting(handle, "assets_review", revision)
            await run.decide(handle, run.decision("assets_review", revision, "reject"))
            await run.approve(handle, "assets_review", revision + 1)
            result = await run.result(handle)
        sequence = await run.activity_sequence(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed"
    counts = Counter(sequence)
    assert counts["measure_rooms"] == 1 and counts["process_assets"] == 2
    assert sequence.index("measure_rooms") < sequence.index("process_assets")
    assert len(provider.room_requests) >= 1  # the rooms of a measured type with photos
    assert len(seen) == 2 and seen[0].room_drafts == seen[1].room_drafts
    assert {d.status for d in seen[0].room_drafts} == {"drafted"}
    for rev in (revision, revision + 1):
        rows = drafted(await takeoffs(run, sid, rev))
        assert rows, f"no drafted row at revision {rev}"
        for row in rows:
            record = row.inputs[md.DRAFT]
            assert record["drawn_by"].endswith("@measure_interior_reference-v1")
            assert record["photos"]
            assert row.inputs["room_area_source"] == "room_photos_draft"
    assert MEASURE_DRAFTS_PATCH in patch_markers(await handle.fetch_history())


@activity.defn(name="measure_rooms")
async def failing_measure_rooms(input: MeasureRoomsInput) -> MeasureRoomsOutput:
    raise ApplicationError("provider down for the test", non_retryable=True)


async def test_a_failed_step_flags_the_assets_review_and_prices_todays_rows(
    harness: Harness,
) -> None:
    run = drafting(harness, RoomsAndPhotos())
    seen: list[ProcessAssetsInput] = []
    try:
        async with run.worker(
            measure_rooms=failing_measure_rooms, process_assets=recording(run, seen)
        ):
            started = await run.start(measure_drafts_interior=True)
            handle, sid = started.handle, started.study_id
            for stage in ("appraisal_review", "rooms_review"):
                await run.approve(handle, stage, 1)  # type: ignore[arg-type]
            status = await run.wait_for(
                handle,
                lambda s: s.awaiting in ("components_review", "assets_review"),
                "the gate after the rooms review",
            )
            if status.awaiting == "components_review":
                await run.approve(handle, "components_review", status.revision)
            at_assets = await run.wait_awaiting(handle, "assets_review", status.revision)
            await run.approve(handle, "assets_review", status.revision)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed"
    assert "assets_review" in at_assets.needs_review_stages
    assert seen and seen[0].room_drafts == ()
    rows = await takeoffs(run, sid, seen[0].revision)
    assert rows and drafted(rows) == []


async def test_with_the_input_unset_nothing_new_is_scheduled(harness: Harness) -> None:
    run = drafting(harness, RoomsAndPhotos())  # the worker's setting on, the input's off
    try:
        async with run.worker():
            started = await run.start()
            await run.approve_all(started.handle)
            await run.result(started.handle)
        sequence = await run.activity_sequence(started.handle)
    finally:
        await run.cleanup()
    assert "measure_rooms" not in sequence
