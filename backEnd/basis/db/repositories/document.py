"""Documents of a study: registration, duplicate detection, upload confirmation."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from basis.db.enums import DocumentKind
from basis.db.errors import DuplicateDocumentError, NotVisibleError, constraint_name
from basis.db.models import Document

SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_DUPLICATE_CONSTRAINTS: Final = frozenset({"uq_documents_study_id_sha256", "uq_documents_gcs_uri"})


@dataclass(frozen=True, slots=True)
class Confirmed:
    """What :meth:`DocumentRepository.confirm_upload` did."""

    document: Document
    confirmed: bool


def normalize_sha256(value: str) -> str:
    """Lowercase hex digest; ``ValueError`` if it is not 64 hex characters."""
    digest = value.strip().lower()
    if not SHA256_RE.fullmatch(digest):
        raise ValueError("sha256 must be 64 hexadecimal characters")
    return digest


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        study_id: uuid.UUID,
        kind: DocumentKind,
        filename: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        gcs_uri: str,
        uploaded_by: uuid.UUID | None,
        document_id: uuid.UUID | None = None,
    ) -> Document:
        """Register a document (``uploaded_at`` stays NULL until :meth:`mark_uploaded`).

        ``document_id`` may be chosen by the caller so the storage URI can contain it.
        The insert runs in a SAVEPOINT: a duplicate raises ``DuplicateDocumentError`` and
        leaves the caller's transaction usable.
        """
        document = Document(
            study_id=study_id,
            kind=kind,
            filename=filename,
            content_type=content_type,
            bytes=size_bytes,
            sha256=normalize_sha256(sha256),
            gcs_uri=gcs_uri,
            uploaded_by=uploaded_by,
        )
        if document_id is not None:
            document.id = document_id
        try:
            async with self._session.begin_nested():
                self._session.add(document)
        except IntegrityError as exc:
            if constraint_name(exc) in _DUPLICATE_CONSTRAINTS:
                raise DuplicateDocumentError(
                    f"study {study_id} already has a document with this sha256 or URI"
                ) from exc
            raise
        await self._session.refresh(document)
        return document

    async def get(
        self, study_id: uuid.UUID, document_id: uuid.UUID, *, populate_existing: bool = False
    ) -> Document | None:
        stmt = select(Document).where(Document.id == document_id, Document.study_id == study_id)
        if populate_existing:
            stmt = stmt.execution_options(populate_existing=True)
        return (await self._session.scalars(stmt)).one_or_none()

    async def find_by_sha256(self, study_id: uuid.UUID, sha256: str) -> Document | None:
        stmt = select(Document).where(
            Document.study_id == study_id, Document.sha256 == normalize_sha256(sha256)
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def list_for_study(self, study_id: uuid.UUID) -> list[Document]:
        stmt = (
            select(Document)
            .where(Document.study_id == study_id)
            .order_by(Document.created_at, Document.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def has_uploaded(self, study_id: uuid.UUID, kind: DocumentKind) -> bool:
        """True when the study has at least one document of ``kind`` whose upload completed."""
        stmt = select(
            exists().where(
                Document.study_id == study_id,
                Document.kind == kind,
                Document.uploaded_at.is_not(None),
            )
        )
        return bool(await self._session.scalar(stmt))

    async def mark_uploaded(self, study_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        """Set ``uploaded_at = now()`` once. Idempotent: a second call returns the row as is."""
        return (await self.confirm_upload(study_id, document_id)).document

    async def confirm_upload(self, study_id: uuid.UUID, document_id: uuid.UUID) -> Confirmed:
        """:meth:`mark_uploaded`, saying whether this call set ``uploaded_at``: the
        compare-and-set (``WHERE uploaded_at IS NULL``) decides under the row lock, so of
        two confirmations at once exactly one is ``confirmed`` and records the upload
        (both recorded it before this; EVAL_REPORT 59)."""
        stmt = (
            update(Document)
            .where(
                Document.id == document_id,
                Document.study_id == study_id,
                Document.uploaded_at.is_(None),
            )
            .values(uploaded_at=func.now())
            .returning(Document)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        document = (await self._session.scalars(stmt)).one_or_none()
        if document is not None:
            return Confirmed(document, confirmed=True)
        existing = await self.get(study_id, document_id, populate_existing=True)
        if existing is None:
            raise NotVisibleError(f"document {document_id} not found in study {study_id}")
        return Confirmed(existing, confirmed=False)
