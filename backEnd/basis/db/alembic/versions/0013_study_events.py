"""``study_events``: the per-study timeline and audit trail; ``rooms.carried_from``;
``studies.failure_code``.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-29

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

* ``study_events``: one append-only row per occurrence in a study's history (stage entered,
  activity completed or failed with its duration, decision recorded, correction applied to a
  row, retry, failure, export; docs/api/TIMELINE_AND_AUDIT.md), protected by the existing
  ``forbid_mutation()`` trigger like ``reviews`` and ``corrections``. ``seq`` (an identity)
  is the insertion order and the keyset cursor; the indexes serve the timeline
  (``study_id, seq``) and the provenance lookup (``study_id, entity_id``).
* ``rooms.carried_from``: the room of the previous revision a room was copied from
  (``apply_corrections``), NULL for rooms the pipeline grouped and for copies made before
  this migration; the lineage a room's provenance is resolved along.
* ``studies.failure_code``: the error code (docs/ERROR_CODES.md) behind ``failure_reason``,
  NULL for failures recorded before this migration.

Nothing is backfilled: events were not recorded before, and a timeline cannot be invented
after the fact (the earlier studies' history is in Temporal and the worker logs).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "study_events"
# Appended to 0001's append-only tables (tests/unit/db/test_models_metadata.py).
APPEND_ONLY_TABLES = (TABLE,)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "stage",
            postgresql.ENUM(name="stage", create_type=False),
            nullable=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("workflow_id", sa.Text(), nullable=True),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("activity", sa.Text(), nullable=True),
        sa.Column("activity_id", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_type IN ('engineer', 'model', 'system')",
            name=op.f("ck_study_events_actor_valid"),
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name=op.f("ck_study_events_duration_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_study_events_actor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_study_events_study_id_studies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_study_events")),
        sa.UniqueConstraint("seq", name=op.f("uq_study_events_seq")),
    )
    op.create_index(op.f("ix_study_events_study_id_seq"), TABLE, ["study_id", "seq"])
    op.create_index(op.f("ix_study_events_study_id_entity_id"), TABLE, ["study_id", "entity_id"])
    op.execute(
        f"CREATE TRIGGER {TABLE}_append_only BEFORE UPDATE OR DELETE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
    )
    op.add_column("rooms", sa.Column("carried_from", sa.UUID(), nullable=True))
    op.add_column("studies", sa.Column("failure_code", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("studies", "failure_code")
    op.drop_column("rooms", "carried_from")
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_append_only ON {TABLE}")
    op.drop_index(op.f("ix_study_events_study_id_entity_id"), table_name=TABLE)
    op.drop_index(op.f("ix_study_events_study_id_seq"), table_name=TABLE)
    op.drop_table(TABLE)
