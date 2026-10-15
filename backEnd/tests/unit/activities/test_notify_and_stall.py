"""``notify_review_pending`` (one outbox row per reminder) and ``mark_stalled`` (guarded)."""

from __future__ import annotations

import pytest
from lifecycletest_support import SeededStudy, count_rows
from sqlalchemy import update

from basis.activities import Deps
from basis.activities.mark_stalled import MarkStalled
from basis.activities.notify_review_pending import NOTIFICATION, NotifyReviewPending
from basis.db.enums import Stage, StudyStatus
from basis.db.models import OutboxMessage, Study
from basis.db.repositories.outbox import OutboxRepository
from basis.db.session import session_scope
from basis.workflow.contracts import MarkStalledInput, NotifyInput

from .conftest import Env

pytestmark = pytest.mark.integration


async def at_stage(deps: Deps, seeded: SeededStudy, stage: Stage, revision: int = 1) -> None:
    async with session_scope(deps.sessionmaker) as session:
        await session.execute(
            update(Study)
            .where(Study.id == seeded.study_id)
            .values(current_stage=stage, status=StudyStatus.WAITING_REVIEW, revision=revision)
        )


async def status_of(deps: Deps, seeded: SeededStudy) -> StudyStatus:
    async with session_scope(deps.sessionmaker) as session:
        study = await session.get(Study, seeded.study_id, populate_existing=True)
        assert study is not None
        return study.status


async def test_a_reminder_is_one_notification_row_even_when_retried(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    payload = NotifyInput(
        study_id=str(seeded.study_id), stage="rooms_review", revision=2, waiting_days=7
    )
    activity = NotifyReviewPending(deps)
    await activity_env.run(activity.run, payload)
    await activity_env.run(activity.run, payload)
    assert await count_rows(deps.sessionmaker, OutboxMessage, kind=NOTIFICATION) == 1
    async with session_scope(deps.sessionmaker) as session:
        [message] = await OutboxRepository(session).list_for_study(seeded.study_id)
    assert message.payload == {
        "type": "review_pending",
        "study_id": str(seeded.study_id),
        "stage": "rooms_review",
        "revision": 2,
        "waiting_days": 7,
    }
    assert message.delivered_at is None


async def test_mark_stalled_while_still_waiting(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await at_stage(deps, seeded, Stage.APPRAISAL_REVIEW)
    payload = MarkStalledInput(study_id=str(seeded.study_id), stage="appraisal_review", revision=1)
    await activity_env.run(MarkStalled(deps).run, payload)
    assert await status_of(deps, seeded) is StudyStatus.STALLED


@pytest.mark.parametrize(
    ("stage", "revision"),
    [(Stage.ROOMS_REVIEW, 1), (Stage.APPRAISAL_REVIEW, 2)],
)
async def test_mark_stalled_does_nothing_once_the_study_moved_on(
    deps: Deps, seeded: SeededStudy, activity_env: Env, stage: Stage, revision: int
) -> None:
    await at_stage(deps, seeded, stage, revision)
    payload = MarkStalledInput(study_id=str(seeded.study_id), stage="appraisal_review", revision=1)
    await activity_env.run(MarkStalled(deps).run, payload)
    assert await status_of(deps, seeded) is StudyStatus.WAITING_REVIEW
