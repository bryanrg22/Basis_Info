"""``ingest_appraisal``: the study's evidence corpus from its appraisal's report PDF.

Picks the study's extraction source and report PDF (:mod:`basis.activities.appraisal_sources`),
downloads them and ingests the report PDF with the retrieval pipeline into the study corpus
version numbered after the input revision: layout parsing, whole tables in
``evidence_tables`` with surrogate chunks, 400/80-token text chunks per page, embeddings, then
publish. Every chunk's ``doc_id`` is the document the PDF was read from (the PDF, the UAD 3.6
ZIP that holds it, or a bare XML's companion PDF), so a citation names its source.

What is ingested, by upload (docs/api/UAD36_INTAKE.md):

* a PDF (legacy 1004 or UAD 3.6): the PDF, unchanged;
* a UAD 3.6 ZIP: the package's report PDF (:mod:`basis.extraction.package` chooses and
  validates it); the ZIP stays the extraction source;
* a bare MISMO 3.6 XML: its companion PDF, if the study has one; else nothing (status
  ``no_pdf``: no corpus version is written, the classifier's study search answers that no
  study corpus exists, and the extraction has no regions).

The output says which (``status``, ``pdf_document_id``); the workflow passes the report PDF to
every ``extract_fields`` of the study.

Idempotency, two layers:

* the activity key (``run_once``): a retried or replayed execution returns the stored
  output without touching storage or the database;
* the business key (study, revision): a new key for a revision that already has a
  published study corpus (a reset execution, a retry after a failed extraction) returns that
  version, and its report PDF is the document its chunks came from. Every row of the version
  is written in the same transaction as the activity's completion, so a failure part-way
  leaves no version, chunk or table behind; two executions racing on one revision collide on
  the version's unique key and the loser retries into the "already published" branch. An
  *unpublished* version for the revision can only be a Stage 2 stub leftover (placeholder
  chunks, model ``stub``); it is deleted and the revision ingested properly, unless a stub
  citation still references it (then a non-retryable error says so).

Errors: a study that does not exist, an appraisal whose object is missing from storage, a
package or XML :mod:`basis.extraction.package` refuses (no or several PDFs, no MISMO XML, a
hostile XML, unsafe paths, encrypted entries, archive limits), a companion that is not a PDF,
and a PDF that cannot be parsed (not a PDF, no pages, too many pages, no text) are
non-retryable ``ValidationError``s, raised before any PDF parser sees a ZIP or an XML. Storage
and database outages propagate and are retried. A scanned appraisal (no text layer) is
ingested as an empty published version (status ``scanned``) and logged; it is not an error,
because extraction has its own OCR path.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import heartbeat, parse_uuid, run_once, validation_error
from basis.activities.appraisal_sources import (
    companion_pdf,
    extraction_source,
    is_bare_xml,
    upload_kind,
)
from basis.activities.deps import Deps
from basis.db.models import Chunk, Document
from basis.db.repositories.activity_result import COMPLETED, ActivityResultRepository
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.study import StudyRepository
from basis.db.session import session_scope
from basis.extraction.package import InvalidPackage, is_pdf, read_appraisal_upload
from basis.retrieval import Retrieval
from basis.retrieval.ingest import PreparedIngest, SourcePdf, prepare_documents, write_prepared
from basis.retrieval.parse import PdfError
from basis.retrieval.store import CorpusError, PostgresChunkStore
from basis.storage import ObjectNotFound
from basis.workflow.contracts import IngestAppraisalInput, IngestAppraisalOutput

logger = logging.getLogger("basis.activities.ingest_appraisal")


def appraisal_document(documents: list[Document]) -> Document | None:
    """The extraction source (:func:`basis.activities.appraisal_sources.extraction_source`)."""
    return extraction_source(documents)


@dataclass(frozen=True)
class _Sources:
    source: Document
    companion: Document | None  # only looked at when the source is a bare XML

    def ids(self) -> tuple[uuid.UUID, uuid.UUID | None]:
        return self.source.id, None if self.companion is None else self.companion.id


@dataclass(frozen=True)
class _Plan:
    """What to ingest: the report PDF's document and its prepared chunks, or nothing."""

    sources: _Sources
    pdf_document: Document | None
    prepared: PreparedIngest | None


class IngestAppraisal:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="ingest_appraisal")
    async def run(self, input: IngestAppraisalInput) -> IngestAppraisalOutput:
        """Read, then download, parse and embed with no database connection held, then
        write the version in ``run_once``'s transaction.

        The embedding takes seconds of CPU (8 s a study with ten ingesting at once on the
        production embedder, EVAL_REPORT 32.4); inside the transaction it held a pooled
        connection all along, and ten studies starting together starved the worker's pool
        (21 failed attempts, mostly ``analyze_photo``). A failure of the preparation is
        raised inside ``run_once``, so it is recorded on the activity key as before.
        """
        study_id = parse_uuid(input.study_id, "study_id")
        retrieval = self._deps.retrieval_deps()
        sources: _Sources | None = None
        async with session_scope(self._deps.sessionmaker) as session:
            claimed = await ActivityResultRepository(session).get(input.idempotency_key)
            if claimed is None or claimed.status != COMPLETED:
                sources = await _to_ingest(session, retrieval, study_id, input.revision)
        plan: _Plan | None = None
        failure: Exception | None = None
        if sources is not None:
            try:
                plan = await self._prepare(sources, study_id)
            except Exception as exc:  # raised inside run_once below, so it is recorded
                failure = exc

        async def work(session: AsyncSession) -> IngestAppraisalOutput:
            if await StudyRepository(session).get(study_id) is None:
                raise validation_error(f"study {study_id} not found")
            current = await _sources(session, study_id)
            if current is None:
                return IngestAppraisalOutput(
                    document_id=None, corpus_version_id=None, chunks=0, status="no_appraisal"
                )
            source_id = str(current.source.id)
            store = PostgresChunkStore(session=session, registry=retrieval.registry)
            existing = await store.find_version("study", study_id, input.revision)
            if existing is not None and existing.published:
                chunks = await store.count_chunks(existing.id)
                pdf_id = await _ingested_doc(session, existing.id)
                if pdf_id is None:  # scanned (no chunks): the rule's choice
                    expected = current.companion if is_bare_xml(current.source) else current.source
                    pdf_id = None if expected is None else str(expected.id)
                return IngestAppraisalOutput(
                    document_id=source_id,
                    corpus_version_id=str(existing.id),
                    chunks=chunks,
                    pdf_document_id=pdf_id,
                    status="ingested" if chunks else "scanned",
                )
            if failure is not None:
                raise failure
            if existing is not None:  # a Stage 2 stub leftover (placeholder chunks)
                try:
                    await store.discard_unpublished(existing.id)
                except CorpusError as exc:
                    raise validation_error(f"study {study_id}: {exc}") from exc
                logger.info(
                    "discarded an unpublished study corpus version",
                    extra={"corpus_version_id": str(existing.id), "study_id": str(study_id)},
                )
            ready = plan
            if ready is None or ready.sources.ids() != current.ids():
                # Only when the first read saw nothing to do (or other documents) and this
                # one does (a race): prepare here, holding the transaction, as before
                # EVAL_REPORT 32.
                ready = await self._prepare(current, study_id)
            if ready.prepared is None or ready.pdf_document is None:
                logger.warning(
                    "UAD 3.6 XML without a report PDF; no study corpus",
                    extra={"document_id": source_id, "study_id": str(study_id)},
                )
                return IngestAppraisalOutput(
                    document_id=source_id, corpus_version_id=None, chunks=0, status="no_pdf"
                )
            result = await write_prepared(
                ready.prepared, store, version=input.revision, progress=lambda step: heartbeat(step)
            )
            if result.scanned_docs:
                logger.warning(
                    "appraisal has no text layer; study corpus is empty",
                    extra={"document_id": str(ready.pdf_document.id), "study_id": str(study_id)},
                )
            return IngestAppraisalOutput(
                document_id=source_id,
                corpus_version_id=str(result.corpus_version_id),
                chunks=result.chunks,
                pdf_document_id=str(ready.pdf_document.id),
                status="scanned" if result.scanned_docs else "ingested",
            )

        return await run_once(
            self._deps,
            name="ingest_appraisal",
            key=input.idempotency_key,
            output_type=IngestAppraisalOutput,
            work=work,
        )

    async def _prepare(self, sources: _Sources, study_id: uuid.UUID) -> _Plan:
        """Download the source (and a bare XML's companion), validate a package or XML, then
        parse, chunk and embed the report PDF (no database access)."""
        source = sources.source
        data = await _download(self._deps, source)
        heartbeat("downloaded", len(data))
        kind = upload_kind(data)
        pdf_document: Document | None = source
        pdf_bytes: bytes | None = data
        if kind != "pdf":
            try:  # the package's own limits; never a PDF parser on a ZIP or an XML
                upload = await asyncio.to_thread(read_appraisal_upload, data)
            except InvalidPackage as exc:
                raise validation_error(f"appraisal {source.id}: {exc}") from exc
            pdf_bytes = upload.pdf
            if upload.kind == "xml":
                pdf_document = sources.companion
                pdf_bytes = None
                if pdf_document is not None:
                    pdf_bytes = await _download(self._deps, pdf_document)
                    if not is_pdf(pdf_bytes):
                        raise validation_error(
                            f"appraisal {pdf_document.id}, the report PDF of the UAD 3.6 XML "
                            f"{source.id}, is not a PDF"
                        )
        if pdf_document is None or pdf_bytes is None:
            return _Plan(sources=sources, pdf_document=None, prepared=None)
        settings = self._deps.settings
        try:
            prepared = await prepare_documents(
                [SourcePdf(str(pdf_document.id), pdf_bytes)],
                corpus="study",
                study_id=study_id,
                embedder=self._deps.retrieval_deps().embedder,
                carry_forward=False,
                progress=lambda step: heartbeat(step),
                chunk_tokens=settings.retrieval_chunk_tokens,
                overlap_tokens=settings.retrieval_chunk_overlap,
            )
        except PdfError as exc:
            raise validation_error(f"appraisal {pdf_document.id}: {exc}") from exc
        return _Plan(sources=sources, pdf_document=pdf_document, prepared=prepared)


async def _sources(session: AsyncSession, study_id: uuid.UUID) -> _Sources | None:
    documents = await DocumentRepository(session).list_for_study(study_id)
    source = extraction_source(documents)
    if source is None:
        return None
    return _Sources(source=source, companion=companion_pdf(documents, source))


async def _ingested_doc(session: AsyncSession, corpus_version_id: uuid.UUID) -> str | None:
    """The document a published study version's chunks came from (None: no chunks)."""
    found = await session.scalars(
        select(Chunk.doc_id).where(Chunk.corpus_version_id == corpus_version_id).distinct()
    )
    doc_ids = sorted(d for d in found.all() if d)
    return doc_ids[0] if doc_ids else None


async def _to_ingest(
    session: AsyncSession, retrieval: Retrieval, study_id: uuid.UUID, revision: int
) -> _Sources | None:
    """The sources to prepare outside the transaction, or None when ``work`` will not
    ingest anything (no study, no appraisal, or the revision's version is published)."""
    if await StudyRepository(session).get(study_id) is None:
        return None
    sources = await _sources(session, study_id)
    if sources is None:
        return None
    store = PostgresChunkStore(session=session, registry=retrieval.registry)
    existing = await store.find_version("study", study_id, revision)
    return None if existing is not None and existing.published else sources


async def _download(deps: Deps, document: Document) -> bytes:
    try:
        return await deps.storage.read(document.gcs_uri)
    except ObjectNotFound as exc:
        # The row says "uploaded" but the object is gone: retrying cannot bring it back.
        raise validation_error(
            f"appraisal {document.id} is missing from storage ({document.gcs_uri})"
        ) from exc
