"""Parallel photo analysis: bounded fan-out, retries observed, permanent failures flagged.

BUILD_PLAN 6.3 ``test_parallel_photos``: one photo activity fails permanently -> the study
continues with that photo flagged; one fails once -> retried and succeeds; the number of
photo activities in flight never exceeds ``photo_concurrency``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from lifecycle_support import Harness
from sqlalchemy import select
from temporalio import activity
from temporalio.client import WorkflowHandle

from basis.db.models import Component, PhotoRoom
from basis.db.session import session_scope
from basis.workflow.contracts import AnalyzePhotoInput, AnalyzePhotoOutput, SimulateFault

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

PHOTOS = ["kitchen_1.jpg", "kitchen_2.jpg", "bathroom_1.jpg", "office_1.jpg"]


async def attempts(handle: WorkflowHandle[Any, Any]) -> dict[str, list[int]]:
    """Activity id -> the attempt numbers that started, from the history."""
    history = await handle.fetch_history()
    by_event: dict[int, str] = {}
    started: dict[str, list[int]] = {}
    for event in history.events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            by_event[event.event_id] = event.activity_task_scheduled_event_attributes.activity_id
        elif event.HasField("activity_task_started_event_attributes"):
            attrs = event.activity_task_started_event_attributes
            started.setdefault(by_event[attrs.scheduled_event_id], []).append(attrs.attempt)
    return started


async def test_permanent_failure_is_flagged_and_a_transient_one_is_retried(
    harness: Harness,
) -> None:
    study = await harness.seed(photos=PHOTOS)
    broken, flaky = str(study.photo_ids[1]), str(study.photo_ids[2])
    faults = {
        broken: SimulateFault(fail_permanently=True),
        flaky: SimulateFault(fail_attempts=1),
    }
    async with harness.worker():
        started = await harness.start(study, simulate_photos=faults)
        handle = started.handle
        status = await harness.wait_awaiting(handle, "appraisal_review", 1)
        assert status.flagged_photos == (broken,)
        assert (status.completed_photos, status.pending_photos) == (4, 0)

        tries = await attempts(handle)
        # The history records only the final attempt of a retried activity.
        assert tries[f"analyze_photo:{flaky}:r1"] == [2]
        assert tries[f"analyze_photo:{broken}:r1"] == [1]  # non-retryable: no retry

        await harness.approve_all(handle)
        result = await harness.result(handle)
    assert result.outcome == "completed"
    assert result.flagged_photos == (broken,)

    async with session_scope(harness.sessionmaker) as session:
        assigned = {
            str(p.document_id)
            for p in (
                await session.scalars(select(PhotoRoom).where(PhotoRoom.study_id == study.study_id))
            ).all()
        }
        components = (
            await session.scalars(select(Component).where(Component.study_id == study.study_id))
        ).all()
    assert broken not in assigned and flaky in assigned
    assert len(assigned) == 3
    assert len(components) == 6  # two per analyzed photo, none duplicated by the retry


async def test_retries_exhausted_flags_the_photo(harness: Harness) -> None:
    study = await harness.seed(photos=["kitchen_1.jpg", "kitchen_2.jpg"])
    hopeless = str(study.photo_ids[0])
    async with harness.worker():
        started = await harness.start(
            study, simulate_photos={hopeless: SimulateFault(fail_attempts=10)}
        )
        status = await harness.wait_awaiting(started.handle, "appraisal_review", 1)
        assert status.flagged_photos == (hopeless,)
        tries = await attempts(started.handle)
        assert tries[f"analyze_photo:{hopeless}:r1"] == [3]  # maximum_attempts=3


async def test_photo_fan_out_is_bounded_by_photo_concurrency(harness: Harness) -> None:
    in_flight = 0
    peak = 0

    @activity.defn(name="analyze_photo")
    async def counting_analyze_photo(input: AnalyzePhotoInput) -> AnalyzePhotoOutput:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.2)
        finally:
            in_flight -= 1
        return AnalyzePhotoOutput(document_id=input.document_id, room_id=None, components=0)

    study = await harness.seed(photos=[f"room_{i}.jpg" for i in range(8)])
    async with harness.worker(analyze_photo=counting_analyze_photo):
        started = await harness.start(study, photo_concurrency=3)
        status = await harness.wait_awaiting(started.handle, "appraisal_review", 1)
    assert status.completed_photos == 8
    assert peak == 3
