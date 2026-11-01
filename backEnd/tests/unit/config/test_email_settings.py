"""APP_URL, EMAIL_PROVIDER, EMAIL_FROM and SMTP_* (docs/api/FIRMS.md section 6).

* ``APP_URL`` is a URL with an optional path; a trailing slash is dropped; a query, a
  fragment or a bare host is refused; outside dev and test plain http is refused except
  localhost.
* ``EMAIL_FROM`` is ``Name <address>`` or an address; blank means unset.
* ``EMAIL_PROVIDER=smtp`` needs ``SMTP_HOST`` and ``EMAIL_FROM``; ``log`` (the default)
  needs nothing.
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
NAMES = ("APP_URL", "EMAIL_PROVIDER", "EMAIL_FROM", "SMTP_HOST", "SMTP_PORT")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    reset_settings()
    yield monkeypatch
    reset_settings()


def staging(env: pytest.MonkeyPatch, **values: str) -> Settings:
    env.setenv("BASIS_ENV", "staging")
    for name, value in {**DEPLOYED, **values}.items():
        env.setenv(name, value)
    return load_settings()


def test_defaults_are_log_and_no_app_url(env: pytest.MonkeyPatch) -> None:
    env.setenv("BASIS_ENV", "test")
    settings = load_settings()
    assert (settings.email_provider, settings.email_from, settings.app_url) == ("log", None, None)
    assert (settings.smtp_port, settings.smtp_starttls) == (587, True)


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("https://cost-seg-basis.web.app", "https://cost-seg-basis.web.app"),
        ("https://cost-seg-basis.web.app/", "https://cost-seg-basis.web.app"),
        (" https://example.test/app/ ", "https://example.test/app"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("", None),
    ],
)
def test_app_url_is_trimmed_of_its_trailing_slash(
    env: pytest.MonkeyPatch, given: str, stored: str | None
) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("APP_URL", given)
    assert load_settings().app_url == stored


@pytest.mark.parametrize(
    "bad", ["example.test", "https://example.test/?x=1", "https://example.test/#join", "ftp://x.y"]
)
def test_app_url_must_be_a_plain_url(env: pytest.MonkeyPatch, bad: str) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("APP_URL", bad)
    with pytest.raises(ConfigError, match="APP_URL"):
        load_settings()


def test_app_url_is_https_when_deployed_except_localhost(env: pytest.MonkeyPatch) -> None:
    assert staging(env, APP_URL="https://app.example.test").app_url == "https://app.example.test"
    reset_settings()
    assert staging(env, APP_URL="http://localhost:3000/app").app_url == "http://localhost:3000/app"
    reset_settings()
    with pytest.raises(ConfigError, match="plain http"):
        staging(env, APP_URL="http://app.example.test")


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("no-reply@basis.example", "no-reply@basis.example"),
        ("Basis <no-reply@basis.example>", "Basis <no-reply@basis.example>"),
        ("  ", None),
    ],
)
def test_email_from_forms(env: pytest.MonkeyPatch, given: str, stored: str | None) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("EMAIL_FROM", given)
    assert load_settings().email_from == stored


@pytest.mark.parametrize("bad", ["Basis", "Basis <no-reply>", "a@b.test\nBcc: c@d.test"])
def test_email_from_must_be_an_address(env: pytest.MonkeyPatch, bad: str) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("EMAIL_FROM", bad)
    with pytest.raises(ConfigError, match="EMAIL_FROM"):
        load_settings()


def test_smtp_needs_a_host_and_a_from_address(env: pytest.MonkeyPatch) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("EMAIL_PROVIDER", "smtp")
    with pytest.raises(ConfigError, match="SMTP_HOST, EMAIL_FROM"):
        load_settings()
    env.setenv("SMTP_HOST", "smtp.example.test")
    reset_settings()
    with pytest.raises(ConfigError, match="EMAIL_FROM"):
        load_settings()
    env.setenv("EMAIL_FROM", "no-reply@basis.example")
    reset_settings()
    settings = load_settings()
    assert (settings.email_provider, settings.smtp_host) == ("smtp", "smtp.example.test")


def test_an_unknown_provider_is_refused(env: pytest.MonkeyPatch) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("EMAIL_PROVIDER", "carrier-pigeon")
    with pytest.raises(Exception, match="email_provider"):
        load_settings()


def test_the_password_is_a_secret(env: pytest.MonkeyPatch) -> None:
    env.setenv("BASIS_ENV", "test")
    env.setenv("SMTP_PASSWORD", "hunter2")  # pragma: allowlist secret
    settings = load_settings()
    assert "hunter2" not in repr(settings) and "hunter2" not in str(settings)
    assert settings.smtp_password is not None
    assert settings.smtp_password.get_secret_value() == "hunter2"  # pragma: allowlist secret
