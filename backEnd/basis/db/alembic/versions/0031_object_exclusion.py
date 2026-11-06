"""A photo object an engineer marked Not counted: ``photo_objects.excluded_at``,
``excluded_by`` and ``exclusion_reason`` (EVAL_REPORT 90, docs/api/OVERRIDES.md).

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-05

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

``PATCH /v1/studies/{id}/photos/{document_id}/objects/{object_id}`` with ``counted: false``
keeps the row (a model detection is the photo's evidence; nothing is deleted) and records
who excluded it, when and why; ``counted`` is false while it is excluded, so every reader of
counted instances leaves it out unchanged. Three checks keep the record whole: the time and
the person go together (``ck_photo_objects_excluded_by_whom``), so do the time and the reason
(``ck_photo_objects_exclusion_has_reason``), and an excluded row is never counted
(``ck_photo_objects_excluded_not_counted``). Nothing is backfilled: no row was excluded
before.

The downgrade refuses while any row is excluded: dropping the columns would leave the row
uncounted with no record of who or why, indistinguishable from a model candidate under its
count threshold.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "photo_objects"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("excluded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("excluded_by", sa.UUID(), nullable=True))
    op.add_column(TABLE, sa.Column("exclusion_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_photo_objects_excluded_by_users"), TABLE, "users", ["excluded_by"], ["id"]
    )
    op.create_check_constraint(
        op.f("ck_photo_objects_excluded_by_whom"),
        TABLE,
        "(excluded_at IS NULL) = (excluded_by IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_photo_objects_exclusion_has_reason"),
        TABLE,
        "(excluded_at IS NULL) = (exclusion_reason IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_photo_objects_excluded_not_counted"),
        TABLE,
        "excluded_at IS NULL OR NOT counted",
    )


def downgrade() -> None:
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {TABLE} WHERE excluded_at IS NOT NULL) THEN "
        "RAISE EXCEPTION 'photo_objects holds objects an engineer marked Not counted; the "
        "downgrade would lose who excluded them and why'; END IF; END $$"
    )
    op.drop_constraint(op.f("ck_photo_objects_excluded_not_counted"), TABLE, type_="check")
    op.drop_constraint(op.f("ck_photo_objects_exclusion_has_reason"), TABLE, type_="check")
    op.drop_constraint(op.f("ck_photo_objects_excluded_by_whom"), TABLE, type_="check")
    op.drop_constraint(op.f("fk_photo_objects_excluded_by_users"), TABLE, type_="foreignkey")
    op.drop_column(TABLE, "exclusion_reason")
    op.drop_column(TABLE, "excluded_by")
    op.drop_column(TABLE, "excluded_at")
