"""``client_errors.kind`` takes ``study_input``: a study whose dollars are held by a missing
input, recorded by the server for the operators (EVAL_REPORT 78).

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-02

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

The firm's engineer is not making a mistake the software can fix: the appraisal lacks a
field, a room has no measured area, an item has no count, the cost table has no row. Those
are recorded in the same store as the browser's errors (``client_errors``, migration 0028), so
an operator sees, per firm, who hit what on which study, with the study's files listed, and
can follow up (docs/api/CLIENT_ERRORS.md). Only the server writes this kind: the browser's
``POST /v1/client-errors`` does not accept it. Nothing is backfilled.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "client_errors"
NAME = "ck_client_errors_kind_valid"
BEFORE = ("error", "unhandledrejection", "api", "console", "resource", "report")
AFTER = (*BEFORE, "study_input")


def _check(kinds: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{k}'" for k in kinds) + ")"


def upgrade() -> None:
    op.drop_constraint(op.f(NAME), TABLE, type_="check")
    op.create_check_constraint(op.f(NAME), TABLE, _check(AFTER))


def downgrade() -> None:
    op.execute(f"DELETE FROM {TABLE} WHERE kind = 'study_input'")
    op.drop_constraint(op.f(NAME), TABLE, type_="check")
    op.create_check_constraint(op.f(NAME), TABLE, _check(BEFORE))
