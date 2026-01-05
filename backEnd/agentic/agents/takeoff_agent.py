"""
Takeoff Agent - Quantity takeoff calculations.

Aggregates detections and applies measurement rules to calculate
quantities for cost estimation using RSMeans standards.
"""

import json
import re
from typing import Optional

from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext


# =============================================================================
# Input/Output Schemas
# =============================================================================


class TakeoffInput(BaseModel):
    """Input for quantity takeoff calculation."""

    component_name: str = Field(..., description="Component type for takeoff")
    detection_count: int = Field(
        default=1, ge=1, description="Number of detections"
    )
    room_type: Optional[str] = Field(
        default=None, description="Room type for context"
    )
    room_area_sf: Optional[float] = Field(
        default=None, ge=0, description="Room area in square feet (if known)"
    )
    unit_dimensions: Optional[dict] = Field(
        default=None,
        description="Detected dimensions (width, height, depth in inches)",
    )
    property_type: str = Field(
        default="commercial",
        description="Property type: 'residential', 'commercial', 'industrial'",
    )


class TakeoffResult(BaseModel):
    """Quantity takeoff result with RSMeans unit references."""

    component_name: str = Field(..., description="Component type")
    quantity: float = Field(..., ge=0, description="Calculated quantity")
    unit: str = Field(
        ...,
        description="RSMeans unit: 'EA' (each), 'SF' (sq ft), 'LF' (linear ft), 'CF' (cubic ft)",
    )
    measurement_method: str = Field(
        ...,
        description="How quantity was determined: 'count', 'area', 'linear', 'estimated'",
    )
    rsmeans_line_item: Optional[str] = Field(
        default=None,
        description="RSMeans line item reference (e.g., '09 68 13.10')",
    )
    unit_cost_reference: Optional[float] = Field(
        default=None, ge=0, description="Reference unit cost from RSMeans"
    )
    cost_basis: Optional[str] = Field(
        default=None,
        description="Cost basis description from RSMeans",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Assumptions made in the calculation",
    )
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Confidence in quantity"
    )
    rsmeans_note: str = Field(
        ..., description="RSMeans reference note"
    )
    citation_refs: list[str] = Field(
        default_factory=list,
        description="Referenced chunk_ids or table_ids from RSMeans",
    )


# =============================================================================
# Takeoff Agent
# =============================================================================


class TakeoffAgent(BaseStageAgent[TakeoffInput, TakeoffResult]):
    """
    Agent for calculating quantity takeoffs.

    Uses RSMeans data to determine appropriate units and
    provides cost basis references for estimation.
    """

    def __init__(self):
        super().__init__(stage_name="takeoff")

    def get_system_prompt(self) -> str:
        return """You are a construction estimator calculating quantity takeoffs using RSMeans data.

Your task: Calculate quantities and identify RSMeans line items for cost estimation.

## CRITICAL RULES

1. **Evidence Required**: Search RSMeans to find:
   - Appropriate measurement unit (EA, SF, LF, CF)
   - Line item reference for the component
   - Unit cost basis for estimation

2. **Search Strategy**:
   - hybrid_search(doc_id="RSMEANS_RSMEANS_BUILDING_2020", query="<component> unit cost")
   - For residential: hybrid_search(doc_id="RSMEANS_RSMEANS_RESIDENTIAL_2020", query="<component>")

3. **Measurement Methods**:
   - count: Individual items (appliances, fixtures) → EA (each)
   - area: Surface coverage (flooring, paint) → SF (square feet)
   - linear: Length-based (trim, wiring) → LF (linear feet)
   - estimated: Based on typical values when detection data is limited

4. **Unit Conversions**:
   - If room area is provided, use it for SF-based components
   - If dimensions are provided, calculate appropriately
   - If only count is available, use EA

5. **Component-Specific Rules**:
   - Flooring: Use room_area_sf, unit=SF
   - Light fixtures: Use detection_count, unit=EA
   - Trim/molding: Estimate perimeter = 2*(room_length + room_width), unit=LF
   - Appliances: Use detection_count, unit=EA
   - HVAC units: Use detection_count, unit=EA

## OUTPUT FORMAT

Return a JSON object:
{
    "component_name": "<component>",
    "quantity": <number>,
    "unit": "EA|SF|LF|CF",
    "measurement_method": "count|area|linear|estimated",
    "rsmeans_line_item": "<line item code>",
    "unit_cost_reference": <cost if found>,
    "cost_basis": "<what the cost includes>",
    "assumptions": ["<assumption 1>", ...],
    "confidence": 0.0-1.0,
    "rsmeans_note": "<RSMeans reference note>",
    "citation_refs": ["<chunk_id>", ...]
}

## DOCUMENT IDS

- RSMEANS_RSMEANS_BUILDING_2020: Building Construction Costs
- RSMEANS_RSMEANS_RESIDENTIAL_2020: Residential Construction Costs

Always use corpus="reference" for RSMeans documents."""

    def get_output_schema(self) -> type[TakeoffResult]:
        return TakeoffResult

    def parse_output(
        self,
        response: str,
        tool_calls: list[dict],
    ) -> TakeoffResult:
        """Parse agent response into structured takeoff result."""
        json_patterns = [
            r'\{[^{}]*"component_name"[^{}]*"quantity"[^{}]*\}',
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

                    if "component_name" in data and "quantity" in data:
                        return TakeoffResult(
                            component_name=data["component_name"],
                            quantity=float(data["quantity"]),
                            unit=data.get("unit", "EA"),
                            measurement_method=data.get("measurement_method", "count"),
                            rsmeans_line_item=data.get("rsmeans_line_item"),
                            unit_cost_reference=data.get("unit_cost_reference"),
                            cost_basis=data.get("cost_basis"),
                            assumptions=data.get("assumptions", []),
                            confidence=data.get("confidence", 0.5),
                            rsmeans_note=data.get("rsmeans_note", "Based on RSMeans data"),
                            citation_refs=data.get("citation_refs", []),
                        )
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        raise ValueError(f"Could not parse takeoff from response: {response[:500]}")


# =============================================================================
# Convenience Functions
# =============================================================================


async def calculate_takeoff(
    component_name: str,
    context: StageContext,
    detection_count: int = 1,
    room_type: Optional[str] = None,
    room_area_sf: Optional[float] = None,
    property_type: str = "commercial",
) -> dict:
    """
    Convenience function to calculate a takeoff.

    Args:
        component_name: Component type
        context: Study context
        detection_count: Number of detections
        room_type: Room type
        room_area_sf: Room area in square feet
        property_type: Property type

    Returns:
        Takeoff result with RSMeans references
    """
    agent = TakeoffAgent()

    input_data = TakeoffInput(
        component_name=component_name,
        detection_count=detection_count,
        room_type=room_type,
        room_area_sf=room_area_sf,
        property_type=property_type,
    )

    result = await agent.run(context, input_data)

    return {
        "component_name": component_name,
        "takeoff": result.result.model_dump() if result.result else None,
        "citations": [c.model_dump() for c in result.citations],
        "confidence": result.confidence,
        "needs_review": result.needs_review,
    }


async def calculate_takeoffs_batch(
    components: list[dict],
    context: StageContext,
    room_type: Optional[str] = None,
    room_area_sf: Optional[float] = None,
    max_concurrent: int = 1,  # Sequential for rate limit
) -> list[dict]:
    """
    Calculate takeoffs for multiple components IN PARALLEL.

    Args:
        components: List of dicts with 'component_name' and optional 'detection_count'
        context: Study context
        room_type: Room type (applied to all)
        room_area_sf: Room area (applied to all)
        max_concurrent: Maximum concurrent calculations (default: 3)

    Returns:
        List of takeoff results
    """
    from ..utils.parallel import parallel_map

    if not components:
        return []

    async def calculate_single_takeoff(comp: dict) -> dict:
        """Calculate takeoff for a single component."""
        result = await calculate_takeoff(
            component_name=comp.get("component_name", comp.get("name", "")),
            context=context,
            detection_count=comp.get("detection_count", comp.get("count", 1)),
            room_type=room_type or comp.get("room_type"),
            room_area_sf=room_area_sf or comp.get("room_area_sf"),
            property_type=comp.get("property_type", "commercial"),
        )
        result["original"] = comp
        return result

    # PARALLEL: Calculate all takeoffs concurrently
    results = await parallel_map(
        items=components,
        async_fn=calculate_single_takeoff,
        max_concurrent=max_concurrent,
        desc=f"Calculating {len(components)} takeoffs",
    )

    return results
