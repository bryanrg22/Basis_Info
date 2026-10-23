"""The components review: ``stage`` gains ``components_processing`` and ``components_review``;
``studies.components_gate``.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-22

Hand-written to match ``basis.db.models`` (``alembic check`` proves it). Patch
``components-gate-v1`` of the StudyWorkflow splits the rooms review in two: the rooms review
approves the grouping, the room step runs on it (``components_processing``), then the
components review (``components_review``) takes the component, count and re-analysis
corrections. The values are added before ``assets_processing`` so the type's order is the
pipeline's (``basis.db.enums.Stage``).

``studies.components_gate`` records which flow the study's execution took
(``record_run_start``): existing studies are backfilled ``false`` (their executions have the
single rooms review); a new study is ``null`` until its workflow starts.

``ALTER TYPE ... ADD VALUE`` is allowed in a transaction on PostgreSQL 12+ (the values are
usable once it commits). PostgreSQL cannot drop an enum value, so the downgrade recreates the
type without them; it refuses while a study or a review row uses one (moving them elsewhere
would rewrite review history, and ``reviews`` is unique per stage and revision), so those
rows must be dealt with by hand first.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADDED = ("components_processing", "components_review")
PREVIOUS = (
    "uploading",
    "extracting",
    "appraisal_review",
    "rooms_review",
    "assets_processing",
    "assets_review",
    "exporting",
    "completed",
)


def upgrade() -> None:
    for label in ADDED:
        op.execute(f"ALTER TYPE stage ADD VALUE IF NOT EXISTS '{label}' BEFORE 'assets_processing'")
    op.add_column("studies", sa.Column("components_gate", sa.Boolean(), nullable=True))
    op.execute("UPDATE studies SET components_gate = false")


def downgrade() -> None:
    added = ", ".join(f"'{label}'" for label in ADDED)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM studies WHERE current_stage::text IN ({added}))
               OR EXISTS (SELECT 1 FROM reviews WHERE stage::text IN ({added})) THEN
                RAISE EXCEPTION 'studies or reviews are at a components stage; '
                    'move or delete them before downgrading below 0009';
            END IF;
        END
        $$
        """
    )
    op.drop_column("studies", "components_gate")
    labels = ", ".join(f"'{label}'" for label in PREVIOUS)
    op.execute("ALTER TYPE stage RENAME TO stage_0009")
    op.execute(f"CREATE TYPE stage AS ENUM ({labels})")
    op.execute("ALTER TABLE studies ALTER COLUMN current_stage DROP DEFAULT")
    op.execute(
        "ALTER TABLE studies ALTER COLUMN current_stage TYPE stage USING current_stage::text::stage"
    )
    op.execute("ALTER TABLE studies ALTER COLUMN current_stage SET DEFAULT 'uploading'")
    op.execute("ALTER TABLE reviews ALTER COLUMN stage TYPE stage USING stage::text::stage")
    op.execute("DROP TYPE stage_0009")
