"""The study's LLM budget: exhausted -> remaining LLM work skipped and flagged, not failed.

BUILD_PLAN 6.3 ``test_budget``: remaining LLM activities are skipped, items are flagged
``needs_review``, and the workflow still reaches review and completes. ``BudgetExceededError``
is non-retryable (one attempt).
"""

from __future__ import annotations

import pytest
from lifecycle_support import Harness
from sqlalchemy import func, select

from basis.db.models import Asset
from basis.db.session import session_scope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

# Every photo here carries an unlisted item ("other") that no rule classifies, so whichever
# two photos the budget covers, process_assets must reserve model budget for a residual
# component (4 iterations x $0.002 = $0.008). Photo order is not guaranteed.
PHOTOS = ["living_1.jpg", "bedroom_1.jpg", "living_2.jpg"]


async def test_budget_exceeded_flags_items_and_the_study_still_completes(
    harness: Harness,
) -> None:
    # 0.025 covers two photos (0.01 each); the third gets 0.005 and the activity refuses it.
    # Assets then need up to 0.008 for the one residual component > 0.005: refused too.
    study = await harness.seed(photos=PHOTOS)
    async with harness.worker():
        started = await harness.start(study, budget_usd=0.025, photo_concurrency=1)
        handle = started.handle
        status = await harness.wait_awaiting(handle, "appraisal_review", 1)
        [third] = status.flagged_photos
        assert status.needs_review_stages == ("rooms_review",)
        assert status.budget_remaining_usd == pytest.approx(0.005)

        await harness.approve(handle, "appraisal_review")
        await harness.approve(handle, "rooms_review")
        status = await harness.wait_awaiting(handle, "assets_review", 1)
        assert status.needs_review_stages == ("rooms_review", "assets_review")
        await harness.approve(handle, "assets_review")
        result = await harness.result(handle)

    assert result.outcome == "completed"
    history = await handle.fetch_history()
    scheduled = {
        e.event_id: e.activity_task_scheduled_event_attributes.activity_id
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    }
    failed = [
        (
            scheduled[e.activity_task_failed_event_attributes.scheduled_event_id],
            e.activity_task_failed_event_attributes.failure.application_failure_info.type,
        )
        for e in history.events
        if e.HasField("activity_task_failed_event_attributes")
    ]
    assert failed == [
        (f"analyze_photo:{third}:r1", "BudgetExceededError"),
        ("process_assets:r1", "BudgetExceededError"),
    ]
    async with session_scope(harness.sessionmaker) as session:
        assets = await session.scalar(
            select(func.count()).select_from(Asset).where(Asset.study_id == study.study_id)
        )
    assert assets == 0  # refused up front: no partial rows


async def test_exhausted_budget_skips_the_activity_entirely(harness: Harness) -> None:
    study = await harness.seed(photos=PHOTOS)
    async with harness.worker():
        started = await harness.start(study, budget_usd=0.02, photo_concurrency=1)
        status = await harness.wait_awaiting(started.handle, "appraisal_review", 1)
    [skipped] = status.flagged_photos
    assert status.budget_remaining_usd == pytest.approx(0.0)
    history = await started.handle.fetch_history()
    photo_activities = [
        e.activity_task_scheduled_event_attributes.activity_id
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
        and e.activity_task_scheduled_event_attributes.activity_type.name == "analyze_photo"
    ]
    assert len(photo_activities) == 2
    assert f"analyze_photo:{skipped}:r1" not in photo_activities  # never scheduled
