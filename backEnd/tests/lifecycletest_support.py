"""Helpers shared by the workflow lifecycle tests and the activity tests.

Imported as a top-level module (``tests/`` is on ``sys.path``, see ``dbtest_support``).
Everything here writes synthetic rows only: file names like ``kitchen_1.jpg``, random
sha256 values, and ``gs://basis-test/...`` URIs. With ``storage`` given, ``seed_study``
also writes synthetic fixture bytes at those URIs (``llm_fakes.fixture_bytes_for``) so the
Stage 3 activities, which read the files, have something real to read.

``make_deps`` wires the offline providers by default: ``FixtureVisionProvider`` for photos
and ``FixtureAzureDi`` (the hand-built Azure DI result) for the appraisal; pass
``llm=None`` / ``azure_di=None`` for the unconfigured paths.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from dbtest_support import Factory
from llm_fakes import FixtureAzureDi, FixtureVisionProvider, fixture_bytes_for
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.activities import Deps
from basis.config import Settings
from basis.db.enums import DocumentKind, Stage
from basis.db.models import ActivityResult, Base, Run
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.run import RunRepository
from basis.db.session import session_scope
from basis.extraction.azure_di import AzureDiClient
from basis.llm.provider import ChatProvider
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
    session: AsyncSession,
    study_id: uuid.UUID,
    owner_id: uuid.UUID,
    kind: DocumentKind,
    name: str,
    storage: Storage | None = None,
) -> uuid.UUID:
    repo = DocumentRepository(session)
    document_id = uuid.uuid4()
    uri = f"gs://{TEST_BUCKET}/studies/{study_id}/{document_id}/{name}"
    is_appraisal = kind is DocumentKind.APPRAISAL
    if storage is not None:
        await storage.put(
            uri,
            fixture_bytes_for(name, appraisal=is_appraisal),
            "application/pdf" if is_appraisal else "image/jpeg",
        )
    await repo.create(
        study_id=study_id,
        kind=kind,
        filename=name,
        content_type="application/pdf" if kind is DocumentKind.APPRAISAL else "image/jpeg",
        size_bytes=1024,
        sha256=secrets.token_hex(32),
        gcs_uri=uri,
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
    storage: Storage | None = None,
) -> SeededStudy:
    """A committed study with an owner, an uploaded appraisal and uploaded photos.

    With ``storage``, each document's bytes are written to its URI (fixture files).
    """
    async with session_scope(sessionmaker) as session:
        make = Factory(session)
        owner = await make.user()
        study = await make.study(owner, stage=stage)
        appraisal_id = (
            None
            if appraisal is None
            else await _document(
                session, study.id, owner.id, DocumentKind.APPRAISAL, appraisal, storage
            )
        )
        photo_ids = [
            await _document(session, study.id, owner.id, DocumentKind.PHOTO, name, storage)
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


_DEFAULT: object = object()


def make_deps(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: Storage,
    settings: Settings,
    *,
    llm: ChatProvider | None | object = _DEFAULT,
    azure_di: AzureDiClient | None | object = _DEFAULT,
) -> Deps:
    return Deps(
        sessionmaker=sessionmaker,
        storage=storage,
        bucket=TEST_BUCKET,
        settings=settings,
        llm=FixtureVisionProvider() if llm is _DEFAULT else llm,  # type: ignore[arg-type]
        azure_di=FixtureAzureDi() if azure_di is _DEFAULT else azure_di,  # type: ignore[arg-type]
    )


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
