"""``study_events.actor_type`` takes ``operator``: a Basis platform operator opened one of a
study's uploaded files for support (``operator.document_accessed``; docs/api/OPS_DOCUMENTS.md).

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-03

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

A firm's studies are visible only to its members; support access to the firm's uploads is
the one exception (Bryan, 2026-11-02), and every open is recorded on the study's timeline
the firm reads, as an access-transparency log records a provider's access to a customer's
content. The rows are append-only (``forbid_mutation()``), so the downgrade refuses while
any exists rather than delete an access record. Nothing is backfilled.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "study_events"
NAME = "ck_study_events_actor_valid"
BEFORE = ("engineer", "model", "system")
AFTER = (*BEFORE, "operator")


def _check(actors: tuple[str, ...]) -> str:
    return "actor_type IN (" + ", ".join(f"'{a}'" for a in actors) + ")"


def upgrade() -> None:
    op.drop_constraint(op.f(NAME), TABLE, type_="check")
    op.create_check_constraint(op.f(NAME), TABLE, _check(AFTER))


def downgrade() -> None:
    # Access records are never deleted: refuse while one exists.
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {TABLE} WHERE actor_type = 'operator') THEN "
        "RAISE EXCEPTION 'study_events holds operator access records; they are append-only "
        "and are not deleted by a downgrade'; END IF; END $$"
    )
    op.drop_constraint(op.f(NAME), TABLE, type_="check")
    op.create_check_constraint(op.f(NAME), TABLE, _check(BEFORE))
