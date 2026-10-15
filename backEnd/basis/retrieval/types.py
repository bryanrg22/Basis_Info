"""Value types the retrieval layer hands to its callers (S3_SPEC section 2).

Plain frozen dataclasses, not ORM rows: callers (the classifier's tools, the evidence
pack, the eval harness) never hold a session-bound object, and a ``Hit`` can be built
in a unit test without a database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

CorpusName = Literal["reference", "study"]


@dataclass(frozen=True, slots=True)
class Hit:
    """One search result. ``score`` is retriever-specific (BM25, cosine, or fused)."""

    chunk_id: uuid.UUID
    doc_id: str
    page_start: int | None
    page_end: int | None
    score: float
    text: str
    table_id: uuid.UUID | None = None

    def pages(self) -> list[int]:
        """Every page the hit covers (empty when the chunk has no page)."""
        if self.page_start is None:
            return []
        end = self.page_end if self.page_end is not None else self.page_start
        return list(range(self.page_start, end + 1))


@dataclass(frozen=True, slots=True)
class Chunk:
    """A stored chunk with the corpus version it belongs to (for citation checks)."""

    id: uuid.UUID
    corpus_version_id: uuid.UUID
    doc_id: str
    chunk_key: str
    page_start: int | None
    page_end: int | None
    section_path: str | None
    text: str
    table_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class Table:
    """A whole table, fetched by id when a surrogate chunk is hit (never chunked)."""

    id: uuid.UUID
    corpus_version_id: uuid.UUID
    doc_id: str | None
    page: int | None
    headers: list[str]
    rows: list[list[Any]]
    markdown: str | None


@dataclass(frozen=True, slots=True)
class CorpusVersionInfo:
    id: uuid.UUID
    corpus: CorpusName
    study_id: uuid.UUID | None
    version: int
    embedding_model: str
    published_at: datetime | None
    created_at: datetime | None = None

    @property
    def published(self) -> bool:
        return self.published_at is not None
