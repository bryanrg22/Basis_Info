"""Manual objects outlined by SAM 3: ``object_prompts`` and where a manual mask came from.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-22

Hand-written to match ``basis.db.models`` (``alembic check`` proves it). An engineer's box
at the rooms review that matches no stored instance is sent to SAM 3 with the label's
phrases (``POST /v1/studies/{id}/photos/{document_id}/objects``):

* ``object_prompts``: one row per such call, keyed to the photo by the composite
  ``(study_id, document_id)`` like ``photo_localizations``: label, ``params`` (phrases,
  box, thresholds), ``status`` (``done``: the localizer answered; ``failed``: it did not,
  ``reason`` says why), instances returned, the kept instance's score, model version,
  predict seconds and cost, and the engineer who drew the box.
* ``photo_objects.mask_source``: where a manual row's outline came from (``candidate``,
  ``sam_box_prompt`` or ``none``; NULL on model rows, ``ck_photo_objects_mask_source_valid``
  and ``ck_photo_objects_manual_has_mask_source``), ``mask_reason`` (why a manual row has
  no outline) and ``prompt_id`` (the call that outlined it).

Existing manual rows are backfilled: a row derived from a candidate took its mask
(``candidate``), any other was a bare box (``none``). Downgrade drops the three columns and
the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "object_prompts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("returned", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("kept_score", sa.REAL(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("predict_seconds", sa.Double(), nullable=True),
        sa.Column("cost_usd", sa.Double(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("returned >= 0", name=op.f("ck_object_prompts_returned_nonnegative")),
        sa.CheckConstraint(
            "status IN ('done', 'failed')", name=op.f("ck_object_prompts_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_object_prompts_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["study_id", "document_id"],
            ["documents.study_id", "documents.id"],
            name=op.f("fk_object_prompts_study_id_document_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_object_prompts_study_id_studies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_prompts")),
    )
    op.create_index(
        op.f("ix_object_prompts_document_id_revision"),
        "object_prompts",
        ["document_id", "revision"],
        unique=False,
    )
    op.add_column("photo_objects", sa.Column("mask_source", sa.Text(), nullable=True))
    op.add_column("photo_objects", sa.Column("mask_reason", sa.Text(), nullable=True))
    op.add_column("photo_objects", sa.Column("prompt_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_photo_objects_prompt_id_object_prompts"),
        "photo_objects",
        "object_prompts",
        ["prompt_id"],
        ["id"],
    )
    op.execute(
        "UPDATE photo_objects SET mask_source = CASE WHEN derived_from IS NOT NULL "
        "THEN 'candidate' ELSE 'none' END WHERE source = 'manual'"
    )
    op.create_check_constraint(
        op.f("ck_photo_objects_mask_source_valid"),
        "photo_objects",
        "mask_source IS NULL OR mask_source IN ('candidate', 'sam_box_prompt', 'none')",
    )
    op.create_check_constraint(
        op.f("ck_photo_objects_manual_has_mask_source"),
        "photo_objects",
        "(source = 'manual') = (mask_source IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_photo_objects_manual_has_mask_source"), "photo_objects", type_="check"
    )
    op.drop_constraint(op.f("ck_photo_objects_mask_source_valid"), "photo_objects", type_="check")
    op.drop_constraint(
        op.f("fk_photo_objects_prompt_id_object_prompts"), "photo_objects", type_="foreignkey"
    )
    op.drop_column("photo_objects", "prompt_id")
    op.drop_column("photo_objects", "mask_reason")
    op.drop_column("photo_objects", "mask_source")
    op.drop_index(op.f("ix_object_prompts_document_id_revision"), table_name="object_prompts")
    op.drop_table("object_prompts")
