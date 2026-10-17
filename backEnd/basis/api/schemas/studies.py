"""Study request and response bodies."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Final

from pydantic import ConfigDict, Field, field_validator

from basis.api.schemas.common import LINE_PATTERN, RequestModel, ResponseModel
from basis.api.schemas.documents import DocumentOut
from basis.api.schemas.reviews import ReviewOut
from basis.db.enums import Stage, StudyStatus
from basis.vision.rooms import AliasCycleError, resolve_aliases

MAX_PROPERTY_NAME: Final = 200
MAX_ROOM_ALIASES: Final = 500
# A file-name room id (basis.vision.rooms.name_hint): floor token, then the slug.
ROOM_ID_PATTERN: Final = r"^(?:b|x|[0-9]+)_[a-z0-9]+(?:_[a-z0-9]+)*$"
RoomId = Annotated[str, Field(max_length=200, pattern=ROOM_ID_PATTERN)]


class StudyCreate(RequestModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"property_name": "1200 Harbor Blvd, Oxnard CA"}]}
    )

    property_name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_PROPERTY_NAME,
            pattern=LINE_PATTERN,
            description="Property name or address shown to reviewers (one line; trimmed).",
        ),
    ]
    org_id: uuid.UUID | None = Field(
        default=None,
        description="Organization that will own the study. Optional when the caller can "
        "create studies (engineer or owner) in exactly one organization.",
    )

    room_aliases: dict[RoomId, RoomId] = Field(
        default_factory=dict,
        max_length=MAX_ROOM_ALIASES,
        description="Rooms to merge when photos are grouped by file name: `{room id: room "
        "id}`, where a room id is the photo file name's room (`1_kitchen` for "
        '"21_1st Floor Kitchen photo 1.JPG", `b_bath` for "Basement Bath.JPG", '
        '`x_mechanicals` for "Mechanicals.JPG"). `{"1_breakfast_nook": "1_kitchen"}` puts '
        "the breakfast nook's photos in the kitchen. Chains are followed; a loop (including a "
        "room aliased to itself) is a 422 validation problem. "
        "Rooms can also be merged at the rooms review (a `room` correction with "
        "`merge_into`).",
        json_schema_extra={"propertyNames": {"pattern": ROOM_ID_PATTERN}},
    )

    @field_validator("property_name")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()

    @field_validator("room_aliases")
    @classmethod
    def _no_loop(cls, value: dict[str, str]) -> dict[str, str]:
        try:
            resolve_aliases(value)
        except AliasCycleError as exc:
            raise ValueError(str(exc)) from None
        return value


class StudyOut(ResponseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "5f0c6a52-2f53-4c39-9a3e-2b0a8f1f7d10",
                    "org_id": "0b8f0f5e-8d3f-4d64-9a4b-7f3a2c1d9e01",
                    "created_by": "a3c1b6f4-0b1e-4b77-8f8a-3d4c5e6f7a80",
                    "property_name": "1200 Harbor Blvd, Oxnard CA",
                    "status": "draft",
                    "current_stage": "uploading",
                    "revision": 1,
                    "room_aliases": {},
                    "failure_reason": None,
                    "created_at": "2026-10-15T17:02:11.204000Z",
                    "updated_at": "2026-10-15T17:02:11.204000Z",
                }
            ]
        },
    )

    id: uuid.UUID
    org_id: uuid.UUID
    created_by: uuid.UUID
    property_name: str
    status: StudyStatus
    current_stage: Stage
    revision: int
    room_aliases: dict[str, str] = Field(
        default_factory=dict,
        description="The study's room aliases (`{room id: room id}`): those given at creation "
        "and the merges made at the rooms review.",
    )
    failure_reason: str | None = Field(
        default=None,
        description="Why the current stage failed, while `status` is `failed` (retry with "
        "`POST /v1/studies/{id}/retry`).",
    )
    created_at: datetime
    updated_at: datetime


class StudyDetail(StudyOut):
    documents: list[DocumentOut] = Field(description="Documents, oldest first.")
    latest_reviews: list[ReviewOut] = Field(
        description="For each review stage decided so far, the decision with the highest revision."
    )


class StudyList(ResponseModel):
    items: list[StudyOut]
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next page; null on the last page."
    )
