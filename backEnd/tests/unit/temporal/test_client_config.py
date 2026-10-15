"""Temporal client construction: Cloud API key implies TLS; dev server uses neither."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from basis.config import Settings, reset_settings
from basis.temporal.client import build_id, client_connect_config


@pytest.fixture
def make_settings(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def _make(**env: str) -> Settings:
        reset_settings()
        monkeypatch.setenv("BASIS_ENV", "test")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return Settings()

    return _make


def test_dev_server_defaults(make_settings) -> None:  # type: ignore[no-untyped-def]
    cfg = client_connect_config(make_settings())
    assert cfg.target_host == "localhost:7233"
    assert cfg.namespace == "default"
    assert cfg.api_key is None
    kwargs = cfg.connect_kwargs()
    assert kwargs["tls"] is False
    assert "api_key" not in kwargs
    assert "data_converter" in kwargs


def test_api_key_implies_tls(make_settings) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(
        TEMPORAL_ADDRESS="basis-staging.abcde.tmprl.cloud:7233",
        TEMPORAL_NAMESPACE="basis-staging.abcde",
        TEMPORAL_API_KEY="fake-temporal-key",  # pragma: allowlist secret
    )
    cfg = client_connect_config(settings)
    assert cfg.tls is True
    kwargs = cfg.connect_kwargs()
    assert kwargs["api_key"] == "fake-temporal-key"  # pragma: allowlist secret
    assert kwargs["tls"] is True


def test_secret_not_in_settings_repr(make_settings) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(TEMPORAL_API_KEY="fake-temporal-key")  # pragma: allowlist secret
    assert isinstance(settings.temporal_api_key, SecretStr)
    assert "fake-temporal-key" not in repr(settings)


def test_build_id_falls_back_to_git_sha(make_settings) -> None:  # type: ignore[no-untyped-def]
    assert build_id(make_settings(GIT_SHA="abc123")) == "abc123"
    assert build_id(make_settings(GIT_SHA="abc123", TEMPORAL_BUILD_ID="build-9")) == "build-9"


def test_new_workflow_settings_defaults(make_settings) -> None:  # type: ignore[no-untyped-def]
    s = make_settings()
    assert s.temporal_task_queue == "basis-studies"
    assert s.photo_concurrency == 5
    assert s.review_reminder_days == 7
    assert s.review_deadline_days == 30
    assert s.study_budget_usd == 25.0
    assert s.relay_poll_seconds == 2.0
    assert s.temporal_worker_versioning is False
