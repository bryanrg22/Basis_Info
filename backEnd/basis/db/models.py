"""SQLAlchemy 2.0 declarative models for every table in S1_SPEC §2.

Conventions (S1_SPEC §1) and how they are enforced here:

* Primary keys are ``UUID DEFAULT gen_random_uuid()`` (server side), except the natural
  keys the spec names (``org_members``, ``document_pages``, ``photo_rooms``,
  ``classification_cache``, ``idempotency_keys``).
* Every table has ``created_at TIMESTAMPTZ NOT NULL DEFAULT now()``. Mutable tables
  (``studies``, ``activity_results``, ``classification_cache``) also have ``updated_at``,
  maintained by the ``set_updated_at()`` trigger.
* Tenancy: every study-scoped child carries ``study_id NOT NULL`` and a composite foreign
  key ``(study_id, <parent>_id) -> <parent> (study_id, id)``, backed by
  ``UNIQUE (study_id, id)`` on the parent. A row therefore cannot reference a parent in
  another study. ``study_id`` is NOT NULL everywhere because a NULL in a composite key
  disables the check (``MATCH SIMPLE``).
* Constraint names follow ``ix_``/``uq_``/``fk_``/``ck_``/``pk_`` via the metadata naming
  convention, so Alembic autogenerate and the hand-written migration agree.

Database-side behavior that is not expressible as SQLAlchemy metadata lives in migration
``0001_initial`` as SQL and is listed here so readers of the models know it exists:

* ``set_updated_at()``: ``BEFORE UPDATE`` row trigger on ``studies``,
  ``activity_results`` and ``classification_cache`` that sets ``updated_at = now()``.
* ``forbid_mutation()``: ``BEFORE UPDATE OR DELETE`` row trigger on ``reviews`` and
  ``corrections`` that raises SQLSTATE ``23001`` (restrict_violation, surfaced by
  SQLAlchemy as ``IntegrityError``) with the message ``<table> rows are append-only``.
  Review decisions and corrections are an audit trail and are never edited.
* ``forbid_firebase_uid_change()``: ``BEFORE UPDATE OF firebase_uid`` trigger on
  ``users`` that raises the same SQLSTATE when ``firebase_uid`` would change
  (BUILD_PLAN §6.5 "userId cannot change").
* ``CREATE EXTENSION IF NOT EXISTS vector`` (pgvector) for ``chunks.embedding``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from basis.db.enums import (
    Bucket,
    ClassificationMethod,
    Decision,
    DocumentKind,
    Stage,
    StudyStatus,
)

EMBEDDING_DIM: Final = 384

NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Tables with an ``updated_at`` column maintained by the set_updated_at() trigger.
UPDATED_AT_TABLES: Final = ("studies", "activity_results", "classification_cache")
# Tables whose rows can never be updated or deleted (forbid_mutation() trigger).
APPEND_ONLY_TABLES: Final = ("reviews", "corrections")


def _enum_values(enum_cls: type[Any]) -> list[str]:
    return [member.value for member in enum_cls]


def _pg_enum(enum_cls: type[Any], name: str) -> ENUM:
    """A native PostgreSQL enum whose labels are the members' values (not their names)."""
    return ENUM(
        enum_cls,
        name=name,
        values_callable=_enum_values,
        validate_strings=True,
        create_type=False,  # created explicitly by the migration
    )


STUDY_STATUS_ENUM: Final = _pg_enum(StudyStatus, "study_status")
STAGE_ENUM: Final = _pg_enum(Stage, "stage")
DOCUMENT_KIND_ENUM: Final = _pg_enum(DocumentKind, "document_kind")
DECISION_ENUM: Final = _pg_enum(Decision, "decision")
CLASSIFICATION_METHOD_ENUM: Final = _pg_enum(ClassificationMethod, "classification_method")
BUCKET_ENUM: Final = _pg_enum(Bucket, "bucket")

PG_ENUMS: Final = (
    STUDY_STATUS_ENUM,
    STAGE_ENUM,
    DOCUMENT_KIND_ENUM,
    DECISION_ENUM,
    CLASSIFICATION_METHOD_ENUM,
    BUCKET_ENUM,
)

# Python None is stored as SQL NULL, never as the JSON literal 'null', so NOT NULL on a
# JSONB column really rejects a missing document.
JSON_DOC: Final = JSONB(none_as_null=True)
MONEY: Final = Numeric(14, 2)
QUANTITY: Final = Numeric(14, 4)
TIMESTAMPTZ: Final = DateTime(timezone=True)

NOW: Final = text("now()")
GEN_UUID: Final = text("gen_random_uuid()")
FALSE: Final = text("false")
ZERO: Final = text("0")


def _confidence_check(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} BETWEEN 0 AND 1", name=f"{column}_range")


class Base(AsyncAttrs, DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        uuid.UUID: PG_UUID(as_uuid=True),
        datetime: TIMESTAMPTZ,
        date: Date,
        str: Text,
        int: Integer,
        bool: Boolean,
        dict[str, Any]: JSON_DOC,
    }


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, server_default=GEN_UUID)


def _created_at() -> Mapped[datetime]:
    return mapped_column(server_default=NOW)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(server_default=NOW)


# --------------------------------------------------------------------- identity, tenancy


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str]
    created_at: Mapped[datetime] = _created_at()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    firebase_uid: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str | None]
    display_name: Mapped[str | None]
    created_at: Mapped[datetime] = _created_at()
    last_seen_at: Mapped[datetime | None]


class OrgMember(Base):
    __tablename__ = "org_members"
    __table_args__ = (
        PrimaryKeyConstraint("org_id", "user_id"),
        CheckConstraint("role IN ('owner', 'engineer', 'viewer')", name="role_valid"),
        # The PK serves lookups by org; "which orgs is this user in" needs its own index.
        Index(None, "user_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str]
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------- studies, documents


class Study(Base):
    __tablename__ = "studies"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_studies_org_id_created_at", "org_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    property_name: Mapped[str]
    status: Mapped[StudyStatus] = mapped_column(STUDY_STATUS_ENUM, server_default=text("'draft'"))
    current_stage: Mapped[Stage] = mapped_column(STAGE_ENUM, server_default=text("'uploading'"))
    # The study's current output revision (S1_SPEC §1: outputs are grouped by revision per
    # study). transition() compares it so a decision on a stale revision cannot apply.
    revision: Mapped[int] = mapped_column(server_default=text("1"))
    workflow_id: Mapped[str | None] = mapped_column(unique=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("study_id", "sha256"),
        UniqueConstraint("study_id", "id"),
        CheckConstraint("bytes > 0", name="bytes_positive"),
        # Lowercase hex only, so case variants cannot slip past the duplicate check.
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    kind: Mapped[DocumentKind] = mapped_column(DOCUMENT_KIND_ENUM)
    filename: Mapped[str]
    content_type: Mapped[str]
    bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    gcs_uri: Mapped[str] = mapped_column(unique=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created_at()


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (PrimaryKeyConstraint("document_id", "page_no"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    page_no: Mapped[int]
    gcs_uri: Mapped[str | None]
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------- extraction


class Extraction(Base):
    __tablename__ = "extractions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision"),
        UniqueConstraint("study_id", "id"),
        ForeignKeyConstraint(["study_id", "document_id"], ["documents.study_id", "documents.id"]),
        _confidence_check("overall_confidence"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    document_id: Mapped[uuid.UUID]
    revision: Mapped[int]
    method: Mapped[str]
    overall_confidence: Mapped[float | None] = mapped_column(REAL)
    needs_review: Mapped[bool] = mapped_column(server_default=FALSE)
    raw: Mapped[Any] = mapped_column(JSON_DOC)
    audit: Mapped[Any | None] = mapped_column(JSON_DOC)
    created_at: Mapped[datetime] = _created_at()


class ExtractionField(Base):
    __tablename__ = "extraction_fields"
    __table_args__ = (
        UniqueConstraint("extraction_id", "section", "field"),
        ForeignKeyConstraint(
            ["study_id", "extraction_id"], ["extractions.study_id", "extractions.id"]
        ),
        _confidence_check("confidence"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    extraction_id: Mapped[uuid.UUID]
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    section: Mapped[str]
    field: Mapped[str]
    value_text: Mapped[str | None]
    value_num: Mapped[Decimal | None] = mapped_column(Numeric)
    value_date: Mapped[date | None]
    confidence: Mapped[float | None] = mapped_column(REAL)
    source: Mapped[str]
    page: Mapped[int | None]
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------- rooms .. costs


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("study_id", "id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    revision: Mapped[int]
    name: Mapped[str]
    room_type: Mapped[str]
    floor: Mapped[str | None]
    source: Mapped[str]
    created_at: Mapped[datetime] = _created_at()


class PhotoRoom(Base):
    __tablename__ = "photo_rooms"
    __table_args__ = (
        PrimaryKeyConstraint("document_id", "revision"),
        ForeignKeyConstraint(["study_id", "room_id"], ["rooms.study_id", "rooms.id"]),
        ForeignKeyConstraint(["study_id", "document_id"], ["documents.study_id", "documents.id"]),
    )

    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    document_id: Mapped[uuid.UUID]
    room_id: Mapped[uuid.UUID]
    revision: Mapped[int]
    created_at: Mapped[datetime] = _created_at()


class Component(Base):
    __tablename__ = "components"
    __table_args__ = (
        UniqueConstraint("study_id", "id"),
        ForeignKeyConstraint(["study_id", "room_id"], ["rooms.study_id", "rooms.id"]),
        ForeignKeyConstraint(["study_id", "document_id"], ["documents.study_id", "documents.id"]),
        _confidence_check("confidence"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    revision: Mapped[int]
    document_id: Mapped[uuid.UUID | None]
    room_id: Mapped[uuid.UUID | None]
    label: Mapped[str]
    material: Mapped[str | None]
    attachment: Mapped[str | None]
    confidence: Mapped[float | None] = mapped_column(REAL)
    bbox: Mapped[Any | None] = mapped_column(JSON_DOC)
    raw: Mapped[Any | None] = mapped_column(JSON_DOC)
    created_at: Mapped[datetime] = _created_at()


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("study_id", "id"),
        ForeignKeyConstraint(
            ["study_id", "component_id"], ["components.study_id", "components.id"]
        ),
        _confidence_check("confidence"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    revision: Mapped[int]
    component_id: Mapped[uuid.UUID | None]
    category: Mapped[str]
    bucket: Mapped[Bucket] = mapped_column(BUCKET_ENUM)
    recovery_period: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    irs_section: Mapped[str | None]
    method: Mapped[ClassificationMethod] = mapped_column(CLASSIFICATION_METHOD_ENUM)
    confidence: Mapped[float | None] = mapped_column(REAL)
    needs_review: Mapped[bool] = mapped_column(server_default=FALSE)
    review_reason: Mapped[str | None]
    verified: Mapped[bool] = mapped_column(server_default=FALSE)
    created_at: Mapped[datetime] = _created_at()


class Takeoff(Base):
    __tablename__ = "takeoffs"
    __table_args__ = (
        UniqueConstraint("asset_id"),
        ForeignKeyConstraint(["study_id", "asset_id"], ["assets.study_id", "assets.id"]),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    asset_id: Mapped[uuid.UUID]
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    unit: Mapped[str]
    formula: Mapped[str | None]
    inputs: Mapped[Any | None] = mapped_column(JSON_DOC)
    created_at: Mapped[datetime] = _created_at()


class CostEstimate(Base):
    __tablename__ = "cost_estimates"
    __table_args__ = (
        UniqueConstraint("asset_id"),
        ForeignKeyConstraint(["study_id", "asset_id"], ["assets.study_id", "assets.id"]),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    asset_id: Mapped[uuid.UUID]
    unit_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    total: Mapped[Decimal | None] = mapped_column(MONEY)
    source_row: Mapped[str | None]
    location_factor: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    cost_year: Mapped[int | None]
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------- evidence


class CorpusVersion(Base):
    __tablename__ = "corpus_versions"
    __table_args__ = (
        # NULLS NOT DISTINCT: the reference corpus has study_id NULL and its versions must
        # still be unique (with the default NULLS DISTINCT they never collide).
        UniqueConstraint("corpus", "study_id", "version", postgresql_nulls_not_distinct=True),
        CheckConstraint("corpus IN ('reference', 'study')", name="corpus_valid"),
        CheckConstraint("(corpus = 'study') = (study_id IS NOT NULL)", name="study_scope"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    corpus: Mapped[str]
    study_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("studies.id"))
    version: Mapped[int]
    embedding_model: Mapped[str]
    published_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created_at()


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("corpus_version_id", "chunk_key"),
        Index(None, "corpus_version_id", "doc_id"),
        # HNSW index on embedding is deferred to S3 (S1_SPEC §2).
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    corpus_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpus_versions.id"))
    doc_id: Mapped[str]
    chunk_key: Mapped[str]
    page_start: Mapped[int | None]
    page_end: Mapped[int | None]
    section_path: Mapped[str | None]
    text: Mapped[str]
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    table_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = _created_at()


class EvidenceTable(Base):
    __tablename__ = "evidence_tables"
    __table_args__ = (UniqueConstraint("corpus_version_id", "doc_id", "page", "id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    corpus_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpus_versions.id"))
    doc_id: Mapped[str | None]
    page: Mapped[int | None]
    headers: Mapped[Any | None] = mapped_column(JSON_DOC)
    rows: Mapped[Any | None] = mapped_column(JSON_DOC)
    markdown: Mapped[str | None]
    created_at: Mapped[datetime] = _created_at()


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (
        ForeignKeyConstraint(["study_id", "asset_id"], ["assets.study_id", "assets.id"]),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    asset_id: Mapped[uuid.UUID]
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id"))
    excerpt: Mapped[str]
    verified_at: Mapped[datetime | None]
    verification_method: Mapped[str | None]
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------- review, runs, accounting


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("study_id", "stage", "revision"),
        UniqueConstraint("idempotency_key"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    stage: Mapped[Stage] = mapped_column(STAGE_ENUM)
    revision: Mapped[int]
    decision: Mapped[Decision] = mapped_column(DECISION_ENUM)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str | None]
    idempotency_key: Mapped[str]
    created_at: Mapped[datetime] = _created_at()


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"), index=True)
    entity_type: Mapped[str]
    entity_id: Mapped[uuid.UUID]
    revision: Mapped[int]
    before: Mapped[Any | None] = mapped_column(JSON_DOC)
    after: Mapped[Any] = mapped_column(JSON_DOC)
    reason: Mapped[str | None]
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("workflow_id", "run_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    workflow_id: Mapped[str]
    run_id: Mapped[str]
    workflow_version: Mapped[str]
    input_revision: Mapped[int]
    started_at: Mapped[datetime] = mapped_column(server_default=NOW)
    ended_at: Mapped[datetime | None]
    outcome: Mapped[str | None]
    created_at: Mapped[datetime] = _created_at()


class ActivityResult(Base):
    __tablename__ = "activity_results"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"))
    activity: Mapped[str]
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    status: Mapped[str]
    output_ref: Mapped[str | None]
    error: Mapped[str | None]
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"))
    activity: Mapped[str | None]
    provider: Mapped[str]
    model: Mapped[str]
    prompt_version: Mapped[str | None]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None]
    created_at: Mapped[datetime] = _created_at()


class ClassificationCacheEntry(Base):
    __tablename__ = "classification_cache"
    __table_args__ = (CheckConstraint("hits >= 0", name="hits_nonnegative"),)

    key: Mapped[str] = mapped_column(primary_key=True)
    result: Mapped[Any] = mapped_column(JSON_DOC)
    hits: Mapped[int] = mapped_column(server_default=ZERO)
    source: Mapped[str]
    rules_version: Mapped[str]
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (Index(None, "expires_at"),)

    # Stored scoped to the user ("<user_id>:<client key>"); see IdempotencyRepository.
    key: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    route: Mapped[str]
    request_hash: Mapped[str] = mapped_column(CHAR(64))
    status_code: Mapped[int | None]
    response: Mapped[Any | None] = mapped_column(JSON_DOC)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime]


class OutboxMessage(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        Index(None, "delivered_at", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOC)
    attempts: Mapped[int] = mapped_column(server_default=ZERO)
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=NOW)
    delivered_at: Mapped[datetime | None]
    last_error: Mapped[str | None]
    created_at: Mapped[datetime] = _created_at()


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    dataset: Mapped[str | None]
    split: Mapped[str | None]
    git_sha: Mapped[str | None]
    model: Mapped[str | None]
    prompt_version: Mapped[str | None]
    created_at: Mapped[datetime] = _created_at()


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = _uuid_pk()
    eval_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_runs.id"))
    metric: Mapped[str | None]
    value: Mapped[float | None] = mapped_column(Double)
    n: Mapped[int | None]
    stderr: Mapped[float | None] = mapped_column(Double)
    details: Mapped[Any | None] = mapped_column(JSON_DOC)
    created_at: Mapped[datetime] = _created_at()
