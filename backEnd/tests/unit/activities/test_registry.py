"""Every activity class is registered under the name the workflow schedules."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from temporalio.activity import _Definition

from basis.activities import ACTIVITY_CLASSES, ACTIVITY_NAMES, build_activities
from basis.config import Settings
from basis.storage import LocalStorage


def registered_name(fn: Callable[..., Any]) -> str | None:
    definition = _Definition.from_callable(fn)
    return None if definition is None else definition.name


@pytest.mark.parametrize("name", ACTIVITY_NAMES)
def test_class_defn_name_matches_its_registry_key(name: str) -> None:
    assert registered_name(ACTIVITY_CLASSES[name].run) == name


def test_build_activities_binds_every_activity(settings: Settings, tmp_path: Any) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from basis.activities import Deps

    deps = Deps(
        sessionmaker=async_sessionmaker(),
        storage=LocalStorage(tmp_path),
        bucket="b",
        settings=settings,
    )
    built = build_activities(deps)
    assert tuple(built) == ACTIVITY_NAMES
    assert all(registered_name(fn) == name for name, fn in built.items())
    assert (
        len(ACTIVITY_NAMES) == len(set(ACTIVITY_NAMES)) == 16  # promote_conventions
    )  # v0.6: localize_photo; reconcile_rooms; v0.9: record_failure
