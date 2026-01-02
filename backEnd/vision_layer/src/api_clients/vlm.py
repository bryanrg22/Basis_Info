"""OpenAI/Azure OpenAI Vision Language Model (VLM) client for region classification."""

import base64
import json
import logging
from io import BytesIO
from typing import Optional, Union

from openai import AsyncAzureOpenAI, AsyncOpenAI
from PIL import Image
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config.settings import get_settings
from ..config.vlm_providers import is_azure_configured
from ..schemas.artifact import VLMClassification

logger = logging.getLogger(__name__)

# Default prompt for component classification
DEFAULT_CLASSIFICATION_PROMPT = """Analyze this image of a building component and provide a detailed classification.

Identify:
1. Component type (e.g., cabinet, appliance, flooring, lighting fixture, HVAC unit)
2. Material (e.g., wood, stainless steel, ceramic tile, vinyl)
3. Condition (new, good, fair, worn, damaged)
4. Color
5. Installation type (built-in, freestanding, mounted, recessed)
6. Any visible brand or model information
7. Estimated dimensions or size category (small, medium, large)

Respond in JSON format:
{
    "component_type": "...",
    "material": "...",
    "condition": "...",
    "color": "...",
    "installation_type": "...",
    "brand": null or "...",
    "model": null or "...",
    "dimensions_note": "..."
}

Be specific and accurate. If you cannot determine a field with confidence, use null."""


class VLMClient:
    """Client for OpenAI/Azure OpenAI Vision API for component classification.

    Automatically detects and uses Azure OpenAI if configured, otherwise falls back to OpenAI.

    Configuration via environment variables:
    - OpenAI: Set OPENAI_API_KEY
    - Azure: Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT_NAME

    Azure settings override OpenAI when fully configured.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        use_azure: Optional[bool] = None,
        azure_endpoint: Optional[str] = None,
        azure_api_version: Optional[str] = None,
    ):
        """Initialize VLM client.

        Args:
            api_key: API key. Falls back to env vars based on provider.
            model: Model/deployment name. Falls back to settings.
            max_tokens: Maximum tokens in response. Falls back to settings.
            temperature: Sampling temperature. Falls back to settings.
            use_azure: Force Azure (True) or OpenAI (False). Auto-detect if None.
            azure_endpoint: Azure endpoint override.
            azure_api_version: Azure API version override.
        """
        settings = get_settings()

        # Determine provider
        if use_azure is None:
            self._is_azure = is_azure_configured()
        else:
            self._is_azure = use_azure

        # Set model/deployment name
        if model:
            self.model = model
        elif self._is_azure:
            self.model = settings.azure_openai_deployment_name or "gpt-4o"
        else:
            self.model = settings.openai_model

        # Set behavior parameters
        self.max_tokens = max_tokens or settings.vlm_max_tokens
        self.temperature = temperature if temperature is not None else settings.vlm_temperature

        # Initialize client
        if self._is_azure:
            self.client = AsyncAzureOpenAI(
                api_key=api_key or settings.azure_openai_api_key,
                azure_endpoint=azure_endpoint or settings.azure_openai_endpoint,
                api_version=azure_api_version or settings.azure_openai_api_version,
            )
            logger.info(f"VLM client initialized with Azure OpenAI (deployment: {self.model})")
        else:
            key = api_key or settings.openai_api_key
            if not key:
                raise ValueError(
                    "No VLM provider configured. Set OPENAI_API_KEY or "
                    "AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_DEPLOYMENT_NAME"
                )
            self.client = AsyncOpenAI(api_key=key)
            logger.info(f"VLM client initialized with OpenAI (model: {self.model})")

    @property
    def is_azure(self) -> bool:
        """Check if using Azure OpenAI."""
        return self._is_azure

    @property
    def provider_name(self) -> str:
        """Get the provider name for logging/provenance."""
        return "azure-openai" if self._is_azure else "openai"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    async def classify_image_url(
        self,
        image_url: str,
        prompt: Optional[str] = None,
        context: Optional[str] = None,
    ) -> VLMClassification:
        """Classify a component from an image URL.

        Args:
            image_url: URL of image to classify.
            prompt: Custom prompt (uses default if not provided).
            context: Additional context about the scene/room.

        Returns:
            VLMClassification with component details.
        """
        system_prompt = "You are an expert building inspector analyzing property images for cost segregation studies."

        user_prompt = prompt or DEFAULT_CLASSIFICATION_PROMPT
        if context:
            user_prompt = f"Context: {context}\n\n{user_prompt}"

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url, "detail": "high"},
                    },
                    {"type": "text", "text": user_prompt},
                ],
            },
        ]

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )

        raw_response = response.choices[0].message.content
        return self._parse_classification(raw_response)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    async def classify_image_bytes(
        self,
        image_bytes: bytes,
        image_format: str = "jpeg",
        prompt: Optional[str] = None,
        context: Optional[str] = None,
    ) -> VLMClassification:
        """Classify a component from image bytes.

        Args:
            image_bytes: Raw image bytes.
            image_format: Image format (jpeg, png, etc.).
            prompt: Custom prompt.
            context: Additional context.

        Returns:
            VLMClassification with component details.
        """
        # Encode as base64 data URL
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/{image_format};base64,{b64_image}"

        return await self.classify_image_url(
            image_url=data_url,
            prompt=prompt,
            context=context,
        )

    async def classify_pil_image(
        self,
        image: Image.Image,
        prompt: Optional[str] = None,
        context: Optional[str] = None,
    ) -> VLMClassification:
        """Classify a component from a PIL Image.

        Args:
            image: PIL Image object.
            prompt: Custom prompt.
            context: Additional context.

        Returns:
            VLMClassification with component details.
        """
        # Convert to JPEG bytes
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=95)
        image_bytes = buffer.getvalue()

        return await self.classify_image_bytes(
            image_bytes=image_bytes,
            image_format="jpeg",
            prompt=prompt,
            context=context,
        )

    def _parse_classification(self, raw_response: str) -> VLMClassification:
        """Parse VLM response into structured classification.

        Handles both clean JSON and JSON embedded in markdown.
        """
        # Try to extract JSON from response
        json_str = raw_response.strip()

        # Handle markdown code blocks
        if "```json" in json_str:
            start = json_str.find("```json") + 7
            end = json_str.find("```", start)
            json_str = json_str[start:end].strip()
        elif "```" in json_str:
            start = json_str.find("```") + 3
            end = json_str.find("```", start)
            json_str = json_str[start:end].strip()

        # Try to find JSON object
        if not json_str.startswith("{"):
            start = json_str.find("{")
            end = json_str.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = json_str[start:end]

        try:
            data = json.loads(json_str)
            return VLMClassification(
                component_type=data.get("component_type", "unknown"),
                material=data.get("material"),
                condition=data.get("condition"),
                color=data.get("color"),
                brand=data.get("brand"),
                model=data.get("model"),
                dimensions_note=data.get("dimensions_note"),
                installation_type=data.get("installation_type"),
                additional_attributes={
                    k: v
                    for k, v in data.items()
                    if k
                    not in [
                        "component_type",
                        "material",
                        "condition",
                        "color",
                        "brand",
                        "model",
                        "dimensions_note",
                        "installation_type",
                    ]
                },
                raw_response=raw_response,
            )
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse VLM JSON response: {e}")
            # Return minimal classification from raw text
            return VLMClassification(
                component_type="unknown",
                raw_response=raw_response,
            )

    async def classify_batch(
        self,
        images: list[Union[str, bytes, Image.Image]],
        prompts: Optional[list[str]] = None,
        contexts: Optional[list[str]] = None,
        max_concurrent: int = 5,
    ) -> list[VLMClassification]:
        """Classify multiple images concurrently.

        Args:
            images: List of image URLs, bytes, or PIL Images.
            prompts: Optional list of prompts (one per image).
            contexts: Optional list of contexts (one per image).
            max_concurrent: Maximum concurrent API calls.

        Returns:
            List of VLMClassification objects.
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)

        async def classify_one(
            idx: int,
            image: Union[str, bytes, Image.Image],
        ) -> VLMClassification:
            async with semaphore:
                prompt = prompts[idx] if prompts else None
                context = contexts[idx] if contexts else None

                if isinstance(image, str):
                    return await self.classify_image_url(image, prompt, context)
                elif isinstance(image, bytes):
                    return await self.classify_image_bytes(image, "jpeg", prompt, context)
                elif isinstance(image, Image.Image):
                    return await self.classify_pil_image(image, prompt, context)
                else:
                    raise ValueError(f"Unsupported image type: {type(image)}")

        tasks = [classify_one(i, img) for i, img in enumerate(images)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        classifications = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Classification failed for image {i}: {result}")
                classifications.append(
                    VLMClassification(
                        component_type="error",
                        raw_response=str(result),
                    )
                )
            else:
                classifications.append(result)

        return classifications
