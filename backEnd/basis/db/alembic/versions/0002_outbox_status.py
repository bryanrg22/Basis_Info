"""Outbox delivery status for the relay (S2_SPEC section 6).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15

Hand-written. Adds ``outbox.status`` (enum ``outbox_status``: ``pending``, ``delivered``,
``dead``; default ``pending``) so a message that failed too often can be parked as
``dead`` instead of being polled forever.

* Backfill: rows with ``delivered_at`` set become ``delivered``; every other row stays
  ``pending``. The column is added with its default, so existing rows get ``pending``
  first and only delivered rows are rewritten.
* ``ck_outbox_delivered_at_matches_status``: ``delivered_at`` is set exactly when the row
  is ``delivered``, so the two columns can never disagree.
* The claim index moves from ``(delivered_at, next_attempt_at)`` to
  ``(status, next_attempt_at)``, matching the relay's new predicate
  ``status = 'pending' AND next_attempt_at <= now()``.

The enum labels are written out here (not imported) so this migration keeps describing
revision 0002 after the Python enum changes. Downgrade drops the column, the constraint
and the type and restores the old index; ``dead`` rows then look pending again (their
``delivered_at`` is NULL), which is the only representation 0001 has.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS: dict[str, tuple[str, ...]] = {
    "outbox_status": ("pending", "delivered", "dead"),
}


def upgrade() -> None:
    for name, labels in ENUMS.items():
        quoted = ", ".join(f"'{label}'" for label in labels)
        op.execute(f"CREATE TYPE {name} AS ENUM ({quoted})")
    op.add_column(
        "outbox",
        sa.Column(
            "status",
            postgresql.ENUM(name="outbox_status", create_type=False),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
    )
    op.execute("UPDATE outbox SET status = 'delivered' WHERE delivered_at IS NOT NULL")
    op.create_check_constraint(
        op.f("ck_outbox_delivered_at_matches_status"),
        "outbox",
        "(status = 'delivered') = (delivered_at IS NOT NULL)",
    )
    op.drop_index(op.f("ix_outbox_delivered_at_next_attempt_at"), table_name="outbox")
    op.create_index(
        op.f("ix_outbox_status_next_attempt_at"),
        "outbox",
        ["status", "next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_outbox_status_next_attempt_at"), table_name="outbox")
    op.create_index(
        op.f("ix_outbox_delivered_at_next_attempt_at"),
        "outbox",
        ["delivered_at", "next_attempt_at"],
        unique=False,
    )
    op.drop_constraint(op.f("ck_outbox_delivered_at_matches_status"), "outbox", type_="check")
    op.drop_column("outbox", "status")
    for name in reversed(ENUMS):
        op.execute(f"DROP TYPE {name}")
