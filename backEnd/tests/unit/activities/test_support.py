"""The claim -> run -> complete protocol every activity uses (``_support.run_once``).

* A completed key returns the stored output without running the work again.
* Two concurrent executions of the same key run the work once (row lock).
* A failure leaves no rows from the work and records ``failed`` with the error.
* Missing run row, missing or stale study: non-retryable ``ValidationError``.
"""

from __future__ import annotations

import asyncio

import pytest
from lifecycletest_support import SeededStudy, count_rows
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities._support import activity_key, run_once
from basis.db.errors import StaleStateError
from basis.db.models import ActivityResult, Room
from basis.db.repositories.activity_result import (
    COMPLETED,
    FAILED,
    ActivityResultRepository,
)
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope

from .conftest import Env

pytestmark = pytest.mark.integration


class Out(BaseModel):
    value: int


async def get_result(
    sessionmaker: async_sessionmaker[AsyncSession], key: str
) -> ActivityResult | None:
    async with session_scope(sessionmaker) as session:
        return await ActivityResultRepository(session).get(key)


async def test_key_defaults_to_workflow_run_and_activity_id(activity_env: Env) -> None:
    activity_env.with_info(workflow_id="study-1", workflow_run_id="run-9", activity_id="x:r1")
    assert await activity_env.run(activity_key) == "study-1:run-9:x:r1"
    assert await activity_env.run(activity_key, "explicit") == "explicit"


async def test_completed_key_returns_the_stored_output(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    calls = 0

    async def work(session: AsyncSession) -> Out:
        nonlocal calls
        calls += 1
        return Out(value=calls * 10)

    async def call() -> Out:
        return await run_once(deps, name="probe", key="k-1", output_type=Out, work=work)

    first = await activity_env.run(call)
    second = await activity_env.run(call)
    assert first == second == Out(value=10)
    assert calls == 1
    row = await get_result(deps.sessionmaker, "k-1")
    assert row is not None and row.status == COMPLETED and row.activity == "probe"
    assert row.output_ref == '{"value":10}'


async def test_concurrent_executions_of_one_key_run_the_work_once(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    calls = 0

    async def work(session: AsyncSession) -> Out:
        nonlocal calls
        calls += 1
        await RoomRepository(session).create(
            study_id=seeded.study_id, revision=1, name="Lobby", room_type="lobby", source="t"
        )
        await asyncio.sleep(0.3)  # hold the row lock while the other attempt waits
        return Out(value=calls)

    async def call() -> Out:
        return await run_once(deps, name="probe", key="k-race", output_type=Out, work=work)

    results = await asyncio.gather(activity_env.run(call), ActivityEnvRunner(call).run())
    assert results[0] == results[1] == Out(value=1)
    assert calls == 1
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 1


class ActivityEnvRunner:
    """A second, independent ActivityEnvironment (a concurrent attempt)."""

    def __init__(self, fn: object) -> None:
        from temporalio.testing import ActivityEnvironment

        self._env = ActivityEnvironment()
        self._fn = fn

    async def run(self) -> Out:
        result: Out = await self._env.run(self._fn)  # type: ignore[arg-type]
        return result


async def test_failure_rolls_back_the_work_and_records_the_error(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    async def work(session: AsyncSession) -> Out:
        await RoomRepository(session).create(
            study_id=seeded.study_id, revision=1, name="Lobby", room_type="lobby", source="t"
        )
        raise RuntimeError("provider timed out")

    async def call() -> Out:
        return await run_once(deps, name="probe", key="k-fail", output_type=Out, work=work)

    with pytest.raises(RuntimeError):
        await activity_env.run(call)
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 0
    row = await get_result(deps.sessionmaker, "k-fail")
    assert row is not None and row.status == FAILED
    assert row.error == "RuntimeError: provider timed out"

    async def ok(session: AsyncSession) -> Out:
        return Out(value=1)

    # The retry claims the same row and completes it.
    async def retry() -> Out:
        return await run_once(deps, name="probe", key="k-fail", output_type=Out, work=ok)

    assert await activity_env.run(retry) == Out(value=1)
    row = await get_result(deps.sessionmaker, "k-fail")
    assert row is not None and (row.status, row.error) == (COMPLETED, None)


async def test_repository_conflicts_become_non_retryable(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    async def work(session: AsyncSession) -> Out:
        raise StaleStateError("study moved on")

    async def call() -> Out:
        return await run_once(deps, name="probe", key="k-stale", output_type=Out, work=work)

    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(call)
    assert caught.value.type == "ValidationError" and caught.value.non_retryable


async def test_without_a_run_row_the_claim_is_refused(deps: Deps, activity_env: Env) -> None:
    async def work(session: AsyncSession) -> Out:
        raise AssertionError("must not run")

    async def call() -> Out:
        return await run_once(deps, name="probe", key="k-orphan", output_type=Out, work=work)

    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(call)
    assert caught.value.non_retryable and "record_run_start" in caught.value.message
