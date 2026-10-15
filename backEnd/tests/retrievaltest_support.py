"""Helpers shared by the retrieval integration tests and the ingest activity tests.

Imported as a top-level module (``tests/`` is on ``sys.path``, see ``dbtest_support``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fixtures import fixture_path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.session import session_scope
from basis.retrieval.bm25 import Bm25Registry
from basis.retrieval.embed import Embedder
from basis.retrieval.ingest import IngestResult, SourcePdf, ingest_documents
from basis.retrieval.store import PostgresChunkStore

CORPUS_DOCS = ("SYN_PUB_A", "SYN_PUB_B", "SYN_PUB_C")


def corpus_sources(*doc_ids: str) -> list[SourcePdf]:
    """The synthetic reference publications (all three by default) as ingest sources."""
    return [
        SourcePdf(doc_id, fixture_path("corpus", f"{doc_id}.pdf").read_bytes())
        for doc_id in doc_ids or CORPUS_DOCS
    ]


async def ingest_reference(
    sessionmaker: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    sources: list[SourcePdf],
    *,
    carry_forward: bool = True,
) -> IngestResult:
    """A new reference version from ``sources`` in one transaction, as the CLI does."""
    async with session_scope(sessionmaker) as session:
        return await ingest_documents(
            sources,
            corpus="reference",
            study_id=None,
            store=PostgresChunkStore(session=session, registry=Bm25Registry()),
            embedder=embedder,
            carry_forward=carry_forward,
        )


@dataclass(frozen=True)
class Corpus:
    result: IngestResult
    manifest: dict[str, Any]
    qrels: dict[str, Any]
