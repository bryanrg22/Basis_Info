"""SAM 2 (Segment Anything Model 2) API client via Replicate."""

import logging
from typing import Optional

import replicate
from tenacity import retry, stop_after_attempt, wait_exponential

from ..schemas.detection import BoundingBox, Detection, Mask, RLEMask

logger = logging.getLogger(__name__)

# Replicate model versions
SAM2_MODEL = "meta/sam-2:fe97b453a6455861e3bac769b441ca1f1086110da7466dbb65cf1eecfd60dc83"


class SAM2Client:
    """Client for SAM 2 segmentation via Replicate.

    SAM 2 provides high-quality segmentation masks given bounding box
    or point prompts.
    """

    def __init__(
        self,
        api_token: Optional[str] = None,
        model_version: str = SAM2_MODEL,
        multimask_output: bool = False,
    ):
        """Initialize SAM 2 client.

        Args:
            api_token: Replicate API token. Falls back to REPLICATE_API_TOKEN env var.
            model_version: Replicate model version string.
            multimask_output: Whether to return multiple mask candidates.
        """
        self.model_version = model_version
        self.multimask_output = multimask_output

        if api_token:
            import os
            os.environ["REPLICATE_API_TOKEN"] = api_token

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    async def segment_box(
        self,
        image_url: str,
        bbox: BoundingBox,
        image_width: int,
        image_height: int,
    ) -> Optional[Mask]:
        """Generate segmentation mask for a bounding box region.

        Args:
            image_url: URL of image to process.
            bbox: Bounding box in normalized coordinates [0, 1].
            image_width: Image width in pixels (for denormalization).
            image_height: Image height in pixels (for denormalization).

        Returns:
            Mask object with RLE or polygon encoding, or None if failed.
        """
        # Convert normalized bbox to pixel coordinates
        x_min, y_min, x_max, y_max = bbox.to_pixels(image_width, image_height)
        box_str = f"{x_min},{y_min},{x_max},{y_max}"

        logger.info(f"Running SAM 2 segmentation on {image_url} with box {box_str}")

        try:
            output = await replicate.async_run(
                self.model_version,
                input={
                    "image": image_url,
                    "box": box_str,
                    "multimask_output": self.multimask_output,
                },
            )

            return self._parse_mask_output(output, image_height, image_width)

        except Exception as e:
            logger.error(f"SAM 2 segmentation failed: {e}")
            return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    async def segment_point(
        self,
        image_url: str,
        point: tuple[float, float],
        image_width: int,
        image_height: int,
        point_label: int = 1,
    ) -> Optional[Mask]:
        """Generate segmentation mask from a point prompt.

        Args:
            image_url: URL of image to process.
            point: Point coordinates in normalized [0, 1] format.
            image_width: Image width in pixels.
            image_height: Image height in pixels.
            point_label: 1 for foreground, 0 for background.

        Returns:
            Mask object or None if failed.
        """
        # Convert to pixel coordinates
        px = int(point[0] * image_width)
        py = int(point[1] * image_height)
        point_str = f"{px},{py}"

        logger.info(f"Running SAM 2 segmentation on {image_url} with point {point_str}")

        try:
            output = await replicate.async_run(
                self.model_version,
                input={
                    "image": image_url,
                    "point_coords": point_str,
                    "point_labels": str(point_label),
                    "multimask_output": self.multimask_output,
                },
            )

            return self._parse_mask_output(output, image_height, image_width)

        except Exception as e:
            logger.error(f"SAM 2 point segmentation failed: {e}")
            return None

    def _parse_mask_output(
        self,
        output: dict,
        height: int,
        width: int,
    ) -> Optional[Mask]:
        """Parse SAM 2 output into Mask object.

        SAM 2 typically returns:
        - RLE encoded mask
        - Or mask URL that needs to be fetched
        """
        if output is None:
            return None

        # Handle different output formats
        if isinstance(output, str):
            # Output is a URL to the mask image - we'll handle this differently
            logger.info(f"SAM 2 returned mask URL: {output}")
            # For now, store as polygon placeholder (real implementation would fetch and decode)
            return Mask(polygon=None, rle=None)

        if isinstance(output, dict):
            # Check for RLE format
            if "counts" in output and "size" in output:
                return Mask(
                    rle=RLEMask(
                        counts=output["counts"],
                        size=tuple(output["size"]),
                    )
                )

            # Check for masks array
            if "masks" in output and len(output["masks"]) > 0:
                mask_data = output["masks"][0]  # Take first/best mask
                if isinstance(mask_data, dict) and "counts" in mask_data:
                    return Mask(
                        rle=RLEMask(
                            counts=mask_data["counts"],
                            size=(height, width),
                        )
                    )

        logger.warning(f"Could not parse SAM 2 output format: {type(output)}")
        return None

    async def segment_detections(
        self,
        image_url: str,
        detections: list[Detection],
        image_width: int,
        image_height: int,
        max_concurrent: int = 10,
    ) -> list[Detection]:
        """Add segmentation masks to a list of detections.

        Args:
            image_url: URL of the source image.
            detections: List of Detection objects with bounding boxes.
            image_width: Image width in pixels.
            image_height: Image height in pixels.
            max_concurrent: Maximum concurrent segmentation calls.

        Returns:
            Updated detections with mask field populated.
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)

        async def segment_one(detection: Detection) -> Detection:
            async with semaphore:
                mask = await self.segment_box(
                    image_url=image_url,
                    bbox=detection.bbox,
                    image_width=image_width,
                    image_height=image_height,
                )
                # Create new detection with mask
                return Detection(
                    detection_id=detection.detection_id,
                    image_id=detection.image_id,
                    label=detection.label,
                    confidence=detection.confidence,
                    bbox=detection.bbox,
                    mask=mask,
                    prompt=detection.prompt,
                )

        tasks = [segment_one(det) for det in detections]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        segmented = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Segmentation failed for detection {i}: {result}")
                segmented.append(detections[i])  # Keep original without mask
            else:
                segmented.append(result)

        return segmented
