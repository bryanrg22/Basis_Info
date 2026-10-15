"""BM25 over a corpus version's chunks, cached per process (from ``build_bm25.py``).

Kept from the legacy index: ``rank_bm25.BM25Okapi`` scoring (k1=1.5, b=0.75) over
:func:`~basis.retrieval.tokenizer.irs_tokenize`, chunks with no tokens left out, only
positive scores returned.

Changed: the IDF. ``BM25Okapi`` uses ``ln((N - n + 0.5) / (n + 0.5))``, which is zero
when a term is in half the chunks and negative above that (rank_bm25 then substitutes
``0.25 * mean idf``, itself negative in a small corpus). On a study corpus of a few
chunks an exact match then scores <= 0 and is dropped: a one-chunk corpus returned
nothing for any query, and a term in 4 of 8 chunks scored 0. :class:`_Bm25` uses the
Lucene / Elasticsearch BM25 IDF ``ln(1 + (N - n + 0.5) / (n + 0.5))``, which is positive
for every term in the corpus and orders terms exactly as the classic IDF does where that
one is positive (both are decreasing in ``n``).

Changed: no pickles. The index is rebuilt from the ``chunks`` rows of a corpus version
(the database is the only source of truth) and cached in a process-level
:class:`Bm25Registry` keyed by ``corpus_version_id``. Published versions are immutable
(migration 0003 enforces it), so a cached index can never go stale; invalidation exists
for tests and for deliberate re-reads. The registry is an LRU so a long-lived worker
that has served many studies does not keep every study's index.

Ties are broken by the order the rows were given (``chunk_key`` order from the store),
so two runs over the same version return the same ranking.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from math import log
from typing import Final

from rank_bm25 import BM25Okapi

from basis.retrieval.tokenizer import Tokenizer, irs_tokenize

logger = logging.getLogger("basis.retrieval.bm25")

DEFAULT_REGISTRY_SIZE: Final = 64


class _Bm25(BM25Okapi):  # type: ignore[misc]
    """``BM25Okapi`` with the Lucene IDF (see module docstring)."""

    def _calc_idf(self, nd: dict[str, int]) -> None:
        for word, freq in nd.items():
            self.idf[word] = log(1.0 + (self.corpus_size - freq + 0.5) / (freq + 0.5))
        self.average_idf = sum(self.idf.values()) / len(self.idf) if self.idf else 0.0


@dataclass(frozen=True, slots=True)
class Bm25Doc:
    chunk_id: uuid.UUID
    text: str


class Bm25Index:
    """An immutable BM25 index over (chunk id, text) pairs."""

    def __init__(self, docs: Iterable[Bm25Doc], tokenizer: Tokenizer = irs_tokenize) -> None:
        self._tokenizer = tokenizer
        ids: list[uuid.UUID] = []
        corpus: list[list[str]] = []
        for doc in docs:
            tokens = tokenizer(doc.text)
            if tokens:
                ids.append(doc.chunk_id)
                corpus.append(tokens)
        self._ids = ids
        self._bm25 = _Bm25(corpus) if corpus else None

    def __len__(self) -> int:
        return len(self._ids)

    def search(self, query: str, k: int) -> list[tuple[uuid.UUID, float]]:
        """Top ``k`` ``(chunk_id, score)`` with score > 0; empty for an empty query."""
        if k <= 0 or self._bm25 is None:
            return []
        tokens = self._tokenizer(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        order = sorted(range(len(self._ids)), key=lambda i: (-float(scores[i]), i))
        return [(self._ids[i], float(scores[i])) for i in order[:k] if scores[i] > 0]


Loader = Callable[[uuid.UUID], Awaitable[Sequence[Bm25Doc]]]


@dataclass
class RegistryStats:
    hits: int = 0
    misses: int = 0
    builds: int = 0
    evictions: int = 0
    build_seconds: dict[uuid.UUID, float] = field(default_factory=dict)


class Bm25Registry:
    """Process-level cache of BM25 indexes keyed by corpus version id.

    ``get`` builds on a miss (the loader fetches the version's chunk rows; tokenizing and
    building run in a thread so the event loop keeps serving heartbeats); concurrent
    misses for one version wait on a per-version lock and build once.
    """

    def __init__(self, max_entries: int = DEFAULT_REGISTRY_SIZE) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._max_entries = max_entries
        self._indexes: OrderedDict[uuid.UUID, Bm25Index] = OrderedDict()
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        self.stats = RegistryStats()

    def __contains__(self, version_id: object) -> bool:
        return version_id in self._indexes

    def __len__(self) -> int:
        return len(self._indexes)

    async def get(self, version_id: uuid.UUID, loader: Loader) -> Bm25Index:
        index = self._indexes.get(version_id)
        if index is not None:
            self.stats.hits += 1
            self._indexes.move_to_end(version_id)
            return index
        lock = self._locks.setdefault(version_id, asyncio.Lock())
        async with lock:
            index = self._indexes.get(version_id)
            if index is not None:  # built while this caller waited
                self.stats.hits += 1
                self._indexes.move_to_end(version_id)
                return index
            self.stats.misses += 1
            try:
                index = await self._build(version_id, loader)
                self._store(version_id, index)
            finally:
                # Waiters still hold the lock object; dropping it here means no lock
                # outlives its build (an asyncio.Lock binds to the loop it waited on).
                self._locks.pop(version_id, None)
            return index

    async def warm(self, version_ids: Iterable[uuid.UUID], loader: Loader) -> None:
        """Build the indexes for ``version_ids`` now (worker start), not on first query."""
        for version_id in version_ids:
            await self.get(version_id, loader)

    def invalidate(self, version_id: uuid.UUID | None = None) -> None:
        """Drop one version's index, or every index when ``version_id`` is None."""
        if version_id is None:
            self._indexes.clear()
        else:
            self._indexes.pop(version_id, None)

    async def _build(self, version_id: uuid.UUID, loader: Loader) -> Bm25Index:
        started = time.perf_counter()
        docs = await loader(version_id)
        loaded = time.perf_counter()
        index = await asyncio.to_thread(Bm25Index, docs)
        seconds = time.perf_counter() - started
        self.stats.builds += 1
        self.stats.build_seconds[version_id] = seconds
        logger.info(
            "bm25 index built",
            extra={
                "corpus_version_id": str(version_id),
                "chunks": len(docs),
                "indexed": len(index),
                "load_seconds": round(loaded - started, 4),
                "build_seconds": round(seconds, 4),
            },
        )
        return index

    def _store(self, version_id: uuid.UUID, index: Bm25Index) -> None:
        self._indexes[version_id] = index
        self._indexes.move_to_end(version_id)
        while len(self._indexes) > self._max_entries:
            self._indexes.popitem(last=False)
            self.stats.evictions += 1


_PROCESS_REGISTRY: Bm25Registry | None = None


def process_registry() -> Bm25Registry:
    """The registry shared by everything in this process (worker, CLI)."""
    global _PROCESS_REGISTRY
    if _PROCESS_REGISTRY is None:
        _PROCESS_REGISTRY = Bm25Registry()
    return _PROCESS_REGISTRY
