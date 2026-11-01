"""The purge: delete a finished study's client files, keep its deliverables and its record.

The rule (``orgs.retention_days``, docs/api/RETENTION_AND_ALERTS.md section 1) selects the
studies: of a firm with a rule, ``completed`` or ``archived``, not purged yet, and whose row
last changed (``studies.updated_at``: the completion, the archive, a later report answer)
more than ``retention_days`` ago. The purge deletes every object under the study's prefix
in storage except ``exports/`` (the export JSON, the assets CSV, the calls transcript, the
report PDF and schedule files, and the signed report, which the report route also writes
there), marks the study (``purged_at``) and appends ``study.purged`` to its timeline. Every
database row stays: the audit record (timeline, decisions, corrections, provenance) must
outlive the files it is about, and the deliverables stay readable.

What goes, by prefix (``gs://<bucket>/studies/<id>/``): the uploaded documents and photos
(``<document>/<filename>``, ``documents.gcs_uri``), the rendered pages (``document_pages``),
the photo analyses (``analyses/``), the room reconciliations (``rooms/``) and the model
call payloads the ledger kept (``llm/``, prompts that embed the documents). The prefix
listing, not the database, is the source of truth for what exists, so an object a code
path wrote without a row is purged too; the database then checks that every document and
page row's object is among the deleted or was already gone (``E.RETENTION_OBJECT_MISSING``).

Order of operations and crashes: objects are deleted first, the mark and the event are
committed after. A crash in between leaves an unmarked study with fewer objects; the next
run lists what is left, deletes it and marks the study. A crash after the commit cannot
leave objects behind. Deleting is idempotent (``Storage.delete`` of a missing object is not
an error), so no run can fail because an earlier one got further.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import StudyStatus
from basis.db.models import Document, DocumentPage, Org, Study
from basis.db.repositories.study_event import StudyEventRepository
from basis.errors.codes import E
from basis.storage import Storage, StorageError, parse_uri

PURGEABLE: Final = (StudyStatus.COMPLETED, StudyStatus.ARCHIVED)
KEPT_PREFIX: Final = "exports/"
PURGED_EVENT: Final = "study.purged"
DEFAULT_LIMIT: Final = 100

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def study_prefix(bucket: str, study_id: uuid.UUID) -> str:
    """``gs://<bucket>/studies/<id>/``: everything the study owns in storage."""
    return f"gs://{bucket}/studies/{study_id}/"


@dataclass(frozen=True, slots=True)
class Candidate:
    study: Study
    org_id: uuid.UUID
    retention_days: int
    eligible_since: datetime  # updated_at + retention_days


@dataclass(slots=True)
class PurgeReport:
    study_id: uuid.UUID
    org_id: uuid.UUID
    retention_days: int
    dry_run: bool
    deleted: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    documents: int = 0
    pages: int = 0
    missing: list[str] = field(default_factory=list)  # document/page objects already gone
    status: str = "purged"  # purged | dry_run

    def as_json(self) -> dict[str, object]:
        return {
            "study_id": str(self.study_id),
            "org_id": str(self.org_id),
            "retention_days": self.retention_days,
            "status": self.status,
            "documents": self.documents,
            "pages": self.pages,
            "objects_deleted": len(self.deleted),
            "objects_kept": len(self.kept),
            "missing": self.missing,
            "kept": self.kept,
        }


async def select_candidates(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    org_id: uuid.UUID | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Candidate]:
    """The studies the rule selects now, oldest change first, at most ``limit``."""
    if limit < 1:
        raise ValueError("limit must be positive")
    now = now or utcnow()
    stmt = (
        select(Study, Org.retention_days)
        .join(Org, Org.id == Study.org_id)
        .where(
            Org.retention_days.is_not(None),
            Study.status.in_(PURGEABLE),
            Study.purged_at.is_(None),
            Study.updated_at + func.make_interval(0, 0, 0, Org.retention_days) < now,
        )
        .order_by(Study.updated_at, Study.id)
        .limit(limit)
    )
    if org_id is not None:
        stmt = stmt.where(Study.org_id == org_id)
    rows = (await session.execute(stmt)).all()
    candidates = []
    for study, days in rows:
        assert days is not None  # the WHERE clause requires a rule
        candidates.append(
            Candidate(
                study=study,
                org_id=study.org_id,
                retention_days=int(days),
                eligible_since=study.updated_at + timedelta(days=int(days)),
            )
        )
    return candidates


async def _owned_uris(session: AsyncSession, study_id: uuid.UUID) -> tuple[list[str], list[str]]:
    documents = list(
        (await session.scalars(select(Document.gcs_uri).where(Document.study_id == study_id))).all()
    )
    pages = list(
        (
            await session.scalars(
                select(DocumentPage.gcs_uri)
                .join(Document, Document.id == DocumentPage.document_id)
                .where(Document.study_id == study_id, DocumentPage.gcs_uri.is_not(None))
            )
        ).all()
    )
    return documents, [uri for uri in pages if uri is not None]


def partition(prefix: str, uris: list[str]) -> tuple[list[str], list[str]]:
    """``(to delete, to keep)`` of the objects under ``prefix``: ``exports/`` is kept."""
    kept_prefix = prefix + KEPT_PREFIX
    delete = [uri for uri in uris if not uri.startswith(kept_prefix)]
    keep = [uri for uri in uris if uri.startswith(kept_prefix)]
    return delete, keep


async def purge_study(
    session: AsyncSession,
    storage: Storage,
    bucket: str,
    candidate: Candidate,
    *,
    dry_run: bool,
    now: datetime | None = None,
) -> PurgeReport:
    """Purge one study (or report what would go). The caller commits ``session``; the
    objects are deleted before that commit (see the module docstring)."""
    now = now or utcnow()
    study = candidate.study
    prefix = study_prefix(bucket, study.id)
    report = PurgeReport(
        study_id=study.id,
        org_id=candidate.org_id,
        retention_days=candidate.retention_days,
        dry_run=dry_run,
        status="dry_run" if dry_run else "purged",
    )
    listed = await storage.list(prefix)
    for uri in listed:
        parse_uri(uri)  # every listed name is addressable; a bad one is a storage bug
    to_delete, to_keep = partition(prefix, listed)
    report.kept = to_keep
    documents, pages = await _owned_uris(session, study.id)
    report.documents, report.pages = len(documents), len(pages)
    present = set(listed)
    report.missing = sorted(uri for uri in (*documents, *pages) if uri not in present)
    for uri in report.missing:
        logger.warning(
            "purge: a document's object was already gone",
            extra={"error_code": E.RETENTION_OBJECT_MISSING, "study_id": str(study.id), "uri": uri},
        )
    if dry_run:
        report.deleted = to_delete
        return report
    for uri in to_delete:
        try:
            await storage.delete(uri)
        except (StorageError, OSError) as exc:
            logger.error(
                "purge: could not delete an object; the study stays unpurged",
                extra={
                    "error_code": E.RETENTION_DELETE_FAILED,
                    "study_id": str(study.id),
                    "uri": uri,
                    "exception_type": type(exc).__name__,
                },
            )
            raise
        report.deleted.append(uri)
    await session.execute(
        update(Study)
        .where(Study.id == study.id, Study.purged_at.is_(None))
        .values(purged_at=now)
        .execution_options(synchronize_session=False)
    )
    await StudyEventRepository(session).record(
        study.id,
        PURGED_EVENT,
        actor="system",
        stage=study.current_stage,
        revision=study.revision,
        detail={
            "retention_days": candidate.retention_days,
            "rule_from": study.updated_at.isoformat(),
            "eligible_since": candidate.eligible_since.isoformat(),
            "documents": report.documents,
            "pages": report.pages,
            "objects_deleted": len(report.deleted),
            "objects_kept": report.kept,
            "missing": report.missing,
        },
    )
    logger.info(
        "study purged",
        extra={
            "error_code": E.RETENTION_PURGED,
            "study_id": str(study.id),
            "objects_deleted": len(report.deleted),
            "objects_kept": len(report.kept),
        },
    )
    return report
