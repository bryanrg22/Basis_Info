"""Firm conventions: ``classification_cache`` scoped by firm; ``assets.convention``.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-29

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).
docs/review_2026-10-15/CLASSIFICATION_CONVENTIONS.md section 5.

* ``classification_cache`` is dropped and created again with ``org_id`` (``NOT NULL``,
  foreign key ``orgs.id``; the primary key becomes ``(org_id, key)``), the approval that
  seeded the row (``study_id``, ``reviewer_id``, ``approved_at``), what the rules table says
  for the same key (``rule_key``, ``agrees_with_rules``, both NULL when there is no rule row)
  and ``approvals`` (default 1). Dropped, not altered: the table is empty on every
  environment (it had no writer; verified on the local stack on 2026-10-29, staging is
  parked), and its old five-part keys can never match the six-part context keys anyway. A
  row present at upgrade time would be lost: it has no firm to belong to. The
  ``set_updated_at()`` trigger goes with the table and is created again.
* ``assets.convention``: the convention an asset was classified from, as of its
  classification (key, study, reviewer, date, approvals, agreement with the rules); NULL
  unless ``method = 'cache'``. NULL for every existing asset.

The downgrade drops ``assets.convention`` and recreates ``0001``'s ``classification_cache``
(empty: a firm-scoped row has no place in the unscoped table).
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "classification_cache"


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            name,
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        )
        for name in ("created_at", "updated_at")
    ]


def _updated_at_trigger() -> None:
    op.execute(
        f"CREATE TRIGGER {TABLE}_set_updated_at BEFORE UPDATE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def upgrade() -> None:
    op.drop_table(TABLE)  # its set_updated_at trigger goes with it
    op.create_table(
        TABLE,
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("hits", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("reviewer_id", sa.UUID(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rule_key", sa.Text(), nullable=True),
        sa.Column("agrees_with_rules", sa.Boolean(), nullable=True),
        sa.Column("approvals", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("hits >= 0", name=op.f("ck_classification_cache_hits_nonnegative")),
        sa.CheckConstraint(
            "approvals >= 1", name=op.f("ck_classification_cache_approvals_positive")
        ),
        sa.CheckConstraint(
            "(rule_key IS NULL) = (agrees_with_rules IS NULL)",
            name=op.f("ck_classification_cache_rule_agreement"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["orgs.id"], name=op.f("fk_classification_cache_org_id_orgs")
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_classification_cache_study_id_studies")
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"], name=op.f("fk_classification_cache_reviewer_id_users")
        ),
        sa.PrimaryKeyConstraint("org_id", "key", name=op.f("pk_classification_cache")),
    )
    _updated_at_trigger()
    op.add_column(
        "assets",
        sa.Column("convention", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_assets_convention_from_cache"),
        "assets",
        "convention IS NULL OR (method = 'cache' AND jsonb_typeof(convention) = 'object')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_assets_convention_from_cache"), "assets", type_="check")
    op.drop_column("assets", "convention")
    op.drop_table(TABLE)
    op.create_table(
        TABLE,
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("hits", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("rules_version", sa.Text(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("hits >= 0", name=op.f("ck_classification_cache_hits_nonnegative")),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_classification_cache")),
    )
    _updated_at_trigger()
