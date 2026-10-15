"""A 300-photo study stays far below Temporal's history limits without continue-as-new.

S2_SPEC section 3: continue-as-new is not needed at this size; this test pins it (fewer
than 10,000 events; Temporal warns at 10,240 and terminates at 51,200). Photo analysis and
the photo listing are mocked so the test measures the workflow, not the database.
"""

from __future__ import annotations

import uuid

import pytest
from lifecycle_support import Harness
from temporalio import activity

from basis.workflow.contracts import (
    AnalyzePhotoInput,
    AnalyzePhotoOutput,
    ListPhotosInput,
    ListPhotosOutput,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session"), pytest.mark.slow]

PHOTOS = 300
MAX_EVENTS = 10_000


@activity.defn(name="list_photos")
async def many_photos(input: ListPhotosInput) -> ListPhotosOutput:
    return ListPhotosOutput(document_ids=tuple(str(uuid.uuid4()) for _ in range(PHOTOS)))


@activity.defn(name="analyze_photo")
async def instant_photo(input: AnalyzePhotoInput) -> AnalyzePhotoOutput:
    return AnalyzePhotoOutput(
        document_id=input.document_id, room_id=None, components=2, cost_usd=0.01
    )


async def test_three_hundred_photos_fit_in_one_history(harness: Harness) -> None:
    async with harness.worker(list_photos=many_photos, analyze_photo=instant_photo):
        started = await harness.start(budget_usd=25.0)
        status = await harness.wait_awaiting(started.handle, "appraisal_review", 1)
        assert status.completed_photos == PHOTOS and status.flagged_photos == ()
        assert status.budget_remaining_usd == pytest.approx(25.0 - PHOTOS * 0.01)
        await harness.approve_all(started.handle)
        result = await harness.result(started.handle)
    assert result.outcome == "completed"

    history = await started.handle.fetch_history()
    events = len(history.events)
    assert events < MAX_EVENTS, events
    # Roughly three events per activity plus workflow tasks; a regression to a per-photo
    # loop of timers or signals would show up here long before the hard limit.
    assert events > 3 * PHOTOS
