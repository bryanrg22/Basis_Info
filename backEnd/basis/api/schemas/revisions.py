"""Revision bodies: reopen, restore, the history and the diff (docs/api/REVISIONS.md)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field

from basis.api.schemas.common import RequestModel, ResponseModel
from basis.db.enums import Decision, Stage

ReviewStage = Literal["appraisal_review", "rooms_review", "components_review", "assets_review"]
OriginKind = Literal["start", "rejection", "reopen", "restore", "restart", "unknown"]
DiffKind = Literal["room", "component", "asset"]
ResumeReason = Literal["reopen", "restore"]


class ReopenIn(RequestModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"stage": "rooms_review", "dry_run": True}]}
    )

    stage: ReviewStage = Field(
        description="The review gate the completed study goes back to; every step after it "
        "recomputes (docs/api/REVISIONS.md section 2)."
    )
    dry_run: bool = Field(
        default=False,
        description="True: answer 200 with the plan (what is kept, what recomputes) and change "
        "nothing; every refusal still applies.",
    )


class RestoreIn(RequestModel):
    dry_run: bool = Field(
        default=False, description="True: answer 200 with the plan and change nothing."
    )


class ResumePlanOut(ResponseModel):
    """200 on a dry run: what a reopen or restore keeps and reruns, and nothing was done."""

    stage: ReviewStage = Field(description="The review gate the new revision opens at.")
    from_revision: int = Field(description="The revision copied into the new one.")
    revision: int = Field(description="The new revision's number.")
    recomputed: list[Stage] = Field(
        description="The processing stages that run again after the gate (the warning)."
    )
    reviews: list[ReviewStage] = Field(description="The gates to pass again, in order.")
    carried: list[str] = Field(description="The tables copied into the new revision as they were.")


class ResumeAccepted(ResumePlanOut):
    """202: the study is at the gate on the new revision (`processing` until the workflow has
    copied the rows and opened the gate) and a new execution runs under the same workflow id."""

    workflow_id: str
    run_id: str = Field(description="The new execution's run id (the workflow id is unchanged).")
    reason: ResumeReason


class RevisionOriginOut(ResponseModel):
    kind: OriginKind = Field(
        description="What made the revision: the `start`, a `rejection` at a gate, a `reopen` "
        "of the completed study, a `restore` of an earlier revision, a `restart` of a cancelled "
        "study; `unknown` for a revision older than the timeline with no rejection on record."
    )
    stage: Stage | None = Field(description="The gate the decision, reopen or restore was made at.")
    actor_id: uuid.UUID | None = Field(description="The reviewer or engineer; null for the system.")
    at: datetime | None
    review_id: uuid.UUID | None = Field(description="The rejection's review, for a `rejection`.")
    from_revision: int | None = Field(
        description="The revision this one was made from (copied, for a reopen or restore)."
    )


class RevisionDecisionOut(ResponseModel):
    stage: Stage
    decision: Decision
    reviewer_id: uuid.UUID
    review_id: uuid.UUID
    at: datetime


class RevisionExportOut(ResponseModel):
    uri: str | None
    sha256: str | None
    at: datetime


class RevisionSignatureOut(ResponseModel):
    id: uuid.UUID
    signed_by: uuid.UUID
    signed_at: datetime


class RevisionOut(ResponseModel):
    revision: int
    started_at: datetime | None = Field(description="When the study entered the revision.")
    ended_at: datetime | None = Field(
        description="When the next revision began; null on the current one."
    )
    origin: RevisionOriginOut
    corrections: dict[str, int] = Field(
        description="By kind, the corrections the decision that made this revision carried "
        "(applied here); `{}` for a start, reopen or restore."
    )
    decisions: list[RevisionDecisionOut] = Field(
        description="The reviews made at this revision, in order."
    )
    stage_reached: Stage | None = Field(
        description="The furthest stage the revision reached; a restore of it opens at the "
        "last gate before that."
    )
    export: RevisionExportOut | None = Field(description="Its export, when it was exported.")
    signature: RevisionSignatureOut | None = Field(
        description="Its signed report, when signed (one per revision, never rewritten)."
    )
    current: bool


class RevisionsOut(ResponseModel):
    study_id: uuid.UUID
    current_revision: int
    items: list[RevisionOut]


class DiffCountsOut(ResponseModel):
    added: int
    removed: int
    changed: int
    unchanged: int


class DiffRowOut(ResponseModel):
    kind: DiffKind
    id: uuid.UUID
    label: str = Field(description="The room's name, the component's label, the asset's label.")


class FieldChangeOut(ResponseModel):
    before: Any
    after: Any


class DiffChangeOut(ResponseModel):
    kind: DiffKind
    from_id: uuid.UUID = Field(description="The row in `from_revision`.")
    to_id: uuid.UUID = Field(description="Its copy in `to_revision`.")
    label: str
    changes: dict[str, FieldChangeOut] = Field(
        description="Per compared field that differs: rooms `name`, `room_type`, `floor`; "
        "components `label`, `material`, `attachment`, `count`, `room` (`{id, name}`); assets "
        "`category`, `bucket`, `recovery_period`, `irs_section`, `method`, `quantity`, `unit`, "
        "`unit_cost`, `total`."
    )


class RevisionDiffOut(ResponseModel):
    study_id: uuid.UUID
    from_revision: int
    to_revision: int
    counts: dict[DiffKind, DiffCountsOut]
    added: list[DiffRowOut] = Field(
        description="Rows of `to_revision` with no ancestor in `from_revision`."
    )
    removed: list[DiffRowOut] = Field(
        description="Rows of `from_revision` with no copy in `to_revision`."
    )
    changed: list[DiffChangeOut] = Field(description="One page (`limit`, `cursor`).")
    next_cursor: str | None


RevisionPath = Annotated[int, Field(ge=1, le=10_000, description="A revision of the study.")]
