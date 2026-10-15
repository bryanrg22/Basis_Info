"""``transition``: converge the study row to the workflow's stage (database only)."""

from __future__ import annotations

import pytest
from sqlalchemy import update
from temporalio.exceptions import ApplicationError
from lifecycletest_support import SeededStudy

from basis.activities import Deps
from basis.activities.transition import Transition
from basis.db.enums import Stage, StudyStatus
from basis.db.models import Study
from basis.db.session import session_scope
from basis.workflow.contracts import TransitionInput

from .conftest import Env

pytestmark = pytest.mark.integration


async def set_study(deps: Deps, seeded: SeededStudy, **values: object) -> None:
    async with session_scope(deps.sessionmaker) as session:
        await session.execute(update(Study).where(Study.id == seeded.study_id).values(**values))


async def study(deps: Deps, seeded: SeededStudy) -> Study:
    async with session_scope(deps.sessionmaker) as session:
        row = await session.get(Study, seeded.study_id, populate_existing=True)
        assert row is not None
        return row


def move(
    seeded: SeededStudy, frm: str, to: str, rev: int, from_rev: int | None = None
) -> TransitionInput:
    return TransitionInput(
        study_id=str(seeded.study_id),
        org_id=str(seeded.org_id),
        from_stage=frm,  # type: ignore[arg-type]
        to_stage=to,  # type: ignore[arg-type]
        revision=rev,
        from_revision=from_rev,
    )


async def test_moves_the_study_and_its_status(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await activity_env.run(Transition(deps).run, move(seeded, "uploading", "extracting", 1))
    row = await study(deps, seeded)
    assert (row.current_stage, row.status, row.revision) == (
        Stage.EXTRACTING,
        StudyStatus.PROCESSING,
        1,
    )


async def test_a_rejection_moves_back_and_starts_a_revision(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await set_study(
        deps, seeded, current_stage=Stage.ROOMS_REVIEW, status=StudyStatus.WAITING_REVIEW
    )
    await activity_env.run(
        Transition(deps).run, move(seeded, "rooms_review", "extracting", 2, from_rev=1)
    )
    row = await study(deps, seeded)
    assert (row.current_stage, row.revision) == (Stage.EXTRACTING, 2)


async def test_already_at_the_target_is_a_no_op(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    # The API's decision endpoint moved the study before the workflow got the decision.
    await set_study(
        deps, seeded, current_stage=Stage.ROOMS_REVIEW, status=StudyStatus.WAITING_REVIEW
    )
    await activity_env.run(
        Transition(deps).run, move(seeded, "appraisal_review", "rooms_review", 1)
    )
    row = await study(deps, seeded)
    assert (row.current_stage, row.status, row.revision) == (
        Stage.ROOMS_REVIEW,
        StudyStatus.WAITING_REVIEW,
        1,
    )


async def test_a_stalled_study_at_the_target_gets_its_stage_status_back(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await set_study(deps, seeded, current_stage=Stage.ROOMS_REVIEW, status=StudyStatus.STALLED)
    await activity_env.run(
        Transition(deps).run, move(seeded, "appraisal_review", "rooms_review", 1)
    )
    assert (await study(deps, seeded)).status is StudyStatus.WAITING_REVIEW


@pytest.mark.parametrize(
    ("stage", "status"),
    [
        (Stage.ASSETS_REVIEW, StudyStatus.WAITING_REVIEW),  # somewhere else entirely
        (Stage.UPLOADING, StudyStatus.ARCHIVED),  # archived
    ],
)
async def test_an_unexpected_state_is_a_non_retryable_error(
    deps: Deps, seeded: SeededStudy, activity_env: Env, stage: Stage, status: StudyStatus
) -> None:
    await set_study(deps, seeded, current_stage=stage, status=status)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(Transition(deps).run, move(seeded, "uploading", "extracting", 1))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable
    assert (await study(deps, seeded)).current_stage is stage


async def test_a_completed_transition_is_not_redone(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = Transition(deps)
    payload = move(seeded, "uploading", "extracting", 1)
    await activity_env.run(activity.run, payload)
    # Someone else moves the study on; a retry of the same activity must not fail or undo.
    await set_study(deps, seeded, current_stage=Stage.APPRAISAL_REVIEW)
    await activity_env.run(activity.run, payload)
    assert (await study(deps, seeded)).current_stage is Stage.APPRAISAL_REVIEW
