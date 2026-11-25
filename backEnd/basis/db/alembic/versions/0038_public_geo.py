"""Public geographic sources: gathers, public views, parcels, incidents (EVAL_REPORT 101).

* ``geo_gathers``: one gather of the public sources around a study's pin; one active per study.
* ``geo_views``: the public top-down images with their georeference, source, credit and terms
  (never updated).
* ``geo_parcels``: the parcel under the pin and the owner's adjacent ones (never updated).
* ``geo_incidents``: failures grouped by code and source, for operators.

Follows 0037, the capture history.
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GATHERS = "geo_gathers"
VIEWS = "geo_views"
PARCELS = "geo_parcels"
INCIDENTS = "geo_incidents"
UPDATED_AT_TABLES = (GATHERS, INCIDENTS)
NO_UPDATE_TABLES = (VIEWS, PARCELS)
JSONB = postgresql.JSONB(none_as_null=True, astext_type=sa.Text())


def _created() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _updated() -> sa.Column[datetime]:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        GATHERS,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("pin_version", sa.Integer(), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("sources", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("lidar", JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        _updated(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name=op.f("ck_geo_gathers_status_valid"),
        ),
        sa.CheckConstraint(
            "lat >= -90 AND lat <= 90 AND lon >= -180 AND lon <= 180",
            name=op.f("ck_geo_gathers_point"),
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_geo_gathers_study_id_studies")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name=op.f("fk_geo_gathers_requested_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_geo_gathers")),
        sa.UniqueConstraint("study_id", "id", name=op.f("uq_geo_gathers_study_id_id")),
    )
    op.create_index(
        "ix_geo_gathers_study_id_created_at",
        GATHERS,
        ["study_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "uq_geo_gathers_one_active",
        GATHERS,
        ["study_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        VIEWS,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("gather_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("year_label", sa.Text(), nullable=True),
        sa.Column("leaf_off", sa.Boolean(), nullable=True),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column("pixel_size_m", sa.Double(), nullable=False),
        sa.Column("native_pixel_m", sa.Double(), nullable=True),
        sa.Column("crs", sa.Text(), server_default=sa.text("'EPSG:3857'"), nullable=False),
        sa.Column("bbox", JSONB, nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("credit", sa.Text(), nullable=False),
        sa.Column("terms_class", sa.Text(), nullable=False),
        sa.Column("terms_url", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        _created(),
        sa.CheckConstraint("kind IN ('current', 'history')", name=op.f("ck_geo_views_kind_valid")),
        sa.CheckConstraint("width > 0 AND height > 0", name=op.f("ck_geo_views_size_positive")),
        sa.CheckConstraint("pixel_size_m > 0", name=op.f("ck_geo_views_pixel_size_positive")),
        sa.ForeignKeyConstraint(
            ["study_id", "gather_id"],
            ["geo_gathers.study_id", "geo_gathers.id"],
            name=op.f("fk_geo_views_study_id_gather_id_geo_gathers"),
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_geo_views_study_id_studies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_geo_views")),
        sa.UniqueConstraint("gather_id", "label", name=op.f("uq_geo_views_gather_id_label")),
    )
    op.create_index(op.f("ix_geo_views_study_id_created_at"), VIEWS, ["study_id", "created_at"])
    op.create_table(
        PARCELS,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("gather_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("parcel_id", sa.Text(), nullable=True),
        sa.Column("under_pin", sa.Boolean(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("area_m2", sa.Double(), nullable=False),
        sa.Column("layer_acres", sa.Double(), nullable=True),
        sa.Column("land_value", sa.Double(), nullable=True),
        sa.Column("improvement_value", sa.Double(), nullable=True),
        sa.Column("total_value", sa.Double(), nullable=True),
        sa.Column("year_built", sa.Integer(), nullable=True),
        sa.Column("building_area_sqft", sa.Double(), nullable=True),
        sa.Column("stories", sa.Double(), nullable=True),
        sa.Column("geometry", JSONB, nullable=False),
        sa.Column("attributes", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("credit", sa.Text(), nullable=False),
        sa.Column("terms_class", sa.Text(), nullable=False),
        _created(),
        sa.CheckConstraint("area_m2 >= 0", name=op.f("ck_geo_parcels_area_not_negative")),
        sa.ForeignKeyConstraint(
            ["study_id", "gather_id"],
            ["geo_gathers.study_id", "geo_gathers.id"],
            name=op.f("fk_geo_parcels_study_id_gather_id_geo_gathers"),
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_geo_parcels_study_id_studies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_geo_parcels")),
    )
    op.create_index(op.f("ix_geo_parcels_gather_id"), PARCELS, ["gather_id"])
    op.create_table(
        INCIDENTS,
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("occurrences", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_study_id", sa.UUID(), nullable=True),
        sa.Column("last_gather_id", sa.UUID(), nullable=True),
        sa.Column("last_url", sa.Text(), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_detail", sa.Text(), nullable=True),
        _created(),
        _updated(),
        sa.CheckConstraint("occurrences >= 1", name=op.f("ck_geo_incidents_occurrences_positive")),
        sa.ForeignKeyConstraint(
            ["last_study_id"],
            ["studies.id"],
            name=op.f("fk_geo_incidents_last_study_id_studies"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_gather_id"],
            ["geo_gathers.id"],
            name=op.f("fk_geo_incidents_last_gather_id_geo_gathers"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("code", "source_id", name=op.f("pk_geo_incidents")),
    )
    op.create_index(op.f("ix_geo_incidents_last_seen_at"), INCIDENTS, ["last_seen_at"])
    for table in UPDATED_AT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    for table in NO_UPDATE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
        )


def downgrade() -> None:
    for table in (INCIDENTS, PARCELS, VIEWS, GATHERS):
        op.drop_table(table)
