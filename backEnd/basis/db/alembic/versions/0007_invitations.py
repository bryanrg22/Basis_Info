"""Invite-only firms: ``invitations``.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21

Hand-written to match ``basis.db.models`` (``alembic check`` proves it). A firm (an
``orgs`` row) is joined only by invitation: an owner, or the operator CLI, invites an
email with a role, and the invitee accepts after signing in with that verified email.

* ``email`` is stored normalized (``ck_invitations_email_normalized``: trimmed, lower
  case, not empty); ``role`` is an ``org_members`` role.
* Outcomes (``accepted``, ``declined``, ``revoked``) each record when and by whom, and at
  most one is set (``ck_invitations_one_outcome``). ``created_by`` is NULL for the CLI,
  and so is ``revoked_by`` when the CLI closes an expired invitation to re-invite.
* ``uq_invitations_org_id_email_open``: one open (unanswered) invitation per firm and
  email, a partial unique index. Expiry is time, which an index predicate cannot use, so
  re-inviting closes an expired one first (``InvitationRepository.create``).
* ``ix_invitations_email``: ``GET /v1/me`` finds the caller's invitations by email.

Nothing existing changes: users keep the orgs and memberships they have. Downgrade drops
the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPEN = "accepted_at IS NULL AND declined_at IS NULL AND revoked_at IS NULL"


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.UUID(), nullable=True),
        sa.Column("declined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declined_by", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "(accepted_at IS NULL) = (accepted_by IS NULL)",
            name=op.f("ck_invitations_accepted_by_whom"),
        ),
        sa.CheckConstraint(
            "(declined_at IS NULL) = (declined_by IS NULL)",
            name=op.f("ck_invitations_declined_by_whom"),
        ),
        sa.CheckConstraint(
            "email = lower(btrim(email)) AND email <> ''",
            name=op.f("ck_invitations_email_normalized"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_invitations_expires_after_created")
        ),
        sa.CheckConstraint(
            "num_nonnulls(accepted_at, declined_at, revoked_at) <= 1",
            name=op.f("ck_invitations_one_outcome"),
        ),
        sa.CheckConstraint(
            "revoked_by IS NULL OR revoked_at IS NOT NULL",
            name=op.f("ck_invitations_revoked_by_whom"),
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'engineer', 'viewer')", name=op.f("ck_invitations_role_valid")
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"], ["users.id"], name=op.f("fk_invitations_accepted_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_invitations_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["declined_by"], ["users.id"], name=op.f("fk_invitations_declined_by_users")
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name=op.f("fk_invitations_org_id_orgs")),
        sa.ForeignKeyConstraint(
            ["revoked_by"], ["users.id"], name=op.f("fk_invitations_revoked_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
    )
    op.create_index(op.f("ix_invitations_email"), "invitations", ["email"], unique=False)
    op.create_index(
        "uq_invitations_org_id_email_open",
        "invitations",
        ["org_id", "email"],
        unique=True,
        postgresql_where=sa.text(OPEN),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_invitations_org_id_email_open",
        table_name="invitations",
        postgresql_where=sa.text(OPEN),
    )
    op.drop_index(op.f("ix_invitations_email"), table_name="invitations")
    op.drop_table("invitations")
