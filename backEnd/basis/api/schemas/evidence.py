"""Evidence (citations) response bodies. Citations are produced from S3 on."""

from __future__ import annotations

import uuid
from datetime import datetime

from basis.api.schemas.common import ResponseModel


class CitationOut(ResponseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    chunk_id: uuid.UUID
    excerpt: str
    verified_at: datetime | None
    verification_method: str | None
