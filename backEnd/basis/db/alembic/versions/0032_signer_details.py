"""Signer details: each engineer's own name, credentials and license (EVAL_REPORT 91).

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-05

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

Revises 0031 (``user_linked_accounts``, #157); nothing here depends on it.

* ``signer_details``: one row per user (``user_id`` primary key), written only by that user
  (``PUT /v1/me/signer``), ``version`` its entity tag, ``set_updated_at()``. A license number
  comes with its state and the state is a two-letter code (the API checks the code is a
  licensing jurisdiction). Nothing is backfilled: neither the firm profile's engineer (typed by
  an owner, tied to no user) nor a past signature's typed engineer (attested for that report,
  not saved by the user as their standing details) is a user's own saved details.
* ``report_signatures.signer_details_version``: the version of the signer's saved details a
  signature printed; NULL for details typed at signing and for every existing signature.
* ``report_signatures.letterhead``: the firm profile's name, address, phone and logo sha256 as
  signed, so the state can show the signed letterhead next to the current one; NULL for every
  existing signature (the values at signing are not known; the version still is).

Adding nullable columns fires no trigger, so the append-only ``report_signatures`` rows are
not rewritten.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "signer_details"
UPDATED_AT_TABLES = (TABLE,)
SIGNATURES = "report_signatures"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("license_number", sa.Text(), nullable=True),
        sa.Column("license_state", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_signer_details_version_positive")),
        sa.CheckConstraint(
            "(license_number IS NULL) = (license_state IS NULL)",
            name=op.f("ck_signer_details_license_pair"),
        ),
        sa.CheckConstraint(
            "license_state ~ '^[A-Z]{2}$'", name=op.f("ck_signer_details_license_state_code")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_signer_details_user_id_users")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_signer_details")),
    )
    for table in UPDATED_AT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    op.add_column(SIGNATURES, sa.Column("signer_details_version", sa.Integer(), nullable=True))
    op.add_column(
        SIGNATURES,
        sa.Column("letterhead", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_report_signatures_letterhead_is_object"),
        SIGNATURES,
        "letterhead IS NULL OR jsonb_typeof(letterhead) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_report_signatures_letterhead_is_object"), SIGNATURES, type_="check")
    op.drop_column(SIGNATURES, "letterhead")
    op.drop_column(SIGNATURES, "signer_details_version")
    op.drop_table(TABLE)  # its trigger goes with it
