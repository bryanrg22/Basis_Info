"""``review_drafts``: an engineer's pending corrections at a review gate, kept on the server.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-01

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

Until this migration the corrections an engineer added at a gate lived only in the browser
until ``POST /v1/studies/{id}/decisions`` sent them: a reload, a closed tab or a switch of
study lost them. One row per ``(study, stage, user)`` now holds the pending ``comment`` and
``corrections`` as one JSONB document (``document``), the ``revision`` they were drafted
against (a correction names rows of that revision) and a ``version`` that is the row's entity
tag (``If-Match`` on ``PUT``/``DELETE /v1/studies/{id}/drafts/{stage}``, as on the intake).
``set_updated_at()`` maintains ``updated_at``. Not append-only: a draft is overwritten by every
save and deleted when its decision is submitted or the engineer discards it (a draft is work
in progress, not history; the decision and its corrections are the record).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "review_drafts"
UPDATED_AT_TABLES = (TABLE,)
REVIEW_STAGES = "('appraisal_review', 'rooms_review', 'components_review', 'assets_review')"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column(
            "stage",
            postgresql.ENUM(name="stage", create_type=False),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # ``stage::text``: comparing the label as text never resolves the enum value, so the
        # constraint is safe in the transaction that added ``components_review`` to the enum
        # (migration 0009; PostgreSQL refuses an unsafe use of a new enum value there).
        sa.CheckConstraint(
            f"stage::text IN {REVIEW_STAGES}", name=op.f("ck_review_drafts_stage_is_review")
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_review_drafts_revision_positive")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_review_drafts_version_positive")),
        sa.CheckConstraint(
            "jsonb_typeof(document) = 'object'", name=op.f("ck_review_drafts_document_is_object")
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_review_drafts_study_id_studies")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_review_drafts_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_drafts")),
        sa.UniqueConstraint(
            "study_id", "stage", "user_id", name=op.f("uq_review_drafts_study_id_stage_user_id")
        ),
    )
    for table in UPDATED_AT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    op.drop_table(TABLE)  # its trigger goes with it
