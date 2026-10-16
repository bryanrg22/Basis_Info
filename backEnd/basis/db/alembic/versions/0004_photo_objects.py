"""Photo localization: ``photo_localizations`` and ``photo_objects`` (SAM 3 in the product).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

* ``photo_localizations``: one row per photo and revision with the outcome of the
  ``localize_photo`` activity (``done`` / ``failed`` / ``skipped``), what was prompted
  (``params``), the model version, predict seconds and cost. Keyed like ``photo_rooms``.
* ``photo_objects``: one row per instance: label, score, normalized box (four columns,
  checked to lie in the unit square and be ordered), an optional run-length mask (JSONB,
  ``basis.vision.localize.Rle``), the share of the image it covers, ``source`` (``model``
  or ``manual``), ``counted`` (the score clears the class's count threshold; manual rows
  always do), who added a manual row and who deleted any row (soft delete, so the audit
  trail stays). Tenancy by the composite ``(study_id, document_id)`` key as everywhere.

Downgrade drops both tables.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "photo_localizations",
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("instances", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("image_width", sa.Integer(), nullable=True),
        sa.Column("image_height", sa.Integer(), nullable=True),
        sa.Column("predict_seconds", sa.Double(), nullable=True),
        sa.Column("cost_usd", sa.Double(), nullable=True),
        sa.Column("carried_from_revision", sa.Integer(), nullable=True),
        _created_at(),
        sa.CheckConstraint(
            "instances >= 0", name=op.f("ck_photo_localizations_instances_nonnegative")
        ),
        sa.CheckConstraint(
            "status IN ('done', 'failed', 'skipped')",
            name=op.f("ck_photo_localizations_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["study_id", "document_id"],
            ["documents.study_id", "documents.id"],
            name=op.f("fk_photo_localizations_study_id_document_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_photo_localizations_study_id_studies")
        ),
        sa.PrimaryKeyConstraint("document_id", "revision", name=op.f("pk_photo_localizations")),
    )
    op.create_table(
        "photo_objects",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("phrase", sa.Text(), nullable=True),
        sa.Column("score", sa.REAL(), nullable=True),
        sa.Column("x1", sa.Double(), nullable=False),
        sa.Column("y1", sa.Double(), nullable=False),
        sa.Column("x2", sa.Double(), nullable=False),
        sa.Column("y2", sa.Double(), nullable=False),
        sa.Column("mask", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("area", sa.REAL(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("counted", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("derived_from", sa.UUID(), nullable=True),
        sa.Column("carried_from", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.UUID(), nullable=True),
        _created_at(),
        sa.CheckConstraint("area BETWEEN 0 AND 1", name=op.f("ck_photo_objects_area_range")),
        sa.CheckConstraint(
            "x1 >= 0 AND y1 >= 0 AND x2 <= 1 AND y2 <= 1 AND x1 <= x2 AND y1 <= y2",
            name=op.f("ck_photo_objects_box_normalized"),
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL) = (deleted_by IS NULL)",
            name=op.f("ck_photo_objects_deleted_by_whom"),
        ),
        sa.CheckConstraint(
            "source = 'manual' OR created_by IS NULL",
            name=op.f("ck_photo_objects_model_has_no_author"),
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 1", name=op.f("ck_photo_objects_score_range")
        ),
        sa.CheckConstraint(
            "source IN ('model', 'manual')", name=op.f("ck_photo_objects_source_valid")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_photo_objects_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"], ["users.id"], name=op.f("fk_photo_objects_deleted_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["study_id", "document_id"],
            ["documents.study_id", "documents.id"],
            name=op.f("fk_photo_objects_study_id_document_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_photo_objects_study_id_studies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photo_objects")),
        sa.UniqueConstraint("study_id", "id", name=op.f("uq_photo_objects_study_id_id")),
    )
    op.create_index(
        op.f("ix_photo_objects_document_id_revision"),
        "photo_objects",
        ["document_id", "revision"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_photo_objects_document_id_revision"), table_name="photo_objects")
    op.drop_table("photo_objects")
    op.drop_table("photo_localizations")
