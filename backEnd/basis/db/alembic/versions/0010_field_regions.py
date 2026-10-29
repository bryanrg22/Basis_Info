"""``extraction_fields.regions``: where each value is printed, for the review page to highlight.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-27

Hand-written to match ``basis.db.models`` (``alembic check`` proves it). A JSON list of
``{"page": n, "bbox": [x0, y0, x1, y1]}`` boxes as page fractions, origin top-left
(:mod:`basis.extraction.regions`). Existing rows get ``[]`` (their extraction kept no geometry:
the review page falls back to its text search for them).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extraction_fields",
        sa.Column(
            "regions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("extraction_fields", "regions")
