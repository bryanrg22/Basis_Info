"""Workflow route bodies (S2_SPEC section 7).

``WorkflowProgress`` mirrors ``basis.workflow.contracts.StudyStatusView`` field by field
on purpose: the contract is the workflow's wire format and changes with workflow
versions; this is the public API and changes only with an API version. A test pins the
two together so a contract change is a visible, deliberate API change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import ConfigDict, Field

from basis.api.schemas.common import NO_NUL_PATTERN, RequestModel, ResponseModel
from basis.db.enums import Stage

MAX_CANCEL_REASON: Final = 1000


class WorkflowStarted(ResponseModel):
    """202: the study's workflow is running (or ran); the same answer on every repeat."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "workflow_id": "study-5f0c6a52-2f53-4c39-9a3e-2b0a8f1f7d10",
                    "run_id": "0193a1b2-7c3d-7e4f-8a9b-0c1d2e3f4a5b",
                }
            ]
        }
    )

    workflow_id: str = Field(description="Always `study-{study_id}`.")
    run_id: str = Field(description="The Temporal run id of the study's execution.")


class WorkflowProgress(ResponseModel):
    stage: Stage
    revision: int
    waiting_since: datetime | None = Field(description="When the current review wait began.")
    pending_photos: int
    completed_photos: int
    decisions_applied: list[str] = Field(description="Review ids the workflow has applied.")
    stalled: bool
    budget_remaining_usd: float
    awaiting: (
        Literal["appraisal_review", "rooms_review", "components_review", "assets_review"] | None
    ) = Field(
        default=None,
        description="The review gate the workflow is waiting at; decisions are accepted only here.",
    )
    flagged_photos: list[str] = Field(
        default_factory=list,
        description=(
            "Photo document ids whose analysis failed permanently, hit the budget, or was flagged."
        ),
    )
    needs_review_stages: list[str] = Field(
        default_factory=list,
        description="Stages whose output needs an engineer's attention because the budget ran out.",
    )
    failed_stage: Stage | None = Field(
        default=None,
        description="The stage that failed, while the workflow waits for "
        "`POST /v1/studies/{id}/retry`; null otherwise.",
    )
    failure: str | None = Field(default=None, description="Why that stage failed.")
    failure_code: str | None = Field(
        default=None,
        description="The error code behind `failure` (`BASIS-<area>-<nnn>`, docs/ERROR_CODES.md).",
    )


class WorkflowRunOut(ResponseModel):
    workflow_id: str
    run_id: str
    workflow_version: str
    input_revision: int
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None


class StudyWorkflowOut(ResponseModel):
    workflow_id: str
    run_id: str
    execution_status: str = Field(
        description="Temporal execution status: `RUNNING`, `COMPLETED`, `FAILED`, `CANCELED`, "
        "`TERMINATED`, `TIMED_OUT` or `CONTINUED_AS_NEW`."
    )
    status: WorkflowProgress | None = Field(
        description="The workflow's `status` query; null when it could not answer (for "
        "example while no worker is running)."
    )
    runs: list[WorkflowRunOut] = Field(description="Recorded runs (`runs` rows), oldest first.")


class CancelIn(RequestModel):
    reason: Annotated[
        str, Field(min_length=1, max_length=MAX_CANCEL_REASON, pattern=NO_NUL_PATTERN)
    ] = "cancelled by owner"


class RetryRequested(ResponseModel):
    """202: the workflow accepted the retry and runs the failed stage again."""

    workflow_id: str
    stage: Stage = Field(description="The stage that runs again.")
    revision: int
    already_requested: bool = Field(
        description="A retry was already accepted and the stage has not restarted yet."
    )


class CancelRequested(ResponseModel):
    """202: the cancel signal was delivered; the workflow records the outcome itself."""

    workflow_id: str
