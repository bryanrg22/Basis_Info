"""``studies.renovations``: the study's renovation projects (docs/api/RENOVATIONS.md,
docs/review_2026-10-15/RENOVATIONS.md, EVAL_REPORT section 102).

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-23

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

Numbered 0039 because open branches claimed 0036 to 0038 (the property capture branch has
0036, its two follow-ups 0037 and 0038). It was written on 0035, the head of ``main`` then,
and is re-pointed to 0038 now that those revisions precede it (docs/DEVELOPING.md section 7,
"A new migration").

* ``studies.renovations``: the document (``basis.domain.renovations.StudyRenovations`` as
  JSONB), NULL for "none".
* ``studies.renovations_version``: its version, the entity tag ``PUT
  /v1/studies/{id}/renovations`` compares (``If-Match``), 0 while there is none.

NULL and 0 for every existing study: nothing is backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "studies",
        sa.Column("renovations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "studies",
        sa.Column("renovations_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_studies_renovations_is_object"),
        "studies",
        "renovations IS NULL OR jsonb_typeof(renovations) = 'object'",
    )
    op.create_check_constraint(
        op.f("ck_studies_renovations_version_not_negative"),
        "studies",
        "renovations_version >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_studies_renovations_version_not_negative"), "studies", type_="check"
    )
    op.drop_constraint(op.f("ck_studies_renovations_is_object"), "studies", type_="check")
    op.drop_column("studies", "renovations_version")
    op.drop_column("studies", "renovations")
