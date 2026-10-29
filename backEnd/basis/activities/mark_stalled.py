"""``mark_stalled``: set ``studies.status = 'stalled'`` after the review deadline.

Only while the study still waits at the same stage and revision, so a timer that fires
after a decision cannot stall a study that has moved on. The workflow keeps waiting; a
late decision moves the study and its status follows the new stage again.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import activity_key, parse_uuid, run_once_no_output
from basis.activities.deps import Deps
from basis.db.enums import Stage
from basis.db.repositories.workflow_state import WorkflowStateRepository
from basis.errors.codes import E
from basis.workflow.contracts import MarkStalledInput

logger = logging.getLogger(__name__)


class MarkStalled:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="mark_stalled")
    async def run(self, input: MarkStalledInput) -> None:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> None:
            changed = await WorkflowStateRepository(session).mark_stalled(
                study_id, Stage(input.stage), input.revision
            )
            logger.warning(
                "study %s %s at %s revision %s",
                study_id,
                "marked stalled" if changed else "not stalled (it has moved on)",
                input.stage,
                input.revision,
                extra={"error_code": E.WF_REVIEW_STALLED},
            )

        await run_once_no_output(self._deps, name="mark_stalled", key=activity_key(), work=work)
