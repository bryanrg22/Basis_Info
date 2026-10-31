"""Review decision request and response bodies (S1_SPEC section 5)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, ConfigDict, Field, FiniteFloat, StrictBool

from basis.api.schemas.common import NO_NUL_PATTERN, RequestModel, ResponseModel, Revision
from basis.db.enums import Decision, Stage

MAX_COMMENT: Final = 4000
MAX_CORRECTIONS: Final = 500
MAX_CORRECTION_FIELDS: Final = 100
MAX_CORRECTION_TEXT: Final = 10_000
MAX_LIST_ITEMS: Final = 100

ReviewStage = Literal["appraisal_review", "rooms_review", "components_review", "assets_review"]

# Correction values are field-level edits: a flat object whose values are JSON scalars or
# lists of scalars. Strings exclude NUL (JSONB cannot store it); floats must be finite
# (JSON has no NaN). Deeper nesting is not needed by any S1-S3 entity.
CorrectionText = Annotated[str, Field(max_length=MAX_CORRECTION_TEXT, pattern=NO_NUL_PATTERN)]
CorrectionScalar = CorrectionText | StrictBool | int | FiniteFloat | None
CorrectionValue = (
    CorrectionScalar | Annotated[list[CorrectionScalar], Field(max_length=MAX_LIST_ITEMS)]
)
FIELD_NAME_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$"
_FIELD_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,63}")


def _check_field_names(value: dict[str, Any]) -> dict[str, Any]:
    if not all(_FIELD_NAME.fullmatch(key) for key in value):
        raise ValueError(f"field names must match {FIELD_NAME_PATTERN}")
    return value


# Key rules are checked by a validator and published as ``propertyNames``: pydantic would
# otherwise emit them as ``patternProperties``, which lets non-matching keys through.
CorrectionFields = Annotated[
    dict[str, CorrectionValue],
    Field(
        max_length=MAX_CORRECTION_FIELDS,
        json_schema_extra={"propertyNames": {"pattern": FIELD_NAME_PATTERN}},
    ),
    AfterValidator(_check_field_names),
]
OptionalText = Annotated[str, Field(max_length=MAX_COMMENT, pattern=NO_NUL_PATTERN)] | None


class CorrectionIn(RequestModel):
    entity_type: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            pattern=r"^[a-z][a-z0-9_]*$",
            description="Kind of entity corrected, e.g. `extraction_field`, `room`, `asset`.",
        ),
    ]
    entity_id: uuid.UUID
    before: CorrectionFields | None = Field(
        default=None, description="The values before the correction, as the reviewer saw them."
    )
    after: CorrectionFields = Field(
        description="The corrected values (`{}` for a `component_remove`; for an "
        "`extraction_field_add` the key, one value, the page and the printed evidence)."
    )
    reason: OptionalText = Field(
        default=None,
        description="Why. Required (non-blank) for `component_remove` and "
        "`extraction_field_add`: removing or adding a row is engineer evidence.",
    )


class DecisionIn(RequestModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "stage": "appraisal_review",
                    "revision": 1,
                    "decision": "approve",
                    "comment": "Year built corrected from the deed.",
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

    stage: ReviewStage = Field(description="The review stage being decided.")
    revision: Revision
    decision: Decision
    comment: OptionalText = None
    corrections: Annotated[list[CorrectionIn], Field(max_length=MAX_CORRECTIONS)] = []


class ReviewOut(ResponseModel):
    id: uuid.UUID
    study_id: uuid.UUID
    stage: Stage
    revision: int
    decision: Decision
    reviewer_id: uuid.UUID
    comment: str | None
    created_at: datetime


class DecisionAccepted(ResponseModel):
    """202: the decision is recorded and the workflow signal is queued (outbox)."""

    review: ReviewOut
    current_stage: Stage = Field(description="The study's stage after the decision.")
    revision: int = Field(description="The study's revision after the decision.")
    corrections_recorded: int
