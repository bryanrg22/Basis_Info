"""Region cropping utilities for VLM input preparation."""

import logging
from io import BytesIO
from pathlib import Path
from typing import Optional, Union
from uuid import uuid4

import httpx
from PIL import Image

from ..schemas.detection import BoundingBox, Detection

logger = logging.getLogger(__name__)


class RegionCropper:
    """Crop detected regions from images for VLM classification.

    Applies configurable padding around detections to provide context
    while focusing the VLM's attention on the specific object.
    """

    def __init__(
        self,
        padding: float = 0.2,
        min_size: int = 64,
        max_size: int = 1024,
        output_format: str = "JPEG",
        output_quality: int = 95,
    ):
        """Initialize region cropper.

        Args:
            padding: Padding ratio around detection bbox (0.2 = 20% on each side).
            min_size: Minimum dimension for output crops.
            max_size: Maximum dimension for output crops (will resize if larger).
            output_format: Image format for saved crops (JPEG, PNG).
            output_quality: JPEG quality (1-100).
        """
        self.padding = padding
        self.min_size = min_size
        self.max_size = max_size
        self.output_format = output_format
        self.output_quality = output_quality
        self._http_client: Optional[httpx.AsyncClient] = None

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Lazy HTTP client initialization."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=60.0)
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def load_image(self, source: Union[str, bytes, Path, Image.Image]) -> Image.Image:
        """Load image from URL, file path, bytes, or existing PIL Image.

        Args:
            source: Image URL, local path, raw bytes, or PIL Image.

        Returns:
            PIL Image object.
        """
        # If already a PIL Image, return it directly
        if isinstance(source, Image.Image):
            return source

        if isinstance(source, bytes):
            return Image.open(BytesIO(source))

        if isinstance(source, Path) or (isinstance(source, str) and not source.startswith(("http://", "https://"))):
            return Image.open(source)

        # URL - fetch via HTTP
        response = await self.http_client.get(source)
        response.raise_for_status()
        return Image.open(BytesIO(response.content))

    def crop_region(
        self,
        image: Image.Image,
        bbox: BoundingBox,
        padding: Optional[float] = None,
    ) -> Image.Image:
        """Crop a region from an image with padding.

        Args:
            image: Source PIL Image.
            bbox: Bounding box in normalized coordinates [0, 1].
            padding: Override default padding ratio.

        Returns:
            Cropped PIL Image.
        """
        width, height = image.size
        pad = padding if padding is not None else self.padding

        # Apply padding to bbox (clamped to [0, 1])
        padded_bbox = bbox.with_padding(pad)

        # Convert to pixel coordinates
        x_min, y_min, x_max, y_max = padded_bbox.to_pixels(width, height)

        # Ensure valid crop region
        x_min = max(0, x_min)
        y_min = max(0, y_min)
        x_max = min(width, x_max)
        y_max = min(height, y_max)

        # Crop
        crop = image.crop((x_min, y_min, x_max, y_max))

        # Resize if needed
        crop = self._resize_if_needed(crop)

        return crop

    def _resize_if_needed(self, image: Image.Image) -> Image.Image:
        """Resize image if outside min/max bounds."""
        width, height = image.size

        # Check minimum size
        if width < self.min_size or height < self.min_size:
            scale = max(self.min_size / width, self.min_size / height)
            new_width = int(width * scale)
            new_height = int(height * scale)
            return image.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Check maximum size
        if width > self.max_size or height > self.max_size:
            scale = min(self.max_size / width, self.max_size / height)
            new_width = int(width * scale)
            new_height = int(height * scale)
            return image.resize((new_width, new_height), Image.Resampling.LANCZOS)

        return image

    def crop_to_bytes(
        self,
        image: Image.Image,
        bbox: BoundingBox,
        padding: Optional[float] = None,
    ) -> bytes:
        """Crop region and return as bytes.

        Args:
            image: Source PIL Image.
            bbox: Bounding box in normalized coordinates.
            padding: Override default padding ratio.

        Returns:
            Image bytes in configured format.
        """
        crop = self.crop_region(image, bbox, padding)

        buffer = BytesIO()
        if self.output_format.upper() == "JPEG":
            crop.convert("RGB").save(
                buffer, format="JPEG", quality=self.output_quality
            )
        else:
            crop.save(buffer, format=self.output_format)

        return buffer.getvalue()

    async def crop_detection(
        self,
        image_source: Union[str, bytes, Path, Image.Image],
        detection: Detection,
        padding: Optional[float] = None,
    ) -> tuple[Image.Image, BoundingBox]:
        """Crop region for a detection.

        Args:
            image_source: Image URL, path, bytes, or PIL Image.
            detection: Detection object with bbox.
            padding: Override default padding ratio.

        Returns:
            Tuple of (cropped image, padded bbox used for crop).
        """
        if isinstance(image_source, Image.Image):
            image = image_source
        else:
            image = await self.load_image(image_source)

        pad = padding if padding is not None else self.padding
        padded_bbox = detection.bbox.with_padding(pad)
        crop = self.crop_region(image, detection.bbox, padding)

        return crop, padded_bbox

    async def crop_all_detections(
        self,
        image_source: Union[str, bytes, Path],
        detections: list[Detection],
        save_dir: Optional[Path] = None,
        padding: Optional[float] = None,
    ) -> list[dict]:
        """Crop regions for all detections in an image.

        Args:
            image_source: Image URL, path, or bytes.
            detections: List of Detection objects.
            save_dir: Optional directory to save crops.
            padding: Override default padding ratio.

        Returns:
            List of dicts with detection_id, crop (PIL Image or path), and padded_bbox.
        """
        image = await self.load_image(image_source)
        results = []

        for detection in detections:
            try:
                crop, padded_bbox = await self.crop_detection(
                    image, detection, padding
                )

                result = {
                    "detection_id": detection.detection_id,
                    "crop": crop,
                    "padded_bbox": padded_bbox,
                    "crop_path": None,
                }

                # Optionally save to disk
                if save_dir:
                    save_dir = Path(save_dir)
                    save_dir.mkdir(parents=True, exist_ok=True)

                    filename = f"{detection.detection_id}.{self.output_format.lower()}"
                    crop_path = save_dir / filename

                    if self.output_format.upper() == "JPEG":
                        crop.convert("RGB").save(
                            crop_path, quality=self.output_quality
                        )
                    else:
                        crop.save(crop_path)

                    result["crop_path"] = str(crop_path)

                results.append(result)

            except Exception as e:
                logger.error(
                    f"Failed to crop detection {detection.detection_id}: {e}"
                )
                continue

        return results

    def get_image_dimensions(self, image: Image.Image) -> tuple[int, int]:
        """Get image dimensions (width, height)."""
        return image.size
