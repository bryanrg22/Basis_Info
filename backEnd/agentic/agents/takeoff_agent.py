"""
Takeoff Agent - Quantity takeoff calculations.

Aggregates detections and applies measurement rules to calculate
quantities for cost estimation using RSMeans standards.

Phase 4 Enhancement: Domain-specific tools for quantity estimation.
"""

import json
import re
from typing import Optional

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext


# =============================================================================
# Domain-Specific Takeoff Tools (Phase 4)
# =============================================================================

# Unit conversion factors
UNIT_CONVERSIONS = {
    ("SF", "SY"): 1 / 9,  # Square feet to square yards
    ("SY", "SF"): 9,  # Square yards to square feet
    ("LF", "SF"): None,  # Requires width
    ("SF", "LF"): None,  # Requires width
    ("EA", "SF"): None,  # Component-specific
    ("CF", "SF"): None,  # Requires depth
    ("SF", "CF"): None,  # Requires depth
    ("LF", "IN"): 12,  # Linear feet to inches
    ("IN", "LF"): 1 / 12,  # Inches to linear feet
    ("AC", "SF"): 43560,  # Acres to square feet
    ("SF", "AC"): 1 / 43560,  # Square feet to acres
}

# Component quantity estimates per unit area
QUANTITY_ESTIMATES = {
    "light_fixture": {
        "unit": "EA",
        "per_sf": 0.01,  # 1 per 100 SF typical
        "min_per_room": 1,
        "notes": "Varies by room type and lighting design",
    },
    "electrical_outlet": {
        "unit": "EA",
        "per_sf": 0.02,  # 1 per 50 SF typical
        "min_per_room": 2,
        "notes": "NEC requires outlets every 12 feet along walls",
    },
    "hvac_vent": {
        "unit": "EA",
        "per_sf": 0.005,  # 1 per 200 SF typical
        "min_per_room": 1,
        "notes": "Based on CFM requirements and room size",
    },
    "fire_sprinkler": {
        "unit": "EA",
        "per_sf": 0.007,  # 1 per 140-225 SF per NFPA 13
        "min_per_room": 1,
        "notes": "NFPA 13 coverage requirements",
    },
    "carpet": {
        "unit": "SY",
        "per_sf": 1 / 9,  # 1 SY per 9 SF
        "waste_factor": 1.10,  # 10% waste
        "notes": "Add 10% for seams and waste",
    },
    "tile": {
        "unit": "SF",
        "per_sf": 1.0,
        "waste_factor": 1.15,  # 15% waste for cuts
        "notes": "Add 15% for cuts and waste",
    },
    "cabinet": {
        "unit": "LF",
        "per_sf": None,  # Based on perimeter
        "notes": "Measured in linear feet of base/wall cabinets",
    },
    "countertop": {
        "unit": "SF",
        "per_sf": None,  # Based on cabinet LF × depth
        "typical_depth_in": 25,  # 25 inch standard depth
        "notes": "Typically 25-inch depth for base cabinets",
    },
    "paint": {
        "unit": "SF",
        "per_sf": 3.5,  # Wall area ≈ 3.5× floor area (8ft ceiling)
        "notes": "Wall area estimate assumes 8ft ceiling",
    },
    "baseboard": {
        "unit": "LF",
        "per_sf": 0.4,  # Perimeter ≈ 0.4× area (for rectangular rooms)
        "notes": "Perimeter estimate for rectangular rooms",
    },
    "door": {
        "unit": "EA",
        "per_sf": 0.005,  # 1 per 200 SF typical
        "min_per_room": 1,
        "notes": "At least one entry door per room",
    },
    "window": {
        "unit": "EA",
        "per_sf": 0.01,  # Varies greatly by design
        "min_per_room": 0,
        "notes": "Highly variable by room and design",
    },
}

# Industry installation rates
INSTALLATION_RATES = {
    "light_fixture": {
        "labor_hours_per_unit": 0.5,
        "typical_crew": "1 electrician",
        "daily_production": 12,
        "unit": "EA",
    },
    "electrical_outlet": {
        "labor_hours_per_unit": 0.75,
        "typical_crew": "1 electrician",
        "daily_production": 10,
        "unit": "EA",
    },
    "carpet": {
        "labor_hours_per_unit": 0.025,  # Per SF
        "typical_crew": "2 carpet installers",
        "daily_production": 500,  # SF
        "unit": "SF",
    },
    "tile": {
        "labor_hours_per_unit": 0.1,  # Per SF
        "typical_crew": "1 tile setter + 1 helper",
        "daily_production": 80,  # SF
        "unit": "SF",
    },
    "cabinet": {
        "labor_hours_per_unit": 1.0,  # Per LF
        "typical_crew": "1 carpenter",
        "daily_production": 8,  # LF
        "unit": "LF",
    },
    "hvac_unit": {
        "labor_hours_per_unit": 8.0,
        "typical_crew": "1 HVAC technician + 1 helper",
        "daily_production": 1,
        "unit": "EA",
    },
    "paint": {
        "labor_hours_per_unit": 0.02,  # Per SF
        "typical_crew": "1 painter",
        "daily_production": 400,  # SF
        "unit": "SF",
    },
    "door": {
        "labor_hours_per_unit": 2.0,
        "typical_crew": "1 carpenter",
        "daily_production": 4,
        "unit": "EA",
    },
}


@tool
def lookup_unit_conversion(from_unit: str, to_unit: str, dimension: float = None) -> dict:
    """
    Look up conversion factor between units.

    Converts between common construction units (SF, SY, LF, EA, CF).
    Some conversions require additional dimension information.

    Args:
        from_unit: Source unit (SF, SY, LF, EA, CF, AC, IN)
        to_unit: Target unit
        dimension: Optional dimension for conversions requiring it (e.g., width for LF↔SF)

    Returns:
        Conversion factor and instructions
    """
    from_upper = from_unit.upper()
    to_upper = to_unit.upper()

    # Same unit
    if from_upper == to_upper:
        return {
            "from_unit": from_unit,
            "to_unit": to_unit,
            "factor": 1.0,
            "formula": f"1 {from_unit} = 1 {to_unit}",
        }

    # Look up direct conversion
    key = (from_upper, to_upper)
    if key in UNIT_CONVERSIONS:
        factor = UNIT_CONVERSIONS[key]
        if factor is None:
            if dimension:
                # Calculate with provided dimension
                if key == ("LF", "SF"):
                    factor = dimension  # LF × width = SF
                    return {
                        "from_unit": from_unit,
                        "to_unit": to_unit,
                        "factor": factor,
                        "formula": f"1 {from_unit} × {dimension} (width) = {factor} {to_unit}",
                        "dimension_used": dimension,
                    }
                elif key == ("SF", "LF"):
                    factor = 1 / dimension  # SF / width = LF
                    return {
                        "from_unit": from_unit,
                        "to_unit": to_unit,
                        "factor": factor,
                        "formula": f"1 {from_unit} ÷ {dimension} (width) = {factor:.4f} {to_unit}",
                        "dimension_used": dimension,
                    }
            return {
                "from_unit": from_unit,
                "to_unit": to_unit,
                "factor": None,
                "requires_dimension": True,
                "hint": f"Conversion from {from_unit} to {to_unit} requires a dimension (e.g., width)",
            }
        return {
            "from_unit": from_unit,
            "to_unit": to_unit,
            "factor": factor,
            "formula": f"1 {from_unit} = {factor} {to_unit}",
        }

    return {
        "from_unit": from_unit,
        "to_unit": to_unit,
        "factor": None,
        "error": f"No conversion found from {from_unit} to {to_unit}",
        "hint": "These units may not be directly convertible",
    }


@tool
def estimate_quantity_from_area(component_name: str, area_sf: float) -> dict:
    """
    Estimate component quantity based on room/area size.

    Uses industry standards to estimate how many of a component
    would typically be found in a given area.

    Args:
        component_name: Name of the component
        area_sf: Area in square feet

    Returns:
        Estimated quantity with unit and calculation notes
    """
    component_lower = component_name.lower().replace(" ", "_")

    # Try exact match
    if component_lower in QUANTITY_ESTIMATES:
        estimate = QUANTITY_ESTIMATES[component_lower]
        per_sf = estimate.get("per_sf")

        if per_sf:
            raw_quantity = area_sf * per_sf
            waste_factor = estimate.get("waste_factor", 1.0)
            min_qty = estimate.get("min_per_room", 0)

            quantity = max(raw_quantity * waste_factor, min_qty)

            return {
                "component": component_name,
                "found": True,
                "area_sf": area_sf,
                "estimated_quantity": round(quantity, 2),
                "unit": estimate["unit"],
                "calculation": f"{area_sf} SF × {per_sf} = {raw_quantity:.2f}, with waste factor {waste_factor}",
                "notes": estimate.get("notes", ""),
            }
        else:
            return {
                "component": component_name,
                "found": True,
                "area_sf": area_sf,
                "estimated_quantity": None,
                "unit": estimate["unit"],
                "notes": estimate.get("notes", ""),
                "hint": "This component requires measurement, not area-based estimation",
            }

    # Try partial match
    for key, estimate in QUANTITY_ESTIMATES.items():
        if key in component_lower or component_lower in key:
            per_sf = estimate.get("per_sf")
            if per_sf:
                raw_quantity = area_sf * per_sf
                waste_factor = estimate.get("waste_factor", 1.0)
                min_qty = estimate.get("min_per_room", 0)
                quantity = max(raw_quantity * waste_factor, min_qty)

                return {
                    "component": component_name,
                    "matched_to": key,
                    "found": True,
                    "area_sf": area_sf,
                    "estimated_quantity": round(quantity, 2),
                    "unit": estimate["unit"],
                    "calculation": f"{area_sf} SF × {per_sf} = {raw_quantity:.2f}",
                    "notes": estimate.get("notes", ""),
                }

    return {
        "component": component_name,
        "found": False,
        "area_sf": area_sf,
        "estimated_quantity": None,
        "hint": "No quantity estimate available. Use detection count or field measurement.",
    }


@tool
def get_industry_installation_rates(component_name: str) -> dict:
    """
    Get industry installation rates for a component.

    Provides labor hours, crew size, and daily production rates
    useful for validating takeoff quantities.

    Args:
        component_name: Name of the component

    Returns:
        Installation rate information
    """
    component_lower = component_name.lower().replace(" ", "_")

    # Try exact match
    if component_lower in INSTALLATION_RATES:
        rates = INSTALLATION_RATES[component_lower]
        return {
            "component": component_name,
            "found": True,
            **rates,
        }

    # Try partial match
    for key, rates in INSTALLATION_RATES.items():
        if key in component_lower or component_lower in key:
            return {
                "component": component_name,
                "matched_to": key,
                "found": True,
                **rates,
            }

    return {
        "component": component_name,
        "found": False,
        "hint": "No installation rates found. Refer to RSMeans for labor data.",
    }


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

    Phase 4 Enhancement: Domain-specific tools for quantity estimation.
    """

    def __init__(self):
        super().__init__(stage_name="takeoff")

    def get_tools(self) -> list[BaseTool]:
        """
        Return tools including domain-specific takeoff tools.

        Phase 4: Adds specialized quantity estimation tools.
        """
        from ..mcp_server.server import get_all_evidence_tools

        # Get base search tools
        base_tools = get_all_evidence_tools()

        # Add domain-specific takeoff tools
        takeoff_tools = [
            lookup_unit_conversion,
            estimate_quantity_from_area,
            get_industry_installation_rates,
        ]

        return base_tools + takeoff_tools

    def get_system_prompt(self) -> str:
        return """You are a construction estimator calculating quantity takeoffs using RSMeans data.

Your task: Calculate quantities and identify RSMeans line items for cost estimation.

## WORKFLOW

1. **Use Domain Tools First**: Get baseline estimates
   - estimate_quantity_from_area for area-based estimates
   - lookup_unit_conversion for unit conversions
   - get_industry_installation_rates for labor validation
2. **Search RSMeans**: Get specific line items and costs
3. **Validate**: Cross-check with industry rates
4. **Return Result**: Combine all information

## DOMAIN-SPECIFIC TOOLS (USE THESE FIRST)

- lookup_unit_conversion(from_unit, to_unit, dimension): Convert between units
- estimate_quantity_from_area(component_name, area_sf): Estimate quantities from area
- get_industry_installation_rates(component_name): Get labor and production rates

## SEARCH STRATEGY (AFTER DOMAIN TOOLS)

1. Use domain tools to get baseline quantity estimates
2. Then search RSMeans for specific line items:
   - hybrid_search(doc_id="RSMEANS_RSMEANS_BUILDING_2020", query="<component> unit cost")
   - For residential: hybrid_search(doc_id="RSMEANS_RSMEANS_RESIDENTIAL_2020", query="<component>")

## MEASUREMENT METHODS

- count: Individual items (appliances, fixtures) → EA (each)
- area: Surface coverage (flooring, paint) → SF (square feet)
- linear: Length-based (trim, wiring) → LF (linear feet)
- estimated: Based on typical values when detection data is limited

## UNIT CONVERSIONS

- If room area is provided, use it for SF-based components
- If dimensions are provided, calculate appropriately
- Use lookup_unit_conversion for complex conversions

## COMPONENT-SPECIFIC RULES

- Flooring: Use room_area_sf, unit=SF
- Light fixtures: Use detection_count, unit=EA
- Trim/molding: Estimate perimeter from area, unit=LF
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
