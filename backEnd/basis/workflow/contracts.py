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
ReviewStage = Literal["appraisal_review", "rooms_review", "components_review", "assets_review"]
StageName = Literal[
    "uploading",
    "extracting",
    "appraisal_review",
    "rooms_review",
    "components_processing",
    "components_review",
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
    # Patch localize-background-v1 (EVAL_REPORT 30): SAM 3 predictions in flight per study,
    # and whether the rooms review waits for every localization (else they finish behind it).
    localize_concurrency: int = Field(default=3, ge=1, le=32)
    rooms_gate_waits_for_localization: bool = False
    # Patch components-gate-v1: the rooms review approves the grouping and a components
    # review follows the room step. False runs the single rooms review of the executions
    # before the patch; the API never sets it (tests use it to run that flow afresh).
    components_gate: bool = True
    review_reminder_days: int = Field(default=7, ge=1)
    review_deadline_days: int = Field(default=30, ge=1)
    # Test-only fault injection per photo document id. The API never sets it, and the
    # analyze_photo activity honours it only when BASIS_ENV is dev or test.
    simulate_photos: dict[str, SimulateFault] = Field(default_factory=dict)


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


# Which correction targets a decision at each review stage may carry. The update
# validator rejects any other entity_type, so a malformed correction is refused before it
# is written to history. ``room_component`` (v0.11, manual add) adds a component to the
# room named by ``entity_id`` (``basis.activities.corrections.check_room_component``);
# ``photo_room`` (v0.12) moves the photo named by ``entity_id`` to the room ``after.room_id``;
# ``photo`` flags a photo for re-analysis. With the components gate (patch
# ``components-gate-v1``) the rooms review takes the grouping only and the components review
# the rest; an execution without it has the single rooms review
# (:data:`SINGLE_ROOMS_GATE_TYPES`; :func:`correction_types` picks). Validators do not run
# when a history replays, so changing a set needs no patch.
CORRECTION_TYPES: dict[str, frozenset[str]] = {
    "appraisal_review": frozenset({"extraction_field"}),
    "rooms_review": frozenset({"room", "photo_room"}),
    "components_review": frozenset({"component", "room_component", "photo"}),
    "assets_review": frozenset({"asset"}),
}
SINGLE_ROOMS_GATE_TYPES: frozenset[str] = frozenset(
    {"room", "component", "room_component", "photo"}
)


def correction_types(stage: str, *, components_gate: bool) -> frozenset[str]:
    """The correction types a decision at ``stage`` may carry; ``KeyError`` for a stage that
    is not a review stage."""
    if stage == "rooms_review" and not components_gate:
        return SINGLE_ROOMS_GATE_TYPES
    return CORRECTION_TYPES[stage]


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
    # The review gate the workflow is waiting at, if any (decisions are accepted only here).
    awaiting: ReviewStage | None = None
    # Photo document ids whose analysis failed permanently, hit the budget, or was flagged.
    flagged_photos: tuple[str, ...] = ()
    # Stages whose output needs an engineer's attention because the budget ran out.
    needs_review_stages: tuple[str, ...] = ()
    # v0.9 (EVAL_REPORT 27): the stage that failed and why, while the workflow waits for
    # a retry (the ``retry`` update); null otherwise.
    failed_stage: StageName | None = None
    failure: str | None = None


class StudyWorkflowResult(_Frozen):
    outcome: Literal["completed", "cancelled"]
    stage: StageName
    revision: int
    export_uri: str | None = None
    export_sha256: str | None = None
    flagged_photos: tuple[str, ...] = ()
    cancel_reason: str | None = None


# --------------------------------------------------------------------------- activities


class TransitionInput(_Frozen):
    """Move the study from ``(from_stage, from_revision)`` to ``(to_stage, revision)``.

    ``from_revision`` defaults to ``revision``; it differs only for a rejection, which
    starts a new revision.
    """

    study_id: str
    org_id: str
    from_stage: StageName
    to_stage: StageName
    revision: int
    from_revision: int | None = None


class ListPhotosInput(_Frozen):
    study_id: str


class ListPhotosOutput(_Frozen):
    document_ids: tuple[str, ...] = ()


class RunStartInput(_Frozen):
    study_id: str
    workflow_id: str
    run_id: str
    workflow_version: str
    input_revision: int
    # Patch components-gate-v1: recorded on the study (``studies.components_gate``).
    components_gate: bool = False


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
    # Patch components-gate-v1: a re-analysis after a components rejection keeps the room
    # the rooms review approved (``apply_corrections`` carried the photo's assignment).
    keep_room: bool = False


class AnalyzePhotoOutput(_Frozen):
    document_id: str
    room_id: str | None
    components: int = 0
    cost_usd: float = 0.0
    needs_review: bool = False


class ReconcileRoomsInput(_Frozen):
    """Build the room inventories of ``revision`` (the room step, EVAL_REPORT 16.5);
    ``vote_only`` (patch components-gate-v1) re-votes the file-name rooms' type and floor
    and builds nothing (no model call), before the rooms review."""

    study_id: str
    revision: int
    idempotency_key: str
    budget_remaining_usd: float
    vote_only: bool = False


class ReconcileRoomsOutput(_Frozen):
    # False when ROOM_RECONCILE is off: nothing was done, assets use the per-photo rows.
    enabled: bool = True
    rooms: int = 0  # rooms of the revision with photos (Unassigned excluded)
    built: int = 0  # inventories written by this execution
    current: int = 0  # inventories already up to date (carried over or written before)
    needs_review: int = 0  # rooms whose inventory is flagged
    cost_usd: float = 0.0
    # ROOM_STEP_MODE=room_call: rooms built by the room call, and rooms sent down that path
    # that fell back to the reconcile path (an unusable answer).
    room_calls: int = 0
    room_call_fallbacks: int = 0
    # MATERIALS_SECOND_LOOK=surface_crops: rooms whose crops ran, and rooms where some photo
    # or surface fell back to the per-photo material (segmenter or call failure).
    surface_crop_rooms: int = 0
    surface_crop_fallbacks: int = 0


LocalizationStatus = Literal["done", "failed", "skipped"]


class LocalizePhotoInput(_Frozen):
    """Localize the objects of a photo whose analysis at ``revision`` succeeded."""

    study_id: str
    document_id: str
    revision: int
    idempotency_key: str
    budget_remaining_usd: float


class LocalizePhotoOutput(_Frozen):
    document_id: str
    status: LocalizationStatus
    instances: int = 0
    counted: int = 0
    cost_usd: float = 0.0
    reason: str | None = None


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


class RecordFailureInput(_Frozen):
    """Record that ``stage`` failed (``reason``), or clear the failure (``reason=None``)."""

    study_id: str
    stage: StageName
    revision: int
    reason: str | None = None


class RetryAck(_Frozen):
    """The answer to the ``retry`` update: the stage that runs again."""

    stage: StageName
    revision: int
    already_requested: bool = False


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
    # Patch components-gate-v1: the execution has the components review (which correction
    # types each gate takes, and what the rooms and components rejections carry over).
    components_gate: bool = False


class ApplyCorrectionsOutput(_Frozen):
    applied: int = 0


StudyWorkflowInput.model_rebuild()
