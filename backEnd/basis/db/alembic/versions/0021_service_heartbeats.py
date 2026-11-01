"""``service_heartbeats``: the latest heartbeat of each worker and relay process.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-31

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

``GET /v1/ops/health`` (docs/api/OPS_HEALTH.md) runs in the API, which cannot see the
worker's effective runtime (its models, provider routes, warm-up) or whether the relay is
alive: each is a separate process with its own settings, and in staging only the worker
holds provider credentials. Each worker and relay process upserts one row here every
``OPS_HEARTBEAT_SECONDS``, keyed by a random per-process id, with its lifecycle state and a
sanitized report. Not append-only: a row is the latest heartbeat, updated in place, and a
process prunes its service's rows older than a day.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "service_heartbeats"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("build_sha", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "service IN ('worker', 'relay')", name=op.f("ck_service_heartbeats_service_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_heartbeats")),
    )
    op.create_index(
        op.f("ix_service_heartbeats_service_heartbeat_at"), TABLE, ["service", "heartbeat_at"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_service_heartbeats_service_heartbeat_at"), table_name=TABLE)
    op.drop_table(TABLE)
