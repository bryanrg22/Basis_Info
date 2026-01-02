"""
MCP vision tools for agentic workflow integration.

Wraps vision_layer pipeline functions as LangChain tools for use by agents.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

# Add vision_layer src to path for imports
VISION_LAYER_PATH = Path(__file__).parent.parent / "src"
if str(VISION_LAYER_PATH) not in sys.path:
    sys.path.insert(0, str(VISION_LAYER_PATH))

from api_clients.grounding_dino import GroundingDINOClient
from api_clients.sam2 import SAM2Client
from api_clients.vlm import VLMClient
from evidence.artifact_store import (
    get_artifact_by_id,
    get_artifacts_by_component,
    get_artifacts_by_image,
    get_vision_artifacts,
    save_vision_artifacts,
    search_artifacts,
)
from evidence.correction_store import Correction, save_correction
from evidence.review_router import get_review_queue, get_review_stats
from pipeline.ingest import PipelineConfig, VisionPipeline
from schemas.artifact import VisionArtifact


def _run_async(coro):
    """Helper to run async functions synchronously for tool compatibility."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in an async context, create a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# Shared clients (lazy initialization)
_dino_client: Optional[GroundingDINOClient] = None
_sam2_client: Optional[SAM2Client] = None
_vlm_client: Optional[VLMClient] = None
_pipeline: Optional[VisionPipeline] = None


def _get_dino_client() -> GroundingDINOClient:
    global _dino_client
    if _dino_client is None:
        _dino_client = GroundingDINOClient()
    return _dino_client


def _get_sam2_client() -> SAM2Client:
    global _sam2_client
    if _sam2_client is None:
        _sam2_client = SAM2Client()
    return _sam2_client


def _get_vlm_client() -> VLMClient:
    global _vlm_client
    if _vlm_client is None:
        _vlm_client = VLMClient()
    return _vlm_client


def _get_pipeline() -> VisionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = VisionPipeline()
    return _pipeline


@tool
def detect_objects_tool(
    image_url: str,
    prompts: Optional[List[str]] = None,
    confidence_threshold: float = 0.3,
) -> List[Dict[str, Any]]:
    """
    Detect objects in an image using Grounding DINO.

    Uses open-vocabulary detection to find objects matching the provided prompts.
    Returns bounding boxes and confidence scores for each detection.

    Best for:
    - Finding specific components in property photos
    - Detecting cabinets, appliances, flooring, lighting, etc.
    - Initial object discovery before classification

    Args:
        image_url: URL of the image to process
        prompts: List of object types to detect (e.g., ["cabinet", "appliance", "flooring"])
                 If not provided, uses default cost segregation prompts
        confidence_threshold: Minimum confidence score (0.0-1.0). Default 0.3

    Returns:
        List of detection dictionaries with:
        - detection_id: Unique identifier for this detection
        - label: Detected object type
        - confidence: Detection confidence (0.0-1.0)
        - bbox: Bounding box {x_min, y_min, x_max, y_max} as normalized coordinates
    """
    default_prompts = [
        "cabinet", "appliance", "lighting fixture", "flooring",
        "countertop", "sink", "window", "door", "HVAC unit",
        "electrical panel", "plumbing fixture",
    ]

    client = _get_dino_client()
    client.box_threshold = confidence_threshold

    async def run_detection():
        detections = await client.detect(
            image_url=image_url,
            prompts=prompts or default_prompts,
            image_id="tool_detection",
        )
        return [
            {
                "detection_id": d.detection_id,
                "label": d.label,
                "confidence": d.confidence,
                "bbox": d.bbox.model_dump(),
            }
            for d in detections
        ]

    return _run_async(run_detection())


@tool
def segment_detections_tool(
    image_url: str,
    detections: List[Dict[str, Any]],
    image_width: int,
    image_height: int,
) -> List[Dict[str, Any]]:
    """
    Generate precise segmentation masks for detections using SAM 2.

    Refines bounding box detections with pixel-level masks for more accurate
    component boundaries.

    Args:
        image_url: URL of the original image
        detections: List of detection dicts from detect_objects_tool
        image_width: Image width in pixels
        image_height: Image height in pixels

    Returns:
        List of detection dictionaries with added mask data
    """
    from schemas.detection import BoundingBox, Detection

    client = _get_sam2_client()

    # Convert dict detections to Detection objects
    detection_objs = []
    for d in detections:
        bbox = BoundingBox(**d["bbox"])
        detection_objs.append(Detection(
            detection_id=d["detection_id"],
            image_id="tool_detection",
            label=d["label"],
            confidence=d["confidence"],
            bbox=bbox,
        ))

    async def run_segmentation():
        segmented = await client.segment_detections(
            image_url=image_url,
            detections=detection_objs,
            image_width=image_width,
            image_height=image_height,
        )
        return [
            {
                "detection_id": d.detection_id,
                "label": d.label,
                "confidence": d.confidence,
                "bbox": d.bbox.model_dump(),
                "has_mask": d.mask is not None,
            }
            for d in segmented
        ]

    return _run_async(run_segmentation())


@tool
def classify_region_tool(
    image_url: str,
    bbox: Dict[str, float],
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Classify a specific region of an image using VLM.

    Crops the specified region and runs vision-language classification
    to determine component type, material, condition, and attributes.

    Args:
        image_url: URL of the image
        bbox: Bounding box as {x_min, y_min, x_max, y_max} in normalized coords (0-1)
        context: Optional context (e.g., "This is a kitchen")

    Returns:
        Classification dict with:
        - component_type: Detected component type
        - material: Material (if determinable)
        - condition: Condition assessment
        - finish: Surface finish
        - color: Primary color
        - attributes: Additional attributes
    """
    from pipeline.cropper import RegionCropper
    from schemas.detection import BoundingBox

    client = _get_vlm_client()

    async def run_classification():
        cropper = RegionCropper()
        try:
            image = await cropper.load_image(image_url)
            bbox_obj = BoundingBox(**bbox)

            crop = cropper.crop_region(image, bbox_obj)
            classification = await client.classify_pil_image(
                image=crop,
                context=context,
            )

            return {
                "component_type": classification.component_type,
                "material": classification.material,
                "condition": classification.condition,
                "finish": classification.finish,
                "color": classification.color,
                "attributes": classification.attributes,
            }
        finally:
            await cropper.close()

    return _run_async(run_classification())


@tool
def process_image_tool(
    image_url: str,
    study_id: str,
    image_id: str,
    room_context: Optional[str] = None,
    save_to_firestore: bool = True,
) -> List[Dict[str, Any]]:
    """
    Run the full vision pipeline on an image.

    Orchestrates detection, segmentation, cropping, and VLM classification
    to produce VisionArtifacts with full provenance tracking.

    This is the primary tool for processing property photos.

    Args:
        image_url: URL of the image to process
        study_id: Study document ID for artifact storage
        image_id: Unique identifier for this image
        room_context: Optional room type (e.g., "kitchen", "bathroom")
        save_to_firestore: If True, saves artifacts to Firestore. Default True.

    Returns:
        List of VisionArtifact dictionaries with:
        - artifact_id: Unique artifact identifier
        - component_type: Classified component type
        - material: Detected material
        - confidence: Overall confidence score
        - grounded: Whether VLM claim matches detection
        - needs_review: Whether engineer review is needed
        - bbox: Bounding box location
        - provenance: Full processing trace
    """
    pipeline = _get_pipeline()

    async def run_pipeline():
        artifacts = await pipeline.process_image(
            image_url=image_url,
            image_id=image_id,
            study_id=study_id,
            room_context=room_context,
        )

        # Convert to dicts
        artifact_dicts = [a.to_dict() for a in artifacts]

        # Save to Firestore if requested
        if save_to_firestore and artifacts:
            save_vision_artifacts(study_id, artifacts)

        return artifact_dicts

    return _run_async(run_pipeline())


@tool
def classify_scene_tool(
    image_url: str,
    image_id: str,
) -> Dict[str, Any]:
    """
    Classify the overall scene/room type in an image.

    Analyzes the full image to determine room type, indoor/outdoor,
    and other scene-level attributes.

    Args:
        image_url: URL of the image
        image_id: Image identifier

    Returns:
        Scene classification with:
        - room_type: Classified room type (kitchen, bathroom, etc.)
        - room_type_confidence: Confidence in room classification
        - indoor_outdoor: "indoor", "outdoor", or "mixed"
        - property_type: "residential", "commercial", or "industrial"
        - floor_level: "ground", "upper", "basement", or "unknown"
    """
    pipeline = _get_pipeline()

    async def run_scene_classification():
        scene = await pipeline.classify_scene(
            image_url=image_url,
            image_id=image_id,
        )
        return scene.model_dump()

    return _run_async(run_scene_classification())


@tool
def get_vision_artifact_tool(
    study_id: str,
    artifact_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Fetch a specific vision artifact by ID.

    Args:
        study_id: Study document ID
        artifact_id: Artifact ID to retrieve

    Returns:
        VisionArtifact dictionary or None if not found
    """
    return get_artifact_by_id(study_id, artifact_id)


@tool
def search_vision_artifacts_tool(
    study_id: str,
    component_type: Optional[str] = None,
    image_id: Optional[str] = None,
    min_confidence: float = 0.0,
    needs_review: Optional[bool] = None,
    grounded: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """
    Search vision artifacts by multiple criteria.

    Filter artifacts by component type, image, confidence level,
    review status, or grounding status.

    Args:
        study_id: Study document ID
        component_type: Filter by component type (e.g., "cabinet")
        image_id: Filter by source image ID
        min_confidence: Minimum confidence threshold (default 0.0)
        needs_review: Filter by review status (True/False/None)
        grounded: Filter by grounding status (True/False/None)

    Returns:
        List of matching VisionArtifact dictionaries
    """
    return search_artifacts(
        study_id=study_id,
        component_type=component_type,
        image_id=image_id,
        min_confidence=min_confidence,
        needs_review=needs_review,
        grounded=grounded,
    )


@tool
def get_artifacts_for_image_tool(
    study_id: str,
    image_id: str,
) -> List[Dict[str, Any]]:
    """
    Get all vision artifacts for a specific image.

    Args:
        study_id: Study document ID
        image_id: Image ID to get artifacts for

    Returns:
        List of VisionArtifact dictionaries for that image
    """
    return get_artifacts_by_image(study_id, image_id)


@tool
def get_review_queue_tool(
    study_id: str,
) -> List[Dict[str, Any]]:
    """
    Get artifacts pending engineer review, sorted by priority.

    Returns artifacts that need review due to low confidence,
    ungrounded claims, or other issues.

    Args:
        study_id: Study document ID

    Returns:
        List of review items with artifact, priority, and reasons
    """
    queue = get_review_queue(study_id)
    return [item.to_dict() for item in queue]


@tool
def get_vision_review_stats_tool(
    study_id: str,
) -> Dict[str, Any]:
    """
    Get review statistics for a study's vision artifacts.

    Args:
        study_id: Study document ID

    Returns:
        Statistics including pending count, reviewed count, and breakdowns
    """
    return get_review_stats(study_id)


@tool
def submit_correction_tool(
    study_id: str,
    artifact_id: str,
    field: str,
    old_value: Any,
    new_value: Any,
    corrected_by: str,
    notes: Optional[str] = None,
) -> str:
    """
    Submit an engineer correction for a vision artifact.

    Records the correction for training data collection and
    optionally applies it to the artifact.

    Args:
        study_id: Study document ID
        artifact_id: Artifact being corrected
        field: Field being corrected (e.g., "component_type", "material")
        old_value: Original value before correction
        new_value: Corrected value
        corrected_by: Engineer ID or email
        notes: Optional explanation of the correction

    Returns:
        Correction ID of the saved correction
    """
    correction = Correction(
        artifact_id=artifact_id,
        field=field,
        old_value=old_value,
        new_value=new_value,
        corrected_by=corrected_by,
        notes=notes,
    )

    return save_correction(
        study_id=study_id,
        correction=correction,
        apply_to_artifact=True,
    )


# Export all tools for easy registration
ALL_VISION_TOOLS = [
    detect_objects_tool,
    segment_detections_tool,
    classify_region_tool,
    process_image_tool,
    classify_scene_tool,
    get_vision_artifact_tool,
    search_vision_artifacts_tool,
    get_artifacts_for_image_tool,
    get_review_queue_tool,
    get_vision_review_stats_tool,
    submit_correction_tool,
]
