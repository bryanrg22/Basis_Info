"""
LLM provider abstraction for OpenAI and Azure OpenAI.

Configured via environment variables:
- Default: OpenAI (OPENAI_API_KEY)
- Azure: Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, plus deployment names

Azure settings override OpenAI when fully configured.

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

from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from .settings import get_settings


def is_azure_configured() -> bool:
    """Check if Azure OpenAI is configured."""
    return get_settings().is_azure_configured()


def get_llm(
    model_override: Optional[str] = None,
    use_vision_model: bool = False,
) -> BaseChatModel:
    """
    Get LLM instance based on configuration.

    Priority:
    1. Azure OpenAI if AZURE_OPENAI_* env vars are all set
    2. OpenAI (default)

    Args:
        model_override: Override the default model name
        use_vision_model: If True, use GPT-4o for vision tasks

    Returns:
        Configured LLM instance

    Raises:
        ValueError: If no LLM provider is configured
    """
    settings = get_settings()

    if settings.is_azure_configured():
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
            elif "mini" in model_override:
                deployment = settings.azure_openai_mini_deployment or deployment
            elif "5.2" in model_override or "5-2" in model_override:
                deployment = settings.azure_openai_vision_deployment or deployment
            elif "4o" in model_override and "mini" not in model_override:
                deployment = settings.azure_openai_vision_deployment or deployment

        # GPT-5-nano only supports temperature=1.0
        temperature = 1.0 if "nano" in (deployment or "") else settings.llm_temperature

        return AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            deployment_name=deployment,
            api_version=settings.azure_openai_api_version,
            temperature=temperature,
            max_tokens=settings.llm_max_tokens,
        )

    if not settings.openai_api_key:
        raise ValueError(
            "No LLM provider configured. Set OPENAI_API_KEY or "
            "AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_DEPLOYMENT_NAME"
        )

    # Default model selection for OpenAI
    if model_override:
        model = model_override
    elif use_vision_model:
        model = "gpt-4o-mini"  # Cheap vision model for testing (swap to gpt-5.2 when approved)
    else:
        model = "gpt-5-nano"  # Cheapest text model (no registration required)

    # GPT-5-nano only supports temperature=1.0
    temperature = 1.0 if "nano" in model else settings.llm_temperature

    return ChatOpenAI(
        api_key=settings.openai_api_key,
        model=model,
        temperature=temperature,
        max_tokens=settings.llm_max_tokens,
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
    # Appraisal extraction agents - use GPT-5-nano for all text-based stages
    "appraisal_extraction": "gpt-5-nano",
    "appraisal_verification": "gpt-5-nano",
    "appraisal_correction": "gpt-5-nano",
}

# Stages that require vision capability (GPT-5.2)
VISION_STAGES = {"vision_analysis", "image_analysis"}


def get_llm_for_stage(stage: str) -> BaseChatModel:
    """
    Get the appropriate LLM for a workflow stage.

    Args:
        stage: The workflow stage name

    Returns:
        Configured LLM for that stage (GPT-5.2 for vision, GPT-5-nano for text)
    """
    use_vision = stage in VISION_STAGES
    model = STAGE_MODELS.get(stage, "gpt-5-nano")
    return get_llm(model_override=model, use_vision_model=use_vision)


def get_vision_llm() -> BaseChatModel:
    """Get LLM for vision tasks (GPT-4o-mini for testing, swap to GPT-5.2 when Azure approved)."""
    return get_llm(model_override="gpt-4o-mini", use_vision_model=True)


def get_text_llm() -> BaseChatModel:
    """Get LLM for text tasks (GPT-5-nano)."""
    return get_llm(model_override="gpt-5-nano", use_vision_model=False)
