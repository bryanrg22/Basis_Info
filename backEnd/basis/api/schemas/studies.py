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

MAX_PROPERTY_NAME: Final = 200


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

    @field_validator("property_name")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


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
