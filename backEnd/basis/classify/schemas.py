"""What the classifier sees (:class:`ComponentView`) and what the model may answer.

:class:`Classification` is the residual model's structured output. It has no field that
names a study, a stage, a status, a review or a decision, and ``extra="forbid"`` rejects
any such key, so a model answer has no path to move a study (BUILD_PLAN 6.6
``test_review_bypass``). The model may *add* a review flag (``needs_review``) but cannot
clear one the pipeline sets.
"""

from __future__ import annotations

import json
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

ModelBucket = Literal["y5", "y7", "y15", "y27_5", "y39"]
MAX_CITATIONS: Final = 5


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
    asset_class: str | None = Field(default=None, max_length=16)
    rationale: str = Field(default="", max_length=1500)
    citations: list[CitationClaim] = Field(default_factory=list, max_length=MAX_CITATIONS)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    needs_review: bool = False
    reason: str | None = Field(default=None, max_length=500)
