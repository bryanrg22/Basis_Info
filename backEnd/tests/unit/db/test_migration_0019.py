"""Migration 0019's frozen tables agree with git and the running release's with the running
table (CLASSIFICATION_CONVENTIONS.md section 10.4). Its SQL is checked against the live
resolver and ``answer_of`` on Postgres (``tests/integration/db/test_migrations.py``)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from basis.domain.rules import (
    ALIASES,
    GENERIC_FLOOR_LABELS,
    MATERIAL_RULES,
    RULES_VERSION,
    row_digests,
)
from evals import freeze_rule_fingerprints as freezer

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "basis/db/alembic/versions/0019_convention_rule_fingerprints.py"
)


def migration() -> Any:
    spec = importlib.util.spec_from_file_location("migration_0019_unit", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = migration()


def test_the_frozen_releases_are_the_three_versions_a_convention_can_carry() -> None:
    assert sorted(M.FROZEN) == ["2026-10-29.1", "2026-10-30.1", "2026-10-31.1"]
    assert [v for v, _ in freezer.RELEASES] == sorted(M.FROZEN)
    # Rows were only ever added in these releases, so every row keeps its digest: the
    # stored rulings all serve again (the defect of section 10.1).
    latest = M.FROZEN["2026-10-31.1"]["row_digests"]
    for version in ("2026-10-29.1", "2026-10-30.1"):
        table = M.FROZEN[version]
        assert {k: latest[k] for k in table["row_digests"]} == table["row_digests"]
        assert table["aliases"] == M.FROZEN["2026-10-31.1"]["aliases"]


@pytest.mark.skipif(RULES_VERSION not in M.FROZEN, reason="the running table is a later release")
def test_the_running_release_is_frozen_as_the_running_table() -> None:
    table = M.FROZEN[RULES_VERSION]
    assert table["row_digests"] == row_digests()
    assert table["aliases"] == dict(sorted(ALIASES.items()))
    assert table["material_rules"] == dict(sorted(MATERIAL_RULES.items()))
    assert table["generic_floors"] == sorted(GENERIC_FLOOR_LABELS)


def test_the_frozen_tables_match_git() -> None:
    try:
        data = freezer.frozen()
    except freezer.FreezeError as exc:
        pytest.skip(f"release commits unavailable: {exc}")
    assert data == M.FROZEN
    assert freezer.committed(MIGRATION.read_text(encoding="utf-8")) == M.FROZEN
