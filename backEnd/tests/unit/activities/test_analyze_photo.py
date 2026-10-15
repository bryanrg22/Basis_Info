"""``analyze_photo`` (stub): room, assignment, two components; budget; faults; cancellation."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from temporalio.exceptions import ApplicationError
from workflowtest_support import SeededStudy, count_rows

from basis.activities import Deps
from basis.activities.analyze_photo import SIMULATED_FAILURE, AnalyzePhoto
from basis.activities.stub_rules import PHOTO_COST_USD
from basis.db.models import ActivityResult, Component, PhotoRoom, Room
from basis.db.repositories.activity_result import COMPLETED
from basis.workflow.contracts import AnalyzePhotoInput, SimulateFault

from .conftest import Env

pytestmark = pytest.mark.integration


def analyze(
    seeded: SeededStudy,
    index: int = 0,
    *,
    budget: float = 1.0,
    simulate: SimulateFault | None = None,
    key: str | None = None,
    document_id: str | None = None,
) -> AnalyzePhotoInput:
    doc = document_id or str(seeded.photo_ids[index])
    return AnalyzePhotoInput(
        study_id=str(seeded.study_id),
        document_id=doc,
        revision=1,
        idempotency_key=key or f"k-photo-{doc}",
        budget_remaining_usd=budget,
        simulate=simulate,
    )


async def test_writes_room_assignment_and_components(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(AnalyzePhoto(deps).run, analyze(seeded, 0))
    assert output.document_id == str(seeded.photo_ids[0])
    assert (output.components, output.cost_usd, output.needs_review) == (2, PHOTO_COST_USD, False)
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 1
    assert await count_rows(deps.sessionmaker, PhotoRoom, study_id=seeded.study_id) == 1
    assert await count_rows(deps.sessionmaker, Component, study_id=seeded.study_id) == 2
    assert activity_env.heartbeats  # heartbeats while working


async def test_two_photos_of_one_room_share_it_even_concurrently(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    from temporalio.testing import ActivityEnvironment

    activity = AnalyzePhoto(deps)
    # kitchen_1.jpg and kitchen_2.jpg, analyzed at the same time by two "workers"
    await asyncio.gather(
        activity_env.run(activity.run, analyze(seeded, 0)),
        ActivityEnvironment().run(activity.run, analyze(seeded, 1)),
    )
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id, name="Kitchen") == 1
    assert await count_rows(deps.sessionmaker, PhotoRoom, study_id=seeded.study_id) == 2


async def test_replay_returns_the_stored_output(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = AnalyzePhoto(deps)
    first = await activity_env.run(activity.run, analyze(seeded, 0))
    assert await activity_env.run(activity.run, analyze(seeded, 0)) == first
    # Same photo and revision under a new key (e.g. carried over already): no new rows.
    again = await activity_env.run(activity.run, analyze(seeded, 0, key="k-new"))
    assert again.room_id == first.room_id and again.cost_usd == 0
    assert await count_rows(deps.sessionmaker, Component, study_id=seeded.study_id) == 2


async def test_budget_too_small_is_a_non_retryable_budget_error(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(deps).run, analyze(seeded, 0, budget=0.005))
    assert caught.value.type == "BudgetExceededError" and caught.value.non_retryable
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 0


async def test_fail_attempts_fails_early_attempts_then_succeeds(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = AnalyzePhoto(deps)
    payload = analyze(seeded, 0, simulate=SimulateFault(fail_attempts=1))
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(activity.run, payload)
    assert caught.value.type == SIMULATED_FAILURE and not caught.value.non_retryable
    activity_env.with_info(attempt=2)
    output = await activity_env.run(activity.run, payload)
    assert output.components == 2


async def test_fail_permanently_is_non_retryable(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    payload = analyze(seeded, 0, simulate=SimulateFault(fail_permanently=True))
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(deps).run, payload)
    assert caught.value.non_retryable


async def test_faults_are_ignored_outside_dev_and_test(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    staging = deps.settings.model_copy(update={"env": "staging"})
    deps_staging = Deps(
        sessionmaker=deps.sessionmaker, storage=deps.storage, bucket=deps.bucket, settings=staging
    )
    assert not deps_staging.faults_enabled
    payload = analyze(seeded, 0, simulate=SimulateFault(fail_permanently=True))
    output = await activity_env.run(AnalyzePhoto(deps_staging).run, payload)
    assert output.components == 2


async def test_cancellation_rolls_back_the_partial_rows(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    payload = analyze(seeded, 0, simulate=SimulateFault(sleep_seconds=10))
    task = asyncio.ensure_future(activity_env.run(AnalyzePhoto(deps).run, payload))
    for _ in range(100):  # wait until it sleeps inside the transaction (room inserted)
        if any(details == ("sleeping",) for details in activity_env.heartbeats):
            break
        await asyncio.sleep(0.05)
    activity_env.env.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 0
    assert await count_rows(deps.sessionmaker, PhotoRoom, study_id=seeded.study_id) == 0
    rows = await count_rows(deps.sessionmaker, ActivityResult, status=COMPLETED)
    assert rows == 0


@pytest.mark.parametrize("which", ["appraisal", "foreign"])
async def test_not_a_photo_of_this_study_is_not_retried(
    deps: Deps, seeded: SeededStudy, activity_env: Env, which: str
) -> None:
    doc = str(seeded.appraisal_id) if which == "appraisal" else str(uuid.uuid4())
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(deps).run, analyze(seeded, document_id=doc))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable
