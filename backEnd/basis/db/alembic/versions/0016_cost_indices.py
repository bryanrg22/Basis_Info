"""``cost_indices`` and ``cost_index_runs``: the cost index store and its ledger.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-30

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

* ``cost_indices``: every cost index value the product has obtained, with its citation
  (COST_INDICES.md section 9): the RSMeans Historical Cost Index by year (``series``
  ``historical``, ``region`` ``US``) and the location factors by city (``series`` ``city``,
  ``region`` ``<state>:<zip prefixes>``), from the released 2020 books (``source``
  ``rsmeans_2020_book``), the firm's manual entries (``manual``), the RSMeans API or a
  public fallback. Append-only (the existing ``forbid_mutation()`` trigger): a revised value
  is a new ``version`` of the same ``(source, series, year, region)`` and the old row stays,
  so a completed study reproduces from the rows it cited. ``obtained_by`` is the user of a
  manual entry (``obtained_by_name`` their name or email), ``obtained_by_name`` alone the
  fetcher's name; ``document_sha256`` the fetched document's hash; ``run_id`` the run that
  added the row.
* ``cost_index_runs``: one append-only row per attempt to obtain values (a refresh run per
  source, a manual entry, a file import): what was asked for, what arrived, what failed,
  who triggered it. The ledger that says when a value arrived.

Nothing is seeded by the migration: ``python -m basis.costs.refresh seed`` (or the first
refresh run) loads the released 2020-book tables from ``basis/data/cost_indices`` with their
citations, idempotently, so the schema change carries no data decision.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNS = "cost_index_runs"
VALUES = "cost_indices"
# Appended to the append-only tables (tests/unit/db/test_models_metadata.py).
APPEND_ONLY_TABLES = (VALUES, RUNS)


def upgrade() -> None:
    op.create_table(
        RUNS,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("actor_name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows_added", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("years_wanted", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("years_added", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failures", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("document_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_type IN ('engineer', 'system')", name=op.f("ck_cost_index_runs_actor_valid")
        ),
        sa.CheckConstraint(
            "rows_added >= 0", name=op.f("ck_cost_index_runs_rows_added_nonnegative")
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'skipped', 'failed')", name=op.f("ck_cost_index_runs_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_cost_index_runs_actor_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_index_runs")),
    )
    op.create_index(op.f("ix_cost_index_runs_source_started_at"), RUNS, ["source", "started_at"])
    op.create_table(
        VALUES,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("series", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("citation", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("derived", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "obtained_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("obtained_by", sa.UUID(), nullable=True),
        sa.Column("obtained_by_name", sa.Text(), nullable=False),
        sa.Column("document_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "series IN ('historical', 'city')", name=op.f("ck_cost_indices_series_valid")
        ),
        sa.CheckConstraint("value > 0", name=op.f("ck_cost_indices_value_positive")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_cost_indices_version_positive")),
        sa.CheckConstraint("year BETWEEN 1900 AND 2200", name=op.f("ck_cost_indices_year_range")),
        sa.ForeignKeyConstraint(
            ["obtained_by"], ["users.id"], name=op.f("fk_cost_indices_obtained_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["cost_index_runs.id"], name=op.f("fk_cost_indices_run_id_cost_index_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_indices")),
        sa.UniqueConstraint(
            "source",
            "series",
            "year",
            "region",
            "version",
            name=op.f("uq_cost_indices_source_series_year_region_version"),
        ),
    )
    op.create_index(op.f("ix_cost_indices_series_year"), VALUES, ["series", "year"])
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
        )


def downgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.drop_index(op.f("ix_cost_indices_series_year"), table_name=VALUES)
    op.drop_table(VALUES)
    op.drop_index(op.f("ix_cost_index_runs_source_started_at"), table_name=RUNS)
    op.drop_table(RUNS)
