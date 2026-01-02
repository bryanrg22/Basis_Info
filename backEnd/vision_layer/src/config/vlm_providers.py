"""
VLM provider abstraction for OpenAI and Azure OpenAI Vision.

Configured via environment variables:
- Default: OpenAI (OPENAI_API_KEY)
- Azure: Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT_NAME

Azure settings override OpenAI when fully configured.
"""

from typing import Optional

from openai import AsyncAzureOpenAI, AsyncOpenAI

from .settings import get_settings


def is_azure_configured() -> bool:
    """Check if Azure OpenAI is configured."""
    return get_settings().is_azure_configured()


def get_openai_client(
    api_key_override: Optional[str] = None,
) -> AsyncOpenAI:
    """
    Get AsyncOpenAI client for direct API usage.

    Args:
        api_key_override: Override the API key from settings

    Returns:
        Configured AsyncOpenAI client

    Raises:
        ValueError: If no API key is available
    """
    settings = get_settings()
    api_key = api_key_override or settings.openai_api_key

    if not api_key:
        raise ValueError(
            "OpenAI API key not configured. Set OPENAI_API_KEY environment variable."
        )

    return AsyncOpenAI(api_key=api_key)


def get_azure_openai_client(
    api_key_override: Optional[str] = None,
    endpoint_override: Optional[str] = None,
) -> AsyncAzureOpenAI:
    """
    Get AsyncAzureOpenAI client for Azure API usage.

    Args:
        api_key_override: Override the API key from settings
        endpoint_override: Override the endpoint from settings

    Returns:
        Configured AsyncAzureOpenAI client

    Raises:
        ValueError: If Azure is not fully configured
    """
    settings = get_settings()

    if not settings.is_azure_configured() and not (api_key_override and endpoint_override):
        raise ValueError(
            "Azure OpenAI not configured. Set AZURE_OPENAI_ENDPOINT, "
            "AZURE_OPENAI_API_KEY, and AZURE_OPENAI_DEPLOYMENT_NAME."
        )

    return AsyncAzureOpenAI(
        api_key=api_key_override or settings.azure_openai_api_key,
        azure_endpoint=endpoint_override or settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
    )


def get_vlm_client(
    api_key_override: Optional[str] = None,
) -> tuple[AsyncOpenAI | AsyncAzureOpenAI, str, bool]:
    """
    Get the appropriate VLM client based on configuration.

    Priority:
    1. Azure OpenAI if AZURE_OPENAI_* env vars are all set
    2. OpenAI (default)

    Args:
        api_key_override: Override the API key from settings

    Returns:
        Tuple of (client, model_name, is_azure)
        - client: AsyncOpenAI or AsyncAzureOpenAI instance
        - model_name: The model/deployment name to use
        - is_azure: Whether using Azure (affects API call format)

    Raises:
        ValueError: If no VLM provider is configured
    """
    settings = get_settings()

    if settings.is_azure_configured():
        client = AsyncAzureOpenAI(
            api_key=api_key_override or settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
        )
        return (client, settings.azure_openai_deployment_name, True)

    if not settings.openai_api_key and not api_key_override:
        raise ValueError(
            "No VLM provider configured. Set OPENAI_API_KEY or "
            "AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_DEPLOYMENT_NAME"
        )

    client = AsyncOpenAI(api_key=api_key_override or settings.openai_api_key)
    return (client, settings.openai_model, False)
