"""``studies.intake``: the study's intake (price, acquisition costs, land, use, dates, location)
as one JSONB document.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-29

Hand-written to match ``basis.db.models`` (``alembic check`` proves it). NULL means no intake
yet (every existing study); the document is validated by ``basis.domain.intake.StudyIntake``
before it is written, so the database only checks that a non-null value is a JSON object.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "studies",
        sa.Column("intake", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_studies_intake_is_object"),
        "studies",
        "intake IS NULL OR jsonb_typeof(intake) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_studies_intake_is_object"), "studies", type_="check")
    op.drop_column("studies", "intake")
