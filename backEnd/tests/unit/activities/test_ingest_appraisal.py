"""``ingest_appraisal``: the synthetic URAR appraisal becomes a published study corpus.

BUILD_PLAN 6.2 ``node/test_ingest_appraisal.py``: idempotent (run twice -> one corpus
version); a failure part-way leaves no partial rows; bad input is not retried.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fixtures import fixture_path, load_fixture_json
from lifecycletest_support import SeededStudy, count_rows, seed_run, seed_study
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.ingest_appraisal import IngestAppraisal
from basis.db.enums import Bucket, ClassificationMethod
from basis.db.models import ActivityResult, Asset, Chunk, Citation, CorpusVersion, EvidenceTable
from basis.db.repositories.corpus import CorpusRepository
from basis.db.session import session_scope
from basis.retrieval import Retrieval
from basis.retrieval.bm25 import Bm25Registry
from basis.retrieval.embed import HashEmbedder
from basis.retrieval.store import PostgresChunkStore
from basis.storage import LocalStorage
from basis.workflow.contracts import IngestAppraisalInput

from .conftest import Env

pytestmark = pytest.mark.integration


def ingest(study_id: uuid.UUID, key: str = "k-ingest", revision: int = 1) -> IngestAppraisalInput:
    return IngestAppraisalInput(study_id=str(study_id), revision=revision, idempotency_key=key)


async def rows(
    sessionmaker: async_sessionmaker[AsyncSession], model: Any, version_id: uuid.UUID
) -> list[Any]:
    async with session_scope(sessionmaker) as session:
        stmt = select(model).where(model.corpus_version_id == version_id)
        return list((await session.scalars(stmt)).all())


async def test_ingests_the_appraisal_into_a_published_study_version(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(IngestAppraisal(deps).run, ingest(seeded.study_id))
    doc_id = str(seeded.appraisal_id)
    assert output.document_id == doc_id and output.chunks > 0
    version_id = uuid.UUID(output.corpus_version_id or "")
    async with session_scope(deps.sessionmaker) as session:
        version = await session.get(CorpusVersion, version_id)
    assert version is not None and version.published_at is not None
    assert (version.corpus, version.study_id, version.version) == ("study", seeded.study_id, 1)
    assert version.embedding_model == "hash-v1-384"

    chunks = await rows(deps.sessionmaker, Chunk, version_id)
    assert len(chunks) == output.chunks
    assert {c.doc_id for c in chunks} == {doc_id}
    assert {c.page_start for c in chunks} == set(range(1, 8))
    assert all(c.chunk_key.startswith(f"{doc_id}:{c.page_start}:") for c in chunks)
    assert all(c.embedding is not None for c in chunks)

    golden = load_fixture_json("urar_sample.tables.json")["tables"]
    tables = await rows(deps.sessionmaker, EvidenceTable, version_id)
    assert sorted((t.page, t.headers) for t in tables) == [
        (g["page"], g["headers"]) for g in golden
    ]
    surrogates = [c for c in chunks if c.table_id is not None]
    assert {c.table_id for c in surrogates} == {t.id for t in tables}
    assert activity_env.heartbeats  # parse, embed and publish steps heartbeat


async def test_the_study_corpus_is_searchable(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(IngestAppraisal(deps).run, ingest(seeded.study_id))
    version_id = uuid.UUID(output.corpus_version_id or "")
    store = PostgresChunkStore(deps.sessionmaker, registry=Bm25Registry())
    [hit] = await store.search_bm25(version_id, "Depreciated Cost of Improvements", 1)
    assert hit.page_start == 6 and hit.table_id is not None  # the cost approach table
    table = await store.get_table(hit.table_id)
    assert table is not None and ["Depreciated Cost of Improvements", "", "", "$312,850"] in (
        table.rows
    )
    [first] = await store.search_bm25(version_id, "Property Address 1290 Example Way", 1)
    assert first.page_start == 1 and "Property Address: 1290 Example Way" in first.text


async def test_running_twice_writes_nothing_new(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = IngestAppraisal(deps)
    first = await activity_env.run(activity.run, ingest(seeded.study_id))
    replay = await activity_env.run(activity.run, ingest(seeded.study_id))
    # A new key for the same revision (a reset execution) reuses the business key.
    other = await activity_env.run(activity.run, ingest(seeded.study_id, key="k-other"))
    assert first == replay == other
    assert await count_rows(deps.sessionmaker, CorpusVersion) == 1
    assert await count_rows(deps.sessionmaker, Chunk) == first.chunks
    assert await count_rows(deps.sessionmaker, EvidenceTable) == 2


async def test_a_new_revision_gets_a_new_version(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = IngestAppraisal(deps)
    one = await activity_env.run(activity.run, ingest(seeded.study_id))
    two = await activity_env.run(activity.run, ingest(seeded.study_id, key="k-r2", revision=2))
    assert one.corpus_version_id != two.corpus_version_id and one.chunks == two.chunks
    assert await count_rows(deps.sessionmaker, CorpusVersion) == 2


async def test_failure_part_way_leaves_no_rows(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    class Exploding(HashEmbedder):
        def embed(self, texts: Any) -> list[list[float]]:
            raise RuntimeError("embedder crashed")

    broken = Deps(
        sessionmaker=deps.sessionmaker,
        storage=deps.storage,
        bucket=deps.bucket,
        settings=deps.settings,
        retrieval=Retrieval(embedder=Exploding(), registry=Bm25Registry()),
    )
    with pytest.raises(RuntimeError, match="embedder crashed"):
        await activity_env.run(IngestAppraisal(broken).run, ingest(seeded.study_id))
    for model in (CorpusVersion, Chunk, EvidenceTable):
        assert await count_rows(deps.sessionmaker, model) == 0
    async with session_scope(deps.sessionmaker) as session:
        result = (await session.scalars(select(ActivityResult))).one()
    assert result.status == "failed" and "embedder crashed" in (result.error or "")
    # The retry (same key) succeeds once the embedder works again.
    output = await activity_env.run(IngestAppraisal(deps).run, ingest(seeded.study_id))
    assert output.chunks > 0


async def test_no_appraisal_yields_no_document(
    deps: Deps, sessionmaker: async_sessionmaker[AsyncSession], activity_env: Env
) -> None:
    study = await seed_study(sessionmaker, appraisal=None, photos=("kitchen_1.jpg",))
    await seed_run(sessionmaker, study.study_id)
    output = await activity_env.run(IngestAppraisal(deps).run, ingest(study.study_id))
    assert (output.document_id, output.corpus_version_id, output.chunks) == (None, None, 0)


async def test_unknown_study_is_not_retried(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(IngestAppraisal(deps).run, ingest(uuid.uuid4()))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable


async def seeded_with(
    sessionmaker: async_sessionmaker[AsyncSession], storage: LocalStorage, content: bytes
) -> SeededStudy:
    study = await seed_study(sessionmaker, storage=storage, appraisal_bytes=content)
    await seed_run(sessionmaker, study.study_id)
    return study


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        (("malformed", "not_a_pdf.pdf"), "not a PDF"),
        (("malformed", "empty.pdf"), "no pages"),
        (("malformed", "huge_page_count.pdf"), "200 pages"),
    ],
)
async def test_unusable_pdfs_are_not_retried(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
    fixture: tuple[str, ...],
    message: str,
) -> None:
    study = await seeded_with(sessionmaker, storage, fixture_path(*fixture).read_bytes())
    with pytest.raises(ApplicationError, match=message) as caught:
        await activity_env.run(IngestAppraisal(deps).run, ingest(study.study_id))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable
    assert await count_rows(deps.sessionmaker, CorpusVersion) == 0


async def test_missing_object_is_not_retried(
    deps: Deps, sessionmaker: async_sessionmaker[AsyncSession], activity_env: Env
) -> None:
    study = await seed_study(sessionmaker)  # rows only: nothing in storage
    await seed_run(sessionmaker, study.study_id)
    with pytest.raises(ApplicationError, match="missing from storage") as caught:
        await activity_env.run(IngestAppraisal(deps).run, ingest(study.study_id))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable


async def test_scanned_appraisal_is_an_empty_published_version(
    deps: Deps,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    activity_env: Env,
    caplog: pytest.LogCaptureFixture,
) -> None:
    study = await seeded_with(
        sessionmaker, storage, fixture_path("urar_scan_noisy.pdf").read_bytes()
    )
    output = await activity_env.run(IngestAppraisal(deps).run, ingest(study.study_id))
    assert output.chunks == 0 and output.corpus_version_id is not None
    async with session_scope(deps.sessionmaker) as session:
        version = await session.get(CorpusVersion, uuid.UUID(output.corpus_version_id))
    assert version is not None and version.published_at is not None
    assert any("no text layer" in r.getMessage() for r in caplog.records)


async def stub_leftover(
    sessionmaker: async_sessionmaker[AsyncSession], study: SeededStudy, *, cited: bool
) -> uuid.UUID:
    """What the Stage 2 stub left: an unpublished version with a placeholder chunk."""
    async with session_scope(sessionmaker) as session:
        corpus = CorpusRepository(session)
        version = await corpus.study_version(study.study_id, 1, "stub")
        await corpus.add_chunk(
            corpus_version_id=version.id, doc_id="stub", chunk_key="stub:p1", text="placeholder"
        )
        if cited:
            [chunk] = await corpus.chunks(version.id)
            asset = Asset(
                study_id=study.study_id,
                revision=1,
                category="stub",
                bucket=Bucket.Y5,
                recovery_period=5,
                method=ClassificationMethod.RULE,
            )
            session.add(asset)
            await session.flush()
            session.add(
                Citation(study_id=study.study_id, asset_id=asset.id, chunk_id=chunk.id, excerpt="x")
            )
        return version.id


async def test_a_stage2_stub_leftover_is_replaced(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    leftover = await stub_leftover(deps.sessionmaker, seeded, cited=False)
    output = await activity_env.run(IngestAppraisal(deps).run, ingest(seeded.study_id))
    assert output.corpus_version_id != str(leftover) and output.chunks > 0
    async with session_scope(deps.sessionmaker) as session:
        assert await session.get(CorpusVersion, leftover) is None
        version = await session.get(CorpusVersion, uuid.UUID(output.corpus_version_id or ""))
    assert version is not None and version.version == 1 and version.published_at is not None
    assert await count_rows(deps.sessionmaker, Chunk) == output.chunks


async def test_a_cited_stub_leftover_is_not_retried(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    leftover = await stub_leftover(deps.sessionmaker, seeded, cited=True)
    with pytest.raises(ApplicationError, match="still referenced") as caught:
        await activity_env.run(IngestAppraisal(deps).run, ingest(seeded.study_id))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable
    async with session_scope(deps.sessionmaker) as session:
        assert await session.get(CorpusVersion, leftover) is not None


@pytest.mark.regression
async def test_no_connection_is_held_while_the_appraisal_is_embedded(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    """EVAL_REPORT 31.4: the download, parsing and embedding (seconds of CPU on the
    production embedder) ran inside run_once's transaction, holding a pooled connection;
    ten studies starting together starved the worker's pool. Now they run before it."""
    engine = deps.sessionmaker.kw["bind"]
    checked_out: list[int] = []

    class Watching(HashEmbedder):
        def embed(self, texts: Any) -> list[list[float]]:
            checked_out.append(engine.sync_engine.pool.checkedout())
            return super().embed(texts)

    watched = Deps(
        sessionmaker=deps.sessionmaker,
        storage=deps.storage,
        bucket=deps.bucket,
        settings=deps.settings,
        retrieval=Retrieval(embedder=Watching(), registry=Bm25Registry()),
    )
    output = await activity_env.run(IngestAppraisal(watched).run, ingest(seeded.study_id))
    assert output.chunks > 0
    assert checked_out and set(checked_out) == {0}
