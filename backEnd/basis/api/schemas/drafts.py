"""Gate draft request and response bodies (docs/api/GATE_DRAFTS_AND_RESUME.md).

A draft is the ``comment`` and ``corrections`` an engineer has lined up for a review stage,
before ``POST .../decisions`` sends them: the same ``CorrectionIn`` the decision takes, so a
draft that would be refused at submit time is refused at save time.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from basis.api.schemas.common import RequestModel, ResponseModel, Revision
from basis.api.schemas.reviews import MAX_CORRECTIONS, CorrectionIn, OptionalText
from basis.db.enums import Stage

StaleReason = Literal["revision", "decided"]


class DraftIn(RequestModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "revision": 1,
                    "comment": "Year built looks wrong; checking the deed.",
                    "corrections": [
                        {
                            "entity_type": "extraction_field",
                            "entity_id": "9d1f8f3e-5c2b-4a1e-8f7d-6b5a4c3d2e10",
                            "before": {"year_built": 1989},
                            "after": {"year_built": 1998},
                            "reason": "Deed shows 1998.",
                        }
                    ],
                }
            ]
        }
    )

    revision: Revision = Field(
        description="The study's current revision, whose rows the corrections name "
        "(`GET /v1/studies/{id}`); another revision is 409 `draft-stale`."
    )
    comment: OptionalText = Field(
        default=None, description="The decision comment being drafted (sent with the decision)."
    )
    corrections: Annotated[list[CorrectionIn], Field(max_length=MAX_CORRECTIONS)] = Field(
        default=[],
        description="The corrections being drafted, checked exactly as `POST .../decisions` "
        "checks them (type for the stage, target in the revision, values, reasons).",
    )


class DraftOut(ResponseModel):
    study_id: uuid.UUID
    stage: Stage
    revision: int = Field(description="The revision the draft was saved against.")
    user_id: uuid.UUID
    comment: str | None
    corrections: list[CorrectionIn]
    version: int = Field(description='The draft\'s entity tag without quotes (`ETag: "3"`).')
    stale: bool = Field(
        description="True when the draft can no longer be submitted: the study moved to "
        "another revision, or the stage was decided at the draft's revision."
    )
    stale_reason: StaleReason | None = Field(
        description="`revision`: the study is at another revision; `decided`: the stage was "
        "decided at the draft's revision; null while the draft is current."
    )
    created_at: datetime
    updated_at: datetime


class OtherDraftOut(ResponseModel):
    """Another engineer's draft on the stage: who, since when, how much; not its content."""

    user_id: uuid.UUID
    display_name: str | None
    email: str | None
    revision: int
    corrections: int = Field(description="How many corrections the draft holds.")
    has_comment: bool
    stale: bool
    updated_at: datetime


class DraftsOut(ResponseModel):
    """The caller's draft on the stage (null when none) and whose other drafts exist."""

    study_id: uuid.UUID
    stage: Stage
    current_revision: int = Field(description="The study's revision now.")
    draft: DraftOut | None
    others: list[OtherDraftOut]
