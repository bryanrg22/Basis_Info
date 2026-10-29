"""``llm_calls`` becomes the ledger of every paid call (EVAL_REPORT section 40).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-29

Hand-written to match ``basis.db.models`` (``alembic check`` proves it). Every new column is
nullable or server-defaulted, so the rows written before this migration stay valid: they are
``kind = 'chat'``, ``status = 'ok'`` model calls whose split, component, entity, parameters
and payload references are unknown (NULL). Nothing is backfilled: the information was not
recorded at the time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "llm_calls"

NULLABLE_TEXT = (
    "error",
    "stage",
    "component",
    "prompt_name",
    "build_sha",
    "entity_type",
    "requested_model",
    "endpoint",
    "response_id",
    "request_id",
    "finish_reason",
    "reasoning_effort",
    "response_format",
    "unit",
    "price_row",
    "pricing_version",
    "request_uri",
    "response_uri",
)
NULLABLE_INT = (
    "revision",
    "activity_attempt",
    "max_output_tokens",
    "image_count",
    "image_bytes",
    "elapsed_ms",
)
ZERO_INT = ("cache_read_input_tokens", "cache_creation_input_tokens", "reasoning_output_tokens")
SHA256 = ("prompt_sha256", "request_sha256", "response_sha256")


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("kind", sa.Text(), server_default=sa.text("'chat'"), nullable=False),
    )
    op.add_column(
        TABLE,
        sa.Column("status", sa.Text(), server_default=sa.text("'ok'"), nullable=False),
    )
    for name in NULLABLE_TEXT:
        op.add_column(TABLE, sa.Column(name, sa.Text(), nullable=True))
    for name in NULLABLE_INT:
        op.add_column(TABLE, sa.Column(name, sa.Integer(), nullable=True))
    for name in SHA256:
        op.add_column(TABLE, sa.Column(name, sa.CHAR(length=64), nullable=True))
    op.add_column(TABLE, sa.Column("entity_id", sa.Uuid(), nullable=True))
    op.add_column(TABLE, sa.Column("temperature", sa.Double(), nullable=True))
    op.add_column(
        TABLE,
        sa.Column("request_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    for name in ZERO_INT:
        op.add_column(
            TABLE,
            sa.Column(name, sa.Integer(), server_default=sa.text("0"), nullable=False),
        )
    op.add_column(TABLE, sa.Column("quantity", sa.Numeric(precision=14, scale=4), nullable=True))
    op.add_column(
        TABLE, sa.Column("unit_price_usd", sa.Numeric(precision=12, scale=8), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_llm_calls_kind_valid"),
        TABLE,
        "kind IN ('chat', 'document_analysis', 'localization', 'segmentation')",
    )
    op.create_check_constraint(
        op.f("ck_llm_calls_status_valid"), TABLE, "status IN ('ok', 'error')"
    )
    op.create_index(
        op.f("ix_llm_calls_study_id_created_at_id"), TABLE, ["study_id", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_calls_study_id_created_at_id"), table_name=TABLE)
    op.drop_constraint(op.f("ck_llm_calls_status_valid"), TABLE, type_="check")
    op.drop_constraint(op.f("ck_llm_calls_kind_valid"), TABLE, type_="check")
    for name in (
        "unit_price_usd",
        "quantity",
        *ZERO_INT,
        "request_params",
        "temperature",
        "entity_id",
        *SHA256,
        *NULLABLE_INT,
        *NULLABLE_TEXT,
        "status",
        "kind",
    ):
        op.drop_column(TABLE, name)
