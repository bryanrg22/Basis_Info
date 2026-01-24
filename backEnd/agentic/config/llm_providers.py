"""
LLM provider abstraction for OpenAI and Azure OpenAI.

Configured via environment variables:
- Default: OpenAI (OPENAI_API_KEY)
- Azure: Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, plus deployment names

Azure settings override OpenAI when fully configured.
On Azure rate limit (429), automatically falls back to OpenAI.

Current configuration (TESTING - no Azure registration required):
- GPT-4o-mini: Used for vision (cheap, has vision capability)
- GPT-5-nano: Used for all text tasks - cheapest option

TODO: When GPT-5.2 Azure registration is approved, update AZURE_OPENAI_VISION_DEPLOYMENT
to gpt-5.2 for best quality vision.

Pricing comparison (per 1M tokens):
- GPT-5.2: $1.75 input, $14.00 output (best vision, requires Azure registration)
- GPT-5-nano: $0.05 input, $0.40 output (text only, no registration needed)
- GPT-4o-mini: $0.15 input, $0.60 output (has vision, no registration needed)
- GPT-4o: $5.00 input, $15.00 output (good vision, no registration needed)
"""

import logging
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from openai import RateLimitError

from .settings import get_settings

logger = logging.getLogger(__name__)

# Session-wide flag: once Azure rate limits, switch all calls to OpenAI
_use_openai_fallback = False


def reset_fallback_state():
    """Reset the fallback state (call at start of new workflow)."""
    global _use_openai_fallback
    _use_openai_fallback = False


def is_using_fallback() -> bool:
    """Check if currently using OpenAI fallback."""
    return _use_openai_fallback


def _switch_to_openai_fallback():
    """Switch all subsequent calls to OpenAI."""
    global _use_openai_fallback
    if not _use_openai_fallback:
        _use_openai_fallback = True
        logger.warning(
            "Azure OpenAI rate limited - switching ALL subsequent calls to OpenAI for this session"
        )


def is_azure_configured() -> bool:
    """Check if Azure OpenAI is configured."""
    return get_settings().is_azure_configured()


def _get_azure_llm(
    model_override: Optional[str] = None,
    use_vision_model: bool = False,
) -> Optional[AzureChatOpenAI]:
    """Get Azure OpenAI LLM if configured."""
    settings = get_settings()

    if not settings.is_azure_configured():
        return None

    # Determine which Azure deployment to use
    if use_vision_model:
        deployment = settings.azure_openai_vision_deployment or settings.azure_openai_deployment_name
    else:
        # Prefer nano/mini deployment for text tasks (cheaper)
        deployment = settings.azure_openai_nano_deployment or settings.azure_openai_mini_deployment or settings.azure_openai_deployment_name

    # Allow explicit model override
    if model_override:
        if "nano" in model_override:
            deployment = settings.azure_openai_nano_deployment or deployment
        elif "4o-mini" in model_override or "4o_mini" in model_override:
            deployment = settings.azure_openai_vision_deployment or settings.azure_openai_mini_deployment or deployment
        elif "mini" in model_override:
            deployment = settings.azure_openai_mini_deployment or settings.azure_openai_vision_deployment or deployment
        elif "5.2" in model_override or "5-2" in model_override:
            deployment = settings.azure_openai_vision_deployment or deployment
        elif "4o" in model_override:
            deployment = settings.azure_openai_vision_deployment or deployment

    # GPT-5-nano only supports temperature=1.0
    temperature = 1.0 if "nano" in (deployment or "") else settings.llm_temperature
    api_version = settings.azure_openai_api_version or "2024-08-01-preview"

    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        deployment_name=deployment,
        api_version=api_version,
        temperature=temperature,
        max_tokens=settings.llm_max_tokens,
        max_retries=0,  # Disable SDK retries - we handle fallback ourselves
    )


def _get_openai_llm(
    model_override: Optional[str] = None,
    use_vision_model: bool = False,
) -> Optional[ChatOpenAI]:
    """Get OpenAI LLM if configured."""
    settings = get_settings()

    if not settings.openai_api_key:
        return None

    # Model selection for OpenAI
    if model_override:
        model = model_override
    elif use_vision_model:
        model = "gpt-4o-mini"
    else:
        model = "gpt-4o-mini"  # Use gpt-4o-mini as fallback (gpt-5-nano may not be on OpenAI)

    # GPT-5-nano only supports temperature=1.0
    temperature = 1.0 if "nano" in model else settings.llm_temperature

    return ChatOpenAI(
        api_key=settings.openai_api_key,
        model=model,
        temperature=temperature,
        max_tokens=settings.llm_max_tokens,
        max_retries=2,  # OpenAI fallback can retry since it's our backup
    )


def _is_rate_limit_error(e: Exception) -> bool:
    """Check if exception is a rate limit error (handles wrapped exceptions)."""
    if isinstance(e, RateLimitError):
        return True
    # Check for wrapped errors
    error_str = str(e).lower()
    return "429" in error_str or "rate" in error_str and "limit" in error_str


class _RateLimitAwareLLM:
    """
    Wrapper that catches rate limits and switches to fallback for all future calls.
    """

    def __init__(self, primary: BaseChatModel, fallback: BaseChatModel):
        self._primary = primary
        self._fallback = fallback

    def _get_active_llm(self) -> BaseChatModel:
        """Get the currently active LLM based on fallback state."""
        if _use_openai_fallback:
            return self._fallback
        return self._primary

    def __getattr__(self, name):
        """Delegate attribute access to the active LLM."""
        return getattr(self._get_active_llm(), name)

    def invoke(self, *args, **kwargs):
        """Invoke with rate limit detection."""
        if _use_openai_fallback:
            return self._fallback.invoke(*args, **kwargs)
        try:
            return self._primary.invoke(*args, **kwargs)
        except Exception as e:
            if _is_rate_limit_error(e):
                _switch_to_openai_fallback()
                return self._fallback.invoke(*args, **kwargs)
            raise

    async def ainvoke(self, *args, **kwargs):
        """Async invoke with rate limit detection."""
        if _use_openai_fallback:
            logger.debug("Using OpenAI fallback for ainvoke")
            return await self._fallback.ainvoke(*args, **kwargs)
        try:
            logger.debug("Trying Azure for ainvoke")
            return await self._primary.ainvoke(*args, **kwargs)
        except Exception as e:
            logger.debug(f"Azure ainvoke failed: {type(e).__name__}: {e}")
            if _is_rate_limit_error(e):
                _switch_to_openai_fallback()
                return await self._fallback.ainvoke(*args, **kwargs)
            raise

    def bind_tools(self, *args, **kwargs):
        """Bind tools to the active LLM."""
        if _use_openai_fallback:
            return self._fallback.bind_tools(*args, **kwargs)
        # Return a new wrapper with bound tools
        return _RateLimitAwareLLM(
            self._primary.bind_tools(*args, **kwargs),
            self._fallback.bind_tools(*args, **kwargs),
        )


def get_llm(
    model_override: Optional[str] = None,
    use_vision_model: bool = False,
) -> BaseChatModel:
    """
    Get LLM instance with automatic session-wide fallback.

    Priority:
    1. Azure OpenAI (primary)
    2. OpenAI (fallback on rate limit)

    On Azure rate limit (429), ALL subsequent calls switch to OpenAI
    for the rest of the session/workflow.

    Args:
        model_override: Override the default model name
        use_vision_model: If True, use GPT-4o for vision tasks

    Returns:
        Configured LLM instance with fallback

    Raises:
        ValueError: If no LLM provider is configured
    """
    # If already using fallback, go straight to OpenAI
    if _use_openai_fallback:
        openai_llm = _get_openai_llm(model_override, use_vision_model)
        if openai_llm:
            return openai_llm

    azure_llm = _get_azure_llm(model_override, use_vision_model)
    openai_llm = _get_openai_llm(model_override, use_vision_model)

    if azure_llm and openai_llm:
        # Return wrapper that handles rate limits and switches globally
        logger.debug("Using Azure OpenAI with session-wide OpenAI fallback")
        return _RateLimitAwareLLM(azure_llm, openai_llm)
    elif azure_llm:
        return azure_llm
    elif openai_llm:
        return openai_llm
    else:
        raise ValueError(
            "No LLM provider configured. Set OPENAI_API_KEY or "
            "AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_DEPLOYMENT_NAME"
        )


# Stage-specific model configuration
# COST OPTIMIZED: Only vision tasks use GPT-5.2, everything else uses GPT-5-nano
STAGE_MODELS: dict[str, str] = {
    # Text-based stages - use GPT-5-nano (cheapest: $0.05/$0.40 per 1M tokens)
    "room_context": "gpt-5-nano",
    "object_context": "gpt-5-nano",
    "asset_classification": "gpt-5-nano",
    "takeoff": "gpt-5-nano",
    "cost_estimation": "gpt-5-nano",
    # Appraisal extraction agents - use GPT-4o-mini (no reasoning overhead, more reliable)
    "appraisal_extraction": "gpt-4o-mini",
    "appraisal_verification": "gpt-4o-mini",
    "appraisal_correction": "gpt-4o-mini",
}

# Stages that require vision capability (GPT-5.2)
VISION_STAGES = {"vision_analysis", "image_analysis"}

# Stages that should use OpenAI directly (avoid Azure rate limit competition with worker)
# These run on the backend while worker uses Azure for vision
OPENAI_DIRECT_STAGES = {"appraisal_extraction", "appraisal_verification", "appraisal_correction"}


def get_llm_for_stage(stage: str) -> BaseChatModel:
    """
    Get the appropriate LLM for a workflow stage.

    Args:
        stage: The workflow stage name

    Returns:
        Configured LLM for that stage

    Note:
        Appraisal stages use OpenAI directly to avoid rate limit
        competition with worker (which uses Azure for vision).
    """
    model = STAGE_MODELS.get(stage, "gpt-5-nano")

    # Appraisal stages use OpenAI directly (backend)
    # Vision stages use Azure with fallback (worker)
    if stage in OPENAI_DIRECT_STAGES:
        logger.info(f"Stage '{stage}' using OpenAI directly (avoiding Azure rate limit competition)")
        openai_llm = _get_openai_llm(model_override=model, use_vision_model=False)
        if openai_llm:
            return openai_llm
        # Fall back to Azure if OpenAI not configured
        logger.warning(f"OpenAI not configured, falling back to Azure for stage '{stage}'")

    use_vision = stage in VISION_STAGES
    return get_llm(model_override=model, use_vision_model=use_vision)


def get_vision_llm() -> BaseChatModel:
    """Get LLM for vision tasks (GPT-4o-mini for testing, swap to GPT-5.2 when Azure approved)."""
    return get_llm(model_override="gpt-4o-mini", use_vision_model=True)


def get_text_llm() -> BaseChatModel:
    """Get LLM for text tasks (GPT-5-nano)."""
    return get_llm(model_override="gpt-5-nano", use_vision_model=False)


def get_vision_llm_for_provider(provider: str) -> BaseChatModel:
    """
    Get vision LLM for a specific provider (used by HybridVisionPool).

    Args:
        provider: "azure" or "openai"

    Returns:
        Vision-capable LLM for the specified provider

    Raises:
        ValueError: If provider is not configured
    """
    if provider == "azure":
        azure_llm = _get_azure_llm(model_override="gpt-4o-mini", use_vision_model=True)
        if azure_llm:
            return azure_llm
        raise ValueError("Azure OpenAI not configured")
    elif provider == "openai":
        openai_llm = _get_openai_llm(model_override="gpt-4o-mini", use_vision_model=True)
        if openai_llm:
            return openai_llm
        raise ValueError("OpenAI not configured")
    else:
        raise ValueError(f"Unknown provider: {provider}")


def get_available_vision_providers() -> list[str]:
    """
    Get list of available vision providers.

    Returns:
        List of provider names that are configured and available
    """
    providers = []
    settings = get_settings()

    if settings.is_azure_configured():
        providers.append("azure")
    if settings.openai_api_key:
        providers.append("openai")

    return providers


def is_hybrid_vision_available() -> bool:
    """
    Check if both Azure and OpenAI are configured for hybrid vision pool.

    Returns:
        True if both providers are available
    """
    providers = get_available_vision_providers()
    return "azure" in providers and "openai" in providers
