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

From migration ``0003_chunks_hnsw``:

* ``forbid_published_corpus_write()``: ``BEFORE INSERT OR UPDATE`` row trigger on
  ``chunks`` and ``evidence_tables`` that raises SQLSTATE ``23001`` when the row's corpus
  version is published, and ``forbid_published_version_update()`` on ``corpus_versions``
  once ``published_at`` is set. A published corpus version is immutable, so a stored
  citation (a chunk id) always means the same text.
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
    OutboxStatus,
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
# Tables whose rows cannot be inserted or updated once their corpus version is published.
PUBLISHED_IMMUTABLE_TABLES: Final = ("chunks", "evidence_tables")


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
OUTBOX_STATUS_ENUM: Final = _pg_enum(OutboxStatus, "outbox_status")  # migration 0002

PG_ENUMS: Final = (
    STUDY_STATUS_ENUM,
    STAGE_ENUM,
    DOCUMENT_KIND_ENUM,
    DECISION_ENUM,
    CLASSIFICATION_METHOD_ENUM,
    BUCKET_ENUM,
    OUTBOX_STATUS_ENUM,
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


# The open (unanswered) invitations: at most one per firm and email. An expired invitation
# is still open here (an index predicate cannot use now()); re-inviting closes it first.
INVITATION_OPEN: Final = "accepted_at IS NULL AND declined_at IS NULL AND revoked_at IS NULL"


class Invitation(Base):
    """An invitation to join a firm (migration ``0007_invitations``).

    Bound to an email, not a link: the invitee sees it after signing in with that verified
    email (``GET /v1/me``) and accepts it in the app. ``email`` is stored normalized
    (trimmed, lower case). Each outcome records when and by whom (a revocation by the CLI
    has no ``revoked_by``); at most one outcome is set. The status (pending, accepted,
    declined, revoked, expired) is derived (``basis.db.repositories.invitation``).
    ``created_by`` is NULL for invitations issued by the operator CLI
    (``python -m basis.admin``).
    """

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'engineer', 'viewer')", name="role_valid"),
        CheckConstraint("email = lower(btrim(email)) AND email <> ''", name="email_normalized"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        CheckConstraint("(accepted_at IS NULL) = (accepted_by IS NULL)", name="accepted_by_whom"),
        CheckConstraint("(declined_at IS NULL) = (declined_by IS NULL)", name="declined_by_whom"),
        # NULL revoked_by on a revoked row: closed by the operator CLI (re-inviting).
        CheckConstraint("revoked_by IS NULL OR revoked_at IS NOT NULL", name="revoked_by_whom"),
        CheckConstraint(
            "num_nonnulls(accepted_at, declined_at, revoked_at) <= 1", name="one_outcome"
        ),
        Index(
            "uq_invitations_org_id_email_open",
            "org_id",
            "email",
            unique=True,
            postgresql_where=text(INVITATION_OPEN),
        ),
        # GET /v1/me looks invitations up by the caller's email.
        Index(None, "email"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    email: Mapped[str]
    role: Mapped[str]
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None]
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    declined_at: Mapped[datetime | None]
    declined_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


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
    # The engineer's room aliases: ``{file-name room id: room id}`` (``basis.vision.rooms``).
    # Set at creation and extended by a rooms-review merge; read by ``analyze_photo``.
    room_aliases: Mapped[dict[str, str]] = mapped_column(
        JSON_DOC, server_default=text("'{}'::jsonb"), default=dict
    )
    # Why the study's current stage failed, while ``status`` is ``failed`` (record_failure).
    failure_reason: Mapped[str | None]
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
    # The canonical file-name room id the room groups (``basis.vision.rooms.name_hint``
    # after the study's aliases); ``None`` for a room keyed by the model's room identity.
    group_key: Mapped[str | None]
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


# Migration 0004: SAM 3 localization of the countable objects in each photo.
LOCALIZATION_STATUSES: Final = ("done", "failed", "skipped")
OBJECT_SOURCES: Final = ("model", "manual")


class PhotoLocalization(Base):
    """One photo's localization outcome per revision (``basis.activities.localize_photo``).

    ``status``: ``done`` (the localizer answered; ``instances`` were written, possibly
    none), ``failed`` (it did not, after retries; ``reason`` says why) or ``skipped``
    (no localizer configured, no budget, or nothing to prompt). Rows of a decided revision
    are copied, not changed, into the next one (``carried_from_revision``).
    """

    __tablename__ = "photo_localizations"
    __table_args__ = (
        PrimaryKeyConstraint("document_id", "revision"),
        ForeignKeyConstraint(["study_id", "document_id"], ["documents.study_id", "documents.id"]),
        CheckConstraint("status IN ('done', 'failed', 'skipped')", name="status_valid"),
        CheckConstraint("instances >= 0", name="instances_nonnegative"),
    )

    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    document_id: Mapped[uuid.UUID]
    revision: Mapped[int]
    status: Mapped[str]
    reason: Mapped[str | None]
    instances: Mapped[int] = mapped_column(server_default=ZERO)
    model_version: Mapped[str | None]
    # Prompted labels and phrases, the threshold sent and the per-class count thresholds.
    params: Mapped[Any | None] = mapped_column(JSON_DOC)
    image_width: Mapped[int | None]
    image_height: Mapped[int | None]
    predict_seconds: Mapped[float | None] = mapped_column(Double)
    cost_usd: Mapped[float | None] = mapped_column(Double)
    carried_from_revision: Mapped[int | None]
    created_at: Mapped[datetime] = _created_at()


class PhotoObject(Base):
    """One instance of a component in a photo: a normalized box, an optional run-length
    mask (``basis.vision.localize.Rle``: row-major, zeros first) and where it came from.

    ``source="model"`` rows come from SAM 3; ``counted`` says whether the score clears
    the class's count threshold (lower-scoring candidates are kept so an engineer's tap can
    select one). ``source="manual"`` rows are an engineer's selection (``created_by``),
    always counted; ``derived_from`` names the candidate a tap or box resolved to.
    Deleting is a soft delete (``deleted_at``/``deleted_by``) so the audit trail stays.

    ``mask_source`` says where a manual row's outline came from (NULL on model rows):
    ``candidate`` (a stored candidate promoted), ``sam_box_prompt`` (SAM 3 prompted with the
    engineer's box, ``prompt_id`` names the call) or ``none`` (the box alone; ``mask_reason``
    says why: nothing matched, the call failed or timed out, no localizer).
    """

    __tablename__ = "photo_objects"
    __table_args__ = (
        UniqueConstraint("study_id", "id"),
        ForeignKeyConstraint(["study_id", "document_id"], ["documents.study_id", "documents.id"]),
        Index(None, "document_id", "revision"),
        CheckConstraint("source IN ('model', 'manual')", name="source_valid"),
        CheckConstraint(
            "x1 >= 0 AND y1 >= 0 AND x2 <= 1 AND y2 <= 1 AND x1 <= x2 AND y1 <= y2",
            name="box_normalized",
        ),
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 1", name="score_range"),
        CheckConstraint("area BETWEEN 0 AND 1", name="area_range"),
        CheckConstraint("source = 'manual' OR created_by IS NULL", name="model_has_no_author"),
        CheckConstraint("(deleted_at IS NULL) = (deleted_by IS NULL)", name="deleted_by_whom"),
        CheckConstraint(
            "mask_source IS NULL OR mask_source IN ('candidate', 'sam_box_prompt', 'none')",
            name="mask_source_valid",
        ),
        CheckConstraint(
            "(source = 'manual') = (mask_source IS NOT NULL)", name="manual_has_mask_source"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    document_id: Mapped[uuid.UUID]
    revision: Mapped[int]
    label: Mapped[str]
    phrase: Mapped[str | None]
    score: Mapped[float | None] = mapped_column(REAL)
    x1: Mapped[float] = mapped_column(Double)
    y1: Mapped[float] = mapped_column(Double)
    x2: Mapped[float] = mapped_column(Double)
    y2: Mapped[float] = mapped_column(Double)
    mask: Mapped[Any | None] = mapped_column(JSON_DOC)
    area: Mapped[float] = mapped_column(REAL)
    source: Mapped[str]
    counted: Mapped[bool] = mapped_column(server_default=text("true"))
    derived_from: Mapped[uuid.UUID | None]
    carried_from: Mapped[uuid.UUID | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None]
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    mask_source: Mapped[str | None]
    mask_reason: Mapped[str | None]
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("object_prompts.id"))
    created_at: Mapped[datetime] = _created_at()


class ObjectPrompt(Base):
    """One SAM 3 call prompted with an engineer's box (``POST .../objects``): the label,
    what was sent (``params``: phrases, box, thresholds), whether the localizer answered
    (``done``) or not (``failed``, ``reason``), how many instances it returned and the
    score of the one kept, the model version, predict seconds and cost, as
    ``photo_localizations`` records a photo's call. Written as soon as the call returns,
    in its own transaction, so a billed call is recorded even when the add that asked
    for it is then refused; the object row it outlined names it (``prompt_id``).
    """

    __tablename__ = "object_prompts"
    __table_args__ = (
        ForeignKeyConstraint(["study_id", "document_id"], ["documents.study_id", "documents.id"]),
        Index(None, "document_id", "revision"),
        CheckConstraint("status IN ('done', 'failed')", name="status_valid"),
        CheckConstraint("returned >= 0", name="returned_nonnegative"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"))
    document_id: Mapped[uuid.UUID]
    revision: Mapped[int]
    label: Mapped[str]
    params: Mapped[Any] = mapped_column(JSON_DOC)
    status: Mapped[str]
    reason: Mapped[str | None]
    returned: Mapped[int] = mapped_column(server_default=ZERO)
    kept_score: Mapped[float | None] = mapped_column(REAL)
    model_version: Mapped[str | None]
    predict_seconds: Mapped[float | None] = mapped_column(Double)
    cost_usd: Mapped[float | None] = mapped_column(Double)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
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
        # Migration 0003: approximate nearest neighbour search by cosine distance.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
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
    """A message to deliver after the transaction that wrote it commits.

    ``status`` (migration ``0002_outbox_status``) replaces ``delivered_at IS NULL`` as the
    claim predicate so dead rows stop being polled; the relay's claim query is served by
    the ``(status, next_attempt_at)`` index. ``delivered_at`` is set exactly when the row
    is ``delivered`` (``ck_outbox_delivered_at_matches_status``).
    """

    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint(
            "(status = 'delivered') = (delivered_at IS NOT NULL)",
            name="delivered_at_matches_status",
        ),
        Index(None, "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOC)
    status: Mapped[OutboxStatus] = mapped_column(
        OUTBOX_STATUS_ENUM, server_default=text("'pending'")
    )
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
