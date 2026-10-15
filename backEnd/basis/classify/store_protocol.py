"""The slice of the retrieval store the classifier needs, and an in-memory store for tests.

S3_SPEC section 2 binds ``basis.retrieval.store.ChunkStore`` (built by S3-A). This module
restates the four methods structurally so ``basis.classify`` does not import
``basis.retrieval``; the Postgres store satisfies this Protocol as long as its records
expose the attributes below (read-only properties here, so frozen dataclasses fit).

:class:`InMemoryChunkStore` is a deterministic stand-in used by unit tests, node tests,
behavioral evals and the eval harness's dry run. Its BM25 is a small Okapi BM25 (k1 1.5,
b 0.75) over lowercased word and code tokens (``57.0``, ``168(e)(3)``); ties break by
chunk key so results never depend on insertion order.
"""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

_TOKEN: Final = re.compile(r"[a-z0-9]+(?:[.()][a-z0-9]+\)?)*")
BM25_K1: Final = 1.5
BM25_B: Final = 0.75


class ChunkRecord(Protocol):
    @property
    def id(self) -> uuid.UUID: ...
    @property
    def corpus_version_id(self) -> uuid.UUID: ...
    @property
    def doc_id(self) -> str: ...
    @property
    def page_start(self) -> int | None: ...
    @property
    def page_end(self) -> int | None: ...
    @property
    def text(self) -> str: ...
    @property
    def table_id(self) -> uuid.UUID | None: ...


class TableRecord(Protocol):
    @property
    def id(self) -> uuid.UUID: ...
    @property
    def corpus_version_id(self) -> uuid.UUID: ...
    @property
    def doc_id(self) -> str | None: ...
    @property
    def page(self) -> int | None: ...
    @property
    def headers(self) -> Any: ...
    @property
    def rows(self) -> Any: ...
    @property
    def markdown(self) -> str | None: ...


class HitRecord(Protocol):
    @property
    def chunk_id(self) -> uuid.UUID: ...
    @property
    def doc_id(self) -> str: ...
    @property
    def page_start(self) -> int | None: ...
    @property
    def page_end(self) -> int | None: ...
    @property
    def score(self) -> float: ...
    @property
    def text(self) -> str: ...
    @property
    def table_id(self) -> uuid.UUID | None: ...


class ChunkStore(Protocol):
    async def search_bm25(
        self, corpus_version_id: uuid.UUID, query: str, k: int
    ) -> Sequence[HitRecord]: ...

    async def search_vector(
        self, corpus_version_id: uuid.UUID, query_embedding: Sequence[float], k: int
    ) -> Sequence[HitRecord]: ...

    async def get_chunk(self, chunk_id: uuid.UUID) -> ChunkRecord | None: ...

    async def get_table(self, table_id: uuid.UUID) -> TableRecord | None: ...


# --------------------------------------------------------------------------- records


@dataclass(frozen=True, slots=True)
class StoredChunk:
    id: uuid.UUID
    corpus_version_id: uuid.UUID
    doc_id: str
    text: str
    page_start: int | None = None
    page_end: int | None = None
    table_id: uuid.UUID | None = None
    chunk_key: str = ""
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class StoredTable:
    id: uuid.UUID
    corpus_version_id: uuid.UUID
    doc_id: str | None
    page: int | None
    headers: Any = None
    rows: Any = None
    markdown: str | None = None


@dataclass(frozen=True, slots=True)
class StoreHit:
    chunk_id: uuid.UUID
    doc_id: str
    page_start: int | None
    page_end: int | None
    score: float
    text: str
    table_id: uuid.UUID | None = None


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _hit(chunk: StoredChunk, score: float) -> StoreHit:
    return StoreHit(
        chunk_id=chunk.id,
        doc_id=chunk.doc_id,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        score=score,
        text=chunk.text,
        table_id=chunk.table_id,
    )


@dataclass
class InMemoryChunkStore:
    """A :class:`ChunkStore` over chunks and tables held in memory."""

    chunks: dict[uuid.UUID, StoredChunk] = field(default_factory=dict)
    tables: dict[uuid.UUID, StoredTable] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def of(
        cls, chunks: Iterable[StoredChunk], tables: Iterable[StoredTable] = ()
    ) -> InMemoryChunkStore:
        return cls(chunks={c.id: c for c in chunks}, tables={t.id: t for t in tables})

    def add(self, chunk: StoredChunk) -> StoredChunk:
        self.chunks[chunk.id] = chunk
        return chunk

    def add_table(self, table: StoredTable) -> StoredTable:
        self.tables[table.id] = table
        return table

    def _version(self, corpus_version_id: uuid.UUID) -> list[StoredChunk]:
        return sorted(
            (c for c in self.chunks.values() if c.corpus_version_id == corpus_version_id),
            key=lambda c: (c.chunk_key, str(c.id)),
        )

    async def search_bm25(self, corpus_version_id: uuid.UUID, query: str, k: int) -> list[StoreHit]:
        self.calls.append(("search_bm25", str(corpus_version_id)))
        terms = tokenize(query)
        docs = self._version(corpus_version_id)
        if not terms or not docs or k <= 0:
            return []
        tokenized = [tokenize(c.text) for c in docs]
        avg_len = sum(len(t) for t in tokenized) / len(tokenized) or 1.0
        df = Counter(term for tokens in tokenized for term in set(tokens))
        n = len(docs)
        scored: list[tuple[float, int]] = []
        for index, tokens in enumerate(tokenized):
            tf = Counter(tokens)
            score = 0.0
            for term in terms:
                if term not in tf:
                    continue
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                norm = tf[term] + BM25_K1 * (1 - BM25_B + BM25_B * len(tokens) / avg_len)
                score += idf * tf[term] * (BM25_K1 + 1) / norm
            if score > 0:
                scored.append((score, index))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [_hit(docs[i], round(s, 6)) for s, i in scored[:k]]

    async def search_vector(
        self, corpus_version_id: uuid.UUID, query_embedding: Sequence[float], k: int
    ) -> list[StoreHit]:
        self.calls.append(("search_vector", str(corpus_version_id)))
        query = list(query_embedding)
        q_norm = math.sqrt(sum(x * x for x in query))
        scored: list[tuple[float, int, StoredChunk]] = []
        for index, chunk in enumerate(self._version(corpus_version_id)):
            if chunk.embedding is None or q_norm == 0:
                continue
            c_norm = math.sqrt(sum(x * x for x in chunk.embedding))
            if c_norm == 0:
                continue
            dot = sum(a * b for a, b in zip(query, chunk.embedding, strict=False))
            scored.append((dot / (q_norm * c_norm), index, chunk))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [_hit(chunk, round(score, 6)) for score, _, chunk in scored[:k]]

    async def get_chunk(self, chunk_id: uuid.UUID) -> StoredChunk | None:
        self.calls.append(("get_chunk", str(chunk_id)))
        return self.chunks.get(chunk_id)

    async def get_table(self, table_id: uuid.UUID) -> StoredTable | None:
        self.calls.append(("get_table", str(table_id)))
        return self.tables.get(table_id)
