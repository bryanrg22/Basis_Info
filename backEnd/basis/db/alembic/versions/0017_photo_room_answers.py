"""``photo_room_answers``: the per-photo model's room answer, once per analysis.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-30

Hand-written to match ``basis.db.models`` (``alembic check`` proves it). ``analyze_photo``
wrote the photo's room type and floor only into its ``components`` rows' ``raw``, so the
rooms' vote (``reconcile_rooms``) could not hear a photo with no component, and a room of
such photos kept whatever the first photo analyzed said (EVAL_REPORT section 48). One row
per ``(document, revision analyzed)``: ``room_type``, ``room_confidence``, ``floor``,
``room_ordinal`` and the stored analysis' ``analysis_uri``. A revision that carries a photo
without re-analyzing it has no row; its answer is the latest row at or before it.

Nothing is backfilled: a photo analyzed before this migration has no row, and the vote reads
its answer from its component rows as before (a photo of those with no component did not
vote then either), so existing studies keep their rooms.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "photo_room_answers"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("room_type", sa.Text(), nullable=False),
        sa.Column("room_confidence", sa.REAL(), nullable=True),
        sa.Column("floor", sa.Integer(), nullable=True),
        sa.Column("room_ordinal", sa.Integer(), nullable=False),
        sa.Column("analysis_uri", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "room_confidence BETWEEN 0 AND 1",
            name=op.f("ck_photo_room_answers_room_confidence_range"),
        ),
        sa.ForeignKeyConstraint(
            ["study_id", "document_id"],
            ["documents.study_id", "documents.id"],
            name=op.f("fk_photo_room_answers_study_id_document_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_photo_room_answers_study_id_studies")
        ),
        sa.PrimaryKeyConstraint("document_id", "revision", name=op.f("pk_photo_room_answers")),
    )
    op.create_index(
        op.f("ix_photo_room_answers_study_id_revision"),
        TABLE,
        ["study_id", "revision"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_photo_room_answers_study_id_revision"), table_name=TABLE)
    op.drop_table(TABLE)
