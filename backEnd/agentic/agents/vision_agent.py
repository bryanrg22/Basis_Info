"""
Vision Analysis Agent - Analyzes images to detect rooms and objects.

Uses GPT-4 Vision to analyze uploaded property photos and identify:
1. Room type (kitchen, bathroom, office, etc.)
2. Objects/components visible in the image
3. Property characteristics relevant to cost segregation

This module provides:
1. VisionAgent class - agentic vision analysis with verification tools
2. parse_vision_response() - multi-strategy JSON parsing with fallbacks
3. analyze_image() - backward-compatible function using VisionAgent internally

Timing logs:
- [TIMING] Image X/N: Xs (download: Xs, vision: Xs)
- [TIMING] Vision analysis complete: N images in Xs (avg Xs/image, W workers)
"""

import asyncio
import base64
import json
import logging
import re
import time
import urllib.request
import ssl
from typing import Any, Optional

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext, AgentOutput
from ..config.llm_providers import get_vision_llm
from ..config.settings import get_settings
from ..observability.tracing import get_tracer
from ..utils.parallel import retry_with_backoff

logger = logging.getLogger(__name__)


class DetectedObject(BaseModel):
    """An object detected in an image."""
    label: str = Field(..., description="Object name (e.g., 'HVAC unit', 'light fixture')")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    description: str = Field(default="", description="Brief description")
    potential_asset: bool = Field(default=True, description="Could be a depreciable asset")


class ImageAnalysisResult(BaseModel):
    """Result of analyzing a single image."""
    image_id: str = Field(..., description="Image identifier")
    room_type: str = Field(..., description="Detected room type")
    room_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    indoor_outdoor: str = Field(default="indoor", description="indoor or outdoor")
    property_type: str = Field(default="commercial", description="residential, commercial, or industrial")
    detected_objects: list[DetectedObject] = Field(default_factory=list)
    description: str = Field(default="", description="Overall image description")


class VisionInput(BaseModel):
    """Input for VisionAgent."""
    image_url: str = Field(..., description="URL to the image")
    image_id: str = Field(..., description="Image identifier")
    property_name: str = Field(default="", description="Property name for context")
    image_base64: Optional[str] = Field(default=None, description="Pre-encoded base64 image data")
    image_type: str = Field(default="image/jpeg", description="MIME type of the image")


# =============================================================================
# JSON Parsing Utilities
# =============================================================================


def parse_vision_response(response_text: str) -> dict | None:
    """
    Parse vision response using multiple strategies.

    Tries these strategies in order:
    1. Direct JSON parse (if response is clean JSON)
    2. Extract JSON from markdown code block
    3. Find outermost braces and parse
    4. Regex extraction as fallback

    Args:
        response_text: Raw response text from vision model

    Returns:
        Parsed dict if successful, None if all strategies fail
    """
    if not response_text:
        return None

    # Strategy 1: Direct JSON parse (if response is clean JSON)
    try:
        return json.loads(response_text.strip())
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract JSON from markdown code block
    json_block_match = re.search(r'```(?:json)?\s*(.*?)\s*```', response_text, re.DOTALL)
    if json_block_match:
        try:
            return json.loads(json_block_match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find outermost braces and parse
    start = response_text.find('{')
    end = response_text.rfind('}') + 1
    if start >= 0 and end > start:
        try:
            return json.loads(response_text[start:end])
        except json.JSONDecodeError:
            pass

    # Strategy 4: Regex extraction with nested object support
    # Look for JSON with room_type key
    json_match = re.search(
        r'\{[^{}]*"room_type"\s*:\s*"[^"]*".*?\}',
        response_text,
        re.DOTALL
    )
    if json_match:
        try:
            # Try to expand to include nested arrays
            match_start = json_match.start()
            brace_count = 0
            bracket_count = 0
            for i, char in enumerate(response_text[match_start:]):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                elif char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1

                if brace_count == 0 and bracket_count == 0:
                    json_str = response_text[match_start:match_start + i + 1]
                    return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    return None


def build_analysis_result(data: dict, image_id: str) -> ImageAnalysisResult:
    """
    Build ImageAnalysisResult from parsed JSON data.

    Args:
        data: Parsed JSON dict
        image_id: Image identifier

    Returns:
        ImageAnalysisResult object
    """
    detected_objects = []
    for obj in data.get("detected_objects", []):
        detected_objects.append(DetectedObject(
            label=obj.get("label", "unknown"),
            confidence=obj.get("confidence", 0.8),
            description=obj.get("description", ""),
            potential_asset=obj.get("potential_asset", True),
        ))

    return ImageAnalysisResult(
        image_id=image_id,
        room_type=data.get("room_type", "unknown"),
        room_confidence=data.get("room_confidence", 0.8),
        indoor_outdoor=data.get("indoor_outdoor", "indoor"),
        property_type=data.get("property_type", "commercial"),
        description=data.get("description", ""),
        detected_objects=detected_objects,
    )


# =============================================================================
# VisionAgent Tools
# =============================================================================


@tool
def verify_room_classification(
    room_type: str,
    detected_objects: list[str],
) -> dict:
    """
    Verify that room type classification is consistent with detected objects.

    Cross-checks the room type against typical objects found in that room type.
    Returns a verification result with confidence adjustment suggestions.

    Args:
        room_type: The classified room type
        detected_objects: List of object labels detected in the image

    Returns:
        Verification result with consistency score and suggestions
    """
    # Room type to expected object mapping
    ROOM_OBJECT_EXPECTATIONS = {
        "kitchen": ["cabinet", "counter", "sink", "appliance", "stove", "oven", "refrigerator", "dishwasher"],
        "bathroom": ["toilet", "sink", "vanity", "shower", "tub", "mirror", "tile"],
        "bedroom": ["bed", "closet", "dresser", "window", "light"],
        "office": ["desk", "chair", "computer", "monitor", "filing", "shelf"],
        "lobby": ["reception", "seating", "desk", "light", "floor", "ceiling"],
        "hallway": ["door", "light", "floor", "ceiling", "fire"],
        "mechanical_room": ["hvac", "electrical", "panel", "pipe", "duct", "equipment"],
        "storage": ["shelf", "rack", "box", "pallet"],
        "exterior": ["door", "window", "roof", "siding", "landscaping", "parking"],
        "parking": ["pavement", "line", "light", "sign"],
    }

    expected_objects = ROOM_OBJECT_EXPECTATIONS.get(room_type.lower(), [])
    detected_lower = [o.lower() for o in detected_objects]

    # Count matches
    matches = sum(1 for exp in expected_objects if any(exp in det for det in detected_lower))

    consistency_score = matches / max(len(expected_objects), 1) if expected_objects else 0.5

    suggestions = []
    if consistency_score < 0.3 and len(detected_objects) > 0:
        suggestions.append(f"Low consistency ({consistency_score:.2f}): Consider re-analyzing or flagging for review")

    return {
        "room_type": room_type,
        "consistency_score": round(consistency_score, 2),
        "expected_object_types": expected_objects[:5],
        "matched_count": matches,
        "suggestions": suggestions,
        "is_consistent": consistency_score >= 0.2 or len(detected_objects) == 0,
    }


@tool
def estimate_detection_confidence(
    num_objects: int,
    room_confidence: float,
    has_description: bool,
) -> dict:
    """
    Estimate overall detection quality based on analysis metrics.

    Helps determine if the analysis should be trusted or needs retry/review.

    Args:
        num_objects: Number of objects detected
        room_confidence: Confidence score for room classification
        has_description: Whether a meaningful description was provided

    Returns:
        Quality assessment with overall confidence score
    """
    quality_score = 0.0
    factors = []

    # Factor 1: Room confidence
    if room_confidence >= 0.8:
        quality_score += 0.4
        factors.append("High room confidence")
    elif room_confidence >= 0.5:
        quality_score += 0.25
        factors.append("Moderate room confidence")
    else:
        quality_score += 0.1
        factors.append("Low room confidence - consider retry")

    # Factor 2: Number of objects
    if num_objects >= 5:
        quality_score += 0.35
        factors.append("Good object coverage")
    elif num_objects >= 2:
        quality_score += 0.2
        factors.append("Some objects detected")
    elif num_objects == 0:
        factors.append("No objects detected - may need review")

    # Factor 3: Description quality
    if has_description:
        quality_score += 0.25
        factors.append("Description provided")

    return {
        "overall_quality": round(quality_score, 2),
        "factors": factors,
        "needs_retry": quality_score < 0.4 and room_confidence < 0.5,
        "needs_review": quality_score < 0.5,
    }


# =============================================================================
# VisionAgent Class
# =============================================================================


class VisionAgent(BaseStageAgent[VisionInput, ImageAnalysisResult]):
    """
    Agentic vision analysis with verification tools.

    Extends BaseStageAgent to provide:
    - Room type classification from property photos
    - Object detection for cost segregation
    - Verification tools to ensure quality
    - Retry logic for ambiguous results
    """

    def __init__(self):
        super().__init__(stage_name="vision_analysis")

    def get_system_prompt(self) -> str:
        return """You are a cost segregation expert analyzing property photos.

Your task: Analyze this image to identify:
1. The room/space type (kitchen, bathroom, office, lobby, mechanical room, exterior, etc.)
2. All visible building components that could be depreciable assets
3. Whether this is indoor or outdoor
4. The likely property type (residential, commercial, or industrial)

For each detected object, identify items that are relevant to cost segregation:
- HVAC equipment (units, vents, thermostats)
- Lighting (fixtures, switches, emergency lights)
- Plumbing (fixtures, water heaters, pipes)
- Electrical (panels, outlets, wiring)
- Flooring (carpet, tile, hardwood)
- Cabinets and countertops
- Appliances
- Windows and doors
- Fire safety (sprinklers, alarms, extinguishers)
- Specialty items (security systems, elevators, etc.)

Available tools:
- verify_room_classification: Check if room type makes sense given detected objects
- estimate_detection_confidence: Assess detection quality

After analysis, use verify_room_classification to validate your room type.
If confidence is low, consider if you should flag for human review.

Return your final analysis as JSON:
{
    "room_type": "kitchen|bathroom|bedroom|office|lobby|hallway|mechanical_room|storage|exterior|parking|other",
    "room_confidence": 0.0-1.0,
    "indoor_outdoor": "indoor|outdoor",
    "property_type": "residential|commercial|industrial",
    "description": "Brief description of what you see",
    "detected_objects": [
        {
            "label": "Object name",
            "confidence": 0.0-1.0,
            "description": "Brief description",
            "potential_asset": true/false
        }
    ]
}"""

    def get_tools(self) -> list[BaseTool]:
        return [
            verify_room_classification,
            estimate_detection_confidence,
        ]

    def get_output_schema(self) -> type[ImageAnalysisResult]:
        return ImageAnalysisResult

    def parse_output(
        self,
        response: str,
        tool_calls: list[dict],
    ) -> ImageAnalysisResult:
        """Parse agent response into ImageAnalysisResult."""
        parsed = parse_vision_response(response)

        if parsed:
            return build_analysis_result(parsed, image_id="")

        # Fallback if parsing fails
        return ImageAnalysisResult(
            image_id="",
            room_type="unknown",
            room_confidence=0.5,
            description=response[:200] if response else "Analysis completed",
            detected_objects=[],
        )


# =============================================================================
# Image Download Utilities
# =============================================================================


def _download_image_sync(url: str) -> Optional[bytes]:
    """Synchronous image download using urllib."""
    try:
        # Create SSL context that doesn't verify (for Firebase Storage URLs)
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; BasisBot/1.0)'}
        )
        with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
            if response.status == 200:
                return response.read()
    except Exception as e:
        logger.error(f"Error downloading image: {e}")
    return None


async def download_image_as_base64(url: str) -> Optional[str]:
    """Download an image and convert to base64."""
    try:
        # Run synchronous download in thread pool to avoid blocking
        image_data = await asyncio.to_thread(_download_image_sync, url)
        if image_data:
            return base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        logger.error(f"Error downloading image: {e}")
    return None


async def analyze_image(
    image_url: str,
    image_id: str,
    property_name: str = "",
    image_index: int = 0,
    total_images: int = 1,
) -> ImageAnalysisResult:
    """
    Analyze a single image using GPT-4 Vision.

    Args:
        image_url: URL to the image (Firebase Storage download URL)
        image_id: Identifier for this image
        property_name: Name of the property for context
        image_index: Index of this image (for logging)
        total_images: Total number of images (for logging)

    Returns:
        ImageAnalysisResult with room type and detected objects
    """
    image_start = time.time()
    tracer = get_tracer()

    with tracer.span("analyze_image_vision"):
        # Download and encode image
        download_start = time.time()
        image_base64 = await download_image_as_base64(image_url)
        download_elapsed = time.time() - download_start

        if not image_base64:
            # Return a default result if image can't be downloaded
            return ImageAnalysisResult(
                image_id=image_id,
                room_type="unknown",
                room_confidence=0.0,
                description="Could not download image for analysis",
                detected_objects=[],
            )

        # Determine image type from URL
        image_type = "image/jpeg"
        if ".png" in image_url.lower():
            image_type = "image/png"
        elif ".gif" in image_url.lower():
            image_type = "image/gif"
        elif ".webp" in image_url.lower():
            image_type = "image/webp"

        # Build the vision prompt
        system_prompt = """You are a cost segregation expert analyzing property photos.

Your task: Analyze this image to identify:
1. The room/space type (kitchen, bathroom, office, lobby, mechanical room, exterior, etc.)
2. All visible building components that could be depreciable assets
3. Whether this is indoor or outdoor
4. The likely property type (residential, commercial, or industrial)

For each detected object, identify items that are relevant to cost segregation:
- HVAC equipment (units, vents, thermostats)
- Lighting (fixtures, switches, emergency lights)
- Plumbing (fixtures, water heaters, pipes)
- Electrical (panels, outlets, wiring)
- Flooring (carpet, tile, hardwood)
- Cabinets and countertops
- Appliances
- Windows and doors
- Fire safety (sprinklers, alarms, extinguishers)
- Specialty items (security systems, elevators, etc.)

Return your analysis as JSON:
{
    "room_type": "kitchen|bathroom|bedroom|office|lobby|hallway|mechanical_room|storage|exterior|parking|other",
    "room_confidence": 0.0-1.0,
    "indoor_outdoor": "indoor|outdoor",
    "property_type": "residential|commercial|industrial",
    "description": "Brief description of what you see",
    "detected_objects": [
        {
            "label": "Object name",
            "confidence": 0.0-1.0,
            "description": "Brief description",
            "potential_asset": true/false
        }
    ]
}"""

        user_content = f"Analyze this property photo"
        if property_name:
            user_content += f" from '{property_name}'"
        user_content += ". Identify the room type and all visible building components."

        # Call GPT-4 Vision (uses Azure if configured, otherwise OpenAI)
        # Get retry settings
        settings = get_settings()
        MAX_PARSE_RETRIES = 2

        try:
            # Get vision LLM (GPT-4o) - supports both Azure and OpenAI
            model = get_vision_llm()

            # Create initial message with image
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_content},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_type};base64,{image_base64}",
                                "detail": "high",
                            },
                        },
                    ],
                },
            ]

            # Retry loop for parsing failures
            for attempt in range(MAX_PARSE_RETRIES + 1):
                vision_start = time.time()

                # Call with retry logic for rate limits
                response = await retry_with_backoff(
                    lambda: model.ainvoke(messages),
                    max_retries=settings.llm_max_retries,
                    base_delay=settings.llm_retry_base_delay,
                    max_delay=settings.llm_retry_max_delay,
                )
                vision_elapsed = time.time() - vision_start
                response_text = response.content

                # Log timing for this image
                image_elapsed = time.time() - image_start
                logger.info(
                    f"[TIMING] Image {image_index + 1}/{total_images}: {image_elapsed:.1f}s "
                    f"(download: {download_elapsed:.1f}s, vision: {vision_elapsed:.1f}s)"
                    + (f" [attempt {attempt + 1}]" if attempt > 0 else "")
                )

                # Use multi-strategy JSON parsing
                parsed = parse_vision_response(response_text)

                if parsed and parsed.get("room_type") != "unknown":
                    # Successfully parsed with valid room type
                    result = build_analysis_result(parsed, image_id)
                    return result

                # If parsing failed or got unknown, try to retry
                if attempt < MAX_PARSE_RETRIES:
                    logger.warning(
                        f"Vision parsing attempt {attempt + 1} failed or returned unknown, retrying..."
                    )
                    # Add clarification to prompt for retry
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({
                        "role": "user",
                        "content": "Please respond with ONLY valid JSON. No explanation, no markdown, just the raw JSON object with room_type, room_confidence, indoor_outdoor, property_type, description, and detected_objects fields."
                    })

            # After all retries, return best-effort result
            if parsed:
                return build_analysis_result(parsed, image_id)

            # Fallback: return basic result
            return ImageAnalysisResult(
                image_id=image_id,
                room_type="unknown",
                room_confidence=0.5,
                description=response_text[:200] if response_text else "Analysis completed",
                detected_objects=[],
            )

        except Exception as e:
            logger.error(f"Error calling vision model: {e}")
            return ImageAnalysisResult(
                image_id=image_id,
                room_type="unknown",
                room_confidence=0.0,
                description=f"Error during analysis: {str(e)}",
                detected_objects=[],
            )


async def analyze_study_images(
    uploaded_files: list[dict],
    property_name: str = "",
    max_concurrent: int = 2,  # Default to 2 concurrent workers
) -> tuple[list[dict], list[dict]]:
    """
    Analyze all images in a study IN PARALLEL.

    Uses max_concurrent workers to analyze images simultaneously.
    With 2 workers, ~50% faster than sequential.

    Timing logs:
    - [TIMING] Image X/N: Xs (download: Xs, vision: Xs) - per image
    - [TIMING] Vision analysis complete: N images in Xs (avg Xs/image, W workers)

    Args:
        uploaded_files: List of uploaded file metadata with downloadURL
        property_name: Property name for context
        max_concurrent: Maximum concurrent image analyses (default: 2)

    Returns:
        Tuple of (rooms, objects) lists for Firestore
    """
    from ..utils.parallel import parallel_map

    batch_start = time.time()
    tracer = get_tracer()

    with tracer.span("analyze_study_images"):
        # Filter to only image files
        image_files = [
            f for f in uploaded_files
            if f.get("type", "").startswith("image/")
        ]

        if not image_files:
            return [], []

        total_images = len(image_files)

        # Define the async function to analyze a single image with index tracking
        async def analyze_single(file_info_with_index: tuple[int, dict]) -> ImageAnalysisResult:
            idx, file_info = file_info_with_index
            download_url = file_info.get("downloadURL")
            file_id = file_info.get("id", "unknown")

            if not download_url:
                return ImageAnalysisResult(
                    image_id=file_id,
                    room_type="unknown",
                    room_confidence=0.0,
                    description="No download URL provided",
                    detected_objects=[],
                )

            return await analyze_image(
                image_url=download_url,
                image_id=file_id,
                property_name=property_name,
                image_index=idx,
                total_images=total_images,
            )

        # Add index to each file for tracking
        indexed_files = list(enumerate(image_files))

        # PARALLEL: Analyze all images concurrently with rate limiting
        results = await parallel_map(
            items=indexed_files,
            async_fn=analyze_single,
            max_concurrent=max_concurrent,
            desc=f"Analyzing {len(image_files)} images",
        )

        # Log summary timing
        batch_elapsed = time.time() - batch_start
        avg_per_image = batch_elapsed / total_images if total_images else 0
        logger.info(
            f"[TIMING] Vision analysis complete: {total_images} images in {batch_elapsed:.1f}s "
            f"(avg {avg_per_image:.1f}s/image, {max_concurrent} workers)"
        )

        # Process results into rooms and objects
        rooms = []
        all_objects = []
        object_id_counter = 1

        for idx, result in enumerate(results):
            file_info = image_files[idx]
            file_id = file_info.get("id", f"file-{idx + 1}")

            # Create room record
            room_id = f"room-{idx + 1}"
            room = {
                "id": room_id,
                "type": result.room_type,
                "name": f"{result.room_type.replace('_', ' ').title()}",
                "confidence": result.room_confidence,
                "indoor_outdoor": result.indoor_outdoor,
                "property_type": result.property_type,
                "description": result.description,
                "photoIds": [file_id],
                "sourceImageId": file_id,
            }
            rooms.append(room)

            # Create object records
            for detected in result.detected_objects:
                if detected.potential_asset:
                    obj = {
                        "id": f"object-{object_id_counter}",
                        "label": detected.label,
                        "original_label": detected.label,
                        "confidence": detected.confidence,
                        "description": detected.description,
                        "room_id": room_id,
                        "room_type": result.room_type,
                        "source_image_id": file_id,
                        "potential_asset": detected.potential_asset,
                    }
                    all_objects.append(obj)
                    object_id_counter += 1

        tracer.log_workflow_transition(
            study_id="",
            from_status="vision_analysis",
            to_status="vision_complete",
            stage_summary={
                "images_analyzed": len(image_files),
                "rooms_detected": len(rooms),
                "objects_detected": len(all_objects),
                "parallel_batch_size": max_concurrent,
            },
        )

        return rooms, all_objects
