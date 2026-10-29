"""``record_run_start``: insert the ``runs`` row of this workflow execution (idempotent), and
record on the study whether the execution has the components review (patch
``components-gate-v1``: ``studies.components_gate``, which the decision route and the object
edits read). A later execution of the study overwrites it with its own."""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import activity_key, parse_uuid, run_once_no_output
from basis.activities.deps import Deps
from basis.db.models import Study
from basis.db.repositories.run import RunRepository
from basis.db.repositories.study_event import StudyEventRepository
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
            await session.execute(
                update(Study)
                .where(Study.id == study_id)
                .values(components_gate=input.components_gate)
                .execution_options(synchronize_session=False)
            )
            await StudyEventRepository(session).record(
                study_id,
                "run.started",
                actor="system",
                revision=input.input_revision,
                workflow_id=input.workflow_id,
                run_id=input.run_id,
                detail={
                    "workflow_version": input.workflow_version,
                    "components_gate": input.components_gate,
                },
            )

        await run_once_no_output(self._deps, name="record_run_start", key=activity_key(), work=work)
