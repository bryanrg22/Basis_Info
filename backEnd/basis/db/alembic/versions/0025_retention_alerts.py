"""Client-data retention and operator alerts (docs/api/RETENTION_AND_ALERTS.md).

Revision ID: 0025
Revises: 0021 (locally; set to the merged head, 0024, when this lands after 0022-0024)
Create Date: 2026-11-01

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

* ``orgs.retention_days``: the firm's rule for client data, NULL (the default) meaning
  keep everything; a positive number of days after which a completed or archived study's
  uploaded files are purged from storage (``basis.retention``).
* ``studies.purged_at``: when the purge ran for the study (NULL: never). The export, the
  signed report, every database row and the timeline survive; only the objects go.
* ``alerts``: one row per operator alert the application raised (a failed study, a stale
  worker heartbeat), keyed so the same occurrence is never raised twice (``key`` is unique:
  the insert that loses is a no-op), with the outbox row that carries it to the sender.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("retention_days", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_orgs_retention_days_positive"),
        "orgs",
        "retention_days IS NULL OR retention_days >= 1",
    )
    op.add_column("studies", sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "alerts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=True),
        sa.Column("service", sa.Text(), nullable=True),
        sa.Column("outbox_id", sa.UUID(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('study_failed', 'heartbeat_stale')", name=op.f("ck_alerts_kind_valid")
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_alerts_study_id_studies")
        ),
        sa.ForeignKeyConstraint(
            ["outbox_id"], ["outbox.id"], name=op.f("fk_alerts_outbox_id_outbox")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        sa.UniqueConstraint("key", name=op.f("uq_alerts_key")),
    )
    op.create_index(op.f("ix_alerts_study_id"), "alerts", ["study_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_alerts_study_id"), table_name="alerts")
    op.drop_table("alerts")
    op.drop_column("studies", "purged_at")
    op.drop_constraint(op.f("ck_orgs_retention_days_positive"), "orgs", type_="check")
    op.drop_column("orgs", "retention_days")
