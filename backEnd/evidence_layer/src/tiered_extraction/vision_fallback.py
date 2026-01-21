"""
GPT-4o Vision Fallback Extractor (Tier 3)

Uses Azure OpenAI's GPT-4o vision capabilities to extract
appraisal fields that other tiers couldn't confidently extract.

This is particularly useful for:
- Handwritten fields
- Faded or scanned documents
- Non-standard form layouts
- Fields with low Azure DI confidence
"""

import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .confidence import FieldResult
from .field_mappings import URAR_SECTIONS

logger = logging.getLogger(__name__)


class VisionFallbackExtractor:
    """
    Extracts appraisal fields using GPT-4o vision.

    Converts PDF pages to images and uses multimodal AI
    to extract specific fields that other methods missed.

    Supports both Azure OpenAI and direct OpenAI as fallback.
    """

    def __init__(self):
        # Load from settings (which reads .env) with fallback to os.environ
        try:
            from agentic.config.settings import get_settings
            settings = get_settings()
            self.azure_endpoint = settings.azure_openai_endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
            self.azure_api_key = settings.azure_openai_api_key or os.environ.get("AZURE_OPENAI_API_KEY")
            self.deployment = settings.azure_openai_deployment_name or os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
            # OpenAI direct fallback - load from settings which reads .env
            self.openai_api_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
            self.openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        except ImportError:
            # Fallback if settings module not available
            self.azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
            self.azure_api_key = os.environ.get("AZURE_OPENAI_API_KEY")
            self.deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
            self.openai_api_key = os.environ.get("OPENAI_API_KEY")
            self.openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o")

        self.azure_client = None
        self.openai_client = None
        self._azure_initialized = False
        self._openai_initialized = False
        self._use_openai_fallback = False  # Set to True after Azure rate limit

    def _ensure_azure_client(self) -> bool:
        """Initialize the Azure OpenAI client if not already done."""
        if self._azure_initialized:
            return self.azure_client is not None

        self._azure_initialized = True

        if not self.azure_endpoint or not self.azure_api_key:
            logger.warning(
                "Azure OpenAI credentials not configured. "
                "Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY"
            )
            return False

        try:
            from openai import AzureOpenAI

            self.azure_client = AzureOpenAI(
                azure_endpoint=self.azure_endpoint,
                api_key=self.azure_api_key,
                api_version="2024-02-15-preview"
            )
            return True

        except ImportError:
            logger.warning(
                "openai package not installed. "
                "Run: pip install openai"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Azure OpenAI client: {e}")
            return False

    def _ensure_openai_client(self) -> bool:
        """Initialize the direct OpenAI client as fallback."""
        if self._openai_initialized:
            return self.openai_client is not None

        self._openai_initialized = True

        if not self.openai_api_key:
            logger.warning("OpenAI API key not configured for fallback")
            return False

        try:
            from openai import OpenAI

            self.openai_client = OpenAI(api_key=self.openai_api_key)
            logger.info("OpenAI fallback client initialized")
            return True

        except ImportError:
            logger.warning("openai package not installed")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            return False

    def _ensure_client(self) -> bool:
        """Initialize an available client (Azure preferred, OpenAI as fallback)."""
        if self._use_openai_fallback:
            return self._ensure_openai_client()
        return self._ensure_azure_client() or self._ensure_openai_client()

    async def extract_fields(
        self,
        pdf_path: str,
        missing_fields: List[tuple],
        max_pages: int = 3
    ) -> Dict[str, FieldResult]:
        """
        Extract specific missing fields using GPT-4o vision.

        Args:
            pdf_path: Path to the appraisal PDF
            missing_fields: List of (section, field_name) tuples to extract
            max_pages: Maximum number of pages to process

        Returns:
            Dictionary mapping "section.field_name" to FieldResult
        """
        results: Dict[str, FieldResult] = {}

        if not self._ensure_client():
            logger.warning("Azure OpenAI client not available")
            return results

        try:
            # Convert PDF pages to images
            images = await self._pdf_to_images(pdf_path, max_pages)
            if not images:
                logger.warning("Failed to convert PDF to images")
                return results

            # Build the extraction prompt
            prompt = self._build_extraction_prompt(missing_fields)

            # Call GPT-4o vision
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._call_vision_api(images, prompt)
            )

            if response:
                results = self._parse_response(response, missing_fields)

            return results

        except Exception as e:
            logger.error(f"Vision extraction failed: {e}")
            return results

    async def _pdf_to_images(
        self,
        pdf_path: str,
        max_pages: int
    ) -> List[str]:
        """
        Convert PDF pages to base64-encoded images.

        Args:
            pdf_path: Path to PDF file
            max_pages: Maximum pages to convert

        Returns:
            List of base64-encoded PNG images
        """
        images = []

        try:
            from pdf2image import convert_from_path

            # Convert PDF pages to PIL images
            pil_images = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: convert_from_path(
                    pdf_path,
                    first_page=1,
                    last_page=max_pages,
                    dpi=150  # Balance quality vs. token usage
                )
            )

            # Convert to base64
            import io
            for img in pil_images:
                buffer = io.BytesIO()
                img.save(buffer, format="PNG")
                base64_image = base64.b64encode(buffer.getvalue()).decode("utf-8")
                images.append(base64_image)

        except ImportError:
            logger.warning(
                "pdf2image package not installed. "
                "Run: pip install pdf2image"
            )
            # Fallback: try using PyMuPDF
            images = await self._pdf_to_images_pymupdf(pdf_path, max_pages)

        except Exception as e:
            logger.error(f"PDF to image conversion failed: {e}")

        return images

    async def _pdf_to_images_pymupdf(
        self,
        pdf_path: str,
        max_pages: int
    ) -> List[str]:
        """Fallback PDF conversion using PyMuPDF."""
        images = []

        try:
            import fitz  # PyMuPDF

            doc = fitz.open(pdf_path)
            for page_num in range(min(max_pages, len(doc))):
                page = doc[page_num]
                pix = page.get_pixmap(dpi=150)
                base64_image = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                images.append(base64_image)
            doc.close()

        except ImportError:
            logger.warning("PyMuPDF not available for PDF conversion")
        except Exception as e:
            logger.error(f"PyMuPDF conversion failed: {e}")

        return images

    def _build_extraction_prompt(
        self,
        missing_fields: List[tuple]
    ) -> str:
        """
        Build a structured prompt for field extraction.

        Args:
            missing_fields: List of (section, field_name) tuples

        Returns:
            Prompt string for GPT-4o
        """
        field_list = []
        for section, field_name in missing_fields:
            display_name = field_name.replace("_", " ").title()
            field_list.append(f"- {section}.{field_name}: {display_name}")

        fields_text = "\n".join(field_list)

        prompt = f"""You are analyzing a URAR (Uniform Residential Appraisal Report) form.
Extract the following specific fields from this appraisal document:

{fields_text}

Return your response as a JSON object with this exact structure:
{{
  "section.field_name": {{
    "value": "extracted value or null if not found",
    "confidence": 0.0 to 1.0 based on how certain you are
  }}
}}

Guidelines:
- For currency values, return just the number without $ or commas (e.g., 680000)
- For dates, use MM/DD/YYYY format
- For measurements, include units (e.g., "3200 sq ft")
- Set confidence lower for handwritten, faded, or partially visible values
- Set confidence to 0.0 if the field is not visible in the document
- Return null for value if you cannot find the field

Only return the JSON object, no additional text."""

        return prompt

    def _call_vision_api(
        self,
        images: List[str],
        prompt: str,
        max_retries: int = 2
    ) -> Optional[str]:
        """
        Call vision API with images and prompt.

        Tries Azure OpenAI first, falls back to direct OpenAI on rate limits.

        Args:
            images: List of base64-encoded images
            prompt: Extraction prompt
            max_retries: Maximum retry attempts per provider

        Returns:
            API response content or None
        """
        import time

        # Build message content with images
        content = [{"type": "text", "text": prompt}]

        for i, img_base64 in enumerate(images):
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_base64}",
                    "detail": "high"  # Use high detail for form reading
                }
            })

        # Try Azure OpenAI first (unless we've already switched to fallback)
        if not self._use_openai_fallback and self._ensure_azure_client():
            result = self._try_provider(
                client=self.azure_client,
                model=self.deployment,
                content=content,
                provider_name="Azure OpenAI",
                max_retries=max_retries
            )
            if result is not None:
                return result

            # Azure failed with rate limits, switch to OpenAI fallback
            logger.warning("Azure OpenAI rate limited, switching to OpenAI fallback")
            self._use_openai_fallback = True

        # Try direct OpenAI as fallback
        if self._ensure_openai_client():
            logger.info(f"Using OpenAI fallback with model: {self.openai_model}")
            result = self._try_provider(
                client=self.openai_client,
                model=self.openai_model,
                content=content,
                provider_name="OpenAI",
                max_retries=max_retries
            )
            if result is not None:
                return result

        logger.error("All vision API providers failed")
        return None

    def _try_provider(
        self,
        client: Any,
        model: str,
        content: List[Dict],
        provider_name: str,
        max_retries: int
    ) -> Optional[str]:
        """
        Try to call a specific provider with retries.

        Returns:
            Response content, or None if all retries failed
        """
        import time

        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": content
                        }
                    ],
                    max_tokens=2000,
                    temperature=0.1
                )

                logger.info(f"{provider_name} vision call succeeded")
                return response.choices[0].message.content

            except Exception as e:
                error_str = str(e)

                # Check for rate limit error (429)
                if "429" in error_str or "RateLimitReached" in error_str or "rate" in error_str.lower():
                    wait_time = (2 ** attempt) * 5  # 5s, 10s
                    logger.warning(
                        f"{provider_name} rate limited (attempt {attempt + 1}/{max_retries}), "
                        f"waiting {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    # Non-retryable error
                    logger.error(f"{provider_name} API call failed: {e}")
                    return None

        # Rate limit retries exhausted for this provider
        logger.warning(f"{provider_name} rate limit retries exhausted")
        return None

    def _parse_response(
        self,
        response: str,
        expected_fields: List[tuple]
    ) -> Dict[str, FieldResult]:
        """
        Parse GPT-4o response into FieldResult objects.

        Args:
            response: Raw API response
            expected_fields: List of expected (section, field_name) tuples

        Returns:
            Dictionary mapping "section.field_name" to FieldResult
        """
        results: Dict[str, FieldResult] = {}

        try:
            # Extract JSON from response (handle markdown code blocks)
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find raw JSON
                json_str = response.strip()

            data = json.loads(json_str)

            for key, field_data in data.items():
                if not isinstance(field_data, dict):
                    continue

                value = field_data.get("value")
                confidence = field_data.get("confidence", 0.7)

                # Validate confidence range
                confidence = max(0.0, min(1.0, float(confidence)))

                # Skip null/empty values
                if value is None or value == "":
                    continue

                results[key] = FieldResult(
                    value=self._normalize_extracted_value(value, key),
                    confidence=confidence,
                    source="gpt4o_vision"
                )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse vision response JSON: {e}")
            logger.debug(f"Response was: {response[:500]}...")
        except Exception as e:
            logger.error(f"Error parsing vision response: {e}")

        return results

    def _normalize_extracted_value(self, value: Any, field_key: str) -> Any:
        """Normalize extracted value based on field type."""
        if value is None:
            return value

        field_name = field_key.split(".")[-1] if "." in field_key else field_key

        # Currency fields - convert to integer
        currency_fields = {
            "contract_price", "appraised_value", "site_value",
            "total_cost_new", "real_estate_taxes", "offering_price",
            "final_opinion_of_market_value",
        }

        if field_name in currency_fields:
            if isinstance(value, str):
                # Remove currency symbols and commas
                cleaned = re.sub(r'[$,\s]', '', value)
                try:
                    return int(float(cleaned))
                except ValueError:
                    return value
            return value

        # Integer fields
        integer_fields = {
            "year_built", "tax_year", "days_on_market",
            "gross_living_area", "basement_area_sqft",
        }

        if field_name in integer_fields:
            if isinstance(value, str):
                cleaned = re.sub(r'[^\d.]', '', value)
                try:
                    return int(float(cleaned))
                except ValueError:
                    return value
            return value

        return value

    def is_available(self) -> bool:
        """Check if vision fallback is configured and available."""
        return self._ensure_client()


async def extract_with_vision(
    pdf_path: str,
    missing_fields: List[tuple]
) -> Dict[str, FieldResult]:
    """
    Convenience function to extract fields using vision.

    Args:
        pdf_path: Path to appraisal PDF
        missing_fields: List of (section, field_name) tuples

    Returns:
        Dictionary of extracted fields
    """
    extractor = VisionFallbackExtractor()
    return await extractor.extract_fields(pdf_path, missing_fields)
