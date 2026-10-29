"""Response bodies of the timeline, its steps and stages, the audit listing, and the
``provenance`` member on the output rows (docs/api/TIMELINE_AND_AUDIT.md)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from basis.api.schemas.common import ResponseModel

ActorType = Literal["engineer", "model", "system"]
Origin = Literal["model", "engineer_added", "engineer_modified"]


class ProvenanceOut(ResponseModel):
    """Who produced a row and from what (``basis.db.repositories.provenance``)."""

    origin: Origin = Field(
        description="`model`: the pipeline wrote it and no engineer changed it; "
        "`engineer_added`: an engineer added the row (a `room_component` correction); "
        "`engineer_modified`: an engineer's correction was applied when the row was copied "
        "into its revision (a row carried unchanged afterwards keeps this)."
    )
    correction_id: uuid.UUID | None = Field(description="The correction that produced it.")
    review_id: uuid.UUID | None = Field(description="The rejection that carried it.")
    stage: str | None = Field(description="The review stage the correction was made at.")
    revision: int | None = Field(description="The revision the correction was made at.")
    at: datetime | None = Field(description="When the engineer made it.")
    by: uuid.UUID | None = Field(description="The reviewer (`users.id`).")
    before: Any | None = Field(
        description="The values the correction replaced (from the copied row), keyed like `after`."
    )
    after: Any | None = Field(description="The correction's values.")
    changed: list[str] = Field(description="The fields the correction set.")
    carried_from: uuid.UUID | None = Field(
        description="The row of the previous revision the correction was applied to."
    )


class EventOut(ResponseModel):
    seq: int = Field(description="Insertion order; the keyset cursor.")
    at: datetime = Field(description="When it was recorded (the database clock).")
    event_type: str = Field(description="A dotted name; the vocabulary is in the docs.")
    actor_type: ActorType
    actor_id: uuid.UUID | None = Field(description="The engineer (`users.id`), when one acted.")
    stage: str | None
    revision: int | None
    workflow_id: str | None
    run_id: str | None
    activity: str | None
    activity_id: str | None
    attempt: int | None
    started_at: datetime | None = Field(description="An activity attempt's start.")
    duration_ms: int | None = Field(description="An activity attempt's duration.")
    entity_type: str | None
    entity_id: uuid.UUID | None
    code: str | None = Field(description="The error code of a failure (docs/ERROR_CODES.md).")
    request_id: str | None = Field(description="The API request that caused it.")
    before: Any | None
    after: Any | None
    detail: Any | None


class TimelinePageOut(ResponseModel):
    study_id: uuid.UUID
    items: list[EventOut]
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next page; null on the last page."
    )


class AttemptOut(ResponseModel):
    attempt: int
    started_at: datetime | None
    ended_at: datetime
    duration_ms: int | None
    outcome: Literal["completed", "failed", "cancelled"]
    code: str | None
    error: str | None


class StepOut(ResponseModel):
    activity: str
    activity_id: str
    started_at: datetime | None
    ended_at: datetime
    duration_ms: int | None = Field(description="The attempts' durations summed.")
    attempts: list[AttemptOut]
    outcome: Literal["completed", "failed", "cancelled"] = Field(description="The last attempt's.")
    code: str | None


class StageOut(ResponseModel):
    stage: str
    revision: int | None
    entered_at: datetime
    entered_by: ActorType = Field(
        description="`engineer`: a decision moved the study; `system`: the workflow did."
    )
    left_at: datetime | None = Field(description="Null while the study is there.")
    duration_ms: int | None
    gate_opened_at: datetime | None = Field(description="When the workflow opened the review.")
    decided_at: datetime | None
    decision: str | None
    review_wait_ms: int | None = Field(description="From the gate opening to the decision.")


class TimelineStepsOut(ResponseModel):
    study_id: uuid.UUID
    steps: list[StepOut]
    stages: list[StageOut]


class AuditItemOut(ResponseModel):
    kind: Literal["decision", "correction", "object_added", "object_deleted", "intake"]
    at: datetime
    by: uuid.UUID | None
    stage: str | None
    revision: int | None
    action: str = Field(
        description="`approved`, `rejected`, `modified`, `added`, `moved`, `merged`, "
        "`reanalyzed`, `deleted`."
    )
    entity_type: str | None
    entity_id: uuid.UUID | None
    before: Any | None
    after: Any | None
    changed: list[str]
    reason: str | None = Field(description="The correction's reason or the review's comment.")
    review_id: uuid.UUID | None
    correction_id: uuid.UUID | None
    produced: list[uuid.UUID] = Field(
        description="The rows of the next revision this correction produced, when recorded."
    )


class AuditOut(ResponseModel):
    study_id: uuid.UUID
    items: list[AuditItemOut]
