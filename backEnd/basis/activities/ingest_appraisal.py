"""``ingest_appraisal`` (Stage 2 stub): the study's evidence corpus from its appraisal.

Stage 2 body: find the study's uploaded appraisal document, create the study corpus
version for this revision, and write three placeholder chunks (one per "page"). Stage 3
replaces the body with layout parsing, table extraction, chunking and embeddings; the
contract (ids in, ids and counts out) stays.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import (
    STUB_EMBEDDING_MODEL,
    heartbeat,
    parse_uuid,
    run_once,
    validation_error,
)
from basis.activities.deps import Deps
from basis.db.enums import DocumentKind
from basis.db.models import Document
from basis.db.repositories.corpus import CorpusRepository
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.study import StudyRepository
from basis.workflow.contracts import IngestAppraisalInput, IngestAppraisalOutput

PLACEHOLDER_PAGES: Final = 3


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
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> IngestAppraisalOutput:
            if await StudyRepository(session).get(study_id) is None:
                raise validation_error(f"study {study_id} not found")
            documents = await DocumentRepository(session).list_for_study(study_id)
            document = appraisal_document(documents)
            if document is None:
                return IngestAppraisalOutput(document_id=None, corpus_version_id=None, chunks=0)
            corpus = CorpusRepository(session)
            version = await corpus.study_version(study_id, input.revision, STUB_EMBEDDING_MODEL)
            for page in range(1, PLACEHOLDER_PAGES + 1):
                heartbeat(page)
                await corpus.add_chunk(
                    corpus_version_id=version.id,
                    doc_id=str(document.id),
                    chunk_key=f"{document.id}:p{page}",
                    text=f"Placeholder text for page {page} of {document.filename}.",
                    page_start=page,
                    page_end=page,
                    section_path=f"page {page}",
                )
            return IngestAppraisalOutput(
                document_id=str(document.id),
                corpus_version_id=str(version.id),
                chunks=PLACEHOLDER_PAGES,
            )

        return await run_once(
            self._deps,
            name="ingest_appraisal",
            key=input.idempotency_key,
            output_type=IngestAppraisalOutput,
            work=work,
        )
