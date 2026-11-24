"""Property pictures: the pin, the captures and their views, the shared capture state, the
navigation log and the incidents (docs/api/PROPERTY_CAPTURE.md, EVAL_REPORT section 98).

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-23

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

* ``property_pins``: one row per study, the address and the point the views are centred on,
  who placed it and whether the engineer confirmed it; ``version`` is the ``If-Match`` tag.
* ``property_captures``: one capture's status (queued, waiting, running, succeeded,
  failed), the catalogue code and the engineer-facing reason of its last pause or failure;
  at most one active capture per study (a partial unique index).
* ``capture_views``: the stored views with their camera, pixel size and imagery date; never
  updated.
* ``capture_state``: the one row every capture worker shares (the lease, the breaker, the
  last success, the worker's heartbeat); seeded here with ``id = 1``.
* ``capture_navigations``: every page load and camera move, so the hourly and daily caps
  survive a restart.
* ``capture_incidents``: one row per failure code with its count, first and last seen, and
  the newest occurrence's study, capture, page and evidence.
* ``alerts.kind`` admits ``capture_blocked`` (one alert per breaker trip).

Nothing is backfilled: nothing was captured before.
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PINS = "property_pins"
CAPTURES = "property_captures"
VIEWS = "capture_views"
STATE = "capture_state"
NAVIGATIONS = "capture_navigations"
INCIDENTS = "capture_incidents"
# Appended to the tables set_updated_at() maintains (tests/unit/db/test_models_metadata.py).
UPDATED_AT_TABLES = (PINS, CAPTURES, STATE, INCIDENTS)
# Appended to the never-updated tables.
NO_UPDATE_TABLES = (VIEWS,)
ACTIVE = "status IN ('queued', 'waiting', 'running')"


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
        PINS,
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("lat", sa.Double(), nullable=True),
        sa.Column("lon", sa.Double(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("place_label", sa.Text(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("confirmed_by", sa.UUID(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _created(),
        _updated(),
        sa.CheckConstraint(
            "(lat IS NULL) = (lon IS NULL)", name=op.f("ck_property_pins_point_whole")
        ),
        sa.CheckConstraint(
            "lat IS NULL OR (lat >= -90 AND lat <= 90)", name=op.f("ck_property_pins_lat_range")
        ),
        sa.CheckConstraint(
            "lon IS NULL OR (lon >= -180 AND lon <= 180)", name=op.f("ck_property_pins_lon_range")
        ),
        sa.CheckConstraint(
            "address IS NOT NULL OR lat IS NOT NULL",
            name=op.f("ck_property_pins_something_to_find"),
        ),
        sa.CheckConstraint(
            "source IN ('engineer', 'search')", name=op.f("ck_property_pins_source_valid")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_property_pins_version_positive")),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_property_pins_study_id_studies")
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], name=op.f("fk_property_pins_confirmed_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_property_pins_updated_by_users")
        ),
        sa.PrimaryKeyConstraint("study_id", name=op.f("pk_property_pins")),
    )
    op.create_table(
        CAPTURES,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("pin_version", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("resume_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("views", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _created(),
        _updated(),
        sa.CheckConstraint(
            "status IN ('queued', 'waiting', 'running', 'succeeded', 'failed')",
            name=op.f("ck_property_captures_status_valid"),
        ),
        sa.CheckConstraint("views >= 0", name=op.f("ck_property_captures_views_not_negative")),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_property_captures_study_id_studies")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name=op.f("fk_property_captures_requested_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_property_captures")),
        sa.UniqueConstraint("study_id", "id", name=op.f("uq_property_captures_study_id_id")),
    )
    op.create_index(
        "ix_property_captures_study_id_created_at",
        CAPTURES,
        ["study_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "uq_property_captures_one_active",
        CAPTURES,
        ["study_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
    )
    op.create_table(
        VIEWS,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("capture_id", sa.UUID(), nullable=False),
        sa.Column("pin_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column("ground_alt_m", sa.Double(), nullable=False),
        sa.Column("range_m", sa.Double(), nullable=False),
        sa.Column("fov_deg", sa.Double(), nullable=False),
        sa.Column("heading_deg", sa.Double(), nullable=False),
        sa.Column("tilt_deg", sa.Double(), nullable=False),
        sa.Column("pixel_size_m", sa.Double(), nullable=True),
        sa.Column("imagery_date", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        _created(),
        sa.CheckConstraint(
            "kind IN ('aerial', 'oblique', 'history', 'street')",
            name=op.f("ck_capture_views_kind_valid"),
        ),
        sa.CheckConstraint("width > 0 AND height > 0", name=op.f("ck_capture_views_size_positive")),
        sa.CheckConstraint(
            "pixel_size_m IS NULL OR pixel_size_m > 0",
            name=op.f("ck_capture_views_pixel_size_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["study_id", "capture_id"],
            ["property_captures.study_id", "property_captures.id"],
            name=op.f("fk_capture_views_study_id_capture_id_property_captures"),
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_capture_views_study_id_studies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capture_views")),
        sa.UniqueConstraint("capture_id", "label", name=op.f("uq_capture_views_capture_id_label")),
    )
    op.create_index(
        op.f("ix_capture_views_study_id_captured_at"), VIEWS, ["study_id", "captured_at"]
    )
    op.create_table(
        STATE,
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("lease_holder", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("breaker_open_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("breaker_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("breaker_code", sa.Text(), nullable=True),
        sa.Column("breaker_reason", sa.Text(), nullable=True),
        sa.Column("trips", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_navigation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_seen_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        _updated(),
        sa.CheckConstraint("id = 1", name=op.f("ck_capture_state_single_row")),
        sa.CheckConstraint("trips >= 0", name=op.f("ck_capture_state_trips_not_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capture_state")),
    )
    op.execute(f"INSERT INTO {STATE} (id) VALUES (1)")
    op.create_table(
        NAVIGATIONS,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("capture_id", sa.UUID(), nullable=True),
        _created(),
        sa.CheckConstraint(
            "kind IN ('load', 'move')", name=op.f("ck_capture_navigations_kind_valid")
        ),
        sa.ForeignKeyConstraint(
            ["capture_id"],
            ["property_captures.id"],
            name=op.f("fk_capture_navigations_capture_id_property_captures"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capture_navigations")),
    )
    op.create_index(
        op.f("ix_capture_navigations_kind_created_at"), NAVIGATIONS, ["kind", "created_at"]
    )
    op.create_table(
        INCIDENTS,
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("occurrences", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_study_id", sa.UUID(), nullable=True),
        sa.Column("last_capture_id", sa.UUID(), nullable=True),
        sa.Column("last_url", sa.Text(), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_detail", sa.Text(), nullable=True),
        sa.Column("last_evidence", sa.Text(), nullable=True),
        _created(),
        _updated(),
        sa.CheckConstraint(
            "occurrences >= 1", name=op.f("ck_capture_incidents_occurrences_positive")
        ),
        sa.ForeignKeyConstraint(
            ["last_study_id"],
            ["studies.id"],
            name=op.f("fk_capture_incidents_last_study_id_studies"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_capture_id"],
            ["property_captures.id"],
            name=op.f("fk_capture_incidents_last_capture_id_property_captures"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_capture_incidents")),
    )
    op.create_index(op.f("ix_capture_incidents_last_seen_at"), INCIDENTS, ["last_seen_at"])
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
    op.drop_constraint(op.f("ck_alerts_kind_valid"), "alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_alerts_kind_valid"),
        "alerts",
        "kind IN ('study_failed', 'heartbeat_stale', 'capture_blocked')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM alerts WHERE kind = 'capture_blocked'")
    op.drop_constraint(op.f("ck_alerts_kind_valid"), "alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_alerts_kind_valid"), "alerts", "kind IN ('study_failed', 'heartbeat_stale')"
    )
    for table in (INCIDENTS, NAVIGATIONS, STATE, VIEWS, CAPTURES, PINS):
        op.drop_table(table)
