"""Document uploads: request a signed URL, then confirm (S1_SPEC sections 5 and 6).

Flow:

1. ``POST /v1/studies/{id}/documents`` registers the document (``uploaded_at`` NULL) at
   ``gs://<bucket>/studies/<study>/<document>/<filename>`` and returns a signed ``PUT``
   URL that accepts exactly the declared size and content type. A document with the same
   sha256 already in the study is a 409 naming the existing document.
2. The browser uploads the bytes straight to storage.
3. ``POST .../documents/{doc_id}/complete`` checks the object: it must exist, have the
   declared size (and content type, when storage reports one), and hash to the declared
   sha256. On success ``uploaded_at`` is set; confirming twice returns the same document.
   On a mismatch the object is deleted (its bytes are not what was registered) and the
   answer is 409; the client uploads again and confirms with a new idempotency key.

4. ``GET .../documents/{doc_id}/content`` returns a confirmed document's bytes to a viewer
   of the study (the frontend shows photos, their masks and the appraisal PDF from it).
   The bytes are immutable (they hash to the registered sha256), so the response is
   cacheable by the browser (private) with the sha256 as its strong ``ETag``. Like the
   completion check, it reads storage with no database connection held.

The completion check does not hold a database connection while it reads the object: it
looks the document up in one short transaction, verifies storage with no transaction
open, and sets ``uploaded_at`` in a second one.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Annotated, Final
from urllib.parse import quote

import anyio.to_thread
from fastapi import Depends, Path, Request, Response, status

from basis.api.errors import Conflict, NotFound
from basis.api.routes.common import (
    get_storage,
    get_storage_bucket,
    problems,
    read_router,
    write_router,
)
from basis.api.schemas.documents import (
    DocumentOut,
    SignedUploadOut,
    UploadRequest,
    UploadTicket,
)
from basis.api.users import (
    CurrentUserDep,
    SessionmakerDep,
    StudyIdPath,
    require_study_access,
    study_access,
)
from basis.db.enums import DocumentKind, OrgRole, StudyStatus
from basis.db.errors import DuplicateDocumentError
from basis.db.models import Study
from basis.db.repositories import DocumentRepository, VisibleStudy
from basis.db.session import SessionDep, session_scope
from basis.storage import ObjectNotFound, Storage, object_uri

UPLOAD_URL_TTL: Final = timedelta(minutes=15)
UPLOAD_ROLE: Final = OrgRole.ENGINEER
CONTENT_CACHE_CONTROL: Final = "private, max-age=3600"

reads = read_router(tags=["documents"])
writes = write_router(tags=["documents"])

StorageDep = Annotated[Storage, Depends(get_storage)]
BucketDep = Annotated[str, Depends(get_storage_bucket)]
DocumentIdPath = Annotated[uuid.UUID, Path(description="Document id.")]


def ensure_not_archived(study: Study) -> None:
    if study.status is StudyStatus.ARCHIVED:
        raise Conflict("The study is archived.", type_suffix="study-archived")


@writes.post(
    "/studies/{study_id}/documents",
    status_code=status.HTTP_201_CREATED,
    summary="Register a document and get a signed upload URL",
    responses=problems(403, 404),
)
async def request_upload(
    body: UploadRequest,
    access: Annotated[VisibleStudy, Depends(study_access(UPLOAD_ROLE))],
    user: CurrentUserDep,
    session: SessionDep,
    storage: StorageDep,
    bucket: BucketDep,
) -> UploadTicket:
    study = access.study
    ensure_not_archived(study)
    documents = DocumentRepository(session)
    document_id = uuid.uuid4()
    uri = object_uri(bucket, study.id, document_id, body.filename)
    try:
        document = await documents.create(
            study_id=study.id,
            kind=DocumentKind(body.kind),
            filename=body.filename,
            content_type=body.content_type,
            size_bytes=body.bytes,
            sha256=body.sha256,
            gcs_uri=uri,
            uploaded_by=user.id,
            document_id=document_id,
        )
    except DuplicateDocumentError:
        existing = await documents.find_by_sha256(study.id, body.sha256)
        named = f" (document {existing.id})" if existing is not None else ""
        raise Conflict(
            f"The study already has a document with this sha256{named}.",
            type_suffix="duplicate-document",
        ) from None
    signed = await storage.signed_put_url(uri, body.content_type, body.bytes, UPLOAD_URL_TTL)
    return UploadTicket(
        document=DocumentOut.model_validate(document),
        upload=SignedUploadOut(
            url=signed.url,
            method=signed.method,
            headers=signed.headers,
            expires_at=signed.expires_at,
        ),
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def verify_object(storage: Storage, document: DocumentOut, uri: str) -> None:
    """Raise 409 unless the stored object is exactly the registered document."""
    try:
        stat = await storage.stat(uri)
    except ObjectNotFound:
        raise Conflict(
            "No object has been uploaded for this document yet.", type_suffix="upload-missing"
        ) from None
    problem: str | None = None
    if stat.size != document.bytes:
        problem = f"the object has {stat.size} bytes; {document.bytes} were declared"
    elif stat.content_type is not None and stat.content_type != document.content_type:
        problem = "the object's content type differs from the declared one"
    else:
        digest = await anyio.to_thread.run_sync(_sha256, await storage.read(uri))
        if digest != document.sha256:
            problem = "the object's sha256 differs from the declared one"
    if problem is not None:
        await storage.delete(uri)
        raise Conflict(
            f"Upload rejected: {problem}. The object was deleted; upload the file again.",
            type_suffix="upload-mismatch",
        )


@writes.post(
    "/studies/{study_id}/documents/{document_id}/complete",
    summary="Confirm an upload after verifying the stored object",
    responses=problems(403, 404),
)
async def complete_upload(
    study_id: StudyIdPath,
    document_id: DocumentIdPath,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    storage: StorageDep,
) -> DocumentOut:
    async with session_scope(sessionmaker) as session:
        access = await require_study_access(session, user, study_id, UPLOAD_ROLE)
        ensure_not_archived(access.study)
        found = await DocumentRepository(session).get(study_id, document_id)
        if found is None:
            raise NotFound("Document not found.")
        document, uri = DocumentOut.model_validate(found), found.gcs_uri
    if document.uploaded_at is not None:
        return document
    await verify_object(storage, document, uri)
    async with session_scope(sessionmaker) as session:
        confirmed = await DocumentRepository(session).mark_uploaded(study_id, document_id)
        return DocumentOut.model_validate(confirmed)


def _content_disposition(filename: str) -> str:
    """``inline`` with the RFC 6266 / 8187 ``filename*`` (UTF-8) and an ASCII fallback."""
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "'")
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


@reads.get(
    "/studies/{study_id}/documents/{document_id}/content",
    summary="Download a confirmed document's bytes (photos, the appraisal)",
    response_class=Response,
    responses={
        200: {"description": "The document's bytes, with its registered content type."},
        304: {"description": "`If-None-Match` matched the document's sha256."},
        **problems(404, 409),
    },
)
async def document_content(
    request: Request,
    study_id: StudyIdPath,
    document_id: DocumentIdPath,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    storage: StorageDep,
) -> Response:
    # Like the completion check: one short transaction, then storage with none open.
    async with session_scope(sessionmaker) as session:
        await require_study_access(session, user, study_id, OrgRole.VIEWER)
        found = await DocumentRepository(session).get(study_id, document_id)
        if found is None:
            raise NotFound("Document not found.")
        uri, content_type, filename = found.gcs_uri, found.content_type, found.filename
        sha256, uploaded = found.sha256, found.uploaded_at is not None
    if not uploaded:
        raise Conflict("The document's upload is not confirmed yet.", type_suffix="upload-missing")
    etag = f'"{sha256}"'
    headers = {"ETag": etag, "Cache-Control": CONTENT_CACHE_CONTROL}
    if etag in [tag.strip() for tag in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    try:
        data = await storage.read(uri)
    except ObjectNotFound:
        raise NotFound("The document's bytes are missing from storage.") from None
    headers["Content-Disposition"] = _content_disposition(filename)
    headers["X-Content-Type-Options"] = "nosniff"
    return Response(content=data, media_type=content_type, headers=headers)
