"""The chunk store: PostgreSQL + pgvector (S3_SPEC section 2).

``ChunkStore`` is the read interface the classifier's tools and the evidence pack use;
every read is scoped by a corpus version id the caller bound at construction (the model
never supplies one). ``CorpusWriter`` is what ingestion needs.

:class:`PostgresChunkStore` implements both over ``corpus_versions``, ``chunks`` and
``evidence_tables``. It runs either

* on its own short transactions (``PostgresChunkStore(sessionmaker=...)``), for readers
  such as the classifier, or
* inside a caller's transaction (``PostgresChunkStore(session=...)``), for ingestion:
  the version row, its tables and chunks and the publish commit or roll back together
  with the caller's other writes (the activity's idempotency record).

Search reads only **published** versions: a reader never sees a version that is still
being written, and the BM25 registry never caches one (published versions are immutable,
migration 0003, so a cached index cannot go stale).

Vector search is ``ORDER BY embedding <=> :q LIMIT k`` (cosine distance; HNSW index from
migration 0003). Before it, ``SET LOCAL hnsw.iterative_scan = strict_order`` (pgvector
>= 0.8): with a ``WHERE corpus_version_id = ...`` filter an HNSW scan otherwise returns
only the matches among its first ``hnsw.ef_search`` (40) candidates, which for a small
study corpus inside a large table can be fewer than ``k`` or none. Rows are returned in
distance order; ties at equal distance are ordered by ``chunk_key`` after the fetch.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.models import Chunk as ChunkRow
from basis.db.models import CorpusVersion
from basis.db.models import EvidenceTable as TableRow
from basis.db.session import session_scope
from basis.retrieval.bm25 import Bm25Doc, Bm25Registry, process_registry
from basis.retrieval.tables import ExtractedTable
from basis.retrieval.types import Chunk, CorpusName, CorpusVersionInfo, Hit, Table


class CorpusError(RuntimeError):
    """A corpus version is missing, unpublished where it must be published, or the reverse."""


class ChunkStore(Protocol):
    async def search_bm25(self, corpus_version_id: uuid.UUID, query: str, k: int) -> list[Hit]: ...

    async def search_vector(
        self, corpus_version_id: uuid.UUID, query_embedding: Sequence[float], k: int
    ) -> list[Hit]: ...

    async def get_chunk(self, chunk_id: uuid.UUID) -> Chunk | None: ...

    async def get_table(self, table_id: uuid.UUID) -> Table | None: ...


@dataclass(frozen=True, slots=True)
class NewChunk:
    """A chunk ready to insert (embedding computed, table id resolved)."""

    doc_id: str
    chunk_key: str
    page_start: int | None
    page_end: int | None
    section_path: str | None
    text: str
    embedding: Sequence[float] | None
    table_id: uuid.UUID | None = None


class CorpusWriter(Protocol):
    async def create_version(
        self,
        corpus: CorpusName,
        study_id: uuid.UUID | None,
        embedding_model: str,
        version: int | None = None,
    ) -> CorpusVersionInfo: ...

    async def latest_version(
        self, corpus: CorpusName, study_id: uuid.UUID | None, *, published_only: bool = True
    ) -> CorpusVersionInfo | None: ...

    async def copy_documents(
        self, source_version_id: uuid.UUID, target_version_id: uuid.UUID, exclude: Collection[str]
    ) -> int: ...

    async def insert_tables(
        self, corpus_version_id: uuid.UUID, tables: Sequence[ExtractedTable]
    ) -> dict[str, uuid.UUID]: ...

    async def insert_chunks(
        self, corpus_version_id: uuid.UUID, chunks: Sequence[NewChunk]
    ) -> list[uuid.UUID]: ...

    async def publish_version(self, corpus_version_id: uuid.UUID) -> CorpusVersionInfo: ...


def _version_info(row: CorpusVersion) -> CorpusVersionInfo:
    return CorpusVersionInfo(
        id=row.id,
        corpus=cast(CorpusName, row.corpus),
        study_id=row.study_id,
        version=row.version,
        embedding_model=row.embedding_model,
        published_at=row.published_at,
        created_at=row.created_at,
    )


def _chunk(row: ChunkRow) -> Chunk:
    return Chunk(
        id=row.id,
        corpus_version_id=row.corpus_version_id,
        doc_id=row.doc_id,
        chunk_key=row.chunk_key,
        page_start=row.page_start,
        page_end=row.page_end,
        section_path=row.section_path,
        text=row.text,
        table_id=row.table_id,
    )


class PostgresChunkStore:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        *,
        session: AsyncSession | None = None,
        registry: Bm25Registry | None = None,
    ) -> None:
        if (sessionmaker is None) == (session is None):
            raise ValueError("pass exactly one of sessionmaker or session")
        self._sessionmaker = sessionmaker
        self._bound = session
        self.registry = registry if registry is not None else process_registry()

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        if self._bound is not None:
            yield self._bound
            return
        assert self._sessionmaker is not None
        async with session_scope(self._sessionmaker) as session:
            yield session

    # ------------------------------------------------------------------ versions

    async def get_version(self, corpus_version_id: uuid.UUID) -> CorpusVersionInfo | None:
        async with self._session() as session:
            row = await session.get(CorpusVersion, corpus_version_id)
            return None if row is None else _version_info(row)

    async def find_version(
        self, corpus: CorpusName, study_id: uuid.UUID | None, version: int
    ) -> CorpusVersionInfo | None:
        stmt = select(CorpusVersion).where(
            CorpusVersion.corpus == corpus,
            CorpusVersion.study_id.is_(None)
            if study_id is None
            else CorpusVersion.study_id == study_id,
            CorpusVersion.version == version,
        )
        async with self._session() as session:
            row = (await session.scalars(stmt)).one_or_none()
            return None if row is None else _version_info(row)

    async def latest_version(
        self, corpus: CorpusName, study_id: uuid.UUID | None, *, published_only: bool = True
    ) -> CorpusVersionInfo | None:
        stmt = select(CorpusVersion).where(
            CorpusVersion.corpus == corpus,
            CorpusVersion.study_id.is_(None)
            if study_id is None
            else CorpusVersion.study_id == study_id,
        )
        if published_only:
            stmt = stmt.where(CorpusVersion.published_at.is_not(None))
        stmt = stmt.order_by(CorpusVersion.version.desc()).limit(1)
        async with self._session() as session:
            row = (await session.scalars(stmt)).one_or_none()
            return None if row is None else _version_info(row)

    async def list_versions(
        self, corpus: CorpusName, study_id: uuid.UUID | None = None
    ) -> list[tuple[CorpusVersionInfo, int, list[str]]]:
        """Every version of a corpus with its chunk count and document ids, oldest first."""
        stmt = (
            select(
                CorpusVersion,
                func.count(ChunkRow.id),
                func.array_remove(func.array_agg(func.distinct(ChunkRow.doc_id)), None),
            )
            .outerjoin(ChunkRow, ChunkRow.corpus_version_id == CorpusVersion.id)
            .where(
                CorpusVersion.corpus == corpus,
                CorpusVersion.study_id.is_(None)
                if study_id is None
                else CorpusVersion.study_id == study_id,
            )
            .group_by(CorpusVersion.id)
            .order_by(CorpusVersion.version)
        )
        async with self._session() as session:
            rows = (await session.execute(stmt)).all()
            return [(_version_info(v), int(n), sorted(docs or [])) for v, n, docs in rows]

    async def create_version(
        self,
        corpus: CorpusName,
        study_id: uuid.UUID | None,
        embedding_model: str,
        version: int | None = None,
    ) -> CorpusVersionInfo:
        """A new unpublished version (``version`` defaults to the corpus's latest + 1).

        Two writers racing for the same number fail on the unique constraint; nothing is
        overwritten.
        """
        if (corpus == "study") != (study_id is not None):
            raise CorpusError("a study corpus needs a study id; the reference corpus has none")
        async with self._session() as session:
            if version is None:
                current = await session.scalar(
                    select(func.max(CorpusVersion.version)).where(
                        CorpusVersion.corpus == corpus,
                        CorpusVersion.study_id.is_(None)
                        if study_id is None
                        else CorpusVersion.study_id == study_id,
                    )
                )
                version = (current or 0) + 1
            row = CorpusVersion(
                corpus=corpus, study_id=study_id, version=version, embedding_model=embedding_model
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return _version_info(row)

    async def publish_version(self, corpus_version_id: uuid.UUID) -> CorpusVersionInfo:
        async with self._session() as session:
            row = (
                await session.scalars(
                    update(CorpusVersion)
                    .where(
                        CorpusVersion.id == corpus_version_id,
                        CorpusVersion.published_at.is_(None),
                    )
                    .values(published_at=func.now())
                    .returning(CorpusVersion)
                    .execution_options(populate_existing=True)
                )
            ).one_or_none()
            if row is None:
                raise CorpusError(f"corpus version {corpus_version_id} is missing or published")
            return _version_info(row)

    async def _require_unpublished(self, session: AsyncSession, version_id: uuid.UUID) -> None:
        row = await session.get(CorpusVersion, version_id)
        if row is None:
            raise CorpusError(f"corpus version {version_id} does not exist")
        if row.published_at is not None:
            raise CorpusError(f"corpus version {version_id} is published and immutable")

    async def _require_published(self, session: AsyncSession, version_id: uuid.UUID) -> None:
        published = await session.scalar(
            select(CorpusVersion.published_at).where(CorpusVersion.id == version_id)
        )
        if published is None:
            raise CorpusError(f"corpus version {version_id} is missing or not published")

    # ------------------------------------------------------------------ writes

    async def insert_tables(
        self, corpus_version_id: uuid.UUID, tables: Sequence[ExtractedTable]
    ) -> dict[str, uuid.UUID]:
        """Insert whole tables; returns ``{ExtractedTable.key: evidence_tables.id}``."""
        if not tables:
            return {}
        ids = [uuid.uuid4() for _ in tables]
        rows = [
            {
                "id": table_id,
                "corpus_version_id": corpus_version_id,
                "doc_id": table.doc_id,
                "page": table.page,
                "headers": list(table.headers),
                "rows": [list(row) for row in table.rows],
                "markdown": table.markdown(),
            }
            for table, table_id in zip(tables, ids, strict=True)
        ]
        async with self._session() as session:
            await self._require_unpublished(session, corpus_version_id)
            await session.execute(insert(TableRow), rows)
        return {table.key: table_id for table, table_id in zip(tables, ids, strict=True)}

    async def insert_chunks(
        self, corpus_version_id: uuid.UUID, chunks: Sequence[NewChunk]
    ) -> list[uuid.UUID]:
        if not chunks:
            return []
        rows = [
            {
                "id": uuid.uuid4(),
                "corpus_version_id": corpus_version_id,
                "doc_id": chunk.doc_id,
                "chunk_key": chunk.chunk_key,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "section_path": chunk.section_path,
                "text": chunk.text,
                "embedding": None if chunk.embedding is None else list(chunk.embedding),
                "table_id": chunk.table_id,
            }
            for chunk in chunks
        ]
        async with self._session() as session:
            await self._require_unpublished(session, corpus_version_id)
            await session.execute(insert(ChunkRow), rows)
        return [cast(uuid.UUID, row["id"]) for row in rows]

    async def copy_documents(
        self, source_version_id: uuid.UUID, target_version_id: uuid.UUID, exclude: Collection[str]
    ) -> int:
        """Copy every document of ``source`` not in ``exclude`` into ``target``.

        Tables get new ids in the target and the copied surrogate chunks point at them,
        so the new version is self-contained; the source version is untouched.
        """
        excluded = list(exclude)
        async with self._session() as session:
            await self._require_unpublished(session, target_version_id)
            table_stmt = select(TableRow).where(TableRow.corpus_version_id == source_version_id)
            chunk_stmt = select(ChunkRow).where(ChunkRow.corpus_version_id == source_version_id)
            if excluded:
                table_stmt = table_stmt.where(TableRow.doc_id.not_in(excluded))
                chunk_stmt = chunk_stmt.where(ChunkRow.doc_id.not_in(excluded))
            tables = (await session.scalars(table_stmt.order_by(TableRow.id))).all()
            remap = {table.id: uuid.uuid4() for table in tables}
            if tables:
                await session.execute(
                    insert(TableRow),
                    [
                        {
                            "id": remap[t.id],
                            "corpus_version_id": target_version_id,
                            "doc_id": t.doc_id,
                            "page": t.page,
                            "headers": t.headers,
                            "rows": t.rows,
                            "markdown": t.markdown,
                        }
                        for t in tables
                    ],
                )
            chunks = (await session.scalars(chunk_stmt.order_by(ChunkRow.chunk_key))).all()
            if chunks:
                await session.execute(
                    insert(ChunkRow),
                    [
                        {
                            "corpus_version_id": target_version_id,
                            "doc_id": c.doc_id,
                            "chunk_key": c.chunk_key,
                            "page_start": c.page_start,
                            "page_end": c.page_end,
                            "section_path": c.section_path,
                            "text": c.text,
                            "embedding": c.embedding,
                            "table_id": None if c.table_id is None else remap.get(c.table_id),
                        }
                        for c in chunks
                    ],
                )
            return len(chunks)

    async def discard_unpublished(self, corpus_version_id: uuid.UUID) -> None:
        """Delete an unpublished version with its chunks and tables.

        Only for leftovers: every write path here publishes in the transaction that
        creates the version, so a committed unpublished version can only come from the
        Stage 2 stub ``ingest_appraisal`` (placeholder chunks, model ``stub``). Runs in a
        savepoint; if something still references its chunks (a stub citation) the
        database refuses and :class:`CorpusError` says so.
        """
        async with self._session() as session:
            await self._require_unpublished(session, corpus_version_id)
            try:
                async with session.begin_nested():
                    await session.execute(
                        delete(ChunkRow).where(ChunkRow.corpus_version_id == corpus_version_id)
                    )
                    await session.execute(
                        delete(TableRow).where(TableRow.corpus_version_id == corpus_version_id)
                    )
                    await session.execute(
                        delete(CorpusVersion).where(CorpusVersion.id == corpus_version_id)
                    )
            except IntegrityError as exc:
                raise CorpusError(
                    f"unpublished corpus version {corpus_version_id} is still referenced"
                ) from exc

    # ------------------------------------------------------------------ reads

    async def count_chunks(self, corpus_version_id: uuid.UUID) -> int:
        async with self._session() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(ChunkRow)
                .where(ChunkRow.corpus_version_id == corpus_version_id)
            )
            return int(count or 0)

    async def _bm25_docs(self, corpus_version_id: uuid.UUID) -> list[Bm25Doc]:
        async with self._session() as session:
            await self._require_published(session, corpus_version_id)
            rows = await session.execute(
                select(ChunkRow.id, ChunkRow.text)
                .where(ChunkRow.corpus_version_id == corpus_version_id)
                .order_by(ChunkRow.chunk_key)
            )
            return [Bm25Doc(chunk_id=row.id, text=row.text) for row in rows]

    async def warm(self, version_ids: Sequence[uuid.UUID]) -> None:
        await self.registry.warm(version_ids, self._bm25_docs)

    async def search_bm25(self, corpus_version_id: uuid.UUID, query: str, k: int) -> list[Hit]:
        index = await self.registry.get(corpus_version_id, self._bm25_docs)
        ranked = index.search(query, k)
        if not ranked:
            return []
        async with self._session() as session:
            rows = (
                await session.scalars(
                    select(ChunkRow).where(ChunkRow.id.in_([cid for cid, _ in ranked]))
                )
            ).all()
        by_id = {row.id: row for row in rows}
        return [_hit(by_id[cid], score) for cid, score in ranked if cid in by_id]

    async def search_vector(
        self, corpus_version_id: uuid.UUID, query_embedding: Sequence[float], k: int
    ) -> list[Hit]:
        if k <= 0:
            return []
        distance = ChunkRow.embedding.cosine_distance(list(query_embedding)).label("distance")
        stmt = (
            select(ChunkRow, distance)
            .where(
                ChunkRow.corpus_version_id == corpus_version_id,
                ChunkRow.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(k)
        )
        async with self._session() as session:
            await self._require_published(session, corpus_version_id)
            await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            rows = (await session.execute(stmt)).all()
        ordered = sorted(rows, key=lambda r: (float(r.distance), r.Chunk.chunk_key))
        return [_hit(r.Chunk, 1.0 - float(r.distance)) for r in ordered]

    async def get_chunk(self, chunk_id: uuid.UUID) -> Chunk | None:
        async with self._session() as session:
            row = await session.get(ChunkRow, chunk_id)
            return None if row is None else _chunk(row)

    async def get_table(self, table_id: uuid.UUID) -> Table | None:
        async with self._session() as session:
            row = await session.get(TableRow, table_id)
            if row is None:
                return None
            return Table(
                id=row.id,
                corpus_version_id=row.corpus_version_id,
                doc_id=row.doc_id,
                page=row.page,
                headers=list(_as_list(row.headers)),
                rows=[list(_as_list(r)) for r in _as_list(row.rows)],
                markdown=row.markdown,
            )


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _hit(row: ChunkRow, score: float) -> Hit:
    return Hit(
        chunk_id=row.id,
        doc_id=row.doc_id,
        page_start=row.page_start,
        page_end=row.page_end,
        score=score,
        text=row.text,
        table_id=row.table_id,
    )


async def warm_latest_reference(
    sessionmaker: async_sessionmaker[AsyncSession], registry: Bm25Registry
) -> uuid.UUID | None:
    """Build the BM25 index of the latest published reference version (worker start).

    Returns the version id, or ``None`` when no reference corpus has been ingested.
    """
    store = PostgresChunkStore(sessionmaker, registry=registry)
    latest = await store.latest_version("reference", None)
    if latest is None:
        return None
    await store.warm([latest.id])
    return latest.id
