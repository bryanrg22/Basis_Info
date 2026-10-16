"""The six real reference PDFs through the production ingest CLI (private data only).

Runs only where ``BASIS_EVAL_DATA_DIR`` points at the private eval data with its
``corpus/`` directory holding the six reference PDFs, and the real embedder
(``sentence-transformers/all-MiniLM-L6-v2``) is already in the local Hugging Face
cache; skipped with the reason otherwise. Marked ``eval_data`` so CI deselects it
(``-m "not eval_data"``) and ``tooling/evidence/capture.py`` runs it as an optional set
when the data is present. Nothing from the corpus is printed or written: only counts,
rates and timings.

``python -m basis.retrieval.ingest --corpus reference --fresh`` runs as a real
subprocess (exactly the command line) with the production embedder into the session's
throwaway pgvector container, emptied first. Then:

* one published reference version, embedded with MiniLM;
* per-document chunk counts within :data:`TOLERANCE` of :data:`BASELINE`, the regression
  baseline measured with :data:`BASELINE_VERSIONS` (parser and chunker); a changed
  version fails the test until the baseline is re-measured and re-pinned;
* every chunk: a 384-d unit-length embedding, one page, non-empty text;
* the two-column pages of Pub 527 and Pub 946: sentences of pymupdf's page text survive
  whole in one chunk of their page (``excerpt_supported``, the citation matcher) at
  least at the pinned rates;
* every citation of the rules table (``basis.domain.rules``; the ``rule_citations``
  eval) resolves through the production path,
  :func:`basis.classify.router.resolve_rule_citations` over the store's BM25;
* the harness's in-memory chunk index (``evals.retrieval``, what ``retrieval_qrels``
  scores) ranks the qrels queries as the store does: BM25 identically, exact cosine
  against pgvector's HNSW within :data:`MIN_VECTOR_AGREEMENT`.

Ingest wall time and the cold BM25 build time are recorded with ``record_property``
(the JUnit report) and printed (``-rP`` or ``-s`` shows them).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from dbtest_support import BACKEND_ROOT, db_settings, truncate_all
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.classify.router import ClassifyContext, resolve_rule_citations
from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker
from basis.domain.rules import RULES, lookup
from basis.llm.provider import Budget
from basis.retrieval.bm25 import Bm25Registry
from basis.retrieval.chunk import CHUNKER_VERSION
from basis.retrieval.embed import (
    DEFAULT_MODEL,
    EMBEDDING_DIM,
    SentenceTransformerEmbedder,
    model_is_cached,
)
from basis.retrieval.hybrid import hybrid_search
from basis.retrieval.parse import PARSER_VERSION, REFERENCE_MAX_PAGES, parse_pdf
from basis.retrieval.store import PostgresChunkStore

ENV_VAR: Final = "BASIS_EVAL_DATA_DIR"
PUB_527: Final = "IRS_IRS_PUB_527__2024"
PUB_946: Final = "IRS_IRS_PUB_946__2024"
DOC_IDS: Final = (
    "IRS_IRS_COST_SEG_ATG__2024",
    PUB_527,
    PUB_946,
    "IRS_REV_PROC_87_56",
    "RSMEANS_RSMEANS_BUILDING_2020",
    "RSMEANS_RSMEANS_RESIDENTIAL_2020",
)

# The regression baseline: pages, text chunks and table chunks per document from the
# ingest CLI over the private corpus, measured 2026-10-16 with these versions. Chunking is
# deterministic, so a run with the same versions reproduces it exactly; the tolerance only
# absorbs a dependency (pdfplumber, pdfminer, pymupdf, tiktoken) moving a few words.
BASELINE_VERSIONS: Final = {
    "parser": "2026-10-16.two-column",
    "chunker": "cl100k_base-400/80-page-v1",
}
BASELINE: Final[dict[str, tuple[int, int, int]]] = {
    # doc_id: (pages, text chunks, table chunks)
    "IRS_IRS_COST_SEG_ATG__2024": (348, 346, 282),
    PUB_527: (37, 132, 8),
    PUB_946: (112, 301, 141),
    "IRS_REV_PROC_87_56": (42, 48, 0),
    "RSMEANS_RSMEANS_BUILDING_2020": (964, 3775, 0),
    "RSMEANS_RSMEANS_RESIDENTIAL_2020": (802, 2433, 0),
}
BASELINE_TOTAL: Final = 7466
TOLERANCE: Final = 0.01  # relative, per document and kind, and for the total

# Sentences of the two-column pages whole in one chunk (pinned a little under the
# measured rate; EVAL_REPORT section 22 has the measurement).
MIN_INTACT: Final = {PUB_527: 0.91, PUB_946: 0.88}  # measured 0.923 (937), 0.892 (1659)
MIN_VECTOR_AGREEMENT: Final = 0.9  # queries whose top-10 pages agree, HNSW vs exact


def _data_dir() -> Path | None:
    value = os.environ.get(ENV_VAR)
    return Path(value).expanduser() if value else None


def _corpus() -> dict[str, Path]:
    root = _data_dir()
    if root is None:
        return {}
    return {
        d: root / "corpus" / f"{d}.pdf" for d in DOC_IDS if (root / "corpus" / f"{d}.pdf").is_file()
    }


def _skip_reason() -> str | None:
    root = _data_dir()
    if root is None:
        return f"{ENV_VAR} is not set (private eval data; this test never runs in CI)"
    if not (root / "corpus").is_dir():
        return f"{ENV_VAR}/corpus does not exist"
    missing = [d for d in DOC_IDS if d not in _corpus()]
    if missing:
        return f"{ENV_VAR}/corpus lacks {', '.join(missing)}"
    if not model_is_cached(DEFAULT_MODEL):
        return f"{DEFAULT_MODEL} is not in the local Hugging Face cache (no download in tests)"
    return None


SKIP_REASON: Final = _skip_reason()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.eval_data,
    pytest.mark.timeout(3600),
    pytest.mark.skipif(SKIP_REASON is not None, reason=SKIP_REASON or ""),
]


@dataclass(frozen=True)
class Ingested:
    database_url: str
    summary: dict[str, Any]
    seconds: float


@pytest.fixture(scope="module")
def ingested(database_url: str) -> Ingested:
    """The six PDFs ingested once for the module by the CLI, into emptied tables."""

    async def empty() -> None:
        engine = create_engine(db_settings(database_url))
        try:
            await truncate_all(engine)
        finally:
            await engine.dispose()

    asyncio.run(empty())
    argv = [
        sys.executable,
        "-m",
        "basis.retrieval.ingest",
        "--corpus",
        "reference",
        "--fresh",
        "--max-pages",
        str(REFERENCE_MAX_PAGES),
    ]
    for doc_id, path in _corpus().items():
        argv += ["--pdf", str(path), "--doc-id", doc_id]
    env = {k: v for k, v in os.environ.items() if k not in ("EMBEDDING_MODEL", "PYTHONPATH")}
    env |= {"BASIS_ENV": "test", "DATABASE_URL": database_url}
    started = time.perf_counter()
    done = subprocess.run(
        argv, cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, timeout=3000, check=False
    )
    seconds = time.perf_counter() - started
    assert done.returncode == 0, done.stderr[-4000:]
    return Ingested(database_url, json.loads(done.stdout), seconds)


@pytest.fixture
async def maker(ingested: Ingested) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(db_settings(ingested.database_url, db_pool_size=5))
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _version_id(store: PostgresChunkStore) -> Any:
    latest = await store.latest_version("reference", None)
    assert latest is not None
    return latest.id


def _report(record_property: Callable[[str, object], None], **values: object) -> None:
    for name, value in values.items():
        record_property(name, value)
    print("real corpus:", json.dumps(values, default=str))


async def test_one_published_version_with_the_baseline_chunk_counts(
    ingested: Ingested,
    maker: async_sessionmaker[AsyncSession],
    record_property: Callable[[str, object], None],
) -> None:
    assert {"parser": PARSER_VERSION, "chunker": CHUNKER_VERSION} == BASELINE_VERSIONS, (
        "the parser or chunker version changed: re-run this test, check the new counts, "
        "and re-pin BASELINE and BASELINE_VERSIONS"
    )
    store = PostgresChunkStore(maker, registry=Bm25Registry())
    versions = await store.list_versions("reference")
    assert len(versions) == 1
    info, chunks, doc_ids = versions[0]
    assert info.published and info.version == 1 and info.embedding_model == DEFAULT_MODEL
    assert sorted(doc_ids) == sorted(DOC_IDS)

    summary = ingested.summary
    counts = {
        d["doc_id"]: (d["pages"], d["text_chunks"], d["table_chunks"]) for d in summary["documents"]
    }
    _report(
        record_property,
        ingest_wall_seconds=round(ingested.seconds, 1),
        ingest_reported_seconds=summary["seconds"],
        chunks=chunks,
        per_document=counts,
    )
    assert chunks == summary["chunks"] and summary["carried_forward_chunks"] == 0
    assert summary["scanned_docs"] == []
    for doc_id, (pages, text_chunks, table_chunks) in BASELINE.items():
        got_pages, got_text, got_tables = counts[doc_id]
        assert got_pages == pages, doc_id
        assert abs(got_text - text_chunks) <= TOLERANCE * text_chunks, (doc_id, got_text)
        assert abs(got_tables - table_chunks) <= TOLERANCE * max(table_chunks, 1), (
            doc_id,
            got_tables,
        )
    assert abs(chunks - BASELINE_TOTAL) <= TOLERANCE * BASELINE_TOTAL


async def test_every_chunk_has_a_unit_embedding_one_page_and_text(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT count(*) AS n,"
                    " count(*) FILTER (WHERE embedding IS NULL) AS no_embedding,"
                    " count(*) FILTER (WHERE vector_dims(embedding) <> :dim) AS wrong_dim,"
                    " count(*) FILTER (WHERE abs(vector_norm(embedding) - 1) > 1e-3) AS not_unit,"
                    " count(*) FILTER (WHERE page_start IS NULL OR page_end IS NULL"
                    "   OR page_start <> page_end) AS not_one_page,"
                    " count(*) FILTER (WHERE length(btrim(text)) = 0) AS empty"
                    " FROM chunks"
                ),
                {"dim": EMBEDDING_DIM},
            )
        ).one()
    assert row.n > 0
    assert (row.no_embedding, row.wrong_dim, row.not_unit, row.not_one_page, row.empty) == (
        0,
        0,
        0,
        0,
        0,
    )


async def _chunk_texts_by_page(
    maker: async_sessionmaker[AsyncSession], doc_id: str
) -> dict[int, list[str]]:
    async with maker() as session:
        rows = await session.execute(
            text("SELECT page_start, text FROM chunks WHERE doc_id = :doc"), {"doc": doc_id}
        )
        by_page: dict[int, list[str]] = defaultdict(list)
        for page, chunk_text in rows:
            by_page[int(page)].append(chunk_text)
    return by_page


@pytest.mark.parametrize("doc_id", [PUB_527, PUB_946])
async def test_two_column_pages_keep_their_sentences_whole(
    doc_id: str,
    maker: async_sessionmaker[AsyncSession],
    record_property: Callable[[str, object], None],
) -> None:
    import pymupdf

    from evals.retrieval import intact_sentences, reference_sentences

    path = _corpus()[doc_id]
    document = await asyncio.to_thread(
        parse_pdf, path.read_bytes(), doc_id, max_pages=REFERENCE_MAX_PAGES
    )
    two_column = [p.number for p in document.pages if p.columns == 2]
    assert two_column, "no page was read as two columns"
    by_page = await _chunk_texts_by_page(maker, doc_id)
    outcomes: list[bool] = []
    with pymupdf.open(path) as pdf:  # type: ignore[no-untyped-call]
        for number in two_column:
            sentences = reference_sentences(pdf[number - 1].get_text())
            outcomes += intact_sentences(sentences, by_page.get(number, []))
    rate = sum(outcomes) / len(outcomes)
    _report(
        record_property,
        doc_id=doc_id,
        two_column_pages=len(two_column),
        sentences=len(outcomes),
        intact_rate=round(rate, 4),
    )
    assert len(outcomes) >= 100
    assert rate >= MIN_INTACT[doc_id]


async def test_every_rules_table_citation_resolves_in_the_ingested_chunks(
    maker: async_sessionmaker[AsyncSession],
    record_property: Callable[[str, object], None],
) -> None:
    store = PostgresChunkStore(maker, registry=Bm25Registry())
    version = await _version_id(store)
    ctx = ClassifyContext(
        study_id="real-corpus-test",
        property_class="residential_rental",
        budget=Budget(remaining_usd=0.0),
        store=store,
        reference_version_id=version,
    )
    claimed = verified = 0
    unresolved: list[str] = []
    for key in RULES:
        hit = lookup(key)
        assert hit is not None and hit.key == key
        ok, dropped = await resolve_rule_citations(hit, ctx)
        claimed += len(hit.citations)
        verified += len(ok)
        unresolved += [f"{key} {d.chunk_id}" for d in dropped]
    _report(record_property, citations=claimed, resolved=verified)
    assert claimed == 77
    assert unresolved == []


async def test_the_harness_index_ranks_like_the_store(
    maker: async_sessionmaker[AsyncSession],
    record_property: Callable[[str, object], None],
) -> None:
    from evals import datasets as ds
    from evals.retrieval import ChunkIndex, CorpusChunk, pages

    root = _data_dir()
    assert root is not None
    qrels_file = root / ds.DATASET_FILES["retrieval_qrels"]
    if not qrels_file.is_file():
        pytest.skip(f"{qrels_file.name} is not in the eval data")
    qrels = ds.RetrievalQrels.model_validate_json(qrels_file.read_text(encoding="utf-8"))
    queries = {q.id: q.query for q in qrels.queries}

    registry = Bm25Registry()
    store = PostgresChunkStore(maker, registry=registry)
    version = await _version_id(store)
    started = time.perf_counter()
    await store.warm([version])
    cold = time.perf_counter() - started

    async with maker() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT doc_id, page_start, chunk_key, text, embedding::text AS e"
                    " FROM chunks WHERE corpus_version_id = :v ORDER BY chunk_key"
                ),
                {"v": version},
            )
        ).all()
    index = ChunkIndex(
        [CorpusChunk(r.doc_id, r.page_start, r.chunk_key, r.text) for r in rows],
        [json.loads(r.e) for r in rows],
    )
    embedder = SentenceTransformerEmbedder()
    bm25_same = vector_same = hybrid_same = 0
    for query in queries.values():
        stored = await store.search_bm25(version, query, 50)
        if pages(stored) == pages(index.bm25(query, 50)):
            bm25_same += 1
        [vector] = await asyncio.to_thread(embedder.embed, [query])
        if pages(await store.search_vector(version, vector, 50)) == pages(index.vector(vector, 50)):
            vector_same += 1
        a = await hybrid_search(store, embedder, version, query, 50)
        b = await hybrid_search(index, embedder, version, query, 50)
        if pages(a) == pages(b):
            hybrid_same += 1
    n = len(queries)
    _report(
        record_property,
        bm25_cold_build_seconds=round(cold, 3),
        bm25_build_seconds=round(registry.stats.build_seconds[version], 3),
        queries=n,
        bm25_top10_pages_equal=bm25_same,
        vector_top10_pages_equal=vector_same,
        hybrid_top10_pages_equal=hybrid_same,
    )
    assert registry.stats.builds == 1
    assert bm25_same == n
    assert vector_same >= MIN_VECTOR_AGREEMENT * n
    assert hybrid_same >= MIN_VECTOR_AGREEMENT * n
