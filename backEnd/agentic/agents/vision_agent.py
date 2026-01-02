"""
Vision Analysis Agent - Analyzes images to detect rooms and objects.

Uses GPT-4 Vision to analyze uploaded property photos and identify:
1. Room type (kitchen, bathroom, office, etc.)
2. Objects/components visible in the image
3. Property characteristics relevant to cost segregation
"""

import asyncio
import base64
import json
import re
import urllib.request
import ssl
from typing import Optional
from pydantic import BaseModel, Field

from ..config.settings import get_settings
from ..observability.tracing import get_tracer


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
        print(f"Error downloading image: {e}")
    return None


async def download_image_as_base64(url: str) -> Optional[str]:
    """Download an image and convert to base64."""
    try:
        # Run synchronous download in thread pool to avoid blocking
        image_data = await asyncio.to_thread(_download_image_sync, url)
        if image_data:
            return base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        print(f"Error downloading image: {e}")
    return None


async def analyze_image(
    image_url: str,
    image_id: str,
    property_name: str = "",
) -> ImageAnalysisResult:
    """
    Analyze a single image using GPT-4 Vision.

    Args:
        image_url: URL to the image (Firebase Storage download URL)
        image_id: Identifier for this image
        property_name: Name of the property for context

    Returns:
        ImageAnalysisResult with room type and detected objects
    """
    tracer = get_tracer()
    settings = get_settings()

    with tracer.span("analyze_image_vision"):
        # Download and encode image
        image_base64 = await download_image_as_base64(image_url)

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

        # Call GPT-4 Vision
        try:
            from langchain_openai import ChatOpenAI

            # Use GPT-4 Vision model
            model = ChatOpenAI(
                model="gpt-4o",  # GPT-4o has vision capabilities
                temperature=0.1,
                api_key=settings.openai_api_key,
            )

            # Create message with image
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

            response = await model.ainvoke(messages)
            response_text = response.content

            # Parse JSON from response
            json_match = re.search(r'\{[^{}]*"room_type".*?\}', response_text, re.DOTALL)
            if json_match:
                # Try to find the full JSON including nested objects array
                try:
                    # Find all JSON-like content
                    start = response_text.find('{')
                    end = response_text.rfind('}') + 1
                    if start >= 0 and end > start:
                        json_str = response_text[start:end]
                        data = json.loads(json_str)

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
                except json.JSONDecodeError:
                    pass

            # Fallback: return basic result
            return ImageAnalysisResult(
                image_id=image_id,
                room_type="unknown",
                room_confidence=0.5,
                description=response_text[:200] if response_text else "Analysis completed",
                detected_objects=[],
            )

        except Exception as e:
            print(f"Error calling vision model: {e}")
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
) -> tuple[list[dict], list[dict]]:
    """
    Analyze all images in a study.

    Args:
        uploaded_files: List of uploaded file metadata with downloadURL
        property_name: Property name for context

    Returns:
        Tuple of (rooms, objects) lists for Firestore
    """
    tracer = get_tracer()

    with tracer.span("analyze_study_images"):
        rooms = []
        all_objects = []
        room_id_counter = 1
        object_id_counter = 1

        # Filter to only image files
        image_files = [
            f for f in uploaded_files
            if f.get("type", "").startswith("image/")
        ]

        if not image_files:
            return [], []

        # Analyze each image
        for file_info in image_files:
            download_url = file_info.get("downloadURL")
            file_id = file_info.get("id", f"file-{room_id_counter}")

            if not download_url:
                continue

            result = await analyze_image(
                image_url=download_url,
                image_id=file_id,
                property_name=property_name,
            )

            # Create room record
            room_id = f"room-{room_id_counter}"
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
            room_id_counter += 1

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
                        "source_image_id": file_id,
                        "potential_asset": detected.potential_asset,
                    }
                    all_objects.append(obj)
                    object_id_counter += 1

        return rooms, all_objects
