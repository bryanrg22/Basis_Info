"""``demo_requests``: the requests for a demo sent from the public site's "Book a demo" form
(docs/api/DEMO_REQUESTS.md, EVAL_REPORT section 95).

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-12

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

One row per request ``POST /v1/demo-requests`` stored: what the visitor typed (name, email
trimmed and lower-cased, company, role, the optional note and the page the form was on) and
what the server saw (the time, the client address the rate limit keys on, the user agent,
the API request id). ``notified_at`` and ``notify_error`` are the relay's record of the
notification email. ``(email, created_at)`` serves the duplicate check (the same email within
the window is answered with the first request's id); ``created_at`` serves the retention
purge; ``seq`` is the operator listing's keyset cursor.

Personal data, so not append-only: the relay updates the two notification columns, and
``python -m basis.retention purge-demo-requests`` deletes rows older than
``DEMO_REQUESTS_RETENTION_DAYS``. The outbox row that asks for the email carries only the
request's id, so the purge leaves nothing personal behind in ``outbox``.

Nothing is backfilled: nothing was recorded before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "demo_requests"
ROLES = ("owner_partner", "engineer", "analyst_staff", "other")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("page", sa.Text(), nullable=True),
        sa.Column("remote_addr", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notify_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN (" + ", ".join(f"'{r}'" for r in ROLES) + ")",
            name=op.f("ck_demo_requests_role_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_requests")),
        sa.UniqueConstraint("seq", name=op.f("uq_demo_requests_seq")),
    )
    op.create_index(op.f("ix_demo_requests_email_created_at"), TABLE, ["email", "created_at"])
    op.create_index(op.f("ix_demo_requests_created_at"), TABLE, ["created_at"])


def downgrade() -> None:
    op.drop_table(TABLE)
