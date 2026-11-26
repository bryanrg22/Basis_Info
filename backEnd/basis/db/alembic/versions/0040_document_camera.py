"""``documents``: a photo's camera fields for measuring a facade on it (EVAL_REPORT 109 and 111,
docs/api/OVERRIDES.md section 4.1).

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-26

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

* ``documents.focal_length_mm``: EXIF ``FocalLength``, in (0, 2000] or NULL;
* ``documents.focal_length_35mm``: EXIF ``FocalLengthIn35mmFilm``, in (0, 5000] or NULL;
* ``documents.camera_model``: EXIF ``Make`` and ``Model``, 1 to 100 characters or NULL.

Read from the photo when its upload is confirmed (:mod:`basis.vision.camera`); nothing else of
the EXIF is kept. NULL for every existing document: nothing is backfilled (a facade on an older
photo asks the engineer for the lens).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("focal_length_mm", sa.Double(), nullable=True))
    op.add_column("documents", sa.Column("focal_length_35mm", sa.Double(), nullable=True))
    op.add_column("documents", sa.Column("camera_model", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_documents_focal_length_mm_range"),
        "documents",
        "focal_length_mm IS NULL OR (focal_length_mm > 0 AND focal_length_mm <= 2000)",
    )
    op.create_check_constraint(
        op.f("ck_documents_focal_length_35mm_range"),
        "documents",
        "focal_length_35mm IS NULL OR (focal_length_35mm > 0 AND focal_length_35mm <= 5000)",
    )
    op.create_check_constraint(
        op.f("ck_documents_camera_model_length"),
        "documents",
        "camera_model IS NULL OR char_length(camera_model) BETWEEN 1 AND 100",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_documents_camera_model_length"), "documents", type_="check")
    op.drop_constraint(op.f("ck_documents_focal_length_35mm_range"), "documents", type_="check")
    op.drop_constraint(op.f("ck_documents_focal_length_mm_range"), "documents", type_="check")
    op.drop_column("documents", "camera_model")
    op.drop_column("documents", "focal_length_35mm")
    op.drop_column("documents", "focal_length_mm")
