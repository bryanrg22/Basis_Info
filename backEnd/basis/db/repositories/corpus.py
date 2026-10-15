"""Evidence corpora: versions and chunks (``corpus_versions``, ``chunks``)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import Corpus
from basis.db.models import Chunk, CorpusVersion


class CorpusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def study_version(
        self, study_id: uuid.UUID, version: int, embedding_model: str
    ) -> CorpusVersion:
        """The study corpus at ``version``, created if missing (unique per study/version)."""
        await self._session.execute(
            pg_insert(CorpusVersion)
            .values(
                corpus=Corpus.STUDY.value,
                study_id=study_id,
                version=version,
                embedding_model=embedding_model,
            )
            .on_conflict_do_nothing(
                index_elements=[CorpusVersion.corpus, CorpusVersion.study_id, CorpusVersion.version]
            )
        )
        stmt = select(CorpusVersion).where(
            CorpusVersion.corpus == Corpus.STUDY.value,
            CorpusVersion.study_id == study_id,
            CorpusVersion.version == version,
        )
        return (await self._session.scalars(stmt)).one()

    async def add_chunk(
        self,
        *,
        corpus_version_id: uuid.UUID,
        doc_id: str,
        chunk_key: str,
        text: str,
        page_start: int | None = None,
        page_end: int | None = None,
        section_path: str | None = None,
    ) -> None:
        """Insert the chunk unless ``(corpus_version_id, chunk_key)`` already exists."""
        await self._session.execute(
            pg_insert(Chunk)
            .values(
                corpus_version_id=corpus_version_id,
                doc_id=doc_id,
                chunk_key=chunk_key,
                text=text,
                page_start=page_start,
                page_end=page_end,
                section_path=section_path,
            )
            .on_conflict_do_nothing(index_elements=[Chunk.corpus_version_id, Chunk.chunk_key])
        )

    async def chunks(self, corpus_version_id: uuid.UUID) -> list[Chunk]:
        stmt = (
            select(Chunk)
            .where(Chunk.corpus_version_id == corpus_version_id)
            .order_by(Chunk.chunk_key)
        )
        return list((await self._session.scalars(stmt)).all())

    async def study_chunks(self, study_id: uuid.UUID) -> list[Chunk]:
        """Chunks of the study's newest corpus version (empty when nothing was ingested)."""
        latest = (
            select(CorpusVersion.id)
            .where(CorpusVersion.corpus == Corpus.STUDY.value, CorpusVersion.study_id == study_id)
            .order_by(CorpusVersion.version.desc())
            .limit(1)
            .scalar_subquery()
        )
        stmt = select(Chunk).where(Chunk.corpus_version_id == latest).order_by(Chunk.chunk_key)
        return list((await self._session.scalars(stmt)).all())
