"""Property pictures: history, imagery dates, leaf-off, street level (EVAL_REPORT 99).

* ``property_captures.notes``: the parts that did not work while the capture went on
  (``[{"part", "code", "message"}]``), default ``[]``.
* ``capture_views``: ``imagery_on`` (the imagery date as a date), ``imagery_date_note`` (how
  it was read or why not), ``leaf`` (``leaf-off``, ``leaf-on``, ``unknown``),
  ``green_share`` (0 to 1), ``camera_lat`` / ``camera_lon`` (both or neither: where a
  street-level camera stood) and ``pano_id``. All nullable: the views taken before keep
  NULL, which reads as "not recorded".

Adding columns is DDL, which the table's ``forbid_mutation()`` UPDATE trigger does not
concern. Nothing is backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAPTURES = "property_captures"
VIEWS = "capture_views"


def upgrade() -> None:
    op.add_column(
        CAPTURES,
        sa.Column(
            "notes",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(VIEWS, sa.Column("imagery_on", sa.Date(), nullable=True))
    op.add_column(VIEWS, sa.Column("imagery_date_note", sa.Text(), nullable=True))
    op.add_column(VIEWS, sa.Column("leaf", sa.Text(), nullable=True))
    op.add_column(VIEWS, sa.Column("green_share", sa.Double(), nullable=True))
    op.add_column(VIEWS, sa.Column("camera_lat", sa.Double(), nullable=True))
    op.add_column(VIEWS, sa.Column("camera_lon", sa.Double(), nullable=True))
    op.add_column(VIEWS, sa.Column("pano_id", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_capture_views_leaf_valid"), VIEWS, "leaf IN ('leaf-off', 'leaf-on', 'unknown')"
    )
    op.create_check_constraint(
        op.f("ck_capture_views_green_share_range"),
        VIEWS,
        "green_share IS NULL OR (green_share >= 0 AND green_share <= 1)",
    )
    op.create_check_constraint(
        op.f("ck_capture_views_camera_point_whole"),
        VIEWS,
        "(camera_lat IS NULL) = (camera_lon IS NULL)",
    )


def downgrade() -> None:
    for name in ("camera_point_whole", "green_share_range", "leaf_valid"):
        op.drop_constraint(op.f(f"ck_capture_views_{name}"), VIEWS, type_="check")
    for column in (
        "pano_id",
        "camera_lon",
        "camera_lat",
        "green_share",
        "leaf",
        "imagery_date_note",
        "imagery_on",
    ):
        op.drop_column(VIEWS, column)
    op.drop_column(CAPTURES, "notes")
