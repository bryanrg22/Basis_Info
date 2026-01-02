"""Grounding DINO API client via Replicate."""

import logging
from typing import Optional

import replicate
from tenacity import retry, stop_after_attempt, wait_exponential

from ..schemas.detection import BoundingBox, Detection

logger = logging.getLogger(__name__)

# Replicate model versions
GROUNDING_DINO_MODEL = "adirik/grounding-dino:efd10a8ddc57ea28773327e881ce95e20cc1d734c589f7dd01d2036921ed78aa"


class GroundingDINOClient:
    """Client for Grounding DINO object detection via Replicate.

    Grounding DINO is an open-vocabulary object detection model that
    detects objects based on text prompts.
    """

    def __init__(
        self,
        api_token: Optional[str] = None,
        model_version: str = GROUNDING_DINO_MODEL,
        box_threshold: float = 0.3,
        text_threshold: float = 0.25,
    ):
        """Initialize Grounding DINO client.

        Args:
            api_token: Replicate API token. Falls back to REPLICATE_API_TOKEN env var.
            model_version: Replicate model version string.
            box_threshold: Minimum confidence for box detection.
            text_threshold: Minimum confidence for text-box association.
        """
        self.model_version = model_version
        self.box_threshold = box_threshold
        self.text_threshold = text_threshold

        # Replicate client uses REPLICATE_API_TOKEN env var by default
        if api_token:
            import os
            os.environ["REPLICATE_API_TOKEN"] = api_token

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    async def detect(
        self,
        image_url: str,
        prompts: list[str],
        image_id: Optional[str] = None,
        box_threshold: Optional[float] = None,
        text_threshold: Optional[float] = None,
    ) -> list[Detection]:
        """Detect objects in image based on text prompts.

        Args:
            image_url: URL of image to process (must be publicly accessible).
            prompts: List of object classes to detect (e.g., ["cabinet", "appliance"]).
            image_id: Optional identifier for the source image.
            box_threshold: Override default box confidence threshold.
            text_threshold: Override default text confidence threshold.

        Returns:
            List of Detection objects with bounding boxes and labels.
        """
        # Combine prompts into single query string
        prompt_str = " . ".join(prompts)

        logger.info(f"Running Grounding DINO on {image_url} with prompts: {prompts}")

        # Run Replicate prediction
        output = await replicate.async_run(
            self.model_version,
            input={
                "image": image_url,
                "query": prompt_str,
                "box_threshold": box_threshold or self.box_threshold,
                "text_threshold": text_threshold or self.text_threshold,
            },
        )

        # Parse output - Grounding DINO returns detections in a specific format
        detections = self._parse_output(output, image_id or image_url, prompts)
        logger.info(f"Detected {len(detections)} objects")

        return detections

    def _parse_output(
        self,
        output: dict,
        image_id: str,
        prompts: list[str],
    ) -> list[Detection]:
        """Parse Replicate output into Detection objects.

        The output format varies by model version. Common format:
        {
            "detections": [
                {
                    "label": "cabinet",
                    "confidence": 0.85,
                    "box": [x_min, y_min, x_max, y_max]  # normalized
                }
            ]
        }
        """
        detections = []

        # Handle different output formats
        if isinstance(output, dict):
            raw_detections = output.get("detections", [])
        elif isinstance(output, list):
            raw_detections = output
        else:
            logger.warning(f"Unexpected output format: {type(output)}")
            return []

        for det in raw_detections:
            try:
                # Extract box coordinates - format may vary
                if "box" in det:
                    box = det["box"]
                elif "bbox" in det:
                    box = det["bbox"]
                else:
                    # Try xyxy format
                    box = [det.get("x_min", 0), det.get("y_min", 0),
                           det.get("x_max", 1), det.get("y_max", 1)]

                # Auto-normalize coordinates (handles both pixel and normalized)
                # Default to 640x480 if image dimensions not known
                bbox = BoundingBox.from_pixels_auto(
                    coords=[float(box[0]), float(box[1]), float(box[2]), float(box[3])],
                    image_width=640,
                    image_height=480,
                )

                # Get label and confidence
                label = det.get("label", det.get("class", "unknown"))
                confidence = float(det.get("confidence", det.get("score", 0.0)))

                # Find matching prompt
                matched_prompt = None
                for prompt in prompts:
                    if prompt.lower() in label.lower() or label.lower() in prompt.lower():
                        matched_prompt = prompt
                        break

                detection = Detection(
                    image_id=image_id,
                    label=label,
                    confidence=confidence,
                    bbox=bbox,
                    prompt=matched_prompt or prompts[0] if prompts else None,
                )
                detections.append(detection)

            except Exception as e:
                logger.warning(f"Failed to parse detection: {det}, error: {e}")
                continue

        return detections

    async def detect_batch(
        self,
        images: list[dict],
        prompts: list[str],
        max_concurrent: int = 5,
    ) -> dict[str, list[Detection]]:
        """Detect objects in multiple images.

        Args:
            images: List of {"image_url": ..., "image_id": ...} dicts.
            prompts: Common prompts to use for all images.
            max_concurrent: Maximum concurrent API calls.

        Returns:
            Dictionary mapping image_id to list of detections.
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)
        results = {}

        async def process_image(img: dict) -> tuple[str, list[Detection]]:
            async with semaphore:
                image_id = img.get("image_id", img["image_url"])
                detections = await self.detect(
                    image_url=img["image_url"],
                    prompts=prompts,
                    image_id=image_id,
                )
                return image_id, detections

        tasks = [process_image(img) for img in images]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for result in completed:
            if isinstance(result, Exception):
                logger.error(f"Batch detection error: {result}")
            else:
                image_id, detections = result
                results[image_id] = detections

        return results
