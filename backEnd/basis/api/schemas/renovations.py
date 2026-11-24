"""``GET`` and ``PUT /v1/studies/{id}/renovations`` (docs/api/RENOVATIONS.md)."""

from __future__ import annotations

import uuid

from basis.api.schemas.common import ResponseModel
from basis.domain.renovations import StudyRenovations


class RenovationsOut(ResponseModel):
    """The study's renovations (``null`` until saved) and their version (also the ``ETag``;
    0 while there are none)."""

    study_id: uuid.UUID
    renovations: StudyRenovations | None
    renovations_version: int


__all__ = ["RenovationsOut"]
