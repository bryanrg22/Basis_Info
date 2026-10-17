"""A failed stage: ``study_status`` gains ``failed``; ``studies.failure_reason``.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16

Hand-written to match ``basis.db.models`` (``alembic check`` proves it). EVAL_REPORT
section 27, item 3: when a stage fails (the appraisal extraction, assets, export), the
workflow records ``status = failed`` and the reason and waits for ``POST .../retry``.

``ALTER TYPE ... ADD VALUE`` is allowed in a transaction on PostgreSQL 12+ (the value is
usable once the transaction commits). PostgreSQL cannot drop an enum value, so the
downgrade moves failed studies back to ``processing`` and recreates the type without it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS = ("draft", "processing", "waiting_review", "stalled", "completed", "archived")


def upgrade() -> None:
    op.execute("ALTER TYPE study_status ADD VALUE IF NOT EXISTS 'failed'")
    op.add_column("studies", sa.Column("failure_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("studies", "failure_reason")
    op.execute("UPDATE studies SET status = 'processing' WHERE status = 'failed'")
    labels = ", ".join(f"'{label}'" for label in PREVIOUS)
    op.execute("ALTER TYPE study_status RENAME TO study_status_0006")
    op.execute(f"CREATE TYPE study_status AS ENUM ({labels})")
    op.execute("ALTER TABLE studies ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE studies ALTER COLUMN status TYPE study_status USING status::text::study_status"
    )
    op.execute("ALTER TABLE studies ALTER COLUMN status SET DEFAULT 'draft'")
    op.execute("DROP TYPE study_status_0006")
