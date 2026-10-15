"""``transition``: move the study row to the workflow's stage (database only).

Search attributes are upserted by the workflow itself (``workflow.upsert_search_attributes``
after this activity succeeds), which is the SDK's mechanism for it: the upsert is a
workflow command recorded in history, so it is replay-safe and needs no client here.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import activity_key, parse_uuid, run_once_no_output
from basis.activities.deps import Deps
from basis.db.enums import Stage
from basis.db.repositories.workflow_state import WorkflowStateRepository
from basis.workflow.contracts import TransitionInput

logger = logging.getLogger(__name__)


class Transition:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="transition")
    async def run(self, input: TransitionInput) -> None:
        study_id = parse_uuid(input.study_id, "study_id")
        from_revision = input.from_revision if input.from_revision is not None else input.revision

        async def work(session: AsyncSession) -> None:
            result = await WorkflowStateRepository(session).converge(
                study_id,
                Stage(input.from_stage),
                from_revision,
                Stage(input.to_stage),
                input.revision,
            )
            logger.info(
                "study %s at %s revision %s (%s)",
                study_id,
                input.to_stage,
                input.revision,
                "moved" if result.changed else "already there",
            )

        await run_once_no_output(self._deps, name="transition", key=activity_key(), work=work)
