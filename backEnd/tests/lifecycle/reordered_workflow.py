"""A deliberately nondeterministic variant of the StudyWorkflow, for the replay test.

Kept in its own module: the workflow sandbox re-imports a workflow's module, so it must
import nothing but the SDK and the workflow package (not the test harness, SQLAlchemy or
the database models).
"""

from __future__ import annotations

from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from basis.workflow.contracts import (
        ListPhotosInput,
        ListPhotosOutput,
        StudyWorkflowInput,
        StudyWorkflowResult,
    )
    from basis.workflow.study_workflow import WORKFLOW_NAME, StudyWorkflow


@workflow.defn(name=WORKFLOW_NAME)
class ReorderedStudyWorkflow(StudyWorkflow):
    """An unguarded change: the photos are listed before the ``extracting`` transition."""

    @workflow.run
    async def run(self, input: StudyWorkflowInput) -> StudyWorkflowResult:
        return await super().run(input)

    async def _transition(self, to_stage: Any, *, new_revision: int | None = None) -> None:
        if to_stage == "extracting" and not getattr(self, "_reordered", False):
            self._reordered = True
            await self._activity(
                "list_photos",
                ListPhotosInput(study_id=self._input.study_id),
                ListPhotosOutput,
                "list_photos:early",
            )
        await super()._transition(to_stage, new_revision=new_revision)
