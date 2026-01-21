"""
Room Classification Agent - IRS context for scene classification.

Takes vision layer scene classifications and enriches them with
IRS-relevant context for downstream asset classification.

Phase 4 Enhancement: Domain-specific tools for room analysis.
"""

import json
import re
from typing import Optional

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext
from ..observability.alerts import alert_on_failure


# =============================================================================
# Domain-Specific Room Tools (Phase 4)
# =============================================================================

# IRS guidance mappings for room types
ROOM_IRS_GUIDANCE = {
    "kitchen": {
        "space_category": "unit_space",
        "typical_section": "1245",
        "guidance": "Kitchen improvements often qualify as Section 1245 personal property. "
                    "Cabinets, countertops, and appliances are typically 5-7 year property.",
        "asset_classes": ["57.0", "00.11"],
        "key_considerations": [
            "Built-in appliances vs freestanding affects classification",
            "Custom cabinetry may be Section 1250 if structural",
            "Countertops typically Section 1245, 5-year",
        ],
    },
    "bathroom": {
        "space_category": "unit_space",
        "typical_section": "1245",
        "guidance": "Bathroom fixtures are generally Section 1245 personal property. "
                    "Vanities, toilets, and specialized finishes qualify for accelerated depreciation.",
        "asset_classes": ["57.0"],
        "key_considerations": [
            "Plumbing fixtures typically Section 1245",
            "Tile and flooring may be Section 1250 depending on installation",
            "Mirrors and accessories are Section 1245",
        ],
    },
    "office": {
        "space_category": "unit_space",
        "typical_section": "mixed",
        "guidance": "Office spaces contain both Section 1245 and 1250 property. "
                    "Built-in millwork and carpet are typically 1245; walls and ceiling are 1250.",
        "asset_classes": ["00.11", "00.12"],
        "key_considerations": [
            "Carpet and flooring typically Section 1245, 5-year",
            "Lighting fixtures typically Section 1245",
            "Structural elements remain Section 1250",
        ],
    },
    "lobby": {
        "space_category": "common_area",
        "typical_section": "mixed",
        "guidance": "Lobby areas in commercial buildings contain significant Section 1245 property. "
                    "Decorative finishes, specialized lighting, and reception fixtures qualify.",
        "asset_classes": ["00.11"],
        "key_considerations": [
            "Decorative elements often Section 1245",
            "Reception desk may be 1245 if not built-in",
            "Specialized flooring and finishes may qualify",
        ],
    },
    "hallway": {
        "space_category": "common_area",
        "typical_section": "1250",
        "guidance": "Hallways are primarily Section 1250 real property. "
                    "However, carpet, lighting, and fire safety equipment may be Section 1245.",
        "asset_classes": ["00.11"],
        "key_considerations": [
            "Carpet typically Section 1245, 5-year",
            "Emergency lighting and fire equipment Section 1245",
            "Walls and ceiling Section 1250",
        ],
    },
    "mechanical_room": {
        "space_category": "service_area",
        "typical_section": "1245",
        "guidance": "Mechanical rooms contain primarily Section 1245 equipment. "
                    "HVAC systems, electrical panels, and plumbing equipment qualify.",
        "asset_classes": ["57.0", "00.3"],
        "key_considerations": [
            "HVAC equipment typically Section 1245, 5-15 year depending on type",
            "Electrical distribution equipment Section 1245",
            "Room structure remains Section 1250",
        ],
    },
    "storage": {
        "space_category": "service_area",
        "typical_section": "mixed",
        "guidance": "Storage areas have limited Section 1245 property. "
                    "Shelving and racking systems may qualify if not permanently attached.",
        "asset_classes": ["00.11"],
        "key_considerations": [
            "Freestanding shelving may be Section 1245",
            "Built-in storage typically Section 1250",
            "Lighting fixtures Section 1245",
        ],
    },
    "exterior": {
        "space_category": "exterior",
        "typical_section": "1250",
        "guidance": "Exterior improvements are typically Section 1250 land improvements. "
                    "Parking, sidewalks, and landscaping have 15-year recovery.",
        "asset_classes": ["00.3"],
        "key_considerations": [
            "Paving and parking are 15-year land improvements",
            "Landscaping typically 15-year",
            "Outdoor lighting may be Section 1245",
        ],
    },
    "parking": {
        "space_category": "exterior",
        "typical_section": "1250",
        "guidance": "Parking improvements are Section 1250 land improvements with 15-year recovery. "
                    "Includes paving, striping, curbs, and lighting infrastructure.",
        "asset_classes": ["00.3"],
        "key_considerations": [
            "Asphalt/concrete paving is 15-year",
            "Parking structure may have different treatment",
            "Parking lot lighting may be Section 1245",
        ],
    },
}

# Typical components found in each room type
ROOM_TYPICAL_COMPONENTS = {
    "kitchen": [
        "cabinet", "countertop", "sink", "faucet", "garbage disposal",
        "dishwasher", "range", "oven", "refrigerator", "microwave",
        "range hood", "light fixture", "flooring", "backsplash",
    ],
    "bathroom": [
        "toilet", "vanity", "sink", "faucet", "mirror", "light fixture",
        "shower", "tub", "tile", "exhaust fan", "towel bar", "flooring",
    ],
    "office": [
        "light fixture", "electrical outlet", "flooring", "carpet",
        "window treatment", "hvac vent", "thermostat", "fire sprinkler",
    ],
    "lobby": [
        "reception desk", "light fixture", "flooring", "elevator",
        "security system", "fire panel", "signage", "decorative element",
    ],
    "hallway": [
        "light fixture", "flooring", "carpet", "fire extinguisher",
        "exit sign", "emergency light", "door", "hvac vent",
    ],
    "mechanical_room": [
        "hvac unit", "air handler", "boiler", "water heater",
        "electrical panel", "generator", "pump", "pipe", "duct",
    ],
    "storage": [
        "shelving", "light fixture", "door", "flooring",
    ],
    "exterior": [
        "door", "window", "roofing", "siding", "gutter",
        "landscaping", "fence", "lighting", "signage",
    ],
    "parking": [
        "pavement", "striping", "curb", "light pole",
        "parking meter", "signage", "bollard",
    ],
}

# Typical room area estimates by property type
ROOM_AREA_ESTIMATES = {
    "residential": {
        "kitchen": {"min": 80, "typical": 150, "max": 400},
        "bathroom": {"min": 35, "typical": 75, "max": 150},
        "bedroom": {"min": 100, "typical": 150, "max": 300},
        "office": {"min": 80, "typical": 120, "max": 200},
        "hallway": {"min": 20, "typical": 50, "max": 100},
        "storage": {"min": 10, "typical": 30, "max": 100},
    },
    "commercial": {
        "office": {"min": 100, "typical": 200, "max": 500},
        "lobby": {"min": 200, "typical": 500, "max": 2000},
        "hallway": {"min": 50, "typical": 150, "max": 500},
        "bathroom": {"min": 50, "typical": 100, "max": 300},
        "mechanical_room": {"min": 100, "typical": 300, "max": 1000},
        "storage": {"min": 50, "typical": 200, "max": 1000},
    },
    "industrial": {
        "office": {"min": 100, "typical": 200, "max": 400},
        "mechanical_room": {"min": 200, "typical": 500, "max": 2000},
        "storage": {"min": 500, "typical": 2000, "max": 10000},
        "bathroom": {"min": 50, "typical": 100, "max": 200},
    },
}


@tool
def search_room_irs_guidance(room_type: str) -> dict:
    """
    Get IRS-specific guidance for a room type.

    Provides pre-compiled guidance about typical Section classifications,
    asset classes, and key considerations for cost segregation.

    Args:
        room_type: The room type to get guidance for

    Returns:
        IRS guidance including typical section, asset classes, and considerations
    """
    room_lower = room_type.lower().replace(" ", "_")

    # Try exact match first
    if room_lower in ROOM_IRS_GUIDANCE:
        guidance = ROOM_IRS_GUIDANCE[room_lower]
        return {
            "room_type": room_type,
            "found": True,
            **guidance,
        }

    # Try partial match
    for key, guidance in ROOM_IRS_GUIDANCE.items():
        if key in room_lower or room_lower in key:
            return {
                "room_type": room_type,
                "matched_to": key,
                "found": True,
                **guidance,
            }

    # No match - return generic guidance
    return {
        "room_type": room_type,
        "found": False,
        "space_category": "unknown",
        "typical_section": "mixed",
        "guidance": "No specific IRS guidance found for this room type. "
                    "Search the IRS Cost Segregation ATG for specific guidance.",
        "asset_classes": [],
        "key_considerations": [
            "Evaluate each component individually",
            "Consider attachment method and function",
            "Search IRS guidance for specific components",
        ],
    }


@tool
def get_typical_room_components(room_type: str) -> dict:
    """
    Get a list of components typically found in a room type.

    Useful for validating detected objects and identifying potentially
    missed components that should be searched for.

    Args:
        room_type: The room type to get components for

    Returns:
        List of typical components and guidance on what to look for
    """
    room_lower = room_type.lower().replace(" ", "_")

    # Try exact match
    if room_lower in ROOM_TYPICAL_COMPONENTS:
        components = ROOM_TYPICAL_COMPONENTS[room_lower]
        return {
            "room_type": room_type,
            "found": True,
            "typical_components": components,
            "hint": f"Ensure these components are considered: {', '.join(components[:5])}...",
        }

    # Try partial match
    for key, components in ROOM_TYPICAL_COMPONENTS.items():
        if key in room_lower or room_lower in key:
            return {
                "room_type": room_type,
                "matched_to": key,
                "found": True,
                "typical_components": components,
                "hint": f"Ensure these components are considered: {', '.join(components[:5])}...",
            }

    return {
        "room_type": room_type,
        "found": False,
        "typical_components": [
            "light fixture", "flooring", "hvac vent", "electrical outlet", "door"
        ],
        "hint": "Generic room - look for standard building components.",
    }


@tool
def estimate_room_area(room_type: str, property_type: str = "commercial") -> dict:
    """
    Estimate typical room area based on room type and property type.

    Provides min, typical, and max area estimates in square feet.
    Useful for quantity takeoffs when actual measurements are not available.

    Args:
        room_type: The room type
        property_type: The property type (residential, commercial, industrial)

    Returns:
        Area estimates in square feet
    """
    room_lower = room_type.lower().replace(" ", "_")
    prop_lower = property_type.lower()

    # Normalize property type
    if prop_lower not in ROOM_AREA_ESTIMATES:
        prop_lower = "commercial"  # Default to commercial

    property_areas = ROOM_AREA_ESTIMATES[prop_lower]

    # Try exact match
    if room_lower in property_areas:
        estimates = property_areas[room_lower]
        return {
            "room_type": room_type,
            "property_type": property_type,
            "found": True,
            "area_sf": estimates,
            "recommended_estimate": estimates["typical"],
            "unit": "SF",
        }

    # Try partial match
    for key, estimates in property_areas.items():
        if key in room_lower or room_lower in key:
            return {
                "room_type": room_type,
                "matched_to": key,
                "property_type": property_type,
                "found": True,
                "area_sf": estimates,
                "recommended_estimate": estimates["typical"],
                "unit": "SF",
            }

    # Default estimates
    return {
        "room_type": room_type,
        "property_type": property_type,
        "found": False,
        "area_sf": {"min": 100, "typical": 200, "max": 500},
        "recommended_estimate": 200,
        "unit": "SF",
        "note": "Using default estimates - actual measurement recommended",
    }


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

    Phase 4 Enhancement: Domain-specific tools for room analysis.
    """

    def __init__(self):
        super().__init__(stage_name="room_context")

    def get_tools(self) -> list[BaseTool]:
        """
        Return tools including domain-specific room tools.

        Phase 4: Adds specialized room analysis tools.
        """
        from ..mcp_server.server import get_all_evidence_tools

        # Get base search tools
        base_tools = get_all_evidence_tools()

        # Add domain-specific room tools
        room_tools = [
            search_room_irs_guidance,
            get_typical_room_components,
            estimate_room_area,
        ]

        return base_tools + room_tools

    def get_system_prompt(self) -> str:
        return """You are a cost segregation expert determining IRS context for room classifications.

Your task: Enrich room classifications with IRS-relevant context for asset classification.

## WORKFLOW

1. **Get Domain Guidance**: Use search_room_irs_guidance to get pre-compiled IRS guidance
2. **Search IRS Corpus**: Search for additional guidance specific to this room
3. **Get Typical Components**: Use get_typical_room_components to know what to expect
4. **Estimate Area**: Use estimate_room_area if area is not provided
5. **Return Enriched Context**: Combine all information

## DOMAIN-SPECIFIC TOOLS (USE THESE FIRST)

- search_room_irs_guidance(room_type): Get IRS-specific room guidance
- get_typical_room_components(room_type): Get list of typical components
- estimate_room_area(room_type, property_type): Get area estimates

## SEARCH STRATEGY (AFTER DOMAIN TOOLS)

1. Use domain tools first to get baseline guidance
2. Then search IRS guidance for specific details:
   - hybrid_search(doc_id="IRS_IRS_COST_SEG_ATG__2024", query="<room_type> cost segregation")
   - For residential: hybrid_search(doc_id="IRS_IRS_PUB_527__2024", query="rental property")

## SPACE CATEGORIES

- common_area: Lobbies, hallways, elevators, shared facilities
- unit_space: Individual units, apartments, offices
- service_area: Mechanical rooms, storage, utility areas
- exterior: Outdoor areas, parking, landscaping

## PROPERTY CLASSES

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


@alert_on_failure("room_agent", study_id_param="context")
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


@alert_on_failure("room_agent", study_id_param="context")
async def enrich_rooms_batch(
    rooms: list[dict],
    context: StageContext,
    max_concurrent: int = 1,  # Sequential for rate limit
) -> list[dict]:
    """
    Enrich multiple rooms IN PARALLEL with IRS context.

    Args:
        rooms: List of room dicts from vision layer
        context: Study context with available documents
        max_concurrent: Maximum concurrent enrichments (default: 3)

    Returns:
        List of enriched room dicts with IRS context
    """
    from ..utils.parallel import parallel_map

    if not rooms:
        return []

    async def enrich_single_room(room: dict) -> dict:
        """Enrich a single room and merge results."""
        result = await enrich_room_context(
            image_id=room.get("sourceImageId", room.get("id", "")),
            room_type=room.get("type", room.get("room_type", "unknown")),
            context=context,
            room_confidence=room.get("confidence", 0.5),
            indoor_outdoor=room.get("indoor_outdoor", "indoor"),
            property_type=room.get("property_type"),
        )

        # Merge enrichment into room data
        return {
            **room,
            "context": result.get("context"),
            "enrichment_confidence": result.get("confidence", 0),
            "citations": result.get("citations", []),
            "needs_review": result.get("needs_review", False),
        }

    # PARALLEL: Enrich all rooms concurrently
    enriched_rooms = await parallel_map(
        items=rooms,
        async_fn=enrich_single_room,
        max_concurrent=max_concurrent,
        desc=f"Enriching {len(rooms)} rooms",
    )

    return enriched_rooms
