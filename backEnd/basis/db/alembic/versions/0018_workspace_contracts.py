"""The engineer workspace's contracts: the recorded cost trail, the intake version, and
cost rows that cannot be rewritten.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-31

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

* ``cost_estimates.trail``: how the row's dollars were reached, recorded when it is priced
  (``basis.domain.cost_trail``): the quantity and its inputs, the base unit cost and its
  row, the year factor with the index values and their store versions, the location factor
  with its row, the rounding and the result. NULL for a row priced before this migration:
  nothing is backfilled, because the factors those rows used were not recorded and are not
  reconstructed from rounded amounts.
* ``studies.intake_version``: incremented by every intake write, the intake's entity tag
  (``If-Match`` on ``PUT /v1/studies/{id}/intake``). 0 while the study has no intake; a
  study that already has one starts at 1.
* ``takeoffs`` and ``cost_estimates``: a ``BEFORE UPDATE`` trigger with the existing
  ``forbid_mutation()`` function. No code updates them (a reprice writes a new revision);
  the trigger makes the recorded trail immutable. DELETE is left alone (a purge removes a
  study's rows).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NO_UPDATE_TABLES = ("takeoffs", "cost_estimates")


def upgrade() -> None:
    op.add_column(
        "cost_estimates",
        sa.Column("trail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "studies",
        sa.Column("intake_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.execute("UPDATE studies SET intake_version = 1 WHERE intake IS NOT NULL")
    op.create_check_constraint(
        op.f("ck_studies_intake_version_not_negative"), "studies", "intake_version >= 0"
    )
    for table in NO_UPDATE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
        )


def downgrade() -> None:
    for table in NO_UPDATE_TABLES:
        op.execute(f"DROP TRIGGER {table}_no_update ON {table}")
    op.drop_constraint(op.f("ck_studies_intake_version_not_negative"), "studies", type_="check")
    op.drop_column("studies", "intake_version")
    op.drop_column("cost_estimates", "trail")
