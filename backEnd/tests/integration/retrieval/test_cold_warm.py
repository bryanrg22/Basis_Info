"""BM25 cold/warm behaviour against Postgres (BUILD_PLAN 6.5 ``test_cold_warm.py``).

The first query of a version loads its chunk rows and builds the index once; later
queries hit the cache (no row load, no rebuild), proven by the registry counters and by
counting loader calls. Timings are logged and recorded, not asserted (CI machines vary).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Sequence

import pytest
from retrievaltest_support import Corpus, corpus_sources, ingest_reference
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.retrieval.bm25 import Bm25Doc, Bm25Registry
from basis.retrieval.embed import HashEmbedder
from basis.retrieval.store import PostgresChunkStore, warm_latest_reference

pytestmark = pytest.mark.integration


def count_loads(store: PostgresChunkStore) -> list[uuid.UUID]:
    """Wrap the store's row loader so the test can count database loads."""
    calls: list[uuid.UUID] = []
    original = store._bm25_docs

    async def counting(version_id: uuid.UUID) -> Sequence[Bm25Doc]:
        calls.append(version_id)
        return await original(version_id)

    store._bm25_docs = counting  # type: ignore[method-assign]
    return calls


async def test_second_query_does_not_rebuild(
    reference_corpus: Corpus,
    store: PostgresChunkStore,
    registry: Bm25Registry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="basis.retrieval.bm25")
    loads = count_loads(store)
    version = reference_corpus.result.corpus_version_id

    started = time.perf_counter()
    cold = await store.search_bm25(version, "section 1245 property", 5)
    cold_seconds = time.perf_counter() - started
    assert (registry.stats.misses, registry.stats.builds, registry.stats.hits) == (1, 1, 0)
    assert loads == [version]

    started = time.perf_counter()
    warm = await store.search_bm25(version, "section 1245 property", 5)
    warm_seconds = time.perf_counter() - started
    assert warm == cold
    assert (registry.stats.misses, registry.stats.builds, registry.stats.hits) == (1, 1, 1)
    assert loads == [version]  # no second load from the database

    for query in ["57.0", "carpet", "168(e)(3)"]:
        await store.search_bm25(version, query, 5)
    assert registry.stats.builds == 1 and registry.stats.hits == 4

    built = [r for r in caplog.records if r.getMessage() == "bm25 index built"]
    assert len(built) == 1
    assert built[0].chunks == reference_corpus.result.chunks  # type: ignore[attr-defined]
    logging.getLogger(__name__).info(
        "cold %.4fs (build %.4fs), warm %.4fs",
        cold_seconds,
        registry.stats.build_seconds[version],
        warm_seconds,
    )


async def test_warm_builds_before_the_first_query(
    reference_corpus: Corpus, store: PostgresChunkStore, registry: Bm25Registry
) -> None:
    loads = count_loads(store)
    version = reference_corpus.result.corpus_version_id
    await store.warm([version])
    assert loads == [version] and registry.stats.builds == 1 and version in registry
    await store.search_bm25(version, "1250", 3)
    assert loads == [version] and registry.stats.hits == 1


async def test_invalidation_forces_one_rebuild(
    reference_corpus: Corpus, store: PostgresChunkStore, registry: Bm25Registry
) -> None:
    version = reference_corpus.result.corpus_version_id
    await store.search_bm25(version, "1250", 3)
    registry.invalidate(version)
    await store.search_bm25(version, "1250", 3)
    await store.search_bm25(version, "1250", 3)
    assert registry.stats.builds == 2 and registry.stats.hits == 1


async def test_two_stores_share_one_registry(
    reference_corpus: Corpus, store: PostgresChunkStore, registry: Bm25Registry
) -> None:
    version = reference_corpus.result.corpus_version_id
    other = PostgresChunkStore(store._sessionmaker, registry=registry)
    await store.search_bm25(version, "1245", 3)
    await other.search_bm25(version, "1245", 3)
    assert registry.stats.builds == 1 and registry.stats.hits == 1


async def test_worker_start_warms_the_latest_reference_version(
    sessionmaker: async_sessionmaker[AsyncSession], embedder: HashEmbedder
) -> None:
    registry = Bm25Registry()
    assert await warm_latest_reference(sessionmaker, registry) is None  # nothing ingested
    v1 = await ingest_reference(sessionmaker, embedder, corpus_sources())
    v2 = await ingest_reference(sessionmaker, embedder, corpus_sources("SYN_PUB_A"))
    assert await warm_latest_reference(sessionmaker, registry) == v2.corpus_version_id
    assert v2.corpus_version_id in registry and v1.corpus_version_id not in registry
