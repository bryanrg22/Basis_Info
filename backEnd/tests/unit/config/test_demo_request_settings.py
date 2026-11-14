"""The demo form's settings (docs/api/DEMO_REQUESTS.md section 7).

* DEMO_REQUEST_ALLOWED_ORIGINS: unset or empty is the local static server
  (http://127.0.0.1:8752, http://localhost:8752) in dev and test and nothing in staging and
  prod; a comma-separated list replaces it; each entry is an origin; outside dev and test
  "*" and plain http other than localhost are refused at startup, as for
  CORS_ALLOWED_ORIGINS.
* DEMO_REQUEST_NOTIFY_EMAIL: unset by default (no address in the code); one bare address,
  trimmed; a display name, a list or a non-address is refused at startup; blank is unset.
* DEMO_REQUESTS_PER_HOUR (10) and DEMO_REQUESTS_RETENTION_DAYS (365) and their bounds.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from basis.config import LOCAL_SITE_ORIGINS, ConfigError, Settings, load_settings, reset_settings

DEPLOYED = {
    # pragma: allowlist nextline secret
    "DATABASE_URL": "postgresql+asyncpg://basis:db-password-123@db/basis",
    "GCS_BUCKET": "basis-staging-files",
    "FIREBASE_PROJECT_ID": "basis-staging",
}
NAMES = (
    "DEMO_REQUEST_ALLOWED_ORIGINS",
    "DEMO_REQUEST_NOTIFY_EMAIL",
    "DEMO_REQUESTS_PER_HOUR",
    "DEMO_REQUESTS_RETENTION_DAYS",
    "CORS_ALLOWED_ORIGINS",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    reset_settings()
    yield monkeypatch
    reset_settings()


def in_env(env: pytest.MonkeyPatch, basis_env: str, **values: str) -> Settings:
    env.setenv("BASIS_ENV", basis_env)
    if basis_env in ("staging", "prod"):
        for name, value in DEPLOYED.items():
            env.setenv(name, value)
    for name, value in values.items():
        env.setenv(name, value)
    return load_settings(env_file="/nonexistent/.env")


@pytest.mark.parametrize("basis_env", ["dev", "test"])
def test_unset_is_the_local_site_in_dev_and_test(env: pytest.MonkeyPatch, basis_env: str) -> None:
    settings = in_env(env, basis_env)
    assert settings.demo_request_allowed_origins is None
    assert settings.demo_request_origins == LOCAL_SITE_ORIGINS
    assert LOCAL_SITE_ORIGINS == ("http://127.0.0.1:8752", "http://localhost:8752")
    # The empty line .env.example ships is unset too.
    assert in_env(env, basis_env, DEMO_REQUEST_ALLOWED_ORIGINS=" ").demo_request_origins == (
        LOCAL_SITE_ORIGINS
    )


@pytest.mark.parametrize("basis_env", ["staging", "prod"])
def test_unset_is_nothing_when_deployed(env: pytest.MonkeyPatch, basis_env: str) -> None:
    assert in_env(env, basis_env).demo_request_origins == ()
    site = in_env(env, basis_env, DEMO_REQUEST_ALLOWED_ORIGINS="https://basis.example")
    assert site.demo_request_origins == ("https://basis.example",)


def test_a_list_replaces_the_default(env: pytest.MonkeyPatch) -> None:
    settings = in_env(
        env, "dev", DEMO_REQUEST_ALLOWED_ORIGINS=" https://basis.example , http://127.0.0.1:9000,, "
    )
    assert settings.demo_request_origins == ("https://basis.example", "http://127.0.0.1:9000")


@pytest.mark.parametrize(
    "origin", ["https://basis.example/", "https://basis.example/demo", "basis.example", "ftp://x"]
)
def test_an_entry_that_is_not_an_origin_is_refused(env: pytest.MonkeyPatch, origin: str) -> None:
    with pytest.raises(ConfigError, match="DEMO_REQUEST_ALLOWED_ORIGINS entry"):
        in_env(env, "test", DEMO_REQUEST_ALLOWED_ORIGINS=origin)


def test_deployed_refuses_a_wildcard_and_plain_http(env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match=r"DEMO_REQUEST_ALLOWED_ORIGINS=\* is for dev"):
        in_env(env, "prod", DEMO_REQUEST_ALLOWED_ORIGINS="*")
    with pytest.raises(ConfigError, match="plain http"):
        in_env(env, "staging", DEMO_REQUEST_ALLOWED_ORIGINS="http://basis.example")
    local = in_env(env, "staging", DEMO_REQUEST_ALLOWED_ORIGINS="http://localhost:8752")
    assert local.demo_request_origins == ("http://localhost:8752",)
    # Dev may say "*" (any page may post the form).
    assert in_env(env, "dev", DEMO_REQUEST_ALLOWED_ORIGINS="*").demo_request_origins == ("*",)


def test_the_notify_email_is_unset_by_default_and_one_bare_address(
    env: pytest.MonkeyPatch,
) -> None:
    assert in_env(env, "prod").demo_request_notify_email is None
    assert in_env(env, "test", DEMO_REQUEST_NOTIFY_EMAIL="  ").demo_request_notify_email is None
    settings = in_env(env, "test", DEMO_REQUEST_NOTIFY_EMAIL=" founder@basis.example ")
    assert settings.demo_request_notify_email == "founder@basis.example"
    for bad in ("Bryan <founder@basis.example>", "a@x.example,b@x.example", "founder"):
        with pytest.raises(ConfigError, match="DEMO_REQUEST_NOTIFY_EMAIL"):
            in_env(env, "test", DEMO_REQUEST_NOTIFY_EMAIL=bad)
    # The shared validator still names ALERT_EMAIL_TO for its own field.
    with pytest.raises(ConfigError, match="ALERT_EMAIL_TO"):
        in_env(env, "test", ALERT_EMAIL_TO="nobody")


def test_the_limits_and_their_bounds(env: pytest.MonkeyPatch) -> None:
    settings = in_env(env, "test")
    assert (settings.demo_requests_per_hour, settings.demo_requests_retention_days) == (10, 365)
    tuned = in_env(env, "test", DEMO_REQUESTS_PER_HOUR="50", DEMO_REQUESTS_RETENTION_DAYS="90")
    assert (tuned.demo_requests_per_hour, tuned.demo_requests_retention_days) == (50, 90)
    for name, value in (
        ("DEMO_REQUESTS_PER_HOUR", "0"),
        ("DEMO_REQUESTS_PER_HOUR", "10001"),
        ("DEMO_REQUESTS_RETENTION_DAYS", "0"),
        ("DEMO_REQUESTS_RETENTION_DAYS", "3651"),
    ):
        with pytest.raises(Exception, match=name.lower()):
            in_env(env, "test", **{name: value})
