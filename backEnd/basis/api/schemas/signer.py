"""Your signer details: ``GET``/``PUT /v1/me/signer`` (docs/api/SIGNERS.md)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from basis.api.schemas.common import ResponseModel
from basis.report.inputs import SignerDetails


class SignerDetailsOut(ResponseModel):
    """Your signer details and their version (also the ``ETag``; 0 while none are saved)."""

    user_id: uuid.UUID
    signer_details: SignerDetails | None = Field(
        description="The name, credentials and license printed on every report you sign; "
        "null until you save them."
    )
    version: int = Field(description="0 while none are saved.")
    updated_at: datetime | None = Field(description="Null while none are saved.")


__all__ = ["SignerDetailsOut"]
