"""Evidence response bodies: what supports each asset of a revision.

Per asset: the component it was classified from, the room, the photo documents (the
component's own photo as ``source``, the room's other photos as ``room``), the localized
instances of the component's label in those photos, and the rule citations with the cited
passage and whether it was verified against the reference text.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from basis.api.schemas.common import ResponseModel
from basis.api.schemas.objects import LocalizationStatusName, PhotoObjectOut
from basis.db.enums import Bucket


class CitationOut(ResponseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    chunk_id: uuid.UUID
    excerpt: str
    verified_at: datetime | None
    verification_method: str | None


class CitedChunkOut(ResponseModel):
    doc_id: str = Field(description="The reference document the passage is from.")
    page_start: int | None
    page_end: int | None
    section_path: str | None


class CitationEvidenceOut(CitationOut):
    verified: bool = Field(description="The excerpt was verified against the cited passage.")
    source: CitedChunkOut


class RoomRefOut(ResponseModel):
    id: uuid.UUID
    name: str
    room_type: str
    floor: str | None


class EvidencePhotoOut(ResponseModel):
    document_id: uuid.UUID
    filename: str
    role: Literal["source", "room"] = Field(
        description="`source`: the photo the component was found in; `room`: another photo "
        "of the same room."
    )
    localization_status: LocalizationStatusName | None = Field(
        description="The photo's localization outcome at this revision, if it ran."
    )
    instances: list[PhotoObjectOut] = Field(
        description="Active localized instances of the component's label in this photo."
    )


class AssetEvidenceOut(ResponseModel):
    asset_id: uuid.UUID
    category: str
    bucket: Bucket
    recovery_period: Decimal
    method: str
    needs_review: bool
    component_id: uuid.UUID | None
    component_label: str | None
    room: RoomRefOut | None
    photos: list[EvidencePhotoOut]
    citations: list[CitationEvidenceOut]


class EvidencePageOut(ResponseModel):
    revision: int = Field(description="The revision these assets belong to.")
    items: list[AssetEvidenceOut]
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next page (same revision); null on the last."
    )
