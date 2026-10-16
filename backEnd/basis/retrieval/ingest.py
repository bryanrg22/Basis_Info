"""Ingestion: PDF -> parsed pages -> tables + chunks -> embeddings -> a published version.

Replaces ``evidence_layer/src/ingest.py`` (JSONL files, pickled BM25, FAISS, a JSON
manifest) with rows in PostgreSQL. Each run creates a **new, immutable**
``corpus_versions`` row; nothing already published is modified.

* ``ingest_documents`` puts one or more PDFs into a new version. For the reference
  corpus the other documents of the latest published version are carried forward by
  default (re-ingesting ``SYN_PUB_B`` alone yields a version with A, the new B, and C),
  so a version is always a complete corpus and old versions keep resolving old
  citations. ``carry_forward=False`` starts an empty corpus.
* ``ingest_pdf`` is the one-document form (S3_SPEC signature).
* Scanned (image-only) documents are not silently empty: they are recorded in
  ``IngestResult.scanned_docs``. A document that is neither scanned nor has any text
  raises :class:`NoTextError`.

CLI (reference corpus only; study corpora are written by the ``ingest_appraisal``
activity)::

    python -m basis.retrieval.ingest --corpus reference --pdf PATH --doc-id ID \\
        [--pdf PATH2 --doc-id ID2 ...] [--fresh] [--max-pages N]
    python -m basis.retrieval.ingest --list-versions [--corpus study --study-id UUID]

It reads ``BASIS_ENV``, ``DATABASE_URL``, ``EMBEDDING_MODEL`` and the chunk window
(``RETRIEVAL_CHUNK_TOKENS``, ``RETRIEVAL_CHUNK_OVERLAP``) like every process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final

from basis.retrieval.chunk import (
    CHUNK_TOKENS,
    OVERLAP_TOKENS,
    ChunkDraft,
    chunk_document,
    chunker_version,
)
from basis.retrieval.embed import Embedder
from basis.retrieval.parse import (
    REFERENCE_MAX_PAGES,
    STUDY_MAX_PAGES,
    PdfError,
    parse_pdf,
)
from basis.retrieval.store import CorpusWriter, NewChunk
from basis.retrieval.types import CorpusName

logger = logging.getLogger("basis.retrieval.ingest")

EMBED_BATCH: Final = 64

Progress = Callable[[str], None]


class NoTextError(PdfError):
    """The PDF has pages but neither text nor a scanned image on any of them."""


@dataclass(frozen=True, slots=True)
class SourcePdf:
    doc_id: str
    data: bytes


@dataclass(frozen=True, slots=True)
class DocStats:
    doc_id: str
    pages: int
    scanned_pages: list[int]
    text_chunks: int
    table_chunks: int
    tables: int


@dataclass(frozen=True, slots=True)
class IngestResult:
    corpus_version_id: uuid.UUID
    version: int
    corpus: CorpusName
    study_id: uuid.UUID | None
    embedding_model: str
    documents: list[DocStats]
    carried_forward_chunks: int
    seconds: float
    scanned_docs: list[str] = field(default_factory=list)
    chunker: str = field(default_factory=chunker_version)

    @property
    def chunks(self) -> int:
        """Chunks this run created from its own documents (carried-forward excluded)."""
        return sum(d.text_chunks + d.table_chunks for d in self.documents)

    @property
    def total_chunks(self) -> int:
        return self.chunks + self.carried_forward_chunks

    def summary(self) -> dict[str, object]:
        return {
            "corpus_version_id": str(self.corpus_version_id),
            "corpus": self.corpus,
            "study_id": None if self.study_id is None else str(self.study_id),
            "version": self.version,
            "embedding_model": self.embedding_model,
            "chunker": self.chunker,
            "chunks": self.chunks,
            "carried_forward_chunks": self.carried_forward_chunks,
            "scanned_docs": self.scanned_docs,
            "documents": [asdict(d) for d in self.documents],
            "seconds": round(self.seconds, 3),
        }


def _noop(_: str) -> None:
    return None


async def ingest_documents(
    sources: Sequence[SourcePdf],
    *,
    corpus: CorpusName,
    study_id: uuid.UUID | None,
    store: CorpusWriter,
    embedder: Embedder,
    version: int | None = None,
    carry_forward: bool | None = None,
    max_pages: int | None = None,
    progress: Progress = _noop,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> IngestResult:
    """Parse, chunk, embed and store ``sources`` as a new published corpus version.

    Every write goes through ``store``; give it a session-bound store so the whole
    version commits or rolls back as one unit. Raises :class:`PdfError` subclasses for
    unusable PDFs before anything is written. ``chunk_tokens`` / ``overlap_tokens`` are
    the chunk window (``RETRIEVAL_CHUNK_TOKENS`` / ``RETRIEVAL_CHUNK_OVERLAP``). Carried
    forward documents keep the chunks they were ingested with: every chunk is a slice of
    one page either way, so a version may mix window sizes, but not embedders.
    """
    if not sources:
        raise ValueError("nothing to ingest")
    doc_ids = [source.doc_id for source in sources]
    if len(set(doc_ids)) != len(doc_ids):
        raise ValueError("doc ids must be unique within one ingest")
    if carry_forward is None:
        carry_forward = corpus == "reference"
    page_limit = max_pages or (REFERENCE_MAX_PAGES if corpus == "reference" else STUDY_MAX_PAGES)
    started = time.perf_counter()

    parsed = []
    for source in sources:
        document = await asyncio.to_thread(
            parse_pdf, source.data, source.doc_id, max_pages=page_limit
        )
        if not document.has_text and not document.scanned:
            raise NoTextError(f"{source.doc_id}: no text and no scanned pages")
        parsed.append(document)
        progress(f"parsed {source.doc_id}: {document.page_count} pages")

    drafts_per_doc = [
        chunk_document(document, chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens)
        for document in parsed
    ]
    all_drafts = [draft for drafts in drafts_per_doc for draft in drafts]
    embeddings = await _embed(embedder, [d.text for d in all_drafts], progress)

    previous = await store.latest_version(corpus, study_id) if carry_forward else None
    if previous is not None and previous.embedding_model != embedder.name:
        raise ValueError(
            f"cannot carry forward version {previous.version} embedded with "
            f"{previous.embedding_model!r} into a {embedder.name!r} version; use --fresh"
        )
    info = await store.create_version(corpus, study_id, embedder.name, version)
    carried = 0
    if previous is not None:
        carried = await store.copy_documents(previous.id, info.id, exclude=doc_ids)
    table_ids = await store.insert_tables(info.id, [t for doc in parsed for t in doc.tables])
    await store.insert_chunks(
        info.id,
        [
            _new_chunk(draft, vector, table_ids)
            for draft, vector in zip(all_drafts, embeddings, strict=True)
        ],
    )
    await store.publish_version(info.id)
    progress(f"published version {info.version}")

    stats = [
        DocStats(
            doc_id=document.doc_id,
            pages=document.page_count,
            scanned_pages=document.scanned_pages,
            text_chunks=sum(1 for d in drafts if d.table_key is None),
            table_chunks=sum(1 for d in drafts if d.table_key is not None),
            tables=len(document.tables),
        )
        for document, drafts in zip(parsed, drafts_per_doc, strict=True)
    ]
    result = IngestResult(
        corpus_version_id=info.id,
        version=info.version,
        corpus=corpus,
        study_id=study_id,
        embedding_model=embedder.name,
        documents=stats,
        carried_forward_chunks=carried,
        seconds=time.perf_counter() - started,
        scanned_docs=[document.doc_id for document in parsed if document.scanned],
        chunker=chunker_version(chunk_tokens, overlap_tokens),
    )
    logger.info("corpus version ingested", extra=result.summary())
    return result


async def ingest_pdf(
    pdf_bytes: bytes,
    doc_id: str,
    corpus: CorpusName,
    study_id: uuid.UUID | None,
    store: CorpusWriter,
    embedder: Embedder,
    *,
    version: int | None = None,
    carry_forward: bool | None = None,
    max_pages: int | None = None,
    progress: Progress = _noop,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> IngestResult:
    """One PDF into a new published version (see :func:`ingest_documents`)."""
    return await ingest_documents(
        [SourcePdf(doc_id, pdf_bytes)],
        corpus=corpus,
        study_id=study_id,
        store=store,
        embedder=embedder,
        version=version,
        carry_forward=carry_forward,
        max_pages=max_pages,
        progress=progress,
        chunk_tokens=chunk_tokens,
        overlap_tokens=overlap_tokens,
    )


async def _embed(embedder: Embedder, texts: list[str], progress: Progress) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start : start + EMBED_BATCH]
        vectors.extend(await asyncio.to_thread(embedder.embed, batch))
        progress(f"embedded {min(start + EMBED_BATCH, len(texts))}/{len(texts)}")
    if len(vectors) != len(texts):
        raise RuntimeError("embedder returned a different number of vectors than texts")
    return vectors


def _new_chunk(draft: ChunkDraft, vector: list[float], table_ids: dict[str, uuid.UUID]) -> NewChunk:
    table_id = None
    if draft.table_key is not None:
        table_id = table_ids[draft.table_key]
    return NewChunk(
        doc_id=draft.doc_id,
        chunk_key=draft.chunk_key,
        page_start=draft.page_start,
        page_end=draft.page_end,
        section_path=draft.section_path,
        text=draft.text,
        embedding=vector,
        table_id=table_id,
    )


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m basis.retrieval.ingest",
        description="Ingest reference PDFs into a new corpus version, or list versions.",
    )
    parser.add_argument("--corpus", choices=("reference", "study"), default="reference")
    parser.add_argument("--pdf", action="append", type=Path, default=[], help="PDF to ingest")
    parser.add_argument(
        "--doc-id", action="append", default=[], help="document id, one per --pdf, same order"
    )
    parser.add_argument(
        "--fresh", action="store_true", help="do not carry the latest version's documents forward"
    )
    parser.add_argument("--max-pages", type=int, default=REFERENCE_MAX_PAGES)
    parser.add_argument("--list-versions", action="store_true")
    parser.add_argument("--study-id", type=uuid.UUID, help="with --list-versions --corpus study")
    return parser


async def _run(args: argparse.Namespace) -> int:
    from basis.config import load_settings
    from basis.db.engine import create_engine
    from basis.db.session import create_sessionmaker, session_scope
    from basis.retrieval.embed import embedder_for
    from basis.retrieval.store import PostgresChunkStore

    settings = load_settings()
    engine = create_engine(settings)
    try:
        sessionmaker = create_sessionmaker(engine)
        if args.list_versions:
            store = PostgresChunkStore(sessionmaker)
            for info, chunks, docs in await store.list_versions(args.corpus, args.study_id):
                print(
                    json.dumps(
                        {
                            "corpus_version_id": str(info.id),
                            "corpus": info.corpus,
                            "study_id": None if info.study_id is None else str(info.study_id),
                            "version": info.version,
                            "embedding_model": info.embedding_model,
                            "published_at": None
                            if info.published_at is None
                            else info.published_at.isoformat(),
                            "chunks": chunks,
                            "doc_ids": docs,
                        }
                    )
                )
            return 0
        sources = [
            SourcePdf(doc_id, path.read_bytes())
            for path, doc_id in zip(args.pdf, args.doc_id, strict=True)
        ]
        embedder = embedder_for(settings)
        async with session_scope(sessionmaker) as session:
            result = await ingest_documents(
                sources,
                corpus="reference",
                study_id=None,
                store=PostgresChunkStore(session=session),
                embedder=embedder,
                carry_forward=not args.fresh,
                max_pages=args.max_pages,
                progress=lambda message: print(message, file=sys.stderr),
                chunk_tokens=settings.retrieval_chunk_tokens,
                overlap_tokens=settings.retrieval_chunk_overlap,
            )
        print(json.dumps(result.summary()))
        return 0
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.list_versions:
        if args.corpus != "reference":
            parser.error("only the reference corpus is ingested from the command line")
        if not args.pdf:
            parser.error("--pdf is required (or use --list-versions)")
        if len(args.pdf) != len(args.doc_id):
            parser.error("give exactly one --doc-id per --pdf")
        if args.max_pages < 1:
            parser.error("--max-pages must be at least 1")
    elif args.corpus == "study" and args.study_id is None:
        parser.error("--corpus study needs --study-id")
    try:
        return asyncio.run(_run(args))
    except (PdfError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
