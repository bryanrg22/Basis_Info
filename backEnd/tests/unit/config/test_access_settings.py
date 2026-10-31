"""Two access settings of the engineer workspace (docs/api/SPEND.md, docs/api/STUDY_RESULT.md).

``SPEND_ACCESS_ROLE`` (``viewer`` | ``engineer`` | ``owner``, default ``owner``): the lowest
firm role that reads a study's spend. ``INTAKE_REQUIRE_IF_MATCH`` (default false): whether a
``PUT /v1/studies/{id}/intake`` without ``If-Match`` is refused (428). The defaults, an
override, and a bad value refused at startup.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from basis.config import load_settings, reset_settings

VARS = ("SPEND_ACCESS_ROLE", "INTAKE_REQUIRE_IF_MATCH")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASIS_ENV", "test")
    monkeypatch.chdir(tmp_path)
    reset_settings()
    yield monkeypatch
    reset_settings()


def test_the_defaults_keep_spend_with_the_owner_and_accept_a_put_without_if_match(
    env: pytest.MonkeyPatch,
) -> None:
    settings = load_settings()
    assert settings.spend_access_role == "owner"
    assert settings.intake_require_if_match is False


@pytest.mark.parametrize("role", ["viewer", "engineer", "owner"])
def test_spend_access_role_is_read(env: pytest.MonkeyPatch, role: str) -> None:
    env.setenv("SPEND_ACCESS_ROLE", role)
    assert load_settings().spend_access_role == role


@pytest.mark.parametrize("role", ["admin", "OWNER ", "", "member"])
def test_an_unknown_spend_access_role_is_refused(env: pytest.MonkeyPatch, role: str) -> None:
    env.setenv("SPEND_ACCESS_ROLE", role)
    with pytest.raises(ValidationError):
        load_settings()


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("1", True), ("false", False)])
def test_intake_require_if_match_is_read(env: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    env.setenv("INTAKE_REQUIRE_IF_MATCH", raw)
    assert load_settings().intake_require_if_match is expected


def test_a_non_boolean_intake_require_if_match_is_refused(env: pytest.MonkeyPatch) -> None:
    env.setenv("INTAKE_REQUIRE_IF_MATCH", "sometimes")
    with pytest.raises(ValidationError):
        load_settings()
