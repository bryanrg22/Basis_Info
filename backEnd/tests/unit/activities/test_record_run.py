"""``record_run_start`` / ``record_run_end``: one ``runs`` row per execution, ended once."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError
from lifecycletest_support import SeededStudy, count_rows, seed_study

from basis.activities import Deps
from basis.activities.record_run_end import RecordRunEnd
from basis.activities.record_run_start import RecordRunStart
from basis.db.models import ActivityResult, Run
from basis.db.repositories.run import RunRepository
from basis.db.session import session_scope
from basis.workflow.contracts import RunEndInput, RunStartInput

from .conftest import Env

pytestmark = pytest.mark.integration


async def get_run(sessionmaker: async_sessionmaker[AsyncSession]) -> Run | None:
    async with session_scope(sessionmaker) as session:
        return await RunRepository(session).get_by_execution("study-x", "run-1")


@pytest.fixture
async def study(sessionmaker: async_sessionmaker[AsyncSession]) -> SeededStudy:
    return await seed_study(sessionmaker, photos=())


def start_input(study: SeededStudy) -> RunStartInput:
    return RunStartInput(
        study_id=str(study.study_id),
        workflow_id="study-x",
        run_id="run-1",
        workflow_version="v1",
        input_revision=1,
    )


async def test_start_creates_one_run_even_when_retried(
    deps: Deps, study: SeededStudy, activity_env: Env
) -> None:
    activity_env.with_info(workflow_id="study-x", workflow_run_id="run-1", activity_id="start")
    activity = RecordRunStart(deps)
    await activity_env.run(activity.run, start_input(study))
    await activity_env.run(activity.run, start_input(study))
    run = await get_run(deps.sessionmaker)
    assert run is not None
    assert (run.study_id, run.workflow_version, run.input_revision, run.outcome) == (
        study.study_id,
        "v1",
        1,
        None,
    )
    assert await count_rows(deps.sessionmaker, Run, workflow_id="study-x") == 1
    assert await count_rows(deps.sessionmaker, ActivityResult, run_id=run.id) == 1


async def test_end_sets_the_outcome_once(deps: Deps, study: SeededStudy, activity_env: Env) -> None:
    activity_env.with_info(workflow_id="study-x", workflow_run_id="run-1", activity_id="start")
    await activity_env.run(RecordRunStart(deps).run, start_input(study))
    activity_env.with_info(activity_id="end")
    end = RecordRunEnd(deps)
    payload = RunEndInput(
        study_id=str(study.study_id), workflow_id="study-x", run_id="run-1", outcome="cancelled"
    )
    await activity_env.run(end.run, payload)
    first = await get_run(deps.sessionmaker)
    assert first is not None and first.outcome == "cancelled" and first.ended_at is not None

    # A retry with another outcome (a new activity id) does not overwrite the first end.
    activity_env.with_info(activity_id="end-again")
    await activity_env.run(end.run, payload.model_copy(update={"outcome": "completed"}))
    again = await get_run(deps.sessionmaker)
    assert again is not None and (again.outcome, again.ended_at) == ("cancelled", first.ended_at)


async def test_start_for_an_unknown_study_is_not_retried(deps: Deps, activity_env: Env) -> None:
    payload = RunStartInput(
        study_id="not-a-uuid", workflow_id="w", run_id="r", workflow_version="v", input_revision=1
    )
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(RecordRunStart(deps).run, payload)
    assert caught.value.non_retryable
