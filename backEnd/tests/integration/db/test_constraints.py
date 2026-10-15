"""Database-enforced invariants (S1_SPEC §2, §7; BUILD_PLAN §6.5).

Every negative test asserts the *name* of the constraint that fired, so a row rejected
for an unrelated reason (a typo, a missing NOT NULL value) cannot make a test pass.
Every tenancy test has a positive control that inserts the same row within one study.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from dbtest_support import Factory
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import Bucket, ClassificationMethod, Decision, DocumentKind, Stage
from basis.db.errors import constraint_name
from basis.db.models import (
    Asset,
    Chunk,
    Citation,
    Component,
    CorpusVersion,
    CostEstimate,
    Document,
    Extraction,
    ExtractionField,
    IdempotencyKey,
    OrgMember,
    PhotoRoom,
    Review,
    Room,
    Study,
    Takeoff,
    User,
)

pytestmark = pytest.mark.integration


def sha(n: int) -> str:
    return f"{n:064x}"


async def violates(session: AsyncSession, constraint: str, *rows: Any) -> IntegrityError:
    """Insert ``rows`` in a SAVEPOINT and assert that exactly ``constraint`` rejects them."""
    with pytest.raises(IntegrityError) as info:
        async with session.begin_nested():
            session.add_all(rows)
    assert constraint_name(info.value) == constraint, str(info.value.orig)
    return info.value


async def inserts(session: AsyncSession, *rows: Any) -> None:
    async with session.begin_nested():
        session.add_all(rows)


async def sql_violates(session: AsyncSession, statement: str, **params: Any) -> IntegrityError:
    with pytest.raises(IntegrityError) as info:
        async with session.begin_nested():
            await session.execute(text(statement), params)
    return info.value


@dataclass
class StudyTree:
    """One study with one row of every study-scoped kind."""

    study: Study
    document: Document
    extraction: Extraction
    room: Room
    component: Component
    asset: Asset
    chunk: Chunk


async def build_tree(session: AsyncSession, make: Factory, owner: User, n: int) -> StudyTree:
    study = await make.study(owner)
    document = Document(
        study_id=study.id,
        kind=DocumentKind.PHOTO,
        filename=f"photo{n}.jpg",
        content_type="image/jpeg",
        bytes=10,
        sha256=sha(n),
        gcs_uri=f"gs://bucket/{study.id}/photo{n}.jpg",
    )
    room = Room(study_id=study.id, revision=1, name="Kitchen", room_type="kitchen", source="vlm")
    await inserts(session, document, room)
    extraction = Extraction(
        study_id=study.id, document_id=document.id, revision=1, method="azure", raw={}
    )
    component = Component(
        study_id=study.id, revision=1, document_id=document.id, room_id=room.id, label="sink"
    )
    await inserts(session, extraction, component)
    asset = Asset(
        study_id=study.id,
        revision=1,
        component_id=component.id,
        category="plumbing",
        bucket=Bucket.Y5,
        recovery_period=Decimal("5.0"),
        method=ClassificationMethod.RULE,
    )
    corpus = CorpusVersion(corpus="study", study_id=study.id, version=1, embedding_model="m")
    await inserts(session, asset, corpus)
    chunk = Chunk(corpus_version_id=corpus.id, doc_id="d", chunk_key=f"k{n}", text="t")
    await inserts(session, chunk)
    return StudyTree(study, document, extraction, room, component, asset, chunk)


@pytest.fixture
async def trees(session: AsyncSession, make: Factory) -> tuple[StudyTree, StudyTree]:
    owner = await make.user()
    return await build_tree(session, make, owner, 1), await build_tree(session, make, owner, 2)


# Each case builds a row in study ``a`` that points at a parent in study ``p``.
ChildFactory = Callable[[StudyTree, StudyTree], Any]

CROSS_STUDY_CASES: dict[str, tuple[ChildFactory, str]] = {
    "component->room": (
        lambda a, p: Component(study_id=a.study.id, revision=1, room_id=p.room.id, label="x"),
        "fk_components_study_id_room_id_rooms",
    ),
    "component->document": (
        lambda a, p: Component(
            study_id=a.study.id, revision=1, document_id=p.document.id, label="x"
        ),
        "fk_components_study_id_document_id_documents",
    ),
    "asset->component": (
        lambda a, p: Asset(
            study_id=a.study.id,
            revision=1,
            component_id=p.component.id,
            category="c",
            bucket=Bucket.Y7,
            recovery_period=Decimal("7.0"),
            method=ClassificationMethod.LLM,
        ),
        "fk_assets_study_id_component_id_components",
    ),
    "takeoff->asset": (
        lambda a, p: Takeoff(
            study_id=a.study.id, asset_id=p.asset.id, quantity=Decimal("1"), unit="ea"
        ),
        "fk_takeoffs_study_id_asset_id_assets",
    ),
    "cost_estimate->asset": (
        lambda a, p: CostEstimate(study_id=a.study.id, asset_id=p.asset.id),
        "fk_cost_estimates_study_id_asset_id_assets",
    ),
    "citation->asset": (
        lambda a, p: Citation(
            study_id=a.study.id, asset_id=p.asset.id, chunk_id=a.chunk.id, excerpt="e"
        ),
        "fk_citations_study_id_asset_id_assets",
    ),
    "photo_room->room": (
        lambda a, p: PhotoRoom(
            study_id=a.study.id, document_id=a.document.id, room_id=p.room.id, revision=2
        ),
        "fk_photo_rooms_study_id_room_id_rooms",
    ),
    "photo_room->document": (
        lambda a, p: PhotoRoom(
            study_id=a.study.id, document_id=p.document.id, room_id=a.room.id, revision=2
        ),
        "fk_photo_rooms_study_id_document_id_documents",
    ),
    "extraction->document": (
        lambda a, p: Extraction(
            study_id=a.study.id, document_id=p.document.id, revision=9, method="m", raw={}
        ),
        "fk_extractions_study_id_document_id_documents",
    ),
    "extraction_field->extraction": (
        lambda a, p: ExtractionField(
            study_id=a.study.id,
            extraction_id=p.extraction.id,
            section="s",
            field="f",
            source="azure",
        ),
        "fk_extraction_fields_study_id_extraction_id_extractions",
    ),
}


@pytest.mark.parametrize("case", sorted(CROSS_STUDY_CASES))
async def test_cross_study_reference_is_rejected(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree], case: str
) -> None:
    build, constraint = CROSS_STUDY_CASES[case]
    a, b = trees
    await violates(session, constraint, build(a, b))


@pytest.mark.parametrize("case", sorted(CROSS_STUDY_CASES))
async def test_same_study_reference_is_accepted(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree], case: str
) -> None:
    build, _ = CROSS_STUDY_CASES[case]
    a, _ = trees
    if case in {"takeoff->asset", "cost_estimate->asset"}:
        # These are unique per asset; use a fresh asset in the same study.
        fresh = Asset(
            study_id=a.study.id,
            revision=1,
            category="c",
            bucket=Bucket.Y15,
            recovery_period=Decimal("15.0"),
            method=ClassificationMethod.MANUAL,
        )
        await inserts(session, fresh)
        a.asset = fresh
    await inserts(session, build(a, a))


async def test_citation_to_missing_chunk_is_rejected(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree]
) -> None:
    a, _ = trees
    citation = Citation(
        study_id=a.study.id, asset_id=a.asset.id, chunk_id=uuid.uuid4(), excerpt="e"
    )
    await violates(session, "fk_citations_chunk_id_chunks", citation)


async def test_null_study_id_cannot_bypass_tenancy(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree]
) -> None:
    # A NULL in a composite FK disables the check (MATCH SIMPLE), so study_id is NOT NULL.
    _, b = trees
    with pytest.raises(IntegrityError, match="not-null"):
        async with session.begin_nested():
            session.add(Component(study_id=None, revision=1, room_id=b.room.id, label="x"))


async def test_duplicate_extraction_revision_is_rejected(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree]
) -> None:
    a, _ = trees
    duplicate = Extraction(
        study_id=a.study.id, document_id=a.document.id, revision=1, method="regex", raw={}
    )
    await violates(session, "uq_extractions_document_id_revision", duplicate)
    await inserts(
        session,
        Extraction(
            study_id=a.study.id, document_id=a.document.id, revision=2, method="regex", raw={}
        ),
    )


def a_document(study_id: uuid.UUID, digest: str, uri: str | None = None) -> Document:
    return Document(
        study_id=study_id,
        kind=DocumentKind.APPRAISAL,
        filename="appraisal.pdf",
        content_type="application/pdf",
        bytes=100,
        sha256=digest,
        gcs_uri=uri or f"gs://bucket/{uuid.uuid4()}",
    )


async def test_duplicate_sha256_in_one_study_is_rejected(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree]
) -> None:
    a, b = trees
    await violates(session, "uq_documents_study_id_sha256", a_document(a.study.id, sha(1)))
    # The same file in another study is fine.
    await inserts(session, a_document(b.study.id, sha(1)))


async def test_duplicate_gcs_uri_is_rejected(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree]
) -> None:
    a, _ = trees
    await violates(
        session, "uq_documents_gcs_uri", a_document(a.study.id, sha(99), a.document.gcs_uri)
    )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"bytes": 0}, "ck_documents_bytes_positive"),
        ({"bytes": -5}, "ck_documents_bytes_positive"),
        ({"sha256": sha(7).upper().replace("0", "A")}, "ck_documents_sha256_hex"),
        ({"sha256": "z" * 64}, "ck_documents_sha256_hex"),
    ],
)
async def test_document_checks(
    session: AsyncSession,
    trees: tuple[StudyTree, StudyTree],
    overrides: dict[str, Any],
    constraint: str,
) -> None:
    a, _ = trees
    document = a_document(a.study.id, sha(50))
    for key, value in overrides.items():
        setattr(document, key, value)
    await violates(session, constraint, document)


def a_review(study_id: uuid.UUID, reviewer_id: uuid.UUID, key: str, revision: int = 1) -> Review:
    return Review(
        study_id=study_id,
        stage=Stage.APPRAISAL_REVIEW,
        revision=revision,
        decision=Decision.APPROVE,
        reviewer_id=reviewer_id,
        idempotency_key=key,
    )


@pytest.fixture
async def review(session: AsyncSession, trees: tuple[StudyTree, StudyTree]) -> Review:
    a, _ = trees
    review = a_review(a.study.id, a.study.created_by, "key-1")
    await inserts(session, review)
    return review


async def test_second_review_of_same_stage_revision_is_rejected(
    session: AsyncSession, review: Review
) -> None:
    await violates(
        session,
        "uq_reviews_study_id_stage_revision",
        a_review(review.study_id, review.reviewer_id, "key-2"),
    )
    await inserts(session, a_review(review.study_id, review.reviewer_id, "key-3", revision=2))


async def test_review_idempotency_key_is_unique(session: AsyncSession, review: Review) -> None:
    await violates(
        session,
        "uq_reviews_idempotency_key",
        a_review(review.study_id, review.reviewer_id, "key-1", revision=5),
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE reviews SET decision = 'reject' WHERE id = :id",
        "UPDATE reviews SET comment = 'edited' WHERE id = :id",
        "DELETE FROM reviews WHERE id = :id",
    ],
)
async def test_reviews_are_immutable(session: AsyncSession, review: Review, statement: str) -> None:
    error = await sql_violates(session, statement, id=review.id)
    assert "reviews rows are append-only" in str(error.orig)
    assert getattr(error.orig, "sqlstate", None) == "23001"
    stored = await session.scalar(
        select(Review.decision)
        .where(Review.id == review.id)
        .execution_options(populate_existing=True)
    )
    assert stored is Decision.APPROVE


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE corrections SET after = '{\"v\": 2}' WHERE id = :id",
        "DELETE FROM corrections WHERE id = :id",
    ],
)
async def test_corrections_are_append_only(
    session: AsyncSession, review: Review, statement: str
) -> None:
    result = await session.execute(
        text(
            "INSERT INTO corrections (study_id, entity_type, entity_id, revision, after, "
            "reviewer_id) VALUES (:s, 'asset', :e, 1, '{\"v\": 1}', :r) RETURNING id"
        ),
        {"s": review.study_id, "e": uuid.uuid4(), "r": review.reviewer_id},
    )
    correction_id = result.scalar_one()
    error = await sql_violates(session, statement, id=correction_id)
    assert "corrections rows are append-only" in str(error.orig)


async def test_firebase_uid_cannot_change(session: AsyncSession, make: Factory) -> None:
    user = await make.user()
    error = await sql_violates(
        session, "UPDATE users SET firebase_uid = 'someone-else' WHERE id = :id", id=user.id
    )
    assert "firebase_uid cannot change" in str(error.orig)
    # Other columns, and a no-op assignment of the same uid, are fine.
    await session.execute(
        text(
            "UPDATE users SET email = 'new@example.test', firebase_uid = firebase_uid "
            "WHERE id = :id"
        ),
        {"id": user.id},
    )


async def test_firebase_uid_is_unique(session: AsyncSession, make: Factory) -> None:
    user = await make.user()
    await violates(session, "uq_users_firebase_uid", User(firebase_uid=user.firebase_uid))


async def test_idempotency_key_duplicate_is_rejected(session: AsyncSession) -> None:
    def row() -> IdempotencyKey:
        return IdempotencyKey(
            key="user:abc",
            route="POST /v1/studies",
            request_hash=sha(1),
            expires_at=text("now() + interval '1 day'"),
        )

    await inserts(session, row())
    await violates(session, "pk_idempotency_keys", row())


@pytest.mark.parametrize(
    ("enum_type", "valid"),
    [
        ("study_status", "draft"),
        ("stage", "uploading"),
        ("document_kind", "photo"),
        ("decision", "approve"),
        ("classification_method", "rule"),
        ("bucket", "y27_5"),
    ],
)
async def test_invalid_enum_value_is_rejected(
    session: AsyncSession, enum_type: str, valid: str
) -> None:
    assert (
        await session.scalar(text(f"SELECT CAST(:v AS {enum_type})::text"), {"v": valid}) == valid
    )
    with pytest.raises(DBAPIError, match="invalid input value for enum"):
        async with session.begin_nested():
            await session.execute(text(f"SELECT CAST(:v AS {enum_type})"), {"v": "bogus"})


async def test_invalid_enum_value_rejected_on_insert(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree]
) -> None:
    a, _ = trees
    with pytest.raises(DBAPIError, match="invalid input value for enum stage"):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE studies SET current_stage = CAST(:v AS stage) WHERE id = :id"),
                {"v": "reviewing", "id": a.study.id},
            )


async def test_org_member_role_check(session: AsyncSession, make: Factory) -> None:
    user = await make.user()
    org = await make.org()
    await violates(
        session,
        "ck_org_members_role_valid",
        OrgMember(org_id=org.id, user_id=user.id, role="admin"),
    )


@pytest.mark.parametrize("value", [-0.01, 1.01])
async def test_confidence_must_be_between_0_and_1(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree], value: float
) -> None:
    a, _ = trees
    component = Component(study_id=a.study.id, revision=1, label="x", confidence=value)
    await violates(session, "ck_components_confidence_range", component)
    await inserts(session, Component(study_id=a.study.id, revision=1, label="x", confidence=1.0))


async def test_takeoff_and_cost_are_unique_per_asset(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree]
) -> None:
    a, _ = trees
    await inserts(
        session,
        Takeoff(study_id=a.study.id, asset_id=a.asset.id, quantity=Decimal("2"), unit="ea"),
        CostEstimate(study_id=a.study.id, asset_id=a.asset.id),
    )
    await violates(
        session,
        "uq_takeoffs_asset_id",
        Takeoff(study_id=a.study.id, asset_id=a.asset.id, quantity=Decimal("3"), unit="ea"),
    )
    await violates(
        session,
        "uq_cost_estimates_asset_id",
        CostEstimate(study_id=a.study.id, asset_id=a.asset.id),
    )


async def test_photo_maps_to_one_room_per_revision(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree]
) -> None:
    a, _ = trees
    other_room = Room(study_id=a.study.id, revision=1, name="Bath", room_type="bath", source="vlm")
    await inserts(session, other_room)
    await inserts(
        session,
        PhotoRoom(study_id=a.study.id, document_id=a.document.id, room_id=a.room.id, revision=1),
    )
    await violates(
        session,
        "pk_photo_rooms",
        PhotoRoom(
            study_id=a.study.id, document_id=a.document.id, room_id=other_room.id, revision=1
        ),
    )


async def test_reference_corpus_versions_are_unique(session: AsyncSession) -> None:
    def reference(version: int) -> CorpusVersion:
        return CorpusVersion(corpus="reference", version=version, embedding_model="m")

    await inserts(session, reference(1))
    # NULLS NOT DISTINCT: study_id is NULL for the reference corpus and still collides.
    await violates(session, "uq_corpus_versions_corpus_study_id_version", reference(1))
    await inserts(session, reference(2))


@pytest.mark.parametrize(
    ("corpus", "with_study", "constraint"),
    [
        ("reference", True, "ck_corpus_versions_study_scope"),
        ("study", False, "ck_corpus_versions_study_scope"),
        ("other", False, "ck_corpus_versions_corpus_valid"),
    ],
)
async def test_corpus_version_checks(
    session: AsyncSession,
    trees: tuple[StudyTree, StudyTree],
    corpus: str,
    with_study: bool,
    constraint: str,
) -> None:
    a, _ = trees
    row = CorpusVersion(
        corpus=corpus, study_id=a.study.id if with_study else None, version=7, embedding_model="m"
    )
    await violates(session, constraint, row)


async def test_chunk_embedding_dimension_is_enforced(
    session: AsyncSession, trees: tuple[StudyTree, StudyTree]
) -> None:
    a, _ = trees
    good = Chunk(
        corpus_version_id=a.chunk.corpus_version_id,
        doc_id="d",
        chunk_key="good",
        text="t",
        embedding=[0.5] * 384,
    )
    await inserts(session, good)
    stored = await session.scalar(
        select(Chunk.embedding).where(Chunk.id == good.id).execution_options(populate_existing=True)
    )
    assert stored is not None and len(stored) == 384
    with pytest.raises(DBAPIError, match="expected 384 dimensions"):
        async with session.begin_nested():
            session.add(
                Chunk(
                    corpus_version_id=a.chunk.corpus_version_id,
                    doc_id="d",
                    chunk_key="bad",
                    text="t",
                    embedding=[0.5] * 3,
                )
            )


async def test_updated_at_trigger_advances_on_update(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    async with committed() as session, session.begin():
        study = await Factory(session).study()
        created_at, first_updated_at = study.created_at, study.updated_at
    assert first_updated_at == created_at
    async with committed() as session, session.begin():
        await session.execute(
            text("UPDATE studies SET property_name = 'renamed' WHERE id = :id"), {"id": study.id}
        )
    async with committed() as session:
        row = (
            await session.execute(
                select(Study.created_at, Study.updated_at).where(Study.id == study.id)
            )
        ).one()
    assert row.created_at == created_at
    assert row.updated_at > first_updated_at
