"""``/v1/ops/studies/{id}/documents``: a study's uploads as a platform operator sees them
(docs/api/OPS_DOCUMENTS.md)."""

from __future__ import annotations

import uuid

from pydantic import Field

from basis.api.schemas.common import ResponseModel
from basis.api.schemas.documents import DocumentOut
from basis.db.enums import StudyStatus


class OpsDocumentOut(DocumentOut):
    uploaded_by: uuid.UUID | None = Field(description="The user who registered the upload.")


class OpsStudyDocumentsOut(ResponseModel):
    study_id: uuid.UUID
    property_name: str
    status: StudyStatus
    org_id: uuid.UUID
    firm_name: str | None
    documents: list[OpsDocumentOut] = Field(
        description="Every document of the study, oldest first, confirmed or not."
    )
