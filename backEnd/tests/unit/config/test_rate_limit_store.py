"""``RATE_LIMIT_STORE`` (``basis.config``, ``basis.api.app``).

* Unset: ``memory`` in dev and test, ``postgres`` in staging and prod.
* Set: honoured, except that ``memory`` is refused in staging and prod (several instances
  serve the API there, and a per-process budget multiplies the published limit).
* ``postgres`` without a database refuses to start (``create_app`` raises ``ConfigError``).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from basis.api.app import create_app
from basis.api.ratelimit import InMemoryRateLimiter
from basis.config import ConfigError, Settings, load_settings, reset_settings

DEPLOYED = {
    "DATABASE_URL": "postgresql+asyncpg://basis:db-password-123@db/basis",
    "GCS_BUCKET": "basis-staging-files",
    "FIREBASE_PROJECT_ID": "basis-staging",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.delenv("RATE_LIMIT_STORE", raising=False)
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    reset_settings()
    yield monkeypatch
    reset_settings()


def deployed(env: pytest.MonkeyPatch, name: str, **extra: str) -> Settings:
    env.setenv("BASIS_ENV", name)
    for key, value in {**DEPLOYED, **extra}.items():
        env.setenv(key, value)
    return load_settings()


@pytest.mark.parametrize("name", ["dev", "test"])
def test_memory_by_default_locally(env: pytest.MonkeyPatch, name: str, tmp_path: object) -> None:
    env.setenv("BASIS_ENV", name)
    env.chdir(str(tmp_path))  # no .env in dev
    settings = load_settings()
    assert settings.rate_limit_store is None
    assert settings.rate_limit_backend == "memory"


@pytest.mark.parametrize("name", ["staging", "prod"])
def test_postgres_by_default_when_deployed(env: pytest.MonkeyPatch, name: str) -> None:
    settings = deployed(env, name)
    assert settings.rate_limit_store is None
    assert settings.rate_limit_backend == "postgres"


@pytest.mark.parametrize("name", ["staging", "prod"])
def test_memory_is_refused_when_deployed(env: pytest.MonkeyPatch, name: str) -> None:
    with pytest.raises(ConfigError, match="RATE_LIMIT_STORE=memory"):
        deployed(env, name, RATE_LIMIT_STORE="memory")


def test_postgres_is_honoured_when_deployed(env: pytest.MonkeyPatch) -> None:
    assert deployed(env, "staging", RATE_LIMIT_STORE="postgres").rate_limit_backend == "postgres"


def test_postgres_may_be_chosen_locally(env: pytest.MonkeyPatch) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("RATE_LIMIT_STORE", "postgres")
    assert load_settings().rate_limit_backend == "postgres"


def test_an_unknown_store_is_refused(env: pytest.MonkeyPatch) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("RATE_LIMIT_STORE", "redis")
    with pytest.raises(Exception, match="rate_limit_store"):
        load_settings()


def test_postgres_without_a_database_refuses_to_start(env: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, rate_limit_store="postgres")  # type: ignore[call-arg]
    with pytest.raises(ConfigError, match="needs a database"):
        create_app(settings)


def test_memory_without_a_database_starts(env: pytest.MonkeyPatch) -> None:
    app = create_app(Settings(_env_file=None, temporal_address=""))  # type: ignore[call-arg]
    assert isinstance(app.state.rate_limiter, InMemoryRateLimiter)


def test_a_deployed_configuration_builds_the_app(env: pytest.MonkeyPatch) -> None:
    """Staging settings (database set, store postgres by default) construct; the limiter is
    installed at startup, when the engine exists."""
    app = create_app(deployed(env, "staging", TEMPORAL_ADDRESS=""))
    assert isinstance(app.state.rate_limiter, InMemoryRateLimiter)  # until the lifespan runs
