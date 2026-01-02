"""
Room Classification Agent - IRS context for scene classification.

Takes vision layer scene classifications and enriches them with
IRS-relevant context for downstream asset classification.
"""

import json
import re
from typing import Optional

from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext


# =============================================================================
# Input/Output Schemas
# =============================================================================


class RoomInput(BaseModel):
    """Input for room context enrichment."""

    image_id: str = Field(..., description="Source image identifier")
    room_type: str = Field(..., description="Room type from vision layer (e.g., 'kitchen', 'office')")
    room_confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Vision confidence"
    )
    indoor_outdoor: str = Field(
        default="indoor",
        description="Indoor/outdoor classification",
    )
    property_type: Optional[str] = Field(
        default=None,
        description="Property type: 'residential', 'commercial', 'industrial'",
    )


class RoomContext(BaseModel):
    """Enriched room context for asset classification."""

    room_type: str = Field(..., description="Normalized room type")
    irs_space_category: str = Field(
        ...,
        description="IRS space category: 'common_area', 'unit_space', 'service_area', 'exterior'",
    )
    property_class: str = Field(
        ...,
        description="Property class for depreciation: 'residential_rental', 'commercial', 'industrial'",
    )
    indoor_outdoor: str = Field(..., description="Indoor or outdoor")
    default_recovery_period: int = Field(
        ..., description="Default building recovery period (27.5 or 39 years)"
    )
    asset_class_hint: Optional[str] = Field(
        default=None,
        description="Suggested asset class code for components in this room",
    )
    component_expectations: list[str] = Field(
        default_factory=list,
        description="Expected component types in this room type",
    )
    irs_note: str = Field(
        ..., description="IRS guidance note for this room context"
    )
    citation_refs: list[str] = Field(
        default_factory=list,
        description="Referenced chunk_ids or table_ids",
    )


# =============================================================================
# Room Context Agent
# =============================================================================


class RoomContextAgent(BaseStageAgent[RoomInput, RoomContext]):
    """
    Agent for enriching room classifications with IRS context.

    Takes vision layer room classifications and provides IRS-relevant
    context for downstream asset classification decisions.
    """

    def __init__(self):
        super().__init__(stage_name="room_context")

    def get_system_prompt(self) -> str:
        return """You are a cost segregation expert determining IRS context for room classifications.

Your task: Enrich room classifications with IRS-relevant context for asset classification.

## CRITICAL RULES

1. **Evidence Required**: Search IRS guidance to determine:
   - Whether this is a "common area" vs "unit space" (affects 1245 vs 1250)
   - The property class (residential rental = 27.5 year, commercial = 39 year)
   - Expected component types and their likely classifications

2. **Search Strategy**:
   - hybrid_search(doc_id="IRS_IRS_COST_SEG_ATG__2024", query="<room_type> cost segregation")
   - For residential: hybrid_search(doc_id="IRS_IRS_PUB_527__2024", query="rental property")

3. **Space Categories**:
   - common_area: Lobbies, hallways, elevators, shared facilities
   - unit_space: Individual units, apartments, offices
   - service_area: Mechanical rooms, storage, utility areas
   - exterior: Outdoor areas, parking, landscaping

4. **Property Classes**:
   - residential_rental: 27.5-year recovery (apartments, condos, houses)
   - commercial: 39-year recovery (offices, retail, warehouses)
   - industrial: 39-year recovery (manufacturing, distribution)

## OUTPUT FORMAT

Return a JSON object:
{
    "room_type": "<normalized room type>",
    "irs_space_category": "common_area|unit_space|service_area|exterior",
    "property_class": "residential_rental|commercial|industrial",
    "indoor_outdoor": "indoor|outdoor",
    "default_recovery_period": 27 or 39,
    "asset_class_hint": "<asset class code if applicable>",
    "component_expectations": ["carpet", "light fixtures", "HVAC", ...],
    "irs_note": "<Brief IRS context explaining the classification>",
    "citation_refs": ["<chunk_id>", ...]
}

## DOCUMENT IDS

- IRS_IRS_COST_SEG_ATG__2024: Cost Segregation Audit Techniques Guide
- IRS_IRS_PUB_527__2024: Residential Rental Property
- IRS_IRS_PUB_946__2024: How To Depreciate Property

Always use corpus="reference" for IRS documents."""

    def get_output_schema(self) -> type[RoomContext]:
        return RoomContext

    def parse_output(
        self,
        response: str,
        tool_calls: list[dict],
    ) -> RoomContext:
        """Parse agent response into structured room context."""
        # Try to find JSON in response
        json_patterns = [
            r'\{[^{}]*"room_type"[^{}]*\}',
            r'```json\s*(\{.*?\})\s*```',
            r'```\s*(\{.*?\})\s*```',
        ]

        for pattern in json_patterns:
            matches = re.findall(pattern, response, re.DOTALL)
            for match in matches:
                try:
                    json_str = match.strip() if isinstance(match, str) else match
                    if not json_str.startswith("{"):
                        continue

                    data = json.loads(json_str)

                    if "room_type" in data and "irs_space_category" in data:
                        return RoomContext(
                            room_type=data["room_type"],
                            irs_space_category=data["irs_space_category"],
                            property_class=data.get("property_class", "commercial"),
                            indoor_outdoor=data.get("indoor_outdoor", "indoor"),
                            default_recovery_period=data.get("default_recovery_period", 39),
                            asset_class_hint=data.get("asset_class_hint"),
                            component_expectations=data.get("component_expectations", []),
                            irs_note=data.get("irs_note", "Room context from IRS guidance"),
                            citation_refs=data.get("citation_refs", []),
                        )
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        # Fallback with defaults
        raise ValueError(f"Could not parse room context from response: {response[:500]}")


# =============================================================================
# Convenience Functions
# =============================================================================


async def enrich_room_context(
    image_id: str,
    room_type: str,
    context: StageContext,
    room_confidence: float = 0.5,
    indoor_outdoor: str = "indoor",
    property_type: Optional[str] = None,
) -> dict:
    """
    Convenience function to enrich a room classification.

    Args:
        image_id: Source image identifier
        room_type: Room type from vision layer
        context: Study context with available documents
        room_confidence: Vision confidence
        indoor_outdoor: Indoor/outdoor classification
        property_type: Property type

    Returns:
        Enriched room context with IRS relevance
    """
    agent = RoomContextAgent()

    input_data = RoomInput(
        image_id=image_id,
        room_type=room_type,
        room_confidence=room_confidence,
        indoor_outdoor=indoor_outdoor,
        property_type=property_type,
    )

    result = await agent.run(context, input_data)

    return {
        "image_id": image_id,
        "original_room_type": room_type,
        "context": result.result.model_dump() if result.result else None,
        "citations": [c.model_dump() for c in result.citations],
        "confidence": result.confidence,
        "needs_review": result.needs_review,
    }
