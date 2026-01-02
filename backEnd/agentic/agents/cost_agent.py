"""
Cost Estimation Agent - RSMeans-backed cost calculations.

Calculates component costs using RSMeans data with proper
citations for audit defensibility.
"""

import json
import re
from typing import Optional

from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext


# =============================================================================
# Input/Output Schemas
# =============================================================================


class CostInput(BaseModel):
    """Input for cost estimation."""

    component_name: str = Field(..., description="Component type")
    quantity: float = Field(..., ge=0, description="Quantity from takeoff")
    unit: str = Field(
        ...,
        description="RSMeans unit: 'EA', 'SF', 'LF', 'CF'",
    )
    quality_tier: str = Field(
        default="standard",
        description="Quality tier: 'economy', 'standard', 'premium', 'luxury'",
    )
    location_factor: float = Field(
        default=1.0, ge=0.5, le=2.0, description="Location cost adjustment factor"
    )
    year_factor: float = Field(
        default=1.0, ge=0.8, le=1.5, description="Year adjustment factor (RSMeans 2020 base)"
    )
    property_type: str = Field(
        default="commercial",
        description="Property type: 'residential', 'commercial', 'industrial'",
    )


class CostEstimate(BaseModel):
    """Detailed cost estimate with RSMeans backing."""

    component_name: str = Field(..., description="Component type")
    quantity: float = Field(..., ge=0, description="Quantity used")
    unit: str = Field(..., description="Unit of measure")

    # Unit cost breakdown
    material_cost_per_unit: float = Field(
        ..., ge=0, description="Material cost per unit"
    )
    labor_cost_per_unit: float = Field(
        ..., ge=0, description="Labor cost per unit"
    )
    equipment_cost_per_unit: float = Field(
        default=0.0, ge=0, description="Equipment cost per unit"
    )
    total_cost_per_unit: float = Field(
        ..., ge=0, description="Total cost per unit"
    )

    # Extended costs
    base_extended_cost: float = Field(
        ..., ge=0, description="Base extended cost (quantity × unit cost)"
    )
    location_adjusted_cost: float = Field(
        ..., ge=0, description="After location factor"
    )
    final_cost: float = Field(
        ..., ge=0, description="Final adjusted cost"
    )

    # RSMeans references
    rsmeans_line_item: Optional[str] = Field(
        default=None, description="RSMeans line item code"
    )
    rsmeans_description: Optional[str] = Field(
        default=None, description="RSMeans line item description"
    )
    cost_includes: list[str] = Field(
        default_factory=list,
        description="What the cost includes (labor, materials, etc.)",
    )
    cost_excludes: list[str] = Field(
        default_factory=list,
        description="What the cost excludes",
    )

    # Adjustments applied
    quality_adjustment: Optional[float] = Field(
        default=None, description="Quality tier adjustment applied"
    )
    year_adjustment: Optional[float] = Field(
        default=None, description="Year adjustment applied"
    )

    # Confidence and notes
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Confidence in estimate"
    )
    rsmeans_note: str = Field(
        ..., description="RSMeans citation and methodology"
    )
    citation_refs: list[str] = Field(
        default_factory=list,
        description="Referenced chunk_ids or table_ids",
    )


# =============================================================================
# Cost Estimation Agent
# =============================================================================


class CostEstimationAgent(BaseStageAgent[CostInput, CostEstimate]):
    """
    Agent for calculating component costs using RSMeans.

    Searches RSMeans for unit costs and applies appropriate
    adjustments for location, quality, and time.
    """

    def __init__(self):
        super().__init__(stage_name="cost_estimation")

    def get_system_prompt(self) -> str:
        return """You are a construction cost estimator using RSMeans data.

Your task: Calculate component costs with proper RSMeans citations.

## CRITICAL RULES

1. **Evidence Required**: Search RSMeans to find:
   - Unit costs (material, labor, equipment)
   - Line item descriptions
   - What's included/excluded in the cost

2. **Search Strategy**:
   - hybrid_search(doc_id="RSMEANS_RSMEANS_BUILDING_2020", query="<component> material labor cost")
   - For residential: hybrid_search(doc_id="RSMEANS_RSMEANS_RESIDENTIAL_2020", query="<component>")
   - Use get_table() if you hit a table surrogate to see full cost data

3. **Cost Calculation**:
   - base_cost = material + labor + equipment
   - extended_cost = base_cost × quantity
   - location_adjusted = extended_cost × location_factor
   - final_cost = location_adjusted × year_factor × quality_factor

4. **Quality Adjustments**:
   - economy: 0.80× base cost
   - standard: 1.00× base cost
   - premium: 1.25× base cost
   - luxury: 1.50× base cost

5. **Cost Components**:
   - Material: Raw materials and supplies
   - Labor: Installation labor (crew costs)
   - Equipment: Tools and machinery rental

## OUTPUT FORMAT

Return a JSON object:
{
    "component_name": "<component>",
    "quantity": <qty>,
    "unit": "EA|SF|LF|CF",
    "material_cost_per_unit": <cost>,
    "labor_cost_per_unit": <cost>,
    "equipment_cost_per_unit": <cost>,
    "total_cost_per_unit": <cost>,
    "base_extended_cost": <qty × unit_cost>,
    "location_adjusted_cost": <adjusted>,
    "final_cost": <final>,
    "rsmeans_line_item": "<line item code>",
    "rsmeans_description": "<description>",
    "cost_includes": ["material", "labor", ...],
    "cost_excludes": ["<exclusion>", ...],
    "quality_adjustment": <factor if applied>,
    "year_adjustment": <factor if applied>,
    "confidence": 0.0-1.0,
    "rsmeans_note": "<RSMeans citation with page/table reference>",
    "citation_refs": ["<chunk_id>", "<table_id>", ...]
}

## DOCUMENT IDS

- RSMEANS_RSMEANS_BUILDING_2020: Building Construction Costs (commercial)
- RSMEANS_RSMEANS_RESIDENTIAL_2020: Residential Construction Costs

Always use corpus="reference" for RSMeans documents."""

    def get_output_schema(self) -> type[CostEstimate]:
        return CostEstimate

    def parse_output(
        self,
        response: str,
        tool_calls: list[dict],
    ) -> CostEstimate:
        """Parse agent response into structured cost estimate."""
        json_patterns = [
            r'\{[^{}]*"component_name"[^{}]*"final_cost"[^{}]*\}',
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

                    if "component_name" in data and "final_cost" in data:
                        material = float(data.get("material_cost_per_unit", 0))
                        labor = float(data.get("labor_cost_per_unit", 0))
                        equipment = float(data.get("equipment_cost_per_unit", 0))
                        total_unit = material + labor + equipment

                        return CostEstimate(
                            component_name=data["component_name"],
                            quantity=float(data.get("quantity", 1)),
                            unit=data.get("unit", "EA"),
                            material_cost_per_unit=material,
                            labor_cost_per_unit=labor,
                            equipment_cost_per_unit=equipment,
                            total_cost_per_unit=data.get("total_cost_per_unit", total_unit),
                            base_extended_cost=float(data.get("base_extended_cost", 0)),
                            location_adjusted_cost=float(data.get("location_adjusted_cost", 0)),
                            final_cost=float(data["final_cost"]),
                            rsmeans_line_item=data.get("rsmeans_line_item"),
                            rsmeans_description=data.get("rsmeans_description"),
                            cost_includes=data.get("cost_includes", []),
                            cost_excludes=data.get("cost_excludes", []),
                            quality_adjustment=data.get("quality_adjustment"),
                            year_adjustment=data.get("year_adjustment"),
                            confidence=data.get("confidence", 0.5),
                            rsmeans_note=data.get("rsmeans_note", "Based on RSMeans data"),
                            citation_refs=data.get("citation_refs", []),
                        )
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        raise ValueError(f"Could not parse cost estimate from response: {response[:500]}")


# =============================================================================
# Convenience Functions
# =============================================================================


async def estimate_cost(
    component_name: str,
    quantity: float,
    unit: str,
    context: StageContext,
    quality_tier: str = "standard",
    location_factor: float = 1.0,
    year_factor: float = 1.0,
    property_type: str = "commercial",
) -> dict:
    """
    Convenience function to estimate a component cost.

    Args:
        component_name: Component type
        quantity: Quantity from takeoff
        unit: RSMeans unit (EA, SF, LF, CF)
        context: Study context
        quality_tier: Quality level
        location_factor: Location adjustment
        year_factor: Year adjustment from 2020 base
        property_type: Property type

    Returns:
        Cost estimate with RSMeans citations
    """
    agent = CostEstimationAgent()

    input_data = CostInput(
        component_name=component_name,
        quantity=quantity,
        unit=unit,
        quality_tier=quality_tier,
        location_factor=location_factor,
        year_factor=year_factor,
        property_type=property_type,
    )

    result = await agent.run(context, input_data)

    return {
        "component_name": component_name,
        "estimate": result.result.model_dump() if result.result else None,
        "citations": [c.model_dump() for c in result.citations],
        "confidence": result.confidence,
        "needs_review": result.needs_review,
    }


async def estimate_costs_batch(
    takeoffs: list[dict],
    context: StageContext,
    quality_tier: str = "standard",
    location_factor: float = 1.0,
    year_factor: float = 1.0,
) -> list[dict]:
    """
    Estimate costs for multiple takeoffs.

    Args:
        takeoffs: List of takeoff dicts with 'component_name', 'quantity', 'unit'
        context: Study context
        quality_tier: Quality tier for all
        location_factor: Location factor for all
        year_factor: Year factor for all

    Returns:
        List of cost estimates
    """
    results = []

    for takeoff in takeoffs:
        result = await estimate_cost(
            component_name=takeoff.get("component_name", ""),
            quantity=takeoff.get("quantity", 1),
            unit=takeoff.get("unit", "EA"),
            context=context,
            quality_tier=takeoff.get("quality_tier", quality_tier),
            location_factor=takeoff.get("location_factor", location_factor),
            year_factor=takeoff.get("year_factor", year_factor),
            property_type=takeoff.get("property_type", "commercial"),
        )
        result["takeoff"] = takeoff
        results.append(result)

    return results


def aggregate_costs(estimates: list[dict]) -> dict:
    """
    Aggregate multiple cost estimates into totals.

    Args:
        estimates: List of cost estimate results

    Returns:
        Aggregated cost summary
    """
    total_final = 0.0
    total_material = 0.0
    total_labor = 0.0
    total_equipment = 0.0
    by_component = {}

    for est in estimates:
        if est.get("estimate"):
            e = est["estimate"]
            total_final += e.get("final_cost", 0)
            total_material += e.get("material_cost_per_unit", 0) * e.get("quantity", 0)
            total_labor += e.get("labor_cost_per_unit", 0) * e.get("quantity", 0)
            total_equipment += e.get("equipment_cost_per_unit", 0) * e.get("quantity", 0)

            comp = e.get("component_name", "unknown")
            if comp not in by_component:
                by_component[comp] = {"quantity": 0, "cost": 0}
            by_component[comp]["quantity"] += e.get("quantity", 0)
            by_component[comp]["cost"] += e.get("final_cost", 0)

    return {
        "total_cost": total_final,
        "material_total": total_material,
        "labor_total": total_labor,
        "equipment_total": total_equipment,
        "by_component": by_component,
        "num_estimates": len(estimates),
        "avg_confidence": sum(e.get("confidence", 0) for e in estimates) / max(len(estimates), 1),
    }
