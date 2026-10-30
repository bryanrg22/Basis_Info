"""``eval_runs`` records the run manifest: release, dirty flag, predictions directory, label
file identity and the whole ``scores.jsonl`` entry.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-29

Hand-written to match ``basis.db.models`` (``alembic check`` proves it). ``evals.run`` writes
one ``eval_runs`` row per scored dataset; until now the row named only the short git sha, the
split and the system, so it could not say whether the checkout was dirty, which Basis release
it belonged to, which predictions directory was scored or which label file (at which version
and content) the scores refer to. ``evals.record`` now gives every run that manifest in files
(``manifest.json``, ``scores.jsonl``), and these columns store the same facts in the database:

* ``git_dirty``: tracked files modified or staged in the scorer's checkout;
* ``release``: the nearest release tag reachable from HEAD;
* ``run_dir``: the predictions directory scored (``--predictions``), NULL for a baseline;
* ``label_sha256`` / ``label_version``: the sha256 and ``v<n>`` of the dataset's label file;
* ``component`` / ``component_version``: the component and version the scored run is filed
  under in the verification tree (its ``manifest.json`` ``tree``), NULL for a baseline;
* ``manifest``: the whole ``scores.jsonl`` entry the row was written from.

Every column is nullable and nothing is backfilled: rows written before this migration carry
NULL because the information was not recorded at the time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "eval_runs"
NULLABLE_TEXT = ("release", "run_dir", "label_version", "component", "component_version")


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("git_dirty", sa.Boolean(), nullable=True))
    for name in NULLABLE_TEXT:
        op.add_column(TABLE, sa.Column(name, sa.Text(), nullable=True))
    op.add_column(TABLE, sa.Column("label_sha256", sa.CHAR(length=64), nullable=True))
    op.add_column(
        TABLE, sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    for name in ("manifest", "label_sha256", *NULLABLE_TEXT, "git_dirty"):
        op.drop_column(TABLE, name)
