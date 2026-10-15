"""``outbox.status`` and migration ``0002_outbox_status`` as metadata (no database).

The database behavior (backfill, the check constraint, claims) is tested against
PostgreSQL in ``tests/integration/db/test_migrations.py`` and
``tests/integration/db/test_outbox_repository.py``.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ENUM

from basis.db.enums import OutboxStatus
from basis.db.models import PG_ENUMS, Base
from basis.db.repositories.outbox import MAX_ERROR_LENGTH, OutboxRepository

VERSIONS = Path(__file__).resolve().parents[3] / "basis/db/alembic/versions"
OUTBOX = Base.metadata.tables["outbox"]


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"migration_{name}", VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_status_values() -> None:
    assert [member.value for member in OutboxStatus] == ["pending", "delivered", "dead"]


def test_status_column_is_a_native_enum_defaulting_to_pending() -> None:
    column = OUTBOX.c.status
    assert isinstance(column.type, ENUM) and column.type.name == "outbox_status"
    assert column.type.native_enum and not column.nullable
    assert str(column.server_default.arg) == "'pending'"  # type: ignore[union-attr]
    assert "outbox_status" in {enum.name for enum in PG_ENUMS}


def test_claim_index_and_consistency_check() -> None:
    indexes = {index.name: [c.name for c in index.columns] for index in OUTBOX.indexes}
    assert indexes == {"ix_outbox_status_next_attempt_at": ["status", "next_attempt_at"]}
    checks = {
        str(c.name): str(c.sqltext) for c in OUTBOX.constraints if isinstance(c, CheckConstraint)
    }
    assert checks["ck_outbox_delivered_at_matches_status"] == (
        "(status = 'delivered') = (delivered_at IS NOT NULL)"
    )


def test_migration_0002_follows_0001_and_matches_the_model() -> None:
    migration = load("0002_outbox_status")
    assert (migration.revision, migration.down_revision) == ("0002", "0001")
    assert migration.ENUMS == {"outbox_status": tuple(member.value for member in OutboxStatus)}


async def test_claim_query_is_pending_due_ordered_and_skip_locked() -> None:
    captured: list[Any] = []

    class Session:
        async def scalars(self, stmt: Any) -> Any:
            captured.append(stmt)

            class Result:
                def all(self) -> list[object]:
                    return []

            return Result()

    await OutboxRepository(Session()).claim_batch(20)  # type: ignore[arg-type]
    sql = str(captured[0].compile(dialect=postgresql.dialect()))
    assert "outbox.status = %(status_1)s" in sql
    assert "outbox.next_attempt_at <= now()" in sql
    assert "ORDER BY outbox.next_attempt_at, outbox.created_at, outbox.id" in sql
    assert "LIMIT %(param_1)s" in sql
    assert sql.endswith("FOR UPDATE SKIP LOCKED")


async def test_argument_checks_need_no_database() -> None:
    repo = OutboxRepository(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await repo.claim_batch(0)
    with pytest.raises(ValueError):
        await repo.mark_failed(uuid.uuid4(), "e", retry_in=timedelta(seconds=-1))
    assert MAX_ERROR_LENGTH == 2000
