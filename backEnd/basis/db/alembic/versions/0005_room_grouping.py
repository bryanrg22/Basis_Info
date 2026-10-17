"""Room grouping by file name: ``rooms.group_key`` and ``studies.room_aliases``.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16

Hand-written to match ``basis.db.models`` (``alembic check`` proves it). Both columns are
additive (EVAL_REPORT section 26, item 1):

* ``rooms.group_key``: the canonical file-name room id a room groups
  (``basis.vision.rooms.name_hint`` after the study's aliases); NULL for a room keyed by the
  model's room identity (every room written before this migration).
* ``studies.room_aliases``: the engineer's ``{room id: room id}`` merges, default ``{}``.

Downgrade drops both.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rooms", sa.Column("group_key", sa.Text(), nullable=True))
    op.add_column(
        "studies",
        sa.Column(
            "room_aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("studies", "room_aliases")
    op.drop_column("rooms", "group_key")
