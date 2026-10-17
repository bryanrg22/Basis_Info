"""``ingest_appraisal``: the study's evidence corpus from its appraisal PDF.

Downloads the study's uploaded appraisal from storage and ingests it with
:func:`basis.retrieval.ingest.ingest_pdf` into the study corpus version numbered after
the input revision: layout parsing, whole tables in ``evidence_tables`` with surrogate
chunks, 400/80-token text chunks per page, embeddings, then publish. The S2 contract is
unchanged (ids in; document id, corpus version id and chunk count out).

Idempotency, two layers:

* the activity key (``run_once``): a retried or replayed execution returns the stored
  output without touching storage or the database;
* the business key (study, revision): a new key for a revision that already has a
  published study corpus (a reset execution) returns that version instead of ingesting
  again. Every row of the version is written in the same transaction as the activity's
  completion, so a failure part-way leaves no version, chunk or table behind; two
  executions racing on one revision collide on the version's unique key and the loser
  retries into the "already published" branch. An *unpublished* version for the
  revision can only be a Stage 2 stub leftover (placeholder chunks, model ``stub``); it
  is deleted and the revision ingested properly, unless a stub citation still references
  it (then a non-retryable error says so).

Errors: a study that does not exist, an appraisal whose object is missing from storage,
and a PDF that cannot be parsed (not a PDF, no pages, too many pages, no text) are
non-retryable ``ValidationError``s. Storage and database outages propagate and are
retried. A scanned appraisal (no text layer) is ingested as an empty published version
and logged; it is not an error, because extraction has its own OCR path.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import heartbeat, parse_uuid, run_once, validation_error
from basis.activities.deps import Deps
from basis.db.enums import DocumentKind
from basis.db.models import Document
from basis.db.repositories.activity_result import COMPLETED, ActivityResultRepository
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.study import StudyRepository
from basis.db.session import session_scope
from basis.retrieval import Retrieval
from basis.retrieval.ingest import PreparedIngest, SourcePdf, prepare_documents, write_prepared
from basis.retrieval.parse import PdfError
from basis.retrieval.store import CorpusError, PostgresChunkStore
from basis.storage import ObjectNotFound
from basis.workflow.contracts import IngestAppraisalInput, IngestAppraisalOutput

logger = logging.getLogger("basis.activities.ingest_appraisal")


def appraisal_document(documents: list[Document]) -> Document | None:
    """The first uploaded appraisal (documents are ordered by creation)."""
    for document in documents:
        if document.kind is DocumentKind.APPRAISAL and document.uploaded_at is not None:
            return document
    return None


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
        document: Document | None = None
        async with session_scope(self._deps.sessionmaker) as session:
            claimed = await ActivityResultRepository(session).get(input.idempotency_key)
            if claimed is None or claimed.status != COMPLETED:
                document = await _to_ingest(session, retrieval, study_id, input.revision)
        prepared: PreparedIngest | None = None
        failure: Exception | None = None
        if document is not None:
            try:
                prepared = await self._prepare(document, study_id)
            except Exception as exc:  # raised inside run_once below, so it is recorded
                failure = exc

        async def work(session: AsyncSession) -> IngestAppraisalOutput:
            if await StudyRepository(session).get(study_id) is None:
                raise validation_error(f"study {study_id} not found")
            documents = await DocumentRepository(session).list_for_study(study_id)
            document = appraisal_document(documents)
            if document is None:
                return IngestAppraisalOutput(document_id=None, corpus_version_id=None, chunks=0)
            store = PostgresChunkStore(session=session, registry=retrieval.registry)
            existing = await store.find_version("study", study_id, input.revision)
            if existing is not None and existing.published:
                return IngestAppraisalOutput(
                    document_id=str(document.id),
                    corpus_version_id=str(existing.id),
                    chunks=await store.count_chunks(existing.id),
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
            ready = prepared
            if ready is None or ready.doc_ids != [str(document.id)]:
                # Only when the first read saw nothing to do and this one does (a race):
                # prepare here, holding the transaction, as before EVAL_REPORT 32.
                ready = await self._prepare(document, study_id)
            result = await write_prepared(
                ready, store, version=input.revision, progress=lambda step: heartbeat(step)
            )
            if result.scanned_docs:
                logger.warning(
                    "appraisal has no text layer; study corpus is empty",
                    extra={"document_id": str(document.id), "study_id": str(study_id)},
                )
            return IngestAppraisalOutput(
                document_id=str(document.id),
                corpus_version_id=str(result.corpus_version_id),
                chunks=result.chunks,
            )

        return await run_once(
            self._deps,
            name="ingest_appraisal",
            key=input.idempotency_key,
            output_type=IngestAppraisalOutput,
            work=work,
        )

    async def _prepare(self, document: Document, study_id: uuid.UUID) -> PreparedIngest:
        """Download, parse, chunk and embed the appraisal (no database access)."""
        pdf_bytes = await _download(self._deps, document)
        heartbeat("downloaded", len(pdf_bytes))
        settings = self._deps.settings
        try:
            return await prepare_documents(
                [SourcePdf(str(document.id), pdf_bytes)],
                corpus="study",
                study_id=study_id,
                embedder=self._deps.retrieval_deps().embedder,
                carry_forward=False,
                progress=lambda step: heartbeat(step),
                chunk_tokens=settings.retrieval_chunk_tokens,
                overlap_tokens=settings.retrieval_chunk_overlap,
            )
        except PdfError as exc:
            raise validation_error(f"appraisal {document.id}: {exc}") from exc


async def _to_ingest(
    session: AsyncSession, retrieval: Retrieval, study_id: uuid.UUID, revision: int
) -> Document | None:
    """The appraisal to prepare outside the transaction, or None when ``work`` will not
    ingest anything (no study, no appraisal, or the revision's version is published)."""
    if await StudyRepository(session).get(study_id) is None:
        return None
    document = appraisal_document(await DocumentRepository(session).list_for_study(study_id))
    if document is None:
        return None
    store = PostgresChunkStore(session=session, registry=retrieval.registry)
    existing = await store.find_version("study", study_id, revision)
    return None if existing is not None and existing.published else document


async def _download(deps: Deps, document: Document) -> bytes:
    try:
        return await deps.storage.read(document.gcs_uri)
    except ObjectNotFound as exc:
        # The row says "uploaded" but the object is gone: retrying cannot bring it back.
        raise validation_error(
            f"appraisal {document.id} is missing from storage ({document.gcs_uri})"
        ) from exc
