"""Invitation emails: ``invitations.email_queued_at``, ``emailed_at``, ``email_error``.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-01

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

An invitation is now emailed through the outbox (docs/api/FIRMS.md): the API or the
operator CLI enqueues the message in the transaction that creates the invitation and sets
``email_queued_at``; the relay renders and sends it and records ``emailed_at`` (success) or
``email_error`` (the last failure; cleared when a later attempt succeeds). All three are
nullable and nothing is backfilled: a row with none set was created before emails existed,
which the API reports as ``delivery: not_sent``, as it always did.

Numbering: written as 0024 on 0021 while 0022 (user activity), 0023 (rate-limit hits) and
0024 (review drafts) were open on other branches; renumbered 0025 on 0024 when those merged.

Downgrade drops the three columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "invitations"
COLUMNS = ("email_queued_at", "emailed_at", "email_error")


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("email_queued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("email_error", sa.Text(), nullable=True))


def downgrade() -> None:
    for name in reversed(COLUMNS):
        op.drop_column(TABLE, name)
