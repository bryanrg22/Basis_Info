"""``client_errors``: every error a user hit in the browser, as the web app reported it
(docs/api/CLIENT_ERRORS.md, EVAL_REPORT section 65).

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-01

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

One row per event the web app's capture module sent to ``POST /v1/client-errors``: the
columns an operator filters on (when, by whose clock; the reporter and their firm, verified
against the memberships; the study when the reporter can see it; the route and screen; the
kind, type, message; the failed request's status, catalogue code and request id; the build;
the browser) and the whole event as JSONB (breadcrumbs, stack, source location, viewport,
language, time since load). ``event_id`` is the client's own id and unique, so a retried
batch stores each event once; ``seq`` is the operator listing's keyset cursor. Rows are never
updated (``forbid_mutation()`` on UPDATE, the ``takeoffs`` rule) and are deleted only by
``python -m basis.retention purge-client-errors`` after ``CLIENT_ERRORS_RETENTION_DAYS``.

Nothing is backfilled: nothing was recorded before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "client_errors"
KINDS = ("error", "unhandledrejection", "api", "console", "resource", "report")
# Appended to the never-updated tables (tests/unit/db/test_models_metadata.py).
NO_UPDATE_TABLES = (TABLE,)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("study_id", sa.UUID(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("route", sa.Text(), nullable=True),
        sa.Column("screen", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("build_sha", sa.Text(), nullable=True),
        sa.Column("web_version", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("event", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in KINDS) + ")",
            name=op.f("ck_client_errors_kind_valid"),
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR (http_status >= 0 AND http_status <= 999)",
            name=op.f("ck_client_errors_http_status_range"),
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name=op.f("fk_client_errors_org_id_orgs")),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_client_errors_study_id_studies")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_client_errors_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_errors")),
        sa.UniqueConstraint("event_id", name=op.f("uq_client_errors_event_id")),
        sa.UniqueConstraint("seq", name=op.f("uq_client_errors_seq")),
    )
    op.create_index(op.f("ix_client_errors_org_id_seq"), TABLE, ["org_id", "seq"])
    op.create_index(op.f("ix_client_errors_user_id_seq"), TABLE, ["user_id", "seq"])
    op.create_index(op.f("ix_client_errors_study_id_seq"), TABLE, ["study_id", "seq"])
    op.create_index(op.f("ix_client_errors_code"), TABLE, ["code"])
    op.create_index(op.f("ix_client_errors_request_id"), TABLE, ["request_id"])
    op.create_index(op.f("ix_client_errors_created_at"), TABLE, ["created_at"])
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_update BEFORE UPDATE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_update ON {TABLE}")
    op.drop_table(TABLE)
