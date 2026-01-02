"""
LLM provider abstraction for OpenAI and Azure OpenAI.

Configured via environment variables:
- Default: OpenAI (OPENAI_API_KEY)
- Azure: Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT_NAME

Azure settings override OpenAI when fully configured.
"""

from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from .settings import get_settings


def is_azure_configured() -> bool:
    """Check if Azure OpenAI is configured."""
    return get_settings().is_azure_configured()


def get_llm(model_override: Optional[str] = None) -> BaseChatModel:
    """
    Get LLM instance based on configuration.

    Priority:
    1. Azure OpenAI if AZURE_OPENAI_* env vars are all set
    2. OpenAI (default)

    Args:
        model_override: Override the default model name

    Returns:
        Configured LLM instance

    Raises:
        ValueError: If no LLM provider is configured
    """
    settings = get_settings()

    if settings.is_azure_configured():
        return AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            deployment_name=model_override or settings.azure_openai_deployment_name,
            api_version=settings.azure_openai_api_version,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )

    if not settings.openai_api_key:
        raise ValueError(
            "No LLM provider configured. Set OPENAI_API_KEY or "
            "AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_DEPLOYMENT_NAME"
        )

    return ChatOpenAI(
        api_key=settings.openai_api_key,
        model=model_override or settings.openai_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )


# Stage-specific model configuration
# Vision-capable models for image-based stages
STAGE_MODELS: dict[str, str] = {
    "room_classification": "gpt-4o",  # Vision capable
    "object_classification": "gpt-4o",  # Vision capable
    "asset_classification": "gpt-4o",  # Complex reasoning
    "takeoff_analysis": "gpt-4o-mini",  # Simpler extraction
    "cost_estimation": "gpt-4o-mini",  # Lookup-based
}


def get_llm_for_stage(stage: str) -> BaseChatModel:
    """
    Get the appropriate LLM for a workflow stage.

    Args:
        stage: The workflow stage name

    Returns:
        Configured LLM for that stage
    """
    model = STAGE_MODELS.get(stage)
    return get_llm(model_override=model)
