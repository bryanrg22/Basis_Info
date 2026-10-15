"""Shared message types between the API, the outbox relay, the workflow, and activities.

These models are the wire format of the Temporal workflow (S2_SPEC section 2). Field
names are part of the contract: changing one is a workflow version change and needs
``workflow.patched`` plus a replay test. Payloads carry identifiers and storage URIs,
never document contents.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DecisionKind = Literal["approve", "reject"]
ReviewStage = Literal["appraisal_review", "rooms_review", "assets_review"]
StageName = Literal[
    "uploading",
    "extracting",
    "appraisal_review",
    "rooms_review",
    "assets_processing",
    "assets_review",
    "exporting",
    "completed",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- workflow


class StudyWorkflowInput(_Frozen):
    study_id: str
    org_id: str
    input_revision: int = Field(ge=1)
    workflow_version: str
    budget_usd: float = Field(gt=0)
    photo_concurrency: int = Field(default=5, ge=1, le=32)
    review_reminder_days: int = Field(default=7, ge=1)
    review_deadline_days: int = Field(default=30, ge=1)


class CorrectionInput(_Frozen):
    entity_type: str
    entity_id: str
    after: dict[str, Any]
    reason: str | None = None


class Decision(_Frozen):
    """An engineer's decision, delivered to the workflow as an update by the outbox relay."""

    review_id: str
    stage: ReviewStage
    revision: int = Field(ge=1)
    decision: DecisionKind
    reviewer_id: str
    corrections: tuple[CorrectionInput, ...] = ()
    submitted_at: datetime


class DecisionAck(_Frozen):
    accepted: bool
    applied_revision: int
    reason: str | None = None


class StudyStatusView(_Frozen):
    stage: StageName
    revision: int
    waiting_since: datetime | None = None
    pending_photos: int = 0
    completed_photos: int = 0
    decisions_applied: tuple[str, ...] = ()
    stalled: bool = False
    budget_remaining_usd: float = 0.0


# --------------------------------------------------------------------------- activities


class TransitionInput(_Frozen):
    study_id: str
    org_id: str
    from_stage: StageName
    to_stage: StageName
    revision: int


class RunStartInput(_Frozen):
    study_id: str
    workflow_id: str
    run_id: str
    workflow_version: str
    input_revision: int


class RunEndInput(_Frozen):
    study_id: str
    workflow_id: str
    run_id: str
    outcome: Literal["completed", "cancelled", "failed"]


class IngestAppraisalInput(_Frozen):
    study_id: str
    revision: int
    idempotency_key: str


class IngestAppraisalOutput(_Frozen):
    document_id: str | None
    corpus_version_id: str | None
    chunks: int = 0


class ExtractFieldsInput(_Frozen):
    study_id: str
    document_id: str | None
    revision: int
    idempotency_key: str
    corrections: tuple[CorrectionInput, ...] = ()


class ExtractFieldsOutput(_Frozen):
    extraction_id: str | None
    fields: int = 0
    needs_review: bool = False


class SimulateFault(_Frozen):
    """Test-only hooks, honoured by Stage 2 stub activities and ignored later."""

    fail_attempts: int = Field(default=0, ge=0)
    sleep_seconds: float = Field(default=0.0, ge=0)
    fail_permanently: bool = False


class AnalyzePhotoInput(_Frozen):
    study_id: str
    document_id: str
    revision: int
    idempotency_key: str
    budget_remaining_usd: float
    simulate: SimulateFault | None = None


class AnalyzePhotoOutput(_Frozen):
    document_id: str
    room_id: str | None
    components: int = 0
    cost_usd: float = 0.0
    needs_review: bool = False


class ProcessAssetsInput(_Frozen):
    study_id: str
    revision: int
    idempotency_key: str
    budget_remaining_usd: float
    corrections: tuple[CorrectionInput, ...] = ()


class ProcessAssetsOutput(_Frozen):
    assets: int = 0
    needs_review: int = 0
    cost_usd: float = 0.0


class ExportReportInput(_Frozen):
    study_id: str
    revision: int
    idempotency_key: str


class ExportReportOutput(_Frozen):
    export_uri: str
    sha256: str


class NotifyInput(_Frozen):
    study_id: str
    stage: ReviewStage
    revision: int
    waiting_days: int


class MarkStalledInput(_Frozen):
    study_id: str
    stage: ReviewStage
    revision: int


class ApplyCorrectionsInput(_Frozen):
    study_id: str
    stage: ReviewStage
    from_revision: int
    to_revision: int
    corrections: tuple[CorrectionInput, ...]


class ApplyCorrectionsOutput(_Frozen):
    applied: int = 0
