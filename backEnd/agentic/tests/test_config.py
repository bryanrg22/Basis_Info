"""Tests for configuration module."""

import os
from unittest.mock import patch

import pytest

from agentic.config.settings import Settings, get_settings
from agentic.config.llm_providers import get_llm, is_azure_configured


class TestSettings:
    """Tests for Settings class."""

    def test_default_values(self):
        """Test default setting values."""
        # Note: pydantic-settings loads from .env file, so we test actual defaults
        # rather than trying to clear all config
        settings = Settings()
        assert settings.langchain_project == "basis-agentic"
        assert settings.llm_temperature == 0.0

    def test_openai_configured(self):
        """Test OpenAI configuration detection."""
        # Clear Azure keys to test OpenAI-only config
        env_overrides = {
            "OPENAI_API_KEY": "sk-test",
            "AZURE_OPENAI_ENDPOINT": "",
            "AZURE_OPENAI_API_KEY": "",
            "AZURE_OPENAI_DEPLOYMENT_NAME": "",
        }
        with patch.dict(os.environ, env_overrides):
            settings = Settings()
            assert settings.openai_api_key == "sk-test"
            assert not settings.is_azure_configured()

    def test_azure_configured(self):
        """Test Azure configuration detection."""
        env = {
            "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com",
            "AZURE_OPENAI_API_KEY": "azure-key",
            "AZURE_OPENAI_DEPLOYMENT_NAME": "gpt-4o",
        }
        with patch.dict(os.environ, env):
            settings = Settings()
            assert settings.is_azure_configured()

    def test_langsmith_configured(self):
        """Test LangSmith configuration detection."""
        with patch.dict(os.environ, {"LANGCHAIN_API_KEY": "ls-test"}):
            settings = Settings()
            assert settings.is_langsmith_configured()


class TestLLMProviders:
    """Tests for LLM provider functions."""

    def test_is_azure_configured_false(self):
        """Test Azure not configured when keys are empty."""
        # Explicitly clear Azure keys (pydantic-settings loads from .env)
        env_overrides = {
            "AZURE_OPENAI_ENDPOINT": "",
            "AZURE_OPENAI_API_KEY": "",
            "AZURE_OPENAI_DEPLOYMENT_NAME": "",
            "OPENAI_API_KEY": "",
        }
        with patch.dict(os.environ, env_overrides):
            get_settings.cache_clear()
            assert not is_azure_configured()

    def test_get_llm_raises_without_config(self):
        """Test error when no LLM configured."""
        # Explicitly clear all LLM keys
        env_overrides = {
            "AZURE_OPENAI_ENDPOINT": "",
            "AZURE_OPENAI_API_KEY": "",
            "AZURE_OPENAI_DEPLOYMENT_NAME": "",
            "OPENAI_API_KEY": "",
        }
        with patch.dict(os.environ, env_overrides):
            get_settings.cache_clear()
            with pytest.raises(ValueError, match="No LLM provider configured"):
                get_llm()
