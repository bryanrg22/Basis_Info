"""Main vision ingestion pipeline orchestrator."""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from ..api_clients.grounding_dino import GroundingDINOClient
from ..api_clients.sam2 import SAM2Client
from ..api_clients.vlm import VLMClient
from ..schemas.artifact import Provenance, VisionArtifact, VLMClassification
from ..schemas.detection import BoundingBox, Detection
from ..schemas.scene import RoomType, SceneClassification
from .cropper import RegionCropper

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuration for vision pipeline."""

    # Detection settings
    detection_prompts: list[str] = field(
        default_factory=lambda: [
            "cabinet",
            "appliance",
            "lighting fixture",
            "flooring",
            "countertop",
            "sink",
            "window",
            "door",
            "HVAC unit",
            "electrical panel",
            "plumbing fixture",
        ]
    )
    detection_threshold: float = 0.3

    # Cropping settings
    crop_padding: float = 0.2
    save_crops: bool = True
    crops_dir: Optional[Path] = None

    # Segmentation settings
    enable_segmentation: bool = True

    # VLM settings
    vlm_model: str = "gpt-4o"
    enable_consistency_check: bool = False
    consistency_passes: int = 3

    # Review thresholds
    low_confidence_threshold: float = 0.5
    require_grounding: bool = True

    # Concurrency
    max_concurrent_detections: int = 5
    max_concurrent_vlm: int = 3


class VisionPipeline:
    """Orchestrates the full vision processing pipeline.

    Flow:
    1. Detect objects with Grounding DINO
    2. Segment detections with SAM 2 (optional)
    3. Crop regions with padding
    4. Classify each crop with VLM
    5. Validate and package as VisionArtifacts
    """

    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        grounding_dino_client: Optional[GroundingDINOClient] = None,
        sam2_client: Optional[SAM2Client] = None,
        vlm_client: Optional[VLMClient] = None,
    ):
        """Initialize vision pipeline.

        Args:
            config: Pipeline configuration.
            grounding_dino_client: Pre-configured detection client.
            sam2_client: Pre-configured segmentation client.
            vlm_client: Pre-configured VLM client.
        """
        self.config = config or PipelineConfig()

        # Initialize clients (lazy if not provided)
        self._dino_client = grounding_dino_client
        self._sam2_client = sam2_client
        self._vlm_client = vlm_client
        self._cropper: Optional[RegionCropper] = None

    @property
    def dino_client(self) -> GroundingDINOClient:
        if self._dino_client is None:
            self._dino_client = GroundingDINOClient(
                box_threshold=self.config.detection_threshold
            )
        return self._dino_client

    @property
    def sam2_client(self) -> SAM2Client:
        if self._sam2_client is None:
            self._sam2_client = SAM2Client()
        return self._sam2_client

    @property
    def vlm_client(self) -> VLMClient:
        if self._vlm_client is None:
            self._vlm_client = VLMClient(model=self.config.vlm_model)
        return self._vlm_client

    @property
    def cropper(self) -> RegionCropper:
        if self._cropper is None:
            self._cropper = RegionCropper(padding=self.config.crop_padding)
        return self._cropper

    async def close(self) -> None:
        """Close all clients."""
        if self._cropper:
            await self._cropper.close()

    async def process_image(
        self,
        image_url: str,
        image_id: str,
        study_id: str,
        room_context: Optional[str] = None,
        custom_prompts: Optional[list[str]] = None,
    ) -> list[VisionArtifact]:
        """Process a single image through the full pipeline.

        Args:
            image_url: URL of image to process.
            image_id: Unique identifier for the image.
            study_id: Parent study identifier.
            room_context: Optional room type context (e.g., "kitchen").
            custom_prompts: Override default detection prompts.

        Returns:
            List of VisionArtifact objects for detected components.
        """
        logger.info(f"Processing image {image_id}")
        artifacts = []

        # Step 1: Detect objects
        prompts = custom_prompts or self.config.detection_prompts
        detections = await self.dino_client.detect(
            image_url=image_url,
            prompts=prompts,
            image_id=image_id,
        )
        logger.info(f"Detected {len(detections)} objects in {image_id}")

        if not detections:
            logger.warning(f"No detections in {image_id}")
            return []

        # Step 2: Load image for dimensions and cropping
        image = await self.cropper.load_image(image_url)
        image_width, image_height = image.size

        # Step 3: Segment detections (optional)
        if self.config.enable_segmentation:
            detections = await self.sam2_client.segment_detections(
                image_url=image_url,
                detections=detections,
                image_width=image_width,
                image_height=image_height,
            )
            logger.info(f"Segmented {len(detections)} detections")

        # Step 4: Crop regions
        crops_dir = None
        if self.config.save_crops and self.config.crops_dir:
            crops_dir = self.config.crops_dir / study_id / image_id

        crop_results = await self.cropper.crop_all_detections(
            image_source=image,
            detections=detections,
            save_dir=crops_dir,
        )
        logger.info(f"Cropped {len(crop_results)} regions")

        # Step 5: Classify each crop with VLM
        for i, crop_result in enumerate(crop_results):
            detection = detections[i]
            crop_image = crop_result["crop"]
            padded_bbox = crop_result["padded_bbox"]
            crop_path = crop_result.get("crop_path")

            try:
                # Add room context to VLM prompt
                context = f"Room type: {room_context}" if room_context else None

                classification = await self.vlm_client.classify_pil_image(
                    image=crop_image,
                    context=context,
                )

                # Step 6: Create artifact with provenance
                artifact = self._create_artifact(
                    study_id=study_id,
                    image_id=image_id,
                    image_url=image_url,
                    detection=detection,
                    classification=classification,
                    padded_bbox=padded_bbox,
                    crop_path=crop_path,
                )
                artifacts.append(artifact)

            except Exception as e:
                logger.error(
                    f"VLM classification failed for detection {detection.detection_id}: {e}"
                )
                # Create artifact with needs_review flag
                artifact = self._create_error_artifact(
                    study_id=study_id,
                    image_id=image_id,
                    image_url=image_url,
                    detection=detection,
                    padded_bbox=padded_bbox,
                    error=str(e),
                )
                artifacts.append(artifact)

        logger.info(f"Created {len(artifacts)} artifacts for {image_id}")
        return artifacts

    def _create_artifact(
        self,
        study_id: str,
        image_id: str,
        image_url: str,
        detection: Detection,
        classification: VLMClassification,
        padded_bbox: BoundingBox,
        crop_path: Optional[str],
    ) -> VisionArtifact:
        """Create a VisionArtifact from detection and classification."""
        # Calculate confidence based on detection and classification
        confidence = detection.confidence

        # Check if grounded (VLM component type matches detection label)
        grounded = self._check_grounding(detection.label, classification.component_type)

        # Determine if needs review
        needs_review = False
        review_reason = None

        if confidence < self.config.low_confidence_threshold:
            needs_review = True
            review_reason = f"Low confidence: {confidence:.2f}"
        elif self.config.require_grounding and not grounded:
            needs_review = True
            review_reason = f"VLM type '{classification.component_type}' not grounded in detection '{detection.label}'"

        provenance = Provenance(
            image_url=image_url,
            image_id=image_id,
            detection_id=detection.detection_id,
            detection_model="grounding-dino-1.5-pro",
            detection_confidence=detection.confidence,
            segmentation_model="sam-2" if detection.mask else None,
            vlm_model=self.config.vlm_model,
            crop_bbox=padded_bbox,
            crop_path=crop_path,
        )

        return VisionArtifact(
            study_id=study_id,
            image_id=image_id,
            detection_id=detection.detection_id,
            classification=classification,
            confidence=confidence,
            grounded=grounded,
            needs_review=needs_review,
            review_reason=review_reason,
            provenance=provenance,
            bbox=detection.bbox,
        )

    def _create_error_artifact(
        self,
        study_id: str,
        image_id: str,
        image_url: str,
        detection: Detection,
        padded_bbox: BoundingBox,
        error: str,
    ) -> VisionArtifact:
        """Create artifact for failed VLM classification."""
        provenance = Provenance(
            image_url=image_url,
            image_id=image_id,
            detection_id=detection.detection_id,
            detection_model="grounding-dino-1.5-pro",
            detection_confidence=detection.confidence,
            vlm_model=self.config.vlm_model,
            crop_bbox=padded_bbox,
        )

        return VisionArtifact(
            study_id=study_id,
            image_id=image_id,
            detection_id=detection.detection_id,
            classification=VLMClassification(
                component_type=detection.label,  # Fall back to detection label
                raw_response=f"VLM Error: {error}",
            ),
            confidence=detection.confidence * 0.5,  # Reduce confidence for error case
            grounded=True,  # Using detection label, so grounded by definition
            needs_review=True,
            review_reason=f"VLM classification failed: {error}",
            provenance=provenance,
            bbox=detection.bbox,
        )

    def _check_grounding(self, detection_label: str, vlm_type: str) -> bool:
        """Check if VLM classification is grounded in detection.

        Simple string matching - could be enhanced with embeddings.
        """
        det_lower = detection_label.lower()
        vlm_lower = vlm_type.lower()

        # Direct match
        if det_lower in vlm_lower or vlm_lower in det_lower:
            return True

        # Common synonyms
        synonyms = {
            "cabinet": ["cupboard", "storage", "drawer"],
            "appliance": ["refrigerator", "oven", "stove", "dishwasher", "microwave"],
            "lighting": ["light", "lamp", "fixture", "chandelier"],
            "flooring": ["floor", "tile", "carpet", "hardwood", "vinyl"],
            "countertop": ["counter", "surface", "worktop"],
        }

        for key, syns in synonyms.items():
            if det_lower in [key] + syns and vlm_lower in [key] + syns:
                return True

        return False

    async def process_batch(
        self,
        images: list[dict],
        study_id: str,
        max_concurrent: int = 3,
    ) -> dict[str, list[VisionArtifact]]:
        """Process multiple images concurrently.

        Args:
            images: List of {"image_url": ..., "image_id": ..., "room_context": ...}.
            study_id: Parent study identifier.
            max_concurrent: Maximum concurrent image processing.

        Returns:
            Dictionary mapping image_id to list of artifacts.
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        results = {}

        async def process_one(img: dict) -> tuple[str, list[VisionArtifact]]:
            async with semaphore:
                image_id = img["image_id"]
                artifacts = await self.process_image(
                    image_url=img["image_url"],
                    image_id=image_id,
                    study_id=study_id,
                    room_context=img.get("room_context"),
                    custom_prompts=img.get("prompts"),
                )
                return image_id, artifacts

        tasks = [process_one(img) for img in images]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for result in completed:
            if isinstance(result, Exception):
                logger.error(f"Batch processing error: {result}")
            else:
                image_id, artifacts = result
                results[image_id] = artifacts

        return results

    async def classify_scene(
        self,
        image_url: str,
        image_id: str,
    ) -> SceneClassification:
        """Classify the overall scene/room type.

        Args:
            image_url: URL of image.
            image_id: Image identifier.

        Returns:
            SceneClassification with room type and attributes.
        """
        prompt = """Analyze this image and classify the room/scene.

Provide:
1. Room type (bedroom, bathroom, kitchen, living_room, office, garage, etc.)
2. Indoor/outdoor classification
3. Property type (residential, commercial, industrial)
4. Floor level if determinable
5. Lighting condition
6. Any notable observations

Respond in JSON:
{
    "room_type": "...",
    "indoor_outdoor": "indoor|outdoor|mixed",
    "property_type": "...",
    "floor_level": "ground|upper|basement|unknown",
    "lighting_condition": "natural|artificial|mixed",
    "notes": "..."
}"""

        classification = await self.vlm_client.classify_image_url(
            image_url=image_url,
            prompt=prompt,
        )

        # Parse room type from VLM response
        raw = classification.raw_response or ""

        # Default values
        room_type = RoomType.UNKNOWN
        indoor_outdoor = "indoor"

        try:
            import json
            data = json.loads(raw) if "{" in raw else {}

            room_str = data.get("room_type", "").lower().replace(" ", "_")
            try:
                room_type = RoomType(room_str)
            except ValueError:
                room_type = RoomType.UNKNOWN

            indoor_outdoor = data.get("indoor_outdoor", "indoor")

            return SceneClassification(
                image_id=image_id,
                room_type=room_type,
                room_type_confidence=0.8,  # Could extract from VLM
                indoor_outdoor=indoor_outdoor,
                property_type=data.get("property_type"),
                floor_level=data.get("floor_level"),
                lighting_condition=data.get("lighting_condition"),
                notes=data.get("notes"),
            )

        except Exception as e:
            logger.warning(f"Failed to parse scene classification: {e}")
            return SceneClassification(
                image_id=image_id,
                room_type=RoomType.UNKNOWN,
                room_type_confidence=0.0,
                notes=f"Parse error: {e}",
            )
