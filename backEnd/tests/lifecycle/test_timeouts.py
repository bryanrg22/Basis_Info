"""Review waits: a reminder every 7 days, ``stalled`` after 30, a late decision still works.

BUILD_PLAN 6.3 ``test_timeouts`` (review wait exceeds the limit -> stalled + alert, not a
hang) with the time-skipping server: ``env.sleep`` moves the clock and the durable timers
fire. Reminders are ``notification`` outbox rows written by ``notify_review_pending``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from lifecycle_support import Harness, skip
from sqlalchemy import select

from basis.db.enums import Stage, StudyStatus
from basis.db.models import OutboxMessage
from basis.db.session import session_scope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

DAY = timedelta(days=1)


async def reminders(harness: Harness, study_id: uuid.UUID) -> list[dict[str, object]]:
    async with session_scope(harness.sessionmaker) as session:
        rows = await session.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.kind == "notification")
            .where(OutboxMessage.payload["study_id"].astext == str(study_id))
            .order_by(OutboxMessage.created_at)
        )
        return [row.payload for row in rows.all()]


async def wait_reminders(
    harness: Harness, study_id: uuid.UUID, count: int
) -> list[dict[str, object]]:
    for _ in range(200):
        found = await reminders(harness, study_id)
        if len(found) >= count:
            return found
        await asyncio.sleep(0.05)
    raise AssertionError(f"expected {count} reminders, found {await reminders(harness, study_id)}")


async def test_reminder_after_seven_days(isolated_harness: Harness) -> None:
    harness = isolated_harness
    async with harness.worker():
        started = await harness.start()
        await harness.wait_awaiting(started.handle, "appraisal_review", 1)
        await skip(harness.env, 6 * DAY)
        await asyncio.sleep(0.3)
        assert await reminders(harness, started.study_id) == []

        await skip(harness.env, 1 * DAY + timedelta(minutes=1))
        [reminder] = await wait_reminders(harness, started.study_id, 1)
        assert reminder["type"] == "review_pending"
        assert (reminder["stage"], reminder["revision"], reminder["waiting_days"]) == (
            "appraisal_review",
            1,
            7,
        )
        status = await harness.status(started.handle)
        assert status.awaiting == "appraisal_review" and status.stalled is False


async def test_deadline_marks_stalled_and_a_late_decision_completes(
    isolated_harness: Harness,
) -> None:
    harness = isolated_harness
    async with harness.worker():
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        for week in range(1, 5):  # days 7, 14, 21, 28
            await skip(harness.env, 7 * DAY)
            await wait_reminders(harness, sid, week)
        study = await harness.study_row(sid)
        assert study.status is StudyStatus.WAITING_REVIEW

        await skip(harness.env, 2 * DAY + timedelta(minutes=1))  # day 30
        status = await harness.wait_for(handle, lambda s: s.stalled, "stalled")
        assert status.awaiting == "appraisal_review"
        study = await harness.study_row(sid)
        assert (study.status, study.current_stage) == (StudyStatus.STALLED, Stage.APPRAISAL_REVIEW)
        found = await reminders(harness, sid)
        assert [r["waiting_days"] for r in found] == [7, 14, 21, 28]

        # Still waiting, still reminding.
        await skip(harness.env, 5 * DAY)
        found = await wait_reminders(harness, sid, 5)
        assert found[-1]["waiting_days"] == 35

        await harness.decide(handle, harness.decision("appraisal_review", 1))
        status = await harness.wait_awaiting(handle, "rooms_review", 1)
        assert status.stalled is False
        study = await harness.study_row(sid)
        assert (study.status, study.current_stage) == (
            StudyStatus.WAITING_REVIEW,
            Stage.ROOMS_REVIEW,
        )
        await harness.approve(handle, "rooms_review")
        await harness.approve(handle, "components_review")
        await harness.approve(handle, "assets_review")
        result = await harness.result(handle)
    assert result.outcome == "completed"


async def test_reminders_respect_the_configured_interval(isolated_harness: Harness) -> None:
    harness = isolated_harness
    async with harness.worker():
        started = await harness.start(review_reminder_days=2, review_deadline_days=3)
        await harness.wait_awaiting(started.handle, "appraisal_review", 1)
        await skip(harness.env, 2 * DAY + timedelta(minutes=1))
        await wait_reminders(harness, started.study_id, 1)
        await skip(harness.env, 1 * DAY)
        await harness.wait_for(started.handle, lambda s: s.stalled, "stalled at day 3")
