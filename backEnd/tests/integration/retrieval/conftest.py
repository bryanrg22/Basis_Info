"""Fixtures for the chunk-store integration tests: real Postgres, hash embedder.

Each test starts from empty tables (``truncate_all``) and its own ``Bm25Registry`` so
registry counters are per test. ``reference_corpus`` ingests the three synthetic
publications into reference version 1 in one transaction, as the CLI does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from dbtest_support import db_settings, truncate_all
from fixtures import load_fixture_json
from retrievaltest_support import Corpus, corpus_sources, ingest_reference
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker
from basis.retrieval.bm25 import Bm25Registry
from basis.retrieval.embed import HashEmbedder
from basis.retrieval.store import PostgresChunkStore


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(db_settings(database_url, db_pool_size=10))
    await truncate_all(engine)
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def registry() -> Bm25Registry:
    return Bm25Registry()


@pytest.fixture
def embedder() -> HashEmbedder:
    return HashEmbedder()


@pytest.fixture
def store(
    sessionmaker: async_sessionmaker[AsyncSession], registry: Bm25Registry
) -> PostgresChunkStore:
    return PostgresChunkStore(sessionmaker, registry=registry)


@pytest.fixture
async def reference_corpus(
    sessionmaker: async_sessionmaker[AsyncSession], embedder: HashEmbedder
) -> Corpus:
    result = await ingest_reference(sessionmaker, embedder, corpus_sources())
    return Corpus(
        result=result,
        manifest=load_fixture_json("corpus", "manifest.json"),
        qrels=load_fixture_json("corpus", "qrels.json"),
    )
