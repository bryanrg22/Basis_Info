"""``record_failure``: a stage failed (``status = failed`` and the reason), or a retry
cleared it (EVAL_REPORT section 26, item 3).

The workflow records the failure only while the study is at the failed stage and
revision; the API's ``POST /v1/studies/{id}/retry`` accepts a retry only for a failed
study, and the workflow clears the failure (``reason=None``) before it runs the stage
again, so the study is ``processing`` while it does.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import activity_key, parse_uuid, run_once_no_output
from basis.activities.deps import Deps
from basis.db.enums import Stage
from basis.db.repositories.workflow_state import WorkflowStateRepository
from basis.workflow.contracts import RecordFailureInput

logger = logging.getLogger(__name__)
MAX_REASON: int = 2000


class RecordFailure:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="record_failure")
    async def run(self, input: RecordFailureInput) -> None:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> None:
            repo = WorkflowStateRepository(session)
            if input.reason is None:
                changed = await repo.clear_failure(study_id)
                logger.info("study %s failure %s", study_id, "cleared" if changed else "not set")
                return
            changed = await repo.mark_failed(
                study_id, Stage(input.stage), input.revision, input.reason[:MAX_REASON]
            )
            logger.error(
                "study %s %s at %s revision %s: %s",
                study_id,
                "failed" if changed else "not marked failed (it has moved on)",
                input.stage,
                input.revision,
                input.reason[:MAX_REASON],
            )

        await run_once_no_output(self._deps, name="record_failure", key=activity_key(), work=work)
