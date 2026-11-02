"""``client_errors`` and ``client_error_issues``: every error a user hit in the browser, as
the web app reported it, and the issues they group into (docs/api/CLIENT_ERRORS.md,
EVAL_REPORT section 65).

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

``client_error_issues``: one row per fingerprint (the same bug seen again), with the counts
and first/last seen the ingest keeps, and the status a person sets (``open`` first,
``in_progress``, ``resolved``, ``ignored``; ``regressed`` set by the ingest when a resolved
issue is seen on another build). Mutable: ``set_updated_at()`` maintains ``updated_at``.

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
ISSUES = "client_error_issues"
KINDS = ("error", "unhandledrejection", "api", "console", "resource", "report")
STATUSES = ("open", "in_progress", "resolved", "ignored", "regressed")
# Appended to the never-updated tables (tests/unit/db/test_models_metadata.py).
NO_UPDATE_TABLES = (TABLE,)
# Appended to the tables set_updated_at() maintains.
UPDATED_AT_TABLES = (ISSUES,)


def upgrade() -> None:
    op.create_table(
        ISSUES,
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status_set_by", sa.UUID(), nullable=True),
        sa.Column("status_set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_build_sha", sa.Text(), nullable=True),
        sa.Column("occurrences", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_build_sha", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")",
            name=op.f("ck_client_error_issues_status_valid"),
        ),
        sa.CheckConstraint(
            "occurrences >= 1", name=op.f("ck_client_error_issues_occurrences_positive")
        ),
        sa.ForeignKeyConstraint(
            ["status_set_by"],
            ["users.id"],
            name=op.f("fk_client_error_issues_status_set_by_users"),
        ),
        sa.PrimaryKeyConstraint("fingerprint", name=op.f("pk_client_error_issues")),
    )
    op.create_index(
        op.f("ix_client_error_issues_status_last_seen_at"), ISSUES, ["status", "last_seen_at"]
    )
    op.create_index(op.f("ix_client_error_issues_last_seen_at"), ISSUES, ["last_seen_at"])
    for table in UPDATED_AT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
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
        sa.Column("fingerprint", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["fingerprint"],
            ["client_error_issues.fingerprint"],
            name=op.f("fk_client_errors_fingerprint_client_error_issues"),
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
    op.create_index(op.f("ix_client_errors_fingerprint_seq"), TABLE, ["fingerprint", "seq"])
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_update BEFORE UPDATE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_update ON {TABLE}")
    op.drop_table(TABLE)
    for table in UPDATED_AT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {table}")
    op.drop_table(ISSUES)
