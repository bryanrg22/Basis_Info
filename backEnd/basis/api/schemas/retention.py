"""``/v1/orgs/{org_id}/retention``: the firm's rule for client data."""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from pydantic import Field

from basis.api.schemas.common import JSON_NUMBER, RequestModel, ResponseModel

# Ten years: longer than any tax record rule a firm cites; the column itself allows more.
MAX_RETENTION_DAYS: Final = 3650

RetentionDays = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_RETENTION_DAYS,
        description="Days after a study completes or is archived until its uploaded files "
        "are purged from storage. `null`: keep everything (the default).",
    ),
    JSON_NUMBER,
]


class RetentionIn(RequestModel):
    retention_days: RetentionDays | None


class RetentionOut(ResponseModel):
    org_id: uuid.UUID
    retention_days: int | None = Field(
        description="The firm's rule (`null`: keep everything). The purge job "
        "(`python -m basis.retention purge`) applies it to completed and archived studies "
        "whose row last changed more than this many days ago, deleting their uploaded "
        "documents, photos, pages and model payloads and keeping the export, the signed "
        "report and the timeline (`study.purged`)."
    )
