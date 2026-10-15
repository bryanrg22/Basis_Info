"""Invariants for basis.config (BUILD_PLAN C1).

* BASIS_ENV is explicit and required; nothing is inferred.
* Only ``dev`` reads a ``.env`` file; ``test``/``staging``/``prod`` never do.
* ``staging``/``prod`` refuse to start without their required values.
* Secrets never appear in ``repr`` or in error messages.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from basis.config import (
    ConfigError,
    Settings,
    get_settings,
    load_settings,
    required_in,
    reset_settings,
)

MANAGED_VARS = (
    "BASIS_ENV",
    "GIT_SHA",
    "LOG_LEVEL",
    "DATABASE_URL",
    "TEMPORAL_ADDRESS",
    "TEMPORAL_NAMESPACE",
    "TEMPORAL_API_KEY",
    "GCS_BUCKET",
    "FIREBASE_PROJECT_ID",
)

DEPLOYED_VALUES = {
    "DATABASE_URL": "postgresql+asyncpg://basis:db-password-123@db/basis",
    "GCS_BUCKET": "basis-staging-files",
    "FIREBASE_PROJECT_ID": "basis-staging",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    """No config variables set, cwd is an empty temp dir, settings cache cleared."""
    for name in MANAGED_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    reset_settings()
    yield monkeypatch
    reset_settings()


def write_env_file(directory: Path, **values: str) -> Path:
    path = directory / ".env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- explicit env


def test_missing_basis_env_raises(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="BASIS_ENV is required"):
        load_settings()


def test_blank_basis_env_raises(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "  ")
    with pytest.raises(ConfigError, match="BASIS_ENV is required"):
        load_settings()


@pytest.mark.parametrize("value", ["production", "PROD", "Dev", "local"])
def test_unknown_basis_env_raises(clean_env: pytest.MonkeyPatch, value: str) -> None:
    clean_env.setenv("BASIS_ENV", value)
    with pytest.raises(ConfigError, match="is not valid"):
        load_settings()


def test_missing_basis_env_not_read_from_env_file(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BASIS_ENV must come from the process; a .env cannot select the environment."""
    write_env_file(tmp_path, BASIS_ENV="dev")
    with pytest.raises(ConfigError, match="BASIS_ENV is required"):
        load_settings()


def test_direct_construction_without_env_raises(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_without_env_raises(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError):
        get_settings()


def test_valid_envs_and_defaults(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "test")
    settings = load_settings()
    assert settings.env == "test"
    assert settings.git_sha == "unknown"
    assert settings.log_level == "INFO"
    assert settings.database_url is None
    assert settings.temporal_address == "localhost:7233"
    assert settings.temporal_namespace == "default"
    assert settings.temporal_api_key is None
    assert settings.gcs_bucket is None
    assert settings.firebase_project_id is None


def test_values_read_from_environment(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "test")
    clean_env.setenv("GIT_SHA", "abc1234")
    clean_env.setenv("LOG_LEVEL", "debug")
    clean_env.setenv("TEMPORAL_ADDRESS", "basis.tmprl.cloud:7233")
    clean_env.setenv("TEMPORAL_NAMESPACE", "basis-test")
    settings = load_settings()
    assert settings.git_sha == "abc1234"
    assert settings.log_level == "DEBUG"
    assert settings.temporal_address == "basis.tmprl.cloud:7233"
    assert settings.temporal_namespace == "basis-test"


def test_invalid_log_level_raises(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "test")
    clean_env.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError):
        load_settings()


def test_settings_are_immutable(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "test")
    settings = load_settings()
    with pytest.raises(ValidationError):
        settings.git_sha = "changed"  # type: ignore[misc]


# --------------------------------------------------------------------------- .env loading


@pytest.mark.parametrize("env", ["test", "staging", "prod"])
def test_non_dev_never_reads_env_file(
    clean_env: pytest.MonkeyPatch, tmp_path: Path, env: str
) -> None:
    write_env_file(
        tmp_path,
        DATABASE_URL="should-not-load",
        GCS_BUCKET="should-not-load",
        FIREBASE_PROJECT_ID="should-not-load",
        GIT_SHA="should-not-load",
    )
    clean_env.setenv("BASIS_ENV", env)
    for name, value in DEPLOYED_VALUES.items():
        if env != "test":
            clean_env.setenv(name, value)  # satisfy required_in so construction succeeds
    settings = load_settings()
    assert settings.git_sha == "unknown"
    assert settings.database_url != "should-not-load"
    assert settings.gcs_bucket != "should-not-load"
    assert settings.firebase_project_id != "should-not-load"


def test_test_env_ignores_env_file(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    write_env_file(tmp_path, DATABASE_URL="should-not-load")
    clean_env.setenv("BASIS_ENV", "test")
    assert load_settings().database_url is None


def test_staging_does_not_satisfy_required_from_env_file(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stray .env on a deployed host cannot paper over missing configuration."""
    write_env_file(tmp_path, **DEPLOYED_VALUES)
    clean_env.setenv("BASIS_ENV", "staging")
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        load_settings()


def test_dev_reads_env_file(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    write_env_file(tmp_path, DATABASE_URL="postgresql://localhost/basis_dev", GIT_SHA="dev-sha")
    clean_env.setenv("BASIS_ENV", "dev")
    settings = load_settings()
    assert settings.database_url == "postgresql://localhost/basis_dev"
    assert settings.git_sha == "dev-sha"


def test_dev_process_env_overrides_env_file(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    write_env_file(tmp_path, GIT_SHA="from-file", BASIS_ENV="prod")
    clean_env.setenv("BASIS_ENV", "dev")
    clean_env.setenv("GIT_SHA", "from-process")
    settings = load_settings()
    assert settings.git_sha == "from-process"
    assert settings.env == "dev"


def test_dev_without_env_file_is_fine(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "dev")
    assert load_settings().database_url is None


def test_dev_env_file_path_is_configurable(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    other = tmp_path / "nested"
    other.mkdir()
    custom = write_env_file(other, GCS_BUCKET="dev-bucket")
    clean_env.setenv("BASIS_ENV", "dev")
    assert load_settings(env_file=custom).gcs_bucket == "dev-bucket"


def test_dev_ignores_unknown_keys_in_env_file(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The legacy backEnd/.env carries many unrelated keys; they must not break dev."""
    write_env_file(tmp_path, SOME_LEGACY_SETTING="x", OPENAI_API_KEY="fake-provider-key")
    clean_env.setenv("BASIS_ENV", "dev")
    load_settings()


# --------------------------------------------------------------------------- required_in


def test_required_in_table() -> None:
    assert required_in("dev") == ()
    assert required_in("test") == ()
    expected = ("database_url", "gcs_bucket", "firebase_project_id")
    assert required_in("staging") == expected
    assert required_in("prod") == expected


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_deployed_env_missing_all_required_lists_names(
    clean_env: pytest.MonkeyPatch, env: str
) -> None:
    clean_env.setenv("BASIS_ENV", env)
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    message = str(excinfo.value)
    assert f"BASIS_ENV={env}" in message
    for name in ("DATABASE_URL", "GCS_BUCKET", "FIREBASE_PROJECT_ID"):
        assert name in message


@pytest.mark.parametrize("missing", sorted(DEPLOYED_VALUES))
def test_prod_missing_one_required_names_only_that_one(
    clean_env: pytest.MonkeyPatch, missing: str
) -> None:
    clean_env.setenv("BASIS_ENV", "prod")
    for name, value in DEPLOYED_VALUES.items():
        if name != missing:
            clean_env.setenv(name, value)
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    message = str(excinfo.value)
    assert missing in message
    for name in DEPLOYED_VALUES:
        if name != missing:
            assert name not in message


def test_prod_blank_required_value_counts_as_missing(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "prod")
    for name, value in DEPLOYED_VALUES.items():
        clean_env.setenv(name, value)
    clean_env.setenv("GCS_BUCKET", "   ")
    with pytest.raises(ConfigError, match="GCS_BUCKET"):
        load_settings()


def test_prod_with_all_required_succeeds(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "prod")
    for name, value in DEPLOYED_VALUES.items():
        clean_env.setenv(name, value)
    settings = load_settings()
    assert settings.env == "prod"
    assert settings.gcs_bucket == "basis-staging-files"


def test_required_error_does_not_leak_secret_values(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "prod")
    clean_env.setenv("DATABASE_URL", DEPLOYED_VALUES["DATABASE_URL"])
    clean_env.setenv("TEMPORAL_API_KEY", "temporal-secret-xyz")
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert "db-password-123" not in rendered
    assert "temporal-secret-xyz" not in rendered


def test_validation_error_does_not_echo_input(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "test")
    clean_env.setenv("TEMPORAL_API_KEY", "temporal-secret-xyz")
    clean_env.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError) as excinfo:
        load_settings()
    assert "temporal-secret-xyz" not in str(excinfo.value)
    assert "LOUD" not in str(excinfo.value)


# --------------------------------------------------------------------------- secrets


def test_secret_is_secretstr_and_not_in_repr(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "test")
    clean_env.setenv("TEMPORAL_API_KEY", "temporal-secret-xyz")
    clean_env.setenv("DATABASE_URL", DEPLOYED_VALUES["DATABASE_URL"])
    settings = load_settings()
    assert isinstance(settings.temporal_api_key, SecretStr)
    assert settings.temporal_api_key.get_secret_value() == "temporal-secret-xyz"
    for rendered in (repr(settings), str(settings), repr(settings.temporal_api_key)):
        assert "temporal-secret-xyz" not in rendered
        assert "db-password-123" not in rendered


def test_model_dump_json_masks_secret(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "test")
    clean_env.setenv("TEMPORAL_API_KEY", "temporal-secret-xyz")
    assert "temporal-secret-xyz" not in load_settings().model_dump_json()


# --------------------------------------------------------------------------- caching


def test_get_settings_is_cached_until_reset(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("BASIS_ENV", "test")
    clean_env.setenv("GIT_SHA", "first")
    first = get_settings()
    clean_env.setenv("GIT_SHA", "second")
    assert get_settings() is first
    reset_settings()
    assert get_settings().git_sha == "second"


def test_settings_fixture_reflects_test_environment(settings: Settings) -> None:
    assert settings.env == "test"
    assert settings.git_sha == os.environ["GIT_SHA"]  # "test-sha" unless the caller set one
