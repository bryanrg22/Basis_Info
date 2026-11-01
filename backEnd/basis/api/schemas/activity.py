"""Response bodies of ``GET /v1/me/activity`` (one user's own study actions) and
``GET /v1/orgs/{org_id}/activity`` (the firm's own history), docs/api/TIMELINE_AND_AUDIT.md
"Your activity" and "Firm activity"."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from basis.api.schemas.common import ResponseModel
from basis.api.schemas.timeline import EventOut

OrgActorType = Literal["engineer", "system"]


class ActivityItemOut(EventOut):
    """A timeline event with the study it belongs to."""

    study_id: uuid.UUID
    property_name: str = Field(description="The study's property name, as it is now.")


class ActivityOut(ResponseModel):
    items: list[ActivityItemOut] = Field(description="Newest first (`seq` descending).")
    since: datetime = Field(
        description="The window's start as applied: the `since` given, else 90 days before "
        "the request. Pass it as `since` on the next page to pin the window."
    )
    until: datetime | None = Field(description="The window's end as applied, when one was given.")
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next (older) page; null on the last page."
    )


class OrgEventOut(ResponseModel):
    seq: int = Field(description="Insertion order; the keyset cursor.")
    at: datetime = Field(description="When it was recorded (the database clock).")
    event_type: str = Field(description="A dotted name; the vocabulary is in the docs.")
    actor_type: OrgActorType = Field(
        description="`engineer`: a signed-in member (`actor_id`); `system`: the operator CLI."
    )
    actor_id: uuid.UUID | None
    subject_user_id: uuid.UUID | None = Field(description="The member the event is about.")
    entity_type: str | None
    entity_id: uuid.UUID | None
    request_id: str | None = Field(description="The API request that caused it.")
    before: Any | None
    after: Any | None
    detail: Any | None


class OrgActivityOut(ResponseModel):
    org_id: uuid.UUID
    items: list[OrgEventOut] = Field(description="Newest first (`seq` descending).")
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next (older) page; null on the last page."
    )
