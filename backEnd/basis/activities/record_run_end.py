"""``record_run_end``: set ``ended_at`` and ``outcome`` on this execution's ``runs`` row."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import activity_key, parse_uuid, run_once_no_output
from basis.activities.deps import Deps
from basis.db.repositories.run import RunRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.workflow.contracts import RunEndInput


class RecordRunEnd:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="record_run_end")
    async def run(self, input: RunEndInput) -> None:
        async def work(session: AsyncSession) -> None:
            await RunRepository(session).end(input.workflow_id, input.run_id, input.outcome)
            await StudyEventRepository(session).record(
                parse_uuid(input.study_id, "study_id"),
                "run.ended",
                actor="system",
                workflow_id=input.workflow_id,
                run_id=input.run_id,
                detail={"outcome": input.outcome},
            )

        await run_once_no_output(self._deps, name="record_run_end", key=activity_key(), work=work)
