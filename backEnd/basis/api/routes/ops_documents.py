"""``GET /v1/ops/studies/{id}/documents`` and ``.../documents/{document_id}/content``: a
platform operator lists and opens the files a firm uploaded to a study, for support
(docs/api/OPS_DOCUMENTS.md; Bryan, 2026-11-02).

A firm's studies are visible only to its members; this is the one exception, for Basis
support: the first firms are new to the product and support has to see what they uploaded
(a missing-input record, docs/api/CLIENT_ERRORS.md 4.1, lists the study's documents by id).
Operators only (``OPS_OPERATOR_UIDS``, :func:`~basis.api.operators.require_operator`); a firm
role, owner included, never grants it.

Every open is recorded, as a cloud provider's access-transparency log records its staff's
access to a customer's content (who, what, when, why): before the bytes are read, a
``operator.document_accessed`` row is appended to the study's timeline (``actor_type``
``operator``, ``actor_id`` the operator's ``users`` row, ``entity_id`` the document,
``detail`` the reason, the file's name, kind and sha256, and the record it came from), so
the firm sees it on ``GET /v1/studies/{id}/timeline`` and the row can never be changed or
deleted (``forbid_mutation()``). ``reason`` is required: what the operator is helping with.

The bytes are served as the firm's own ``.../documents/{id}/content`` serves them (the
registered content type, ``Content-Disposition`` with the name, ``nosniff``), but never
cached (``no-store``: the operator's browser keeps no copy) and with no ``304``: each open
is an access, so each is recorded. Listing records nothing on the timeline (names and sizes,
no content); it is logged.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Final

from fastapi import Depends, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from basis.api.errors import Conflict, NotFound
from basis.api.operators import require_operator
from basis.api.routes.common import get_storage, problems, read_router
from basis.api.routes.documents import _content_disposition
from basis.api.schemas.ops_documents import OpsDocumentOut, OpsStudyDocumentsOut
from basis.api.users import CurrentUserDep, SessionmakerDep, StudyIdPath
from basis.db.models import Org, Study
from basis.db.repositories import DocumentRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import SessionDep, session_scope
from basis.errors.codes import E
from basis.storage import ObjectNotFound, Storage

ACCESS_EVENT: Final = "operator.document_accessed"
OPERATOR_ACTOR: Final = "operator"
MIN_REASON_CHARS: Final = 3
MAX_REASON_CHARS: Final = 300
NO_STORE: Final = "no-store"
DETAIL_STUDY_NOT_FOUND: Final = "Study not found."

logger = logging.getLogger(__name__)

reads = read_router(tags=["ops"])

StorageDep = Annotated[Storage, Depends(get_storage)]
DocumentIdPath = Annotated[uuid.UUID, Path(description="Document id.")]
Reason = Annotated[
    str,
    Query(
        min_length=MIN_REASON_CHARS,
        max_length=MAX_REASON_CHARS,
        description="Why the file is opened (what support is helping with); recorded on the "
        "study's timeline, which the firm sees. No client figures.",
    ),
]
ClientErrorId = Annotated[
    uuid.UUID | None,
    Query(description="The missing-input or browser error record the open comes from."),
]


async def _study(session: AsyncSession, study_id: uuid.UUID) -> Study:
    study = await session.get(Study, study_id)
    if study is None:
        raise NotFound(DETAIL_STUDY_NOT_FOUND, code=E.STUDY_NOT_FOUND)
    return study


@reads.get(
    "/ops/studies/{study_id}/documents",
    summary="A study's uploaded files, any firm (platform operators)",
    description="Every document of the study with its uploader, for support. Operators only "
    "(`OPS_OPERATOR_UIDS`); a firm role never grants it (docs/api/OPS_DOCUMENTS.md).",
    dependencies=[Depends(require_operator)],
    responses=problems(403, 404),
)
async def list_study_documents(
    study_id: StudyIdPath, user: CurrentUserDep, session: SessionDep
) -> OpsStudyDocumentsOut:
    study = await _study(session, study_id)
    firm = await session.get(Org, study.org_id)
    documents = await DocumentRepository(session).list_for_study(study_id)
    logger.info(
        "operator listed a study's documents",
        extra={"study_id": str(study_id), "org_id": str(study.org_id), "operator": str(user.id)},
    )
    return OpsStudyDocumentsOut(
        study_id=study.id,
        property_name=study.property_name,
        status=study.status,
        org_id=study.org_id,
        firm_name=None if firm is None else firm.name,
        documents=[OpsDocumentOut.model_validate(d) for d in documents],
    )


@reads.get(
    "/ops/studies/{study_id}/documents/{document_id}/content",
    summary="Open a firm's uploaded file (platform operators; every open is recorded)",
    description="The document's bytes, as the firm's own `.../content` serves them, never "
    "cached. Each open appends `operator.document_accessed` to the study's timeline (the "
    "operator, the reason, the file) before the bytes are read; the firm sees it. Operators "
    "only (`OPS_OPERATOR_UIDS`; docs/api/OPS_DOCUMENTS.md).",
    response_class=Response,
    dependencies=[Depends(require_operator)],
    responses={
        200: {"description": "The document's bytes, with its registered content type."},
        **problems(403, 404, 409),
    },
)
async def open_study_document(
    study_id: StudyIdPath,
    document_id: DocumentIdPath,
    reason: Reason,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    storage: StorageDep,
    client_error_id: ClientErrorId = None,
) -> Response:
    why = reason.strip()
    # The access is recorded and committed before storage is read, with no transaction open
    # while it is (like the firm's route): a recorded open whose bytes then fail to load is
    # still an access that was granted.
    async with session_scope(sessionmaker) as session:
        study = await _study(session, study_id)
        found = await DocumentRepository(session).get(study_id, document_id)
        if found is None:
            raise NotFound("Document not found.", code=E.UPLOAD_DOCUMENT_NOT_FOUND)
        if found.uploaded_at is None:
            raise Conflict(
                "The document's upload is not confirmed yet.", type_suffix="upload-missing"
            )
        uri, content_type, filename = found.gcs_uri, found.content_type, found.filename
        await StudyEventRepository(session).record(
            study_id,
            ACCESS_EVENT,
            actor=OPERATOR_ACTOR,
            actor_id=user.id,
            stage=study.current_stage,
            revision=study.revision,
            entity_type="document",
            entity_id=document_id,
            detail={
                "reason": why,
                "client_error_id": None if client_error_id is None else str(client_error_id),
                "filename": filename,
                "kind": found.kind.value,
                "sha256": found.sha256,
                "bytes": found.bytes,
            },
        )
    logger.info(
        "operator opened a firm's document",
        extra={
            "study_id": str(study_id),
            "document_id": str(document_id),
            "operator": str(user.id),
        },
    )
    try:
        data = await storage.read(uri)
    except ObjectNotFound:
        raise NotFound(
            "The document's bytes are missing from storage.", code=E.UPLOAD_BYTES_MISSING
        ) from None
    headers = {
        "Cache-Control": NO_STORE,
        "Content-Disposition": _content_disposition(filename),
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=data, media_type=content_type, headers=headers)
