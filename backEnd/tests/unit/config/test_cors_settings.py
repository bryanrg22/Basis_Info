"""CORS_ALLOWED_ORIGINS (docs/deploy/STAGING.md, frontend section).

* Read from the environment as a comma-separated list; blanks and surrounding spaces are
  dropped; unset is the empty tuple (no CORS).
* Every entry is an origin (scheme://host[:port]); a path, a trailing slash or a bare host
  is refused at startup.
* Outside dev and test, "*" and plain-http origins other than localhost are refused.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from basis.config import ConfigError, Settings, load_settings, reset_settings

DEPLOYED = {
    # pragma: allowlist nextline secret
    "DATABASE_URL": "postgresql+asyncpg://basis:db-password-123@db/basis",
    "GCS_BUCKET": "basis-staging-files",
    "FIREBASE_PROJECT_ID": "basis-staging",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    reset_settings()
    yield monkeypatch
    reset_settings()


def deployed(env: pytest.MonkeyPatch, origins: str) -> Settings:
    env.setenv("BASIS_ENV", "staging")
    for name, value in DEPLOYED.items():
        env.setenv(name, value)
    env.setenv("CORS_ALLOWED_ORIGINS", origins)
    return load_settings()


def test_unset_means_no_origins(env: pytest.MonkeyPatch) -> None:
    env.setenv("BASIS_ENV", "test")
    assert load_settings().cors_allowed_origins == ()


def test_comma_separated_list_is_split_and_trimmed(env: pytest.MonkeyPatch) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("CORS_ALLOWED_ORIGINS", " https://cost-seg-basis.web.app , http://localhost:3000,, ")
    assert load_settings().cors_allowed_origins == (
        "https://cost-seg-basis.web.app",
        "http://localhost:3000",
    )


def test_blank_value_means_no_origins(env: pytest.MonkeyPatch) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("CORS_ALLOWED_ORIGINS", "  ")
    assert load_settings().cors_allowed_origins == ()


@pytest.mark.parametrize(
    "origin",
    [
        "https://cost-seg-basis.web.app/",
        "https://cost-seg-basis.web.app/app",
        "cost-seg-basis.web.app",
        "ftp://cost-seg-basis.web.app",
        "https://",
        "https://-bad.example",
        "https://host:port",
    ],
)
def test_entries_that_are_not_origins_are_refused(env: pytest.MonkeyPatch, origin: str) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("CORS_ALLOWED_ORIGINS", origin)
    with pytest.raises(ConfigError, match="is not an origin"):
        load_settings()


def test_wildcard_is_allowed_in_dev_and_test(env: pytest.MonkeyPatch) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("CORS_ALLOWED_ORIGINS", "*")
    assert load_settings().cors_allowed_origins == ("*",)


def test_wildcard_is_refused_in_staging(env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match=r"CORS_ALLOWED_ORIGINS=\* is for dev and test only"):
        deployed(env, "https://cost-seg-basis.web.app,*")


def test_plain_http_is_refused_in_staging_except_localhost(env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="plain http"):
        deployed(env, "http://cost-seg-basis.web.app")


def test_staging_accepts_https_and_local_http(env: pytest.MonkeyPatch) -> None:
    settings = deployed(
        env, "https://cost-seg-basis.web.app,http://localhost:3000,http://127.0.0.1:3000"
    )
    assert settings.cors_allowed_origins == (
        "https://cost-seg-basis.web.app",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )


def test_localhost_lookalike_is_refused_in_staging(env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="plain http"):
        deployed(env, "http://localhost.evil.example")


# --------------------------------------------------------------------------- LOCAL_UPLOAD_BASE_URL


def test_local_upload_base_url_is_read_in_dev_and_test(env: pytest.MonkeyPatch) -> None:
    env.setenv("BASIS_ENV", "dev")
    env.setenv("LOCAL_UPLOAD_BASE_URL", "http://127.0.0.1:8000")
    assert load_settings().local_upload_base_url == "http://127.0.0.1:8000"


def test_local_upload_base_url_is_refused_when_deployed(env: pytest.MonkeyPatch) -> None:
    env.setenv("LOCAL_UPLOAD_BASE_URL", "http://127.0.0.1:8000")
    with pytest.raises(ConfigError, match="LOCAL_UPLOAD_BASE_URL is for local stacks"):
        deployed(env, "")


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/", "127.0.0.1:8000", "http://api/v1"])
def test_local_upload_base_url_must_be_a_base_url(env: pytest.MonkeyPatch, url: str) -> None:
    env.setenv("BASIS_ENV", "dev")
    env.setenv("LOCAL_UPLOAD_BASE_URL", url)
    with pytest.raises(ConfigError, match="is not a base URL"):
        load_settings()
