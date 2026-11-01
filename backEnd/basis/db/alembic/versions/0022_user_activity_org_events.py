"""``study_events (actor_id, seq DESC)`` for the per-user activity; ``org_events``, the
firm's own history.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-01

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

* ``ix_study_events_actor_id_seq``: ``GET /v1/me/activity`` reads one user's own actions
  across every study they can see, newest first, keyset-paginated on ``seq``
  (docs/api/TIMELINE_AND_AUDIT.md "Your activity"). The existing indexes are keyed by
  study; without this one that read scans every event the user's firms ever recorded.
* ``org_events``: one append-only row per action on a firm that is not about a study (an
  invitation created, accepted, declined or revoked, a member's role changed, a member
  removed, the report profile set, the firm created), the way ``study_events`` records a
  study's history: same ``seq`` identity, same ``forbid_mutation()`` trigger, read by
  ``GET /v1/orgs/{org_id}/activity``. Before this migration those actions left only their
  end state (the ``invitations`` row, the ``org_members`` row or its absence), so a member
  removed and re-invited, or a role changed twice, had no record of who did what when.

Nothing is backfilled: firm actions were not recorded before, and a history cannot be
invented after the fact (an accepted invitation's row still says who accepted it and
when, so the invitation list stays the record of what happened before).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "org_events"
ACTOR_INDEX = "ix_study_events_actor_id_seq"
# Appended to the append-only tables (tests/unit/db/test_models_metadata.py).
APPEND_ONLY_TABLES = (TABLE,)


def upgrade() -> None:
    op.create_index(ACTOR_INDEX, "study_events", ["actor_id", sa.text("seq DESC")])
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("subject_user_id", sa.UUID(), nullable=True),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
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
            "actor_type IN ('engineer', 'system')", name=op.f("ck_org_events_actor_valid")
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_org_events_actor_id_users")
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name=op.f("fk_org_events_org_id_orgs")),
        sa.ForeignKeyConstraint(
            ["subject_user_id"], ["users.id"], name=op.f("fk_org_events_subject_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_events")),
        sa.UniqueConstraint("seq", name=op.f("uq_org_events_seq")),
    )
    op.create_index(op.f("ix_org_events_org_id_seq"), TABLE, ["org_id", "seq"])
    op.execute(
        f"CREATE TRIGGER {TABLE}_append_only BEFORE UPDATE OR DELETE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_append_only ON {TABLE}")
    op.drop_index(op.f("ix_org_events_org_id_seq"), table_name=TABLE)
    op.drop_table(TABLE)
    op.drop_index(ACTOR_INDEX, table_name="study_events")
