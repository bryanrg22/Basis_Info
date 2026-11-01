"""The report's inputs and the engineer's signature (STUDY_REPORT_SPEC 11.13).

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-31

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

* ``firm_profiles``: one row per firm, the letterhead and signing engineer the report
  prints (``basis.report.inputs.FirmProfile`` as JSONB), ``version`` its entity tag
  (``If-Match`` on ``PUT /v1/orgs/{id}/profile``), ``set_updated_at()``.
* ``studies.report_answers`` and ``studies.report_answers_version``: the study's answers
  for the report (``StudyAnswers``) beside the intake, versioned the same way. NULL and 0
  for every existing study: nothing is backfilled.
* ``report_signatures``: one signature per revision (``UNIQUE (study_id, revision)``) with
  the attested engineer, the certification version and the sha256 of the export, the
  reviewed preview and the signed PDF. ``forbid_mutation()`` on UPDATE: a signature is
  never rewritten. DELETE is left alone, as on the cost rows (a purge removes a study).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPDATED_AT_TABLES = ("firm_profiles",)
NO_UPDATE_TABLES = ("report_signatures",)
SHA256 = (
    "export_sha256 ~ '^[0-9a-f]{64}$' AND reviewed_sha256 ~ '^[0-9a-f]{64}$' "
    "AND pdf_sha256 ~ '^[0-9a-f]{64}$'"
)


def upgrade() -> None:
    op.create_table(
        "firm_profiles",
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=False),
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
            "jsonb_typeof(profile) = 'object'", name=op.f("ck_firm_profiles_profile_is_object")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_firm_profiles_version_positive")),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name=op.f("fk_firm_profiles_org_id_orgs")),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_firm_profiles_updated_by_users")
        ),
        sa.PrimaryKeyConstraint("org_id", name=op.f("pk_firm_profiles")),
    )
    op.add_column(
        "studies",
        sa.Column("report_answers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "studies",
        sa.Column(
            "report_answers_version", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_studies_report_answers_is_object"),
        "studies",
        "report_answers IS NULL OR jsonb_typeof(report_answers) = 'object'",
    )
    op.create_check_constraint(
        op.f("ck_studies_report_answers_version_not_negative"),
        "studies",
        "report_answers_version >= 0",
    )
    op.create_table(
        "report_signatures",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("signed_by", sa.UUID(), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("engineer", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("statement_version", sa.Integer(), nullable=False),
        sa.Column("export_sha256", sa.Text(), nullable=False),
        sa.Column("reviewed_sha256", sa.Text(), nullable=False),
        sa.Column("pdf_uri", sa.Text(), nullable=False),
        sa.Column("pdf_sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_report_signatures_revision_positive")),
        sa.CheckConstraint(
            "jsonb_typeof(engineer) = 'object'",
            name=op.f("ck_report_signatures_engineer_is_object"),
        ),
        sa.CheckConstraint(SHA256, name=op.f("ck_report_signatures_sha256_hex")),
        sa.ForeignKeyConstraint(
            ["signed_by"], ["users.id"], name=op.f("fk_report_signatures_signed_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["study_id"], ["studies.id"], name=op.f("fk_report_signatures_study_id_studies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_signatures")),
        sa.UniqueConstraint(
            "study_id", "revision", name=op.f("uq_report_signatures_study_id_revision")
        ),
    )
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
    op.drop_table("report_signatures")  # its trigger goes with it
    op.drop_constraint(
        op.f("ck_studies_report_answers_version_not_negative"), "studies", type_="check"
    )
    op.drop_constraint(op.f("ck_studies_report_answers_is_object"), "studies", type_="check")
    op.drop_column("studies", "report_answers_version")
    op.drop_column("studies", "report_answers")
    op.drop_table("firm_profiles")  # its trigger goes with it
