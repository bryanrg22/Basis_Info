"""``ingest_appraisal`` (stub): study corpus version + 3 chunks; idempotent; heartbeats.

BUILD_PLAN 6.2 ``node/test_ingest_appraisal.py``: run twice -> one corpus version.
"""

from __future__ import annotations

import uuid

import pytest
from lifecycletest_support import SeededStudy, count_rows, seed_run, seed_study
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.ingest_appraisal import IngestAppraisal
from basis.db.models import Chunk, CorpusVersion
from basis.db.session import session_scope
from basis.workflow.contracts import IngestAppraisalInput

from .conftest import Env

pytestmark = pytest.mark.integration


def ingest(study_id: uuid.UUID, key: str = "k-ingest") -> IngestAppraisalInput:
    return IngestAppraisalInput(study_id=str(study_id), revision=1, idempotency_key=key)


async def test_writes_a_corpus_version_and_three_chunks(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(IngestAppraisal(deps).run, ingest(seeded.study_id))
    assert output.document_id == str(seeded.appraisal_id) and output.chunks == 3
    async with session_scope(deps.sessionmaker) as session:
        version = await session.get(CorpusVersion, uuid.UUID(output.corpus_version_id or ""))
        assert version is not None
        assert (version.corpus, version.study_id, version.version) == ("study", seeded.study_id, 1)
        chunks = (
            await session.scalars(select(Chunk).where(Chunk.corpus_version_id == version.id))
        ).all()
    assert sorted(c.page_start for c in chunks) == [1, 2, 3]
    assert {c.doc_id for c in chunks} == {str(seeded.appraisal_id)}
    assert len(activity_env.heartbeats) == 3


async def test_running_twice_writes_nothing_new(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = IngestAppraisal(deps)
    first = await activity_env.run(activity.run, ingest(seeded.study_id))
    replay = await activity_env.run(activity.run, ingest(seeded.study_id))
    # A new key for the same revision (a reset execution) reuses the business keys.
    other = await activity_env.run(activity.run, ingest(seeded.study_id, key="k-other"))
    assert first == replay == other
    assert await count_rows(deps.sessionmaker, CorpusVersion) == 1
    assert await count_rows(deps.sessionmaker, Chunk) == 3


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
