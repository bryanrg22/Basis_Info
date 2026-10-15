"""The Postgres chunk store with the synthetic corpus (BUILD_PLAN 6.5 retrieval rows).

* every grade-2 page of ``qrels.json`` is in the top 5 pages by BM25 and by hybrid;
* vector search returns a chunk's own embedding's neighbour first (hash embedder);
* whole tables come back by id from their surrogate hits;
* study corpora are isolated from each other;
* published versions are immutable, and v1 citations still resolve after a v2.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from dbtest_support import Factory
from fixtures import fixture_path
from retrievaltest_support import Corpus, corpus_sources, ingest_reference
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.models import Chunk as ChunkRow
from basis.db.models import CorpusVersion, EvidenceTable
from basis.db.session import session_scope
from basis.retrieval.bm25 import Bm25Registry
from basis.retrieval.embed import HashEmbedder
from basis.retrieval.hybrid import hybrid_search
from basis.retrieval.ingest import ingest_documents, ingest_pdf
from basis.retrieval.metrics import page_keys
from basis.retrieval.store import CorpusError, NewChunk, PostgresChunkStore
from basis.retrieval.verify import check_citation, resolve_citation

pytestmark = pytest.mark.integration

CANDIDATES = 10  # chunks fetched per retriever before reducing to pages
TOP_PAGES = 5


def grade2_queries(qrels: dict[str, Any]) -> list[tuple[str, set[str]]]:
    out = []
    for query in qrels["queries"]:
        pages = {f"{r['doc_id']}:{r['page']}" for r in query["relevant"] if r["grade"] == 2}
        if pages:
            out.append((query["query"], pages))
    return out


# --------------------------------------------------------------------------- ingestion


async def test_ingest_creates_one_published_version(
    reference_corpus: Corpus, store: PostgresChunkStore
) -> None:
    result = reference_corpus.result
    assert (result.version, result.corpus, result.study_id) == (1, "reference", None)
    assert result.embedding_model == "hash-v1-384"
    assert [d.doc_id for d in result.documents] == ["SYN_PUB_A", "SYN_PUB_B", "SYN_PUB_C"]
    assert [d.tables for d in result.documents] == [1, 1, 1]
    assert result.carried_forward_chunks == 0 and result.scanned_docs == []
    info = await store.get_version(result.corpus_version_id)
    assert info is not None and info.published
    assert await store.count_chunks(result.corpus_version_id) == result.chunks
    latest = await store.latest_version("reference", None)
    assert latest is not None and latest.id == result.corpus_version_id


async def test_every_chunk_has_an_embedding_and_page(
    reference_corpus: Corpus, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(sessionmaker) as session:
        rows = (await session.scalars(select(ChunkRow))).all()
    assert len(rows) == reference_corpus.result.chunks
    for row in rows:
        assert row.embedding is not None and len(row.embedding) == 384
        assert row.page_start == row.page_end and row.page_start is not None
        doc, page, ordinal = row.chunk_key.split(":")
        assert (doc, int(page)) == (row.doc_id, row.page_start) and int(ordinal) >= 0


# --------------------------------------------------------------------------- qrels


async def test_every_grade2_page_is_in_the_bm25_top5(
    reference_corpus: Corpus, store: PostgresChunkStore
) -> None:
    version = reference_corpus.result.corpus_version_id
    queries = grade2_queries(reference_corpus.qrels)
    assert len(queries) == 10
    for query, pages in queries:
        hits = await store.search_bm25(version, query, CANDIDATES)
        top = set(page_keys(hits)[:TOP_PAGES])
        assert pages <= top, f"{query!r}: missing {pages - top}"


@pytest.mark.parametrize("method", ["rrf", "weighted"])
async def test_every_grade2_page_is_in_the_hybrid_top5(
    reference_corpus: Corpus, store: PostgresChunkStore, embedder: HashEmbedder, method: str
) -> None:
    version = reference_corpus.result.corpus_version_id
    for query, pages in grade2_queries(reference_corpus.qrels):
        hits = await hybrid_search(
            store,
            embedder,
            version,
            query,
            CANDIDATES,
            method=method,  # type: ignore[arg-type]
        )
        top = set(page_keys(hits)[:TOP_PAGES])
        assert pages <= top, f"{method} {query!r}: missing {pages - top}"


# --------------------------------------------------------------------------- vectors


async def test_vector_search_returns_the_nearest_neighbour_first(
    reference_corpus: Corpus,
    store: PostgresChunkStore,
    embedder: HashEmbedder,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    version = reference_corpus.result.corpus_version_id
    async with session_scope(sessionmaker) as session:
        rows = (await session.scalars(select(ChunkRow).order_by(ChunkRow.chunk_key))).all()
    for row in rows:
        [query] = embedder.embed([row.text])
        hits = await store.search_vector(version, query, 3)
        assert hits[0].chunk_id == row.id
        assert hits[0].score == pytest.approx(1.0, abs=1e-5)
        assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


async def test_vector_search_prefers_lexical_neighbours(
    reference_corpus: Corpus, store: PostgresChunkStore, embedder: HashEmbedder
) -> None:
    version = reference_corpus.result.corpus_version_id
    [query] = embedder.embed(["half-year convention and mid-month convention"])
    [first] = await store.search_vector(version, query, 1)
    assert (first.doc_id, first.page_start) == ("SYN_PUB_A", 4)
    assert await store.search_vector(version, query, 0) == []


# --------------------------------------------------------------------------- tables


async def test_surrogate_hits_lead_to_the_whole_table(
    reference_corpus: Corpus, store: PostgresChunkStore
) -> None:
    version = reference_corpus.result.corpus_version_id
    expected = {d["doc_id"]: d["tables"][0] for d in reference_corpus.manifest["documents"]}
    hits = await store.search_bm25(version, "00.12 information systems", 3)
    surrogate = next(h for h in hits if h.table_id is not None)
    table = await store.get_table(surrogate.table_id)  # type: ignore[arg-type]
    assert table is not None and table.corpus_version_id == version
    assert (table.doc_id, table.page) == ("SYN_PUB_A", 3)
    assert table.headers == expected["SYN_PUB_A"]["headers"]
    assert table.rows == expected["SYN_PUB_A"]["rows"]
    assert table.markdown is not None and "| 00.12 | Information systems |" in table.markdown
    assert await store.get_table(uuid.uuid4()) is None


async def test_get_chunk(reference_corpus: Corpus, store: PostgresChunkStore) -> None:
    version = reference_corpus.result.corpus_version_id
    [hit] = await store.search_bm25(version, "168(e)(3)", 1)
    chunk = await store.get_chunk(hit.chunk_id)
    assert chunk is not None
    assert (chunk.corpus_version_id, chunk.doc_id, chunk.chunk_key) == (
        version,
        "SYN_PUB_A",
        "SYN_PUB_A:1:0",
    )
    assert chunk.section_path == (
        "Synthetic Publication A: Recovery Periods for Business Property > Sec. 1. Purpose"
    )
    assert await store.get_chunk(uuid.uuid4()) is None


# --------------------------------------------------------------------------- isolation


async def make_study(sessionmaker: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    async with session_scope(sessionmaker) as session:
        make = Factory(session)
        return (await make.study(await make.user())).id


async def ingest_study(
    sessionmaker: async_sessionmaker[AsyncSession],
    study_id: uuid.UUID,
    data: bytes,
    doc_id: str,
) -> uuid.UUID:
    async with session_scope(sessionmaker) as session:
        result = await ingest_pdf(
            data,
            doc_id,
            "study",
            study_id,
            PostgresChunkStore(session=session, registry=Bm25Registry()),
            HashEmbedder(),
            version=1,
        )
    return result.corpus_version_id


async def test_study_corpora_are_isolated(
    sessionmaker: async_sessionmaker[AsyncSession], store: PostgresChunkStore
) -> None:
    urar = fixture_path("urar_sample.pdf").read_bytes()
    one, two = await make_study(sessionmaker), await make_study(sessionmaker)
    v_one = await ingest_study(sessionmaker, one, urar, "appraisal-one")
    v_two = await ingest_study(sessionmaker, two, urar, "appraisal-two")
    for query in ["Sales Comparison Approach", "1290 Example Way", "Indicated Value by Cost"]:
        hits_one = await store.search_bm25(v_one, query, 10)
        assert hits_one and {h.doc_id for h in hits_one} == {"appraisal-one"}
        [vector] = HashEmbedder().embed([query])
        assert {h.doc_id for h in await store.search_vector(v_one, vector, 10)} == {"appraisal-one"}
        # A chunk of study two never resolves as evidence for study one's version.
        [foreign] = await store.search_bm25(v_two, query, 1)
        assert await resolve_citation(store, foreign.chunk_id, v_one) is None
        assert await resolve_citation(store, foreign.chunk_id, v_two) is not None
    latest_one = await store.latest_version("study", one)
    assert latest_one is not None and latest_one.id == v_one and latest_one.study_id == one


async def test_a_study_corpus_needs_its_study(store: PostgresChunkStore) -> None:
    with pytest.raises(CorpusError):
        await store.create_version("study", None, "m")
    with pytest.raises(CorpusError):
        await store.create_version("reference", uuid.uuid4(), "m")


# --------------------------------------------------------------------------- versions


async def test_published_versions_are_immutable(
    reference_corpus: Corpus,
    store: PostgresChunkStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    version = reference_corpus.result.corpus_version_id
    extra = NewChunk("SYN_PUB_A", "SYN_PUB_A:9:0", 9, 9, None, "late text", None)
    with pytest.raises(CorpusError, match="published"):
        await store.insert_chunks(version, [extra])
    with pytest.raises(CorpusError):
        await store.publish_version(version)
    # The database refuses even writes that bypass the store.
    with pytest.raises(IntegrityError, match="immutable"):
        async with session_scope(sessionmaker) as session:
            session.add(ChunkRow(corpus_version_id=version, doc_id="x", chunk_key="x", text="x"))
    with pytest.raises(IntegrityError, match="immutable"):
        async with session_scope(sessionmaker) as session:
            await session.execute(
                text("UPDATE chunks SET text = 'changed' WHERE corpus_version_id = :v"),
                {"v": version},
            )
    with pytest.raises(IntegrityError, match="published"):
        async with session_scope(sessionmaker) as session:
            await session.execute(
                text("UPDATE corpus_versions SET embedding_model = 'x' WHERE id = :v"),
                {"v": version},
            )
    assert await store.count_chunks(version) == reference_corpus.result.chunks


async def test_v1_citations_resolve_after_v2(
    reference_corpus: Corpus,
    store: PostgresChunkStore,
    embedder: HashEmbedder,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    v1 = reference_corpus.result.corpus_version_id
    [cited] = await store.search_bm25(v1, "57.0 class life", 1)
    excerpt = "Assets in class 57.0 have a class life of 9 years"

    # Re-ingest one document: the others are carried forward into v2.
    v2_result = await ingest_reference(sessionmaker, embedder, corpus_sources("SYN_PUB_B"))
    v2 = v2_result.corpus_version_id
    assert v2_result.version == 2 and v2 != v1
    assert v2_result.carried_forward_chunks + v2_result.chunks == reference_corpus.result.chunks
    latest = await store.latest_version("reference", None)
    assert latest is not None and latest.id == v2

    old = await check_citation(store, cited.chunk_id, v1, excerpt)
    assert old.ok and old.chunk is not None and old.chunk.text == cited.text
    assert not (await check_citation(store, cited.chunk_id, v2, excerpt)).ok  # v1 id, v2 version
    [in_v2] = await store.search_bm25(v2, "57.0 class life", 1)
    assert in_v2.chunk_id != cited.chunk_id and in_v2.text == cited.text
    assert (await check_citation(store, in_v2.chunk_id, v2, excerpt)).ok

    listing = await store.list_versions("reference")
    assert [(info.version, docs) for info, _, docs in listing] == [
        (1, ["SYN_PUB_A", "SYN_PUB_B", "SYN_PUB_C"]),
        (2, ["SYN_PUB_A", "SYN_PUB_B", "SYN_PUB_C"]),
    ]
    # Carried-forward surrogates point at v2's copies of the tables, not v1's.
    async with session_scope(sessionmaker) as session:
        tables = {
            t.id: t.corpus_version_id for t in (await session.scalars(select(EvidenceTable))).all()
        }
        surrogates = (
            await session.scalars(
                select(ChunkRow).where(
                    ChunkRow.corpus_version_id == v2, ChunkRow.table_id.is_not(None)
                )
            )
        ).all()
    assert len(surrogates) == 3 and all(tables[s.table_id] == v2 for s in surrogates)  # type: ignore[index]


async def test_fresh_version_does_not_carry_forward(
    reference_corpus: Corpus, store: PostgresChunkStore, embedder: HashEmbedder, sessionmaker: Any
) -> None:
    result = await ingest_reference(
        sessionmaker, embedder, corpus_sources("SYN_PUB_C"), carry_forward=False
    )
    assert result.carried_forward_chunks == 0
    [(_, _, docs)] = [row for row in await store.list_versions("reference") if row[0].version == 2]
    assert docs == ["SYN_PUB_C"]


async def test_carry_forward_refuses_a_different_embedding_model(
    reference_corpus: Corpus, store: PostgresChunkStore, sessionmaker: Any
) -> None:
    with pytest.raises(ValueError, match="cannot carry forward"):
        await ingest_reference(sessionmaker, HashEmbedder(dim=16), corpus_sources("SYN_PUB_A"))
    assert [info.version for info, _, _ in await store.list_versions("reference")] == [1]


async def test_search_refuses_unpublished_and_unknown_versions(
    store: PostgresChunkStore, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with session_scope(sessionmaker) as session:
        draft = await PostgresChunkStore(session=session).create_version("reference", None, "m")
    for version in (draft.id, uuid.uuid4()):
        with pytest.raises(CorpusError):
            await store.search_bm25(version, "anything", 5)
        with pytest.raises(CorpusError):
            await store.search_vector(version, [1.0] + [0.0] * 383, 5)
    assert version not in store.registry


async def test_a_failed_ingest_leaves_no_rows(
    sessionmaker: async_sessionmaker[AsyncSession], embedder: HashEmbedder
) -> None:
    class FailsAtPublish(PostgresChunkStore):
        async def publish_version(self, corpus_version_id: uuid.UUID) -> Any:
            assert await self.count_chunks(corpus_version_id) > 0  # rows were written
            raise RuntimeError("crash before publish")

    with pytest.raises(RuntimeError, match="crash before publish"):
        async with session_scope(sessionmaker) as session:
            await ingest_documents(
                corpus_sources(),
                corpus="reference",
                study_id=None,
                store=FailsAtPublish(session=session),
                embedder=embedder,
            )

    class ShortChanged(HashEmbedder):
        def embed(self, texts: Any) -> list[list[float]]:
            return super().embed(texts)[:-1]

    with pytest.raises(RuntimeError, match="different number"):
        await ingest_reference(sessionmaker, ShortChanged(), corpus_sources())
    async with session_scope(sessionmaker) as session:
        for model in (CorpusVersion, ChunkRow, EvidenceTable):
            assert (await session.scalars(select(model))).all() == []
