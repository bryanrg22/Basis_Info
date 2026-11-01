"""``rate_limit_hits``: the sliding-window log of the Postgres-backed rate limiter.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-01

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

The in-memory limiter counts per API process, so on Cloud Run the published limit is
multiplied by the number of instances. :class:`basis.api.ratelimit_pg.PostgresRateLimiter`
(the default in staging and prod, ``RATE_LIMIT_STORE``) keeps the same sliding-window log
here, one row per accepted request, so every process sees one budget per caller. A key's
expired rows are deleted on its next request and a sweep removes rows older than an hour.
``UNLOGGED``: ephemeral state (a crash resets the budgets) and no WAL per request.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "rate_limit_hits"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_limit_hits")),
        prefixes=["UNLOGGED"],
    )
    op.create_index(op.f("ix_rate_limit_hits_key_created_at"), TABLE, ["key", "created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_rate_limit_hits_key_created_at"), table_name=TABLE)
    op.drop_table(TABLE)
