"""``record_run_start``: insert the ``runs`` row of this workflow execution (idempotent)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import activity_key, parse_uuid, run_once_no_output
from basis.activities.deps import Deps
from basis.db.repositories.run import RunRepository
from basis.db.session import session_scope
from basis.workflow.contracts import RunStartInput


class RecordRunStart:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="record_run_start")
    async def run(self, input: RunStartInput) -> None:
        study_id = parse_uuid(input.study_id, "study_id")
        # The runs row must exist before the idempotency claim, which references it.
        async with session_scope(self._deps.sessionmaker) as session:
            await RunRepository(session).start(
                study_id=study_id,
                workflow_id=input.workflow_id,
                run_id=input.run_id,
                workflow_version=input.workflow_version,
                input_revision=input.input_revision,
            )

        async def work(session: AsyncSession) -> None:
            return None

        await run_once_no_output(self._deps, name="record_run_start", key=activity_key(), work=work)
