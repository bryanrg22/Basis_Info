"""What the classifier sees (:class:`ComponentView`) and what the model may answer.

:class:`Classification` is the residual model's structured output. It has no field that
names a study, a stage, a status, a review or a decision, and ``extra="forbid"`` rejects
any such key, so a model answer has no path to move a study (BUILD_PLAN 6.6
``test_review_bypass``). The model may *add* a review flag (``needs_review``) but cannot
clear one the pipeline sets.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ModelBucket = Literal["y5", "y7", "y15", "y27_5", "y39"]
MAX_CITATIONS: Final = 5
ASSET_CLASS_MAX: Final = 16


# A Rev. Proc. 87-56 class code at the start of the answer ("57.0", "00.11"), followed by
# its title or nothing: the form of every asset_class in the rules table.
_LEADING_CLASS_CODE: Final = re.compile(r"\s*(\d{2}\.\d{1,2})(?![\d.])")


def clip_asset_class(value: Any) -> Any:
    """An ``asset_class`` reduced to its class code, or cut to :data:`ASSET_CLASS_MAX`.

    OpenAI's strict schema does not enforce ``maxLength``, and an over-long answer failed
    validation in the provider, so the whole loop failed (EVAL_REPORT section 17.2: 43
    OpenAI loop failures). 38 of the 43 began with the class code and went on with its
    title ("00.3 Land Improvements", "57.0 Distributive Trades and Services"), so a leading
    code is kept alone, the form of every accepted answer. Other text over the limit is cut
    like the vision notes (:func:`basis.vision.reconcile.clip_text`). ``asset_class`` is not
    stored on the asset or used in any decision (the cache of approved assets writes
    ``None``), so neither case flags the row. The full text is in the provider response's
    ``raw``, which is not persisted."""
    if not isinstance(value, str):
        return value
    code = _LEADING_CLASS_CODE.match(value)
    if code is not None:
        return code.group(1)
    if len(value) > ASSET_CLASS_MAX:
        return value[: ASSET_CLASS_MAX - 3].rstrip() + "..."
    return value


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ComponentView(_Frozen):
    """One component as the classifier receives it. Every text field is untrusted data."""

    component_id: str
    label: str
    material: str | None = None
    attachment: str | None = None
    room_type: str | None = None
    room_name: str | None = None
    property_class: Literal["residential_rental", "nonresidential"] = "residential_rental"
    count: int = Field(default=1, ge=1)
    notes: str | None = Field(default=None, max_length=2000)  # e.g. text read from the photo
    room_guidance: str | None = None

    def as_prompt_data(self) -> str:
        """Canonical JSON of the view (sorted keys), embedded in the user message as data."""
        return json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False)


class CitationClaim(_Frozen):
    chunk_id: str = Field(max_length=64)
    excerpt: str = Field(max_length=1000, description="Copied verbatim from the chunk text")


class Classification(_Frozen):
    """The residual classifier's answer for one component."""

    category: str = Field(min_length=1, max_length=120)
    section: Literal["1245", "1250"]
    bucket: ModelBucket
    asset_class: str | None = Field(default=None, max_length=ASSET_CLASS_MAX)
    rationale: str = Field(default="", max_length=1500)
    citations: list[CitationClaim] = Field(default_factory=list, max_length=MAX_CITATIONS)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    needs_review: bool = False
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("asset_class", mode="before")
    @classmethod
    def _clip_asset_class(cls, value: Any) -> Any:
        return clip_asset_class(value)
