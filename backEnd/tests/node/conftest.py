"""Node tests: the real ``process_assets``/``export_report`` bodies against Postgres.

A reference corpus (published version 1) is written to Postgres and mirrored, with the
same chunk ids, into an :class:`InMemoryChunkStore`, so verified citations satisfy the
``citations.chunk_id`` foreign key exactly as with the Postgres store. Classification
runs one component at a time (``classify_concurrency=1``) so a scripted provider's turns
are consumed in component order.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from classifytest_support import REF_VERSION, reference_chunks
from lifecycletest_support import SeededStudy, make_deps, seed_run, seed_study
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from unit.activities.conftest import (  # noqa: F401  (fixtures re-exported for this dir)
    Env,
    activity_env,
    db_test_settings,
    sessionmaker,
    storage,
)

from basis.activities import Deps
from basis.classify.store_protocol import InMemoryChunkStore
from basis.config import Settings
from basis.db.enums import Corpus
from basis.db.models import Chunk, CorpusVersion, Room
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.llm.provider import ScriptedProvider, ScriptedTurn
from basis.storage import LocalStorage


async def seed_reference_corpus(sm: async_sessionmaker[AsyncSession]) -> InMemoryChunkStore:
    chunks = reference_chunks()
    async with session_scope(sm) as session:
        session.add(
            CorpusVersion(
                id=REF_VERSION,
                corpus=Corpus.REFERENCE.value,
                study_id=None,
                version=1,
                embedding_model="test",
                published_at=datetime.now(UTC),
            )
        )
        await session.flush()
        for c in chunks:
            session.add(
                Chunk(
                    id=c.id,
                    corpus_version_id=c.corpus_version_id,
                    doc_id=c.doc_id,
                    chunk_key=c.chunk_key,
                    page_start=c.page_start,
                    page_end=c.page_end,
                    text=c.text,
                )
            )
    return InMemoryChunkStore.of(chunks)


def node_deps(
    sm: async_sessionmaker[AsyncSession],
    files: LocalStorage,
    settings: Settings,
    store: InMemoryChunkStore,
    turns: Sequence[ScriptedTurn],
) -> Deps:
    serial = settings.model_copy(update={"classify_concurrency": 1})
    return make_deps(sm, files, serial, chat_provider=ScriptedProvider(turns), chunk_store=store)


async def add_room_with(
    sm: async_sessionmaker[AsyncSession],
    study_id: uuid.UUID,
    name: str,
    room_type: str,
    labels: Sequence[tuple[str, str | None]],
    revision: int = 1,
) -> Room:
    async with session_scope(sm) as session:
        rooms = RoomRepository(session)
        room = await rooms.create(
            study_id=study_id, revision=revision, name=name, room_type=room_type, source="test"
        )
        for label, material in labels:
            await rooms.add_component(
                study_id=study_id,
                revision=revision,
                document_id=None,
                room_id=room.id,
                label=label,
                material=material,
                attachment=None,
                confidence=0.9,
            )
        return room


@pytest.fixture
async def study(sessionmaker: async_sessionmaker[AsyncSession]) -> SeededStudy:  # noqa: F811
    seeded = await seed_study(sessionmaker, photos=())
    await seed_run(sessionmaker, seeded.study_id)
    return seeded


@pytest.fixture
async def reference_store(
    sessionmaker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> InMemoryChunkStore:
    return await seed_reference_corpus(sessionmaker)
