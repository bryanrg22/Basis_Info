"""
Cost Estimation Agent - RSMeans-backed cost calculations.

Calculates component costs using RSMeans data with proper
citations for audit defensibility.

Phase 4 Enhancement: Domain-specific tools for cost estimation.
"""

import json
import re
from typing import Optional

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext
from ..observability.alerts import alert_on_failure


# =============================================================================
# Domain-Specific Cost Tools (Phase 4)
# =============================================================================

# Regional cost factors by state (RSMeans city cost indexes)
REGIONAL_COST_FACTORS = {
    "AL": 0.85, "AK": 1.25, "AZ": 0.95, "AR": 0.82,
    "CA": 1.15, "CO": 0.98, "CT": 1.12, "DE": 1.02,
    "FL": 0.92, "GA": 0.89, "HI": 1.30, "ID": 0.90,
    "IL": 1.05, "IN": 0.95, "IA": 0.92, "KS": 0.90,
    "KY": 0.88, "LA": 0.87, "ME": 0.95, "MD": 0.98,
    "MA": 1.15, "MI": 1.00, "MN": 1.02, "MS": 0.80,
    "MO": 0.95, "MT": 0.92, "NE": 0.88, "NV": 1.02,
    "NH": 1.00, "NJ": 1.15, "NM": 0.90, "NY": 1.20,
    "NC": 0.85, "ND": 0.88, "OH": 0.95, "OK": 0.85,
    "OR": 1.02, "PA": 1.00, "RI": 1.08, "SC": 0.82,
    "SD": 0.85, "TN": 0.85, "TX": 0.88, "UT": 0.92,
    "VT": 0.95, "VA": 0.92, "WA": 1.05, "WV": 0.92,
    "WI": 0.98, "WY": 0.88, "DC": 1.02,
}

# Year adjustment factors from RSMeans 2020 base
YEAR_ADJUSTMENT_FACTORS = {
    2020: 1.00,
    2021: 1.03,
    2022: 1.08,
    2023: 1.12,
    2024: 1.15,
    2025: 1.18,
    2026: 1.22,
}

# Material/Labor split by component type
MATERIAL_LABOR_SPLIT = {
    "light_fixture": {"material_pct": 0.65, "labor_pct": 0.30, "equipment_pct": 0.05},
    "electrical_outlet": {"material_pct": 0.45, "labor_pct": 0.50, "equipment_pct": 0.05},
    "hvac_unit": {"material_pct": 0.55, "labor_pct": 0.35, "equipment_pct": 0.10},
    "carpet": {"material_pct": 0.60, "labor_pct": 0.35, "equipment_pct": 0.05},
    "tile": {"material_pct": 0.50, "labor_pct": 0.45, "equipment_pct": 0.05},
    "cabinet": {"material_pct": 0.70, "labor_pct": 0.28, "equipment_pct": 0.02},
    "countertop": {"material_pct": 0.65, "labor_pct": 0.30, "equipment_pct": 0.05},
    "plumbing_fixture": {"material_pct": 0.60, "labor_pct": 0.35, "equipment_pct": 0.05},
    "paint": {"material_pct": 0.35, "labor_pct": 0.60, "equipment_pct": 0.05},
    "door": {"material_pct": 0.65, "labor_pct": 0.32, "equipment_pct": 0.03},
    "window": {"material_pct": 0.70, "labor_pct": 0.25, "equipment_pct": 0.05},
    "roofing": {"material_pct": 0.45, "labor_pct": 0.45, "equipment_pct": 0.10},
    "siding": {"material_pct": 0.55, "labor_pct": 0.40, "equipment_pct": 0.05},
    "default": {"material_pct": 0.55, "labor_pct": 0.40, "equipment_pct": 0.05},
}

# Component cost database (typical unit costs from RSMeans)
TYPICAL_UNIT_COSTS = {
    "light_fixture": {
        "economy": {"material": 35, "labor": 25, "equipment": 5, "unit": "EA"},
        "standard": {"material": 85, "labor": 35, "equipment": 5, "unit": "EA"},
        "premium": {"material": 200, "labor": 50, "equipment": 10, "unit": "EA"},
        "luxury": {"material": 500, "labor": 75, "equipment": 15, "unit": "EA"},
    },
    "electrical_outlet": {
        "economy": {"material": 8, "labor": 25, "equipment": 2, "unit": "EA"},
        "standard": {"material": 15, "labor": 35, "equipment": 3, "unit": "EA"},
        "premium": {"material": 35, "labor": 45, "equipment": 5, "unit": "EA"},
        "luxury": {"material": 75, "labor": 55, "equipment": 8, "unit": "EA"},
    },
    "carpet": {
        "economy": {"material": 2.50, "labor": 1.50, "equipment": 0.25, "unit": "SF"},
        "standard": {"material": 4.50, "labor": 2.00, "equipment": 0.30, "unit": "SF"},
        "premium": {"material": 8.00, "labor": 2.50, "equipment": 0.40, "unit": "SF"},
        "luxury": {"material": 15.00, "labor": 3.00, "equipment": 0.50, "unit": "SF"},
    },
    "tile": {
        "economy": {"material": 4.00, "labor": 6.00, "equipment": 0.50, "unit": "SF"},
        "standard": {"material": 8.00, "labor": 8.00, "equipment": 0.75, "unit": "SF"},
        "premium": {"material": 15.00, "labor": 10.00, "equipment": 1.00, "unit": "SF"},
        "luxury": {"material": 30.00, "labor": 15.00, "equipment": 1.50, "unit": "SF"},
    },
    "cabinet": {
        "economy": {"material": 100, "labor": 35, "equipment": 5, "unit": "LF"},
        "standard": {"material": 200, "labor": 50, "equipment": 8, "unit": "LF"},
        "premium": {"material": 400, "labor": 75, "equipment": 12, "unit": "LF"},
        "luxury": {"material": 800, "labor": 100, "equipment": 20, "unit": "LF"},
    },
    "hvac_unit": {
        "economy": {"material": 2500, "labor": 1000, "equipment": 250, "unit": "EA"},
        "standard": {"material": 4000, "labor": 1500, "equipment": 400, "unit": "EA"},
        "premium": {"material": 6500, "labor": 2000, "equipment": 600, "unit": "EA"},
        "luxury": {"material": 10000, "labor": 2500, "equipment": 800, "unit": "EA"},
    },
}


@tool
def search_rsmeans_database(component_name: str, quality_tier: str = "standard") -> dict:
    """
    Search for component cost data from RSMeans database.

    Provides typical unit costs including material, labor, and equipment
    breakdown for the component.

    Args:
        component_name: Name of the component
        quality_tier: Quality level (economy, standard, premium, luxury)

    Returns:
        Cost data with material/labor/equipment breakdown
    """
    component_lower = component_name.lower().replace(" ", "_")
    tier = quality_tier.lower()

    if tier not in ["economy", "standard", "premium", "luxury"]:
        tier = "standard"

    # Try exact match
    if component_lower in TYPICAL_UNIT_COSTS:
        costs = TYPICAL_UNIT_COSTS[component_lower]
        tier_costs = costs.get(tier, costs.get("standard"))
        total = tier_costs["material"] + tier_costs["labor"] + tier_costs["equipment"]

        return {
            "component": component_name,
            "quality_tier": tier,
            "found": True,
            "material_cost": tier_costs["material"],
            "labor_cost": tier_costs["labor"],
            "equipment_cost": tier_costs["equipment"],
            "total_unit_cost": total,
            "unit": tier_costs["unit"],
            "source": "RSMeans Building Construction Costs 2020",
            "note": "Costs are national averages - apply location factor",
        }

    # Try partial match
    for key, costs in TYPICAL_UNIT_COSTS.items():
        if key in component_lower or component_lower in key:
            tier_costs = costs.get(tier, costs.get("standard"))
            total = tier_costs["material"] + tier_costs["labor"] + tier_costs["equipment"]

            return {
                "component": component_name,
                "matched_to": key,
                "quality_tier": tier,
                "found": True,
                "material_cost": tier_costs["material"],
                "labor_cost": tier_costs["labor"],
                "equipment_cost": tier_costs["equipment"],
                "total_unit_cost": total,
                "unit": tier_costs["unit"],
                "source": "RSMeans Building Construction Costs 2020",
                "note": "Costs are national averages - apply location factor",
            }

    return {
        "component": component_name,
        "quality_tier": tier,
        "found": False,
        "hint": "Search RSMeans corpus directly for this component's cost data",
    }


@tool
def get_regional_cost_factor(state_code: str, year: int = 2024) -> dict:
    """
    Get regional cost adjustment factor for a location.

    Combines state-level cost index with year adjustment to provide
    a multiplier for national average costs.

    Args:
        state_code: Two-letter state code (e.g., "CA", "TX", "NY")
        year: Year for cost adjustment (2020-2026)

    Returns:
        Combined cost factor and calculation breakdown
    """
    state_upper = state_code.upper()

    # Get state factor
    state_factor = REGIONAL_COST_FACTORS.get(state_upper)
    if state_factor is None:
        state_factor = 1.0
        state_found = False
    else:
        state_found = True

    # Get year factor
    if year < 2020:
        year_factor = 0.95  # Estimate for older years
        year_note = "Estimated for pre-2020"
    elif year > 2026:
        year_factor = YEAR_ADJUSTMENT_FACTORS[2026] * (1 + 0.03 * (year - 2026))
        year_note = "Projected from 2026"
    else:
        year_factor = YEAR_ADJUSTMENT_FACTORS.get(year, 1.0)
        year_note = f"RSMeans {year} adjustment"

    combined_factor = state_factor * year_factor

    return {
        "state_code": state_upper,
        "year": year,
        "state_factor": round(state_factor, 3),
        "state_found": state_found,
        "year_factor": round(year_factor, 3),
        "year_note": year_note,
        "combined_factor": round(combined_factor, 3),
        "calculation": f"{state_factor:.3f} (state) × {year_factor:.3f} (year) = {combined_factor:.3f}",
        "usage": "Multiply national average cost by this factor",
    }


@tool
def calculate_material_labor_split(component_name: str, total_cost: float = None) -> dict:
    """
    Get the typical material/labor/equipment split for a component.

    Useful for understanding cost composition and validating
    cost estimates against industry norms.

    Args:
        component_name: Name of the component
        total_cost: Optional total cost to split

    Returns:
        Percentage breakdown and calculated amounts if total provided
    """
    component_lower = component_name.lower().replace(" ", "_")

    # Try exact match
    split = MATERIAL_LABOR_SPLIT.get(component_lower)
    if not split:
        # Try partial match
        for key, s in MATERIAL_LABOR_SPLIT.items():
            if key in component_lower or component_lower in key:
                split = s
                break

    if not split:
        split = MATERIAL_LABOR_SPLIT["default"]
        found = False
    else:
        found = True

    result = {
        "component": component_name,
        "found": found,
        "material_pct": split["material_pct"],
        "labor_pct": split["labor_pct"],
        "equipment_pct": split["equipment_pct"],
        "percentages": f"{split['material_pct']*100:.0f}% material, {split['labor_pct']*100:.0f}% labor, {split['equipment_pct']*100:.0f}% equipment",
    }

    if total_cost:
        result["total_cost"] = total_cost
        result["material_amount"] = round(total_cost * split["material_pct"], 2)
        result["labor_amount"] = round(total_cost * split["labor_pct"], 2)
        result["equipment_amount"] = round(total_cost * split["equipment_pct"], 2)

    return result


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

    Phase 4 Enhancement: Domain-specific tools for cost estimation.
    """

    def __init__(self):
        super().__init__(stage_name="cost_estimation")

    def get_tools(self) -> list[BaseTool]:
        """
        Return tools including domain-specific cost tools.

        Phase 4: Adds specialized cost estimation tools.
        """
        from ..mcp_server.server import get_all_evidence_tools

        # Get base search tools
        base_tools = get_all_evidence_tools()

        # Add domain-specific cost tools
        cost_tools = [
            search_rsmeans_database,
            get_regional_cost_factor,
            calculate_material_labor_split,
        ]

        return base_tools + cost_tools

    def get_system_prompt(self) -> str:
        return """You are a construction cost estimator using RSMeans data.

Your task: Calculate component costs with proper RSMeans citations.

## WORKFLOW

1. **Use Domain Tools First**: Get baseline costs and factors
   - search_rsmeans_database for typical unit costs
   - get_regional_cost_factor for location and year adjustments
   - calculate_material_labor_split to verify cost breakdown
2. **Search RSMeans Corpus**: Get specific line items and detailed costs
3. **Calculate**: Apply adjustments and compute final cost
4. **Return Result**: Complete cost estimate with citations

## DOMAIN-SPECIFIC TOOLS (USE THESE FIRST)

- search_rsmeans_database(component_name, quality_tier): Get typical unit costs
- get_regional_cost_factor(state_code, year): Get location and year adjustment
- calculate_material_labor_split(component_name, total_cost): Verify cost breakdown

## SEARCH STRATEGY (AFTER DOMAIN TOOLS)

1. Use domain tools to get baseline cost data
2. Then search RSMeans for specific line items:
   - hybrid_search(doc_id="RSMEANS_RSMEANS_BUILDING_2020", query="<component> material labor cost")
   - For residential: hybrid_search(doc_id="RSMEANS_RSMEANS_RESIDENTIAL_2020", query="<component>")
   - Use get_table() if you hit a table surrogate to see full cost data

## COST CALCULATION

- base_cost = material + labor + equipment (from search_rsmeans_database)
- extended_cost = base_cost × quantity
- location_adjusted = extended_cost × location_factor (from get_regional_cost_factor)
- final_cost = location_adjusted × quality_factor

## QUALITY ADJUSTMENTS

- economy: 0.80× base cost
- standard: 1.00× base cost
- premium: 1.25× base cost
- luxury: 1.50× base cost

## COST COMPONENTS

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


@alert_on_failure("cost_agent", study_id_param="context")
async def estimate_cost(
    component_name: str,
    quantity: float,
    unit: str,
    context: StageContext,
    quality_tier: str = "standard",
    location_factor: float = 1.0,
    year_factor: float = 1.0,
    property_type: str = "commercial",
    emit_feedback_on_issues: bool = True,
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
        emit_feedback_on_issues: Whether to emit feedback when issues are found

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

    estimate_dict = result.result.model_dump() if result.result else None

    # Phase 5: Emit feedback for cost outliers
    if emit_feedback_on_issues and estimate_dict and context.study_id:
        total_cost_per_unit = estimate_dict.get("total_cost_per_unit", 0)

        # Check against typical cost ranges
        component_lower = component_name.lower().replace(" ", "_")
        for key, ranges in TYPICAL_UNIT_COSTS.items():
            if key in component_lower or component_lower in key:
                tier_costs = ranges.get(quality_tier, ranges.get("standard"))
                if tier_costs:
                    expected_total = (
                        tier_costs.get("material", 0) +
                        tier_costs.get("labor", 0) +
                        tier_costs.get("equipment", 0)
                    )

                    # Flag as outlier if more than 3x expected or less than 1/3 expected
                    if expected_total > 0 and total_cost_per_unit > 0:
                        if total_cost_per_unit > expected_total * 3:
                            try:
                                from ..graph.feedback import emit_feedback, FeedbackType, SuggestedAction

                                await emit_feedback(
                                    feedback_type=FeedbackType.COST_OUTLIER,
                                    source_stage="cost",
                                    target_stage="classification",
                                    component_id=component_name,
                                    study_id=context.study_id,
                                    message=f"Unit cost ${total_cost_per_unit:.2f} for '{component_name}' is significantly above typical (${expected_total:.2f}).",
                                    suggested_action=SuggestedAction.FLAG_FOR_REVIEW,
                                    details={
                                        "component_name": component_name,
                                        "actual_unit_cost": total_cost_per_unit,
                                        "expected_unit_cost": expected_total,
                                        "ratio": total_cost_per_unit / expected_total,
                                    },
                                    process_immediately=False,
                                )
                            except Exception as e:
                                import logging
                                logging.getLogger(__name__).debug(f"Failed to emit feedback: {e}")

                        elif total_cost_per_unit < expected_total / 3:
                            try:
                                from ..graph.feedback import emit_feedback, FeedbackType, SuggestedAction

                                await emit_feedback(
                                    feedback_type=FeedbackType.COST_OUTLIER,
                                    source_stage="cost",
                                    target_stage="classification",
                                    component_id=component_name,
                                    study_id=context.study_id,
                                    message=f"Unit cost ${total_cost_per_unit:.2f} for '{component_name}' is significantly below typical (${expected_total:.2f}).",
                                    suggested_action=SuggestedAction.FLAG_FOR_REVIEW,
                                    details={
                                        "component_name": component_name,
                                        "actual_unit_cost": total_cost_per_unit,
                                        "expected_unit_cost": expected_total,
                                        "ratio": total_cost_per_unit / expected_total,
                                    },
                                    process_immediately=False,
                                )
                            except Exception as e:
                                import logging
                                logging.getLogger(__name__).debug(f"Failed to emit feedback: {e}")
                break

    return {
        "component_name": component_name,
        "estimate": estimate_dict,
        "citations": [c.model_dump() for c in result.citations],
        "confidence": result.confidence,
        "needs_review": result.needs_review,
    }


@alert_on_failure("cost_agent", study_id_param="context")
async def estimate_costs_batch(
    takeoffs: list[dict],
    context: StageContext,
    quality_tier: str = "standard",
    location_factor: float = 1.0,
    year_factor: float = 1.0,
    max_concurrent: int = 1,  # Sequential for rate limit
) -> list[dict]:
    """
    Estimate costs for multiple takeoffs IN PARALLEL.

    Args:
        takeoffs: List of takeoff dicts with 'component_name', 'quantity', 'unit'
        context: Study context
        quality_tier: Quality tier for all
        location_factor: Location factor for all
        year_factor: Year factor for all
        max_concurrent: Maximum concurrent estimations (default: 10)

    Returns:
        List of cost estimates
    """
    from ..utils.parallel import parallel_map

    if not takeoffs:
        return []

    async def estimate_single_cost(takeoff: dict) -> dict:
        """Estimate cost for a single takeoff."""
        # Get component name from takeoff or nested takeoff result
        takeoff_data = takeoff.get("takeoff", {}) or {}
        component_name = (
            takeoff.get("component_name") or
            takeoff_data.get("component_name") or
            ""
        )
        quantity = takeoff.get("quantity") or takeoff_data.get("quantity", 1)
        unit = takeoff.get("unit") or takeoff_data.get("unit", "EA")

        result = await estimate_cost(
            component_name=component_name,
            quantity=quantity,
            unit=unit,
            context=context,
            quality_tier=takeoff.get("quality_tier", quality_tier),
            location_factor=takeoff.get("location_factor", location_factor),
            year_factor=takeoff.get("year_factor", year_factor),
            property_type=takeoff.get("property_type", "commercial"),
        )
        result["takeoff"] = takeoff
        return result

    # PARALLEL: Estimate all costs concurrently
    results = await parallel_map(
        items=takeoffs,
        async_fn=estimate_single_cost,
        max_concurrent=max_concurrent,
        desc=f"Estimating {len(takeoffs)} costs",
    )

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
