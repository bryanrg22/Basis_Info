"""``record_failure``: a failed stage marks the study (guarded), a retry clears it.

EVAL_REPORT section 27, item 3. Real rows (Postgres container).
"""

from __future__ import annotations

from typing import Any

import pytest
from lifecycletest_support import SeededStudy
from sqlalchemy import update

from basis.activities import Deps
from basis.activities.record_failure import MAX_REASON, RecordFailure
from basis.db.enums import Stage, StudyStatus
from basis.db.models import Study
from basis.db.session import session_scope
from basis.workflow.contracts import RecordFailureInput

from .conftest import Env

pytestmark = pytest.mark.integration


async def at_stage(deps: Deps, seeded: SeededStudy, stage: Stage, status: StudyStatus) -> None:
    async with session_scope(deps.sessionmaker) as session:
        await session.execute(
            update(Study)
            .where(Study.id == seeded.study_id)
            .values(current_stage=stage, status=status, revision=1)
        )


async def row(deps: Deps, seeded: SeededStudy) -> Study:
    async with session_scope(deps.sessionmaker) as session:
        study = await session.get(Study, seeded.study_id, populate_existing=True)
        assert study is not None
        return study


def failure(seeded: SeededStudy, stage: str = "extracting", reason: str | None = "boom") -> Any:
    return RecordFailureInput(
        study_id=str(seeded.study_id),
        stage=stage,
        revision=1,
        reason=reason,  # type: ignore[arg-type]
    )


async def test_a_failure_is_recorded_and_a_retry_clears_it(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await at_stage(deps, seeded, Stage.EXTRACTING, StudyStatus.PROCESSING)
    activity = RecordFailure(deps)
    await activity_env.with_info(activity_id="record_failure:extracting:r1:1").run(
        activity.run, failure(seeded, reason="extract_fields: x" * 500)
    )
    study = await row(deps, seeded)
    assert (study.status, study.current_stage) == (StudyStatus.FAILED, Stage.EXTRACTING)
    assert study.failure_reason is not None and len(study.failure_reason) == MAX_REASON
    clear = activity_env.with_info(activity_id="record_failure:extracting:r1:1:cleared")
    await clear.run(activity.run, failure(seeded, reason=None))
    study = await row(deps, seeded)
    assert (study.status, study.failure_reason) == (StudyStatus.PROCESSING, None)
    # Clearing a study that is not failed changes nothing.
    again = activity_env.with_info(activity_id="record_failure:extracting:r1:2:cleared")
    await again.run(activity.run, failure(seeded, reason=None))
    assert (await row(deps, seeded)).status is StudyStatus.PROCESSING


async def test_a_study_that_moved_on_is_not_marked_failed(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await at_stage(deps, seeded, Stage.APPRAISAL_REVIEW, StudyStatus.WAITING_REVIEW)
    await activity_env.run(RecordFailure(deps).run, failure(seeded, stage="extracting"))
    study = await row(deps, seeded)
    assert (study.status, study.failure_reason) == (StudyStatus.WAITING_REVIEW, None)


async def test_an_archived_study_is_not_marked_failed(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await at_stage(deps, seeded, Stage.EXTRACTING, StudyStatus.ARCHIVED)
    await activity_env.run(RecordFailure(deps).run, failure(seeded))
    assert (await row(deps, seeded)).status is StudyStatus.ARCHIVED


async def test_a_cleared_failure_at_a_review_stage_goes_back_to_waiting_review(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await at_stage(deps, seeded, Stage.ASSETS_PROCESSING, StudyStatus.FAILED)
    await activity_env.run(RecordFailure(deps).run, failure(seeded, "assets_processing", None))
    assert (await row(deps, seeded)).status is StudyStatus.PROCESSING
