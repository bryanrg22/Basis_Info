"""Model-provider settings read the legacy ``backEnd/.env`` names unchanged.

The ``.env`` written here is synthetic (fake keys); the real one is never read in tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from basis.config import load_settings, reset_settings

PROVIDER_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "TEXT_MODEL",
    "VISION_MODEL",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_DEPLOYMENT_NAME",
    "AZURE_OPENAI_VISION_DEPLOYMENT",
    "AZURE_OPENAI_NANO_DEPLOYMENT",
    "AZURE_OPENAI_TEXT_DEPLOYMENT",
    "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
    "AZURE_DOCUMENT_INTELLIGENCE_KEY",
    "LLM_REQUEST_TIMEOUT",
    "LLM_REQUEST_TIMEOUT_SECONDS",
    "LLM_PRIMARY",
    "EXTRACTION_REVIEW_THRESHOLD",
)

# pragma: allowlist nextline secret
LEGACY_ENV = """\
OPENAI_API_KEY=sk-fake-for-tests
OPENAI_MODEL=gpt-4.1-mini
AZURE_OPENAI_ENDPOINT=https://example.openai.azure.com
AZURE_OPENAI_API_KEY=fake-azure-key
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o-mini
AZURE_OPENAI_VISION_DEPLOYMENT=vision-deployment
AZURE_OPENAI_NANO_DEPLOYMENT=nano-deployment
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://example.cognitiveservices.azure.com
AZURE_DOCUMENT_INTELLIGENCE_KEY=fake-di-key
LLM_REQUEST_TIMEOUT=45
LANGCHAIN_TRACING_V2=true
REPLICATE_API_TOKEN=r8_unused
"""


@pytest.fixture
def clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    for name in PROVIDER_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    reset_settings()
    yield monkeypatch
    reset_settings()


def test_defaults_without_credentials(clean: pytest.MonkeyPatch) -> None:
    clean.setenv("BASIS_ENV", "test")
    settings = load_settings()
    assert settings.openai_api_key is None and settings.azure_openai_endpoint is None
    assert (settings.text_model, settings.vision_model) == ("gpt-4o-mini", "gpt-4o-mini")
    assert settings.llm_request_timeout_seconds == 60
    assert settings.extraction_review_threshold == 0.90
    assert settings.azure_openai_api_version == "2024-10-21"
    assert settings.llm_primary == "azure"


def test_the_legacy_env_file_works_unchanged_in_dev(
    clean: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(LEGACY_ENV, encoding="utf-8")
    clean.setenv("BASIS_ENV", "dev")
    settings = load_settings()
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-fake-for-tests"
    assert settings.text_model == "gpt-4.1-mini"  # from OPENAI_MODEL
    assert settings.azure_openai_endpoint == "https://example.openai.azure.com"
    assert settings.azure_openai_deployment_name == "gpt-4o-mini"
    assert settings.azure_openai_vision_deployment == "vision-deployment"
    assert settings.azure_openai_text_deployment == "nano-deployment"  # from ..._NANO_...
    assert settings.azure_document_intelligence_key is not None
    assert settings.llm_request_timeout_seconds == 45  # from LLM_REQUEST_TIMEOUT
    assert "fake-azure-key" not in repr(settings)  # secrets stay masked


def test_new_names_are_accepted_too(clean: pytest.MonkeyPatch) -> None:
    clean.setenv("BASIS_ENV", "test")
    clean.setenv("TEXT_MODEL", "gpt-5-mini")
    clean.setenv("VISION_MODEL", "gpt-4o")
    clean.setenv("AZURE_OPENAI_TEXT_DEPLOYMENT", "text-dep")
    clean.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "30")
    clean.setenv("EXTRACTION_REVIEW_THRESHOLD", "0.85")
    settings = load_settings()
    assert (settings.text_model, settings.vision_model) == ("gpt-5-mini", "gpt-4o")
    assert settings.azure_openai_text_deployment == "text-dep"
    assert settings.llm_request_timeout_seconds == 30
    assert settings.extraction_review_threshold == 0.85


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("EXTRACTION_REVIEW_THRESHOLD", "1.5"),
        ("LLM_REQUEST_TIMEOUT_SECONDS", "0"),
        ("LLM_PRIMARY", "anthropic"),
    ],
)
def test_invalid_values_are_refused(clean: pytest.MonkeyPatch, name: str, value: str) -> None:
    clean.setenv("BASIS_ENV", "test")
    clean.setenv(name, value)
    with pytest.raises(ValueError):
        load_settings()
