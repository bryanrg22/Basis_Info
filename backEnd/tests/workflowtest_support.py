"""Helpers shared by the workflow lifecycle tests and the activity tests.

Imported as a top-level module (``tests/`` is on ``sys.path``, see ``dbtest_support``).
Everything here writes synthetic rows only: file names like ``kitchen_1.jpg``, random
sha256 values, and ``gs://basis-test/...`` URIs that point at nothing.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from dbtest_support import Factory
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.activities import Deps
from basis.config import Settings
from basis.db.enums import DocumentKind, Stage
from basis.db.models import ActivityResult, Base, Run
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.run import RunRepository
from basis.db.session import session_scope
from basis.storage import Storage

TEST_BUCKET: Final = "basis-test"
DEFAULT_PHOTOS: Final = ("kitchen_1.jpg", "kitchen_2.jpg", "bathroom_1.jpg")
DEFAULT_APPRAISAL: Final = "appraisal_12_main_st.pdf"


@dataclass(frozen=True)
class SeededStudy:
    study_id: uuid.UUID
    org_id: uuid.UUID
    owner_id: uuid.UUID
    appraisal_id: uuid.UUID | None
    photo_ids: list[uuid.UUID] = field(default_factory=list)


async def _document(
    session: AsyncSession, study_id: uuid.UUID, owner_id: uuid.UUID, kind: DocumentKind, name: str
) -> uuid.UUID:
    repo = DocumentRepository(session)
    document_id = uuid.uuid4()
    await repo.create(
        study_id=study_id,
        kind=kind,
        filename=name,
        content_type="application/pdf" if kind is DocumentKind.APPRAISAL else "image/jpeg",
        size_bytes=1024,
        sha256=secrets.token_hex(32),
        gcs_uri=f"gs://{TEST_BUCKET}/studies/{study_id}/{document_id}/{name}",
        uploaded_by=owner_id,
        document_id=document_id,
    )
    await repo.mark_uploaded(study_id, document_id)
    return document_id


async def seed_study(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    photos: Sequence[str] = DEFAULT_PHOTOS,
    appraisal: str | None = DEFAULT_APPRAISAL,
    stage: Stage = Stage.UPLOADING,
) -> SeededStudy:
    """A committed study with an owner, an uploaded appraisal and uploaded photos."""
    async with session_scope(sessionmaker) as session:
        make = Factory(session)
        owner = await make.user()
        study = await make.study(owner, stage=stage)
        appraisal_id = (
            None
            if appraisal is None
            else await _document(session, study.id, owner.id, DocumentKind.APPRAISAL, appraisal)
        )
        photo_ids = [
            await _document(session, study.id, owner.id, DocumentKind.PHOTO, name)
            for name in photos
        ]
        return SeededStudy(
            study_id=study.id,
            org_id=study.org_id,
            owner_id=owner.id,
            appraisal_id=appraisal_id,
            photo_ids=photo_ids,
        )


async def seed_run(
    sessionmaker: async_sessionmaker[AsyncSession],
    study_id: uuid.UUID,
    *,
    workflow_id: str = "test",
    run_id: str = "test-run",
) -> Run:
    """The ``runs`` row ``ActivityEnvironment``'s default info refers to."""
    async with session_scope(sessionmaker) as session:
        return await RunRepository(session).start(
            study_id=study_id,
            workflow_id=workflow_id,
            run_id=run_id,
            workflow_version="test",
            input_revision=1,
        )


def make_deps(
    sessionmaker: async_sessionmaker[AsyncSession], storage: Storage, settings: Settings
) -> Deps:
    return Deps(sessionmaker=sessionmaker, storage=storage, bucket=TEST_BUCKET, settings=settings)


async def count_rows(
    sessionmaker: async_sessionmaker[AsyncSession], model: type[Base], **where: object
) -> int:
    async with session_scope(sessionmaker) as session:
        stmt = select(func.count()).select_from(model)
        for name, value in where.items():
            stmt = stmt.where(getattr(model, name) == value)
        return int((await session.scalar(stmt)) or 0)


async def activity_results(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> list[ActivityResult]:
    async with session_scope(sessionmaker) as session:
        rows = await session.scalars(select(ActivityResult).order_by(ActivityResult.created_at))
        return list(rows.all())
