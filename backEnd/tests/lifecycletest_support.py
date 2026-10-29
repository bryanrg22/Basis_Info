"""Helpers shared by the workflow lifecycle tests and the activity tests.

Imported as a top-level module (``tests/`` is on ``sys.path``, see ``dbtest_support``).
Everything here writes synthetic rows only: file names like ``Kitchen photo 1.jpg``, random
sha256 values, and ``gs://basis-test/...`` URIs. With ``storage`` given, ``seed_study``
writes bytes at those URIs so the Stage 3 activities, which read the files, have something
real to read: the appraisal gets ``appraisal_bytes`` (default the synthetic
``urar_sample.pdf``) and the photos get ``llm_fakes.fixture_bytes_for``; the document rows
carry the real size and sha256 of what was written. ``add_appraisal`` registers another
appraisal (a UAD 3.6 ZIP or XML, a companion PDF) in its own transaction, so registration
order is ``created_at`` order as in production.

``make_deps`` wires the hash embedder with a fresh BM25 registry (no model download) and
the offline providers by default: ``FixtureVisionProvider`` for photos and ``FixtureAzureDi``
(the hand-built Azure DI result) for the appraisal; pass ``llm=None`` / ``azure_di=None``
for the unconfigured paths.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from typing import Any, Final

from classifytest_support import REF_VERSION, lifecycle_reference_chunks
from dbtest_support import Factory
from fixtures import fixture_path
from llm_fakes import FixtureAzureDi, FixtureVisionProvider, fixture_bytes_for
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.activities import Deps
from basis.classify.schemas import Classification
from basis.classify.store_protocol import ChunkStore, InMemoryChunkStore, StoredChunk
from basis.config import Settings
from basis.db.enums import Corpus, DocumentKind, Stage
from basis.db.models import ActivityResult, Base, Chunk, CorpusVersion, Run
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.run import RunRepository
from basis.db.repositories.study import StudyRepository
from basis.db.session import session_scope
from basis.domain.intake import StudyIntake, StudyIntakeIn, with_location
from basis.extraction.azure_di import AzureDiClient
from basis.llm.provider import ChatProvider, ScriptedProvider, ScriptedTurn
from basis.retrieval import Retrieval
from basis.retrieval.bm25 import Bm25Registry
from basis.retrieval.chunk import chunk_document
from basis.retrieval.embed import HashEmbedder
from basis.retrieval.parse import parse_pdf
from basis.storage import Storage
from basis.vision.localize import Localizer
from basis.vision.surface_segmenter import SurfaceSegmenter

TEST_BUCKET: Final = "basis-test"
# Named like a real package (two views of one kitchen, one bathroom), so production's
# file-name grouping (ROOM_GROUPING=filename) and the model's room identity agree.
DEFAULT_PHOTOS: Final = ("Kitchen photo 1.jpg", "Kitchen photo 2.jpg", "Bathroom photo 1.jpg")
DEFAULT_APPRAISAL: Final = "appraisal_12_main_st.pdf"


def sample_appraisal_pdf() -> bytes:
    """The synthetic URAR appraisal (``tests/fixtures/synthetic/urar_sample.pdf``)."""
    return fixture_path("urar_sample.pdf").read_bytes()


@cache
def sample_appraisal_chunks() -> int:
    """How many chunks one ingestion of the sample appraisal writes (text + tables)."""
    return len(chunk_document(parse_pdf(sample_appraisal_pdf(), "sample")))


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
    content: bytes | None = None,
    content_type: str | None = None,
) -> tuple[uuid.UUID, str]:
    repo = DocumentRepository(session)
    document_id = uuid.uuid4()
    uri = f"gs://{TEST_BUCKET}/studies/{study_id}/{document_id}/{name}"
    is_appraisal = kind is DocumentKind.APPRAISAL
    declared = content_type or ("application/pdf" if is_appraisal else "image/jpeg")
    # Explicit content (the appraisal) is hashed for real; fixture bytes written for the
    # photos share images between files, so their rows keep a random sha256 (the
    # documents table is unique on (study_id, sha256)).
    explicit = content is not None
    if storage is not None and content is None:
        content = fixture_bytes_for(name, appraisal=is_appraisal)
    if storage is not None and content is not None:
        await storage.put(uri, content, declared)
    await repo.create(
        study_id=study_id,
        kind=kind,
        filename=name,
        content_type=declared,
        size_bytes=1024 if content is None else len(content),
        sha256=hashlib.sha256(content).hexdigest()
        if explicit and content
        else secrets.token_hex(32),
        gcs_uri=uri,
        uploaded_by=owner_id,
        document_id=document_id,
    )
    await repo.mark_uploaded(study_id, document_id)
    return document_id, uri


async def seed_study(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    photos: Sequence[str] = DEFAULT_PHOTOS,
    appraisal: str | None = DEFAULT_APPRAISAL,
    stage: Stage = Stage.UPLOADING,
    storage: Storage | None = None,
    appraisal_bytes: bytes | None = None,
) -> SeededStudy:
    """A committed study with an owner, an uploaded appraisal and uploaded photos.

    With ``storage``, each document's bytes are written to its URI: the appraisal gets
    ``appraisal_bytes`` (default the synthetic URAR sample) and the photos get fixture
    images, so the real ``ingest_appraisal``, ``extract_fields`` and ``analyze_photo``
    can read them.
    """
    content = None
    if storage is not None and appraisal is not None:
        content = appraisal_bytes if appraisal_bytes is not None else sample_appraisal_pdf()
    async with session_scope(sessionmaker) as session:
        make = Factory(session)
        owner = await make.user()
        study = await make.study(owner, stage=stage)
        appraisal_id = None
        if appraisal is not None:
            appraisal_id, _uri = await _document(
                session, study.id, owner.id, DocumentKind.APPRAISAL, appraisal, storage, content
            )
        photo_ids = [
            (await _document(session, study.id, owner.id, DocumentKind.PHOTO, name, storage))[0]
            for name in photos
        ]
        return SeededStudy(
            study_id=study.id,
            org_id=study.org_id,
            owner_id=owner.id,
            appraisal_id=appraisal_id,
            photo_ids=photo_ids,
        )


APPRAISAL_TYPES: Final = {
    "pdf": "application/pdf",
    "zip": "application/zip",
    "xml": "application/xml",
}


def intake_document(**overrides: Any) -> StudyIntake:
    """A complete intake (every field a start needs) as stored on a study; ``overrides``
    replace fields of the request body before validation."""
    body: dict[str, Any] = {
        "purchase_price": "650000.00",
        "acquisition_costs": [{"category": "recording_fees", "amount": "180.00"}],
        "land_method": "appraisal_site_value",
        "appraisal_site_value": "90000.00",
        "property_use": "residential_rental",
        "dwelling_unit_income_share": "1",
        "placed_in_service_date": "2025-05-01",
        "state": "NY",
        "zip": "12345",
    }
    body.update(overrides)
    return with_location(
        StudyIntakeIn.model_validate(body), appraisal_state=None, appraisal_zip=None
    )


async def set_intake(
    sessionmaker: async_sessionmaker[AsyncSession], study_id: uuid.UUID, intake: StudyIntake
) -> None:
    """Store ``intake`` on the study (what ``PUT /v1/studies/{id}/intake`` does)."""
    async with session_scope(sessionmaker) as session:
        await StudyRepository(session).set_intake(study_id, intake)


async def add_appraisal(
    sessionmaker: async_sessionmaker[AsyncSession],
    study: SeededStudy,
    name: str,
    content: bytes,
    storage: Storage | None,
    *,
    content_type: str | None = None,
) -> uuid.UUID:
    """Register and confirm one more appraisal of ``study`` (its own transaction); the
    content type defaults from the file extension (:data:`APPRAISAL_TYPES`)."""
    declared = content_type or APPRAISAL_TYPES[name.rsplit(".", 1)[-1].lower()]
    async with session_scope(sessionmaker) as session:
        document_id, _uri = await _document(
            session,
            study.study_id,
            study.owner_id,
            DocumentKind.APPRAISAL,
            name,
            storage,
            content,
            declared,
        )
    return document_id


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


# One scripted answer per residual classification: a building-bucket guess with no
# citations, which the pipeline writes as needs_review (missing evidence). Enough turns for
# every lifecycle scenario; an unexpected extra call fails loudly (ScriptedProvider).
SCRIPTED_CLASSIFIER_TURNS: Final = 400


def scripted_classifier(turns: int = SCRIPTED_CLASSIFIER_TURNS) -> ScriptedProvider:
    answer = Classification(
        category="Building component (scripted)",
        section="1250",
        bucket="y27_5",
        confidence=0.4,
        needs_review=True,
        reason="scripted test answer",
    )
    return ScriptedProvider([ScriptedTurn(parsed=answer) for _ in range(turns)])


async def seed_reference_corpus(
    sessionmaker: async_sessionmaker[AsyncSession], chunks: Sequence[StoredChunk] | None = None
) -> InMemoryChunkStore:
    """A published reference corpus (version 1, ``REF_VERSION``) in Postgres, mirrored
    with the same chunk ids into an in-memory store for the activities.

    Default chunks: :func:`classifytest_support.lifecycle_reference_chunks`, so every
    rules-table citation resolves and rule assets get verified citations as in production.
    The chunks are inserted before the version is published: a published version's chunks
    are immutable (migration 0003).
    """
    rows = list(lifecycle_reference_chunks() if chunks is None else chunks)
    async with session_scope(sessionmaker) as session:
        session.add(
            CorpusVersion(
                id=REF_VERSION,
                corpus=Corpus.REFERENCE.value,
                study_id=None,
                version=1,
                embedding_model="test",
            )
        )
        await session.flush()
        session.add_all(
            Chunk(
                id=c.id,
                corpus_version_id=c.corpus_version_id,
                doc_id=c.doc_id,
                chunk_key=c.chunk_key,
                page_start=c.page_start,
                page_end=c.page_end,
                text=c.text,
            )
            for c in rows
        )
        await session.flush()
        await session.execute(
            update(CorpusVersion)
            .where(CorpusVersion.id == REF_VERSION)
            .values(published_at=datetime.now(UTC))
        )
    return InMemoryChunkStore.of(rows)


_DEFAULT: Final = object()


def make_deps(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: Storage,
    settings: Settings,
    *,
    llm: ChatProvider | None | object = _DEFAULT,
    azure_di: AzureDiClient | None | object = _DEFAULT,
    chat_provider: ChatProvider | None | object = _DEFAULT,
    chunk_store: ChunkStore | None | object = _DEFAULT,
    localizer: Localizer | None = None,
    segmenter: SurfaceSegmenter | None = None,
) -> Deps:
    """Deps for tests: hash embedder + fresh BM25 registry, the offline vision/Azure DI
    fixtures, a scripted classifier and an empty in-memory store by default. No localizer
    unless given (photos are then localized ``skipped``, as without REPLICATE_API_TOKEN)."""
    provider = scripted_classifier() if chat_provider is _DEFAULT else chat_provider
    store = InMemoryChunkStore() if chunk_store is _DEFAULT else chunk_store
    return Deps(
        sessionmaker=sessionmaker,
        storage=storage,
        bucket=TEST_BUCKET,
        settings=settings,
        retrieval=Retrieval(embedder=HashEmbedder(), registry=Bm25Registry()),
        llm=FixtureVisionProvider() if llm is _DEFAULT else llm,  # type: ignore[arg-type]
        azure_di=FixtureAzureDi() if azure_di is _DEFAULT else azure_di,  # type: ignore[arg-type]
        chat_provider=provider,  # type: ignore[arg-type]
        chunk_store=store,  # type: ignore[arg-type]
        localizer=localizer,
        segmenter=segmenter,
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
