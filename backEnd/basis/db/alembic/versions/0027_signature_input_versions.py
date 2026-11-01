"""``report_signatures``: the versions of the inputs a report was signed with.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-01

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

A signed report is rendered from the firm's profile and the study's report answers as they
were at signing; both stay writable afterwards (the firm's profile serves every study), and
the signed PDF never changes. ``GET /v1/studies/{id}/report/state`` says when either input
changed after signing by comparing the current versions with the two kept here
(EVAL_REPORT section 62). NULL on signatures recorded before this migration: for those the
state answers ``changed_since_signing: null`` (unknown) rather than guessing from timestamps,
which two transactions can order differently from their writes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "report_signatures"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("firm_profile_version", sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("report_answers_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "report_answers_version")
    op.drop_column(TABLE, "firm_profile_version")
