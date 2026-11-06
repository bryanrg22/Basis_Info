"""``user_linked_accounts``: more sign-in accounts for one Basis user (docs/api/FIRMS.md,
"One person, one user").

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-06

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

A user is keyed by the Firebase uid they first signed in with, and ``users.firebase_uid``
never changes (``forbid_firebase_uid_change()``, migration 0001). When the same person
comes back with a new uid (their Firebase account was deleted and created again, or a
local Auth emulator lost its accounts), Basis used to create a second user with no firm;
a firm that re-invited them then listed the same email twice. A row here maps the new uid
to the existing user, so the person keeps their firms and their history. Rows are written
by the operator CLI (``basis.admin link-account``) or, where
``AUTH_LINK_VERIFIED_EMAIL`` is on (local stacks), at sign-in for a verified email that
matches a firm member (EVAL_REPORT section 93). Nothing is backfilled.

Other open pull requests also add migrations; whichever merges later is renumbered and
re-chained at merge.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_linked_accounts",
        sa.Column("firebase_uid", sa.Text(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("linked_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "linked_by IN ('sign_in', 'operator')",
            name=op.f("ck_user_linked_accounts_linked_by_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_linked_accounts_user_id_users")
        ),
        sa.PrimaryKeyConstraint("firebase_uid", name=op.f("pk_user_linked_accounts")),
    )
    op.create_index(op.f("ix_user_linked_accounts_user_id"), "user_linked_accounts", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_user_linked_accounts_user_id"), table_name="user_linked_accounts")
    op.drop_table("user_linked_accounts")
