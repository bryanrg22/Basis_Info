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
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_EFFORT",
    "VISION_PHOTO_MODE",
    "VISION_REASONING_EFFORT",
    "VISION_MAX_TOKENS",
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
    assert (settings.text_model, settings.vision_model) == ("gpt-4o-mini", "gpt-6.1-sol")
    assert settings.llm_request_timeout_seconds == 60
    assert settings.extraction_review_threshold == 0.90
    assert settings.azure_openai_api_version == "2024-10-21"
    assert settings.llm_primary == "azure"
    assert settings.anthropic_api_key is None and settings.anthropic_effort == "low"
    # EVAL_REPORT section 12.1: gpt-6.1-sol, decomposed, effort low, output budget 6000.
    assert settings.vision_photo_mode == "decomposed"
    assert settings.vision_reasoning_effort == "low"
    assert settings.vision_max_tokens is None
    assert settings.vision_output_budget == 6000


@pytest.mark.parametrize(
    ("model", "budget"),
    [
        ("gpt-6.1-sol", 6000),
        ("gpt-5-mini", 6000),
        ("o4-mini", 6000),
        ("gpt-4.1", 1200),
        ("gpt-4o-mini", 1200),
        ("claude-opus-5-5", 1200),  # the Anthropic provider adds its own thinking headroom
    ],
)
def test_the_output_budget_default_follows_the_model_family(
    clean: pytest.MonkeyPatch, model: str, budget: int
) -> None:
    clean.setenv("BASIS_ENV", "test")
    clean.setenv("VISION_MODEL", model)
    assert load_settings().vision_output_budget == budget


def test_vision_overrides_are_read(clean: pytest.MonkeyPatch) -> None:
    clean.setenv("BASIS_ENV", "test")
    clean.setenv("VISION_MODEL", "claude-opus-5-5")
    clean.setenv("VISION_REASONING_EFFORT", "medium")
    clean.setenv("VISION_MAX_TOKENS", "3000")
    settings = load_settings()
    assert settings.vision_reasoning_effort == "medium"
    assert (settings.vision_max_tokens, settings.vision_output_budget) == (3000, 3000)


def test_explicit_legacy_vision_values_still_work(clean: pytest.MonkeyPatch) -> None:
    """A deployment that pinned the pre-12.1 configuration keeps it exactly."""
    clean.setenv("BASIS_ENV", "test")
    clean.setenv("VISION_MODEL", "gpt-4o-mini")
    clean.setenv("VISION_PHOTO_MODE", "single")
    clean.setenv("VISION_MAX_TOKENS", "1200")
    settings = load_settings()
    assert (settings.vision_model, settings.vision_photo_mode) == ("gpt-4o-mini", "single")
    assert settings.vision_output_budget == 1200
    # An explicit budget wins over the family default, also on a reasoning model.
    clean.setenv("VISION_MODEL", "gpt-6.1-sol")
    assert load_settings().vision_output_budget == 1200


def test_anthropic_settings_are_read(clean: pytest.MonkeyPatch) -> None:
    clean.setenv("BASIS_ENV", "test")
    clean.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-for-tests")  # pragma: allowlist secret
    clean.setenv("ANTHROPIC_EFFORT", "medium")
    clean.setenv("LLM_PRIMARY", "anthropic")
    settings = load_settings()
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-fake-for-tests"
    assert "sk-ant-fake" not in repr(settings)  # secrets stay masked
    assert (settings.anthropic_effort, settings.llm_primary) == ("medium", "anthropic")


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
    clean.setenv("VISION_PHOTO_MODE", "decomposed")
    settings = load_settings()
    assert (settings.text_model, settings.vision_model) == ("gpt-5-mini", "gpt-4o")
    assert settings.azure_openai_text_deployment == "text-dep"
    assert settings.llm_request_timeout_seconds == 30
    assert settings.extraction_review_threshold == 0.85
    assert settings.vision_photo_mode == "decomposed"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("EXTRACTION_REVIEW_THRESHOLD", "1.5"),
        ("LLM_REQUEST_TIMEOUT_SECONDS", "0"),
        ("LLM_PRIMARY", "gemini"),  # anthropic is a valid primary now
        ("ANTHROPIC_EFFORT", "extreme"),
        ("VISION_REASONING_EFFORT", "extreme"),
        ("VISION_MAX_TOKENS", "10"),
        ("VISION_MAX_TOKENS", "100000"),
        ("VISION_PHOTO_MODE", "triple"),
        ("VISION_PHOTO_MODE", "both"),
    ],
)
def test_invalid_values_are_refused(clean: pytest.MonkeyPatch, name: str, value: str) -> None:
    clean.setenv("BASIS_ENV", "test")
    clean.setenv(name, value)
    with pytest.raises(ValueError):
        load_settings()
