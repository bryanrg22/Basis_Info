"""
Object Context Agent - IRS-relevant context for detected objects.

Takes vision layer detections and provides context about attachment type,
function, and other IRS-relevant properties for asset classification.

Phase 4 Enhancement: Domain-specific tools for component analysis.
"""

import json
import re
from typing import Optional

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext


# =============================================================================
# Domain-Specific Object Tools (Phase 4)
# =============================================================================

# Component standards and specifications
COMPONENT_STANDARDS = {
    "hvac_unit": {
        "industry_standards": ["ASHRAE 90.1", "ENERGY STAR", "AHRI"],
        "typical_lifespan_years": 15,
        "typical_section": "1245",
        "typical_recovery": "15-year",
        "notes": "Standalone HVAC units typically Section 1245. Central systems may be 1250.",
    },
    "light_fixture": {
        "industry_standards": ["NEC", "ENERGY STAR", "DLC"],
        "typical_lifespan_years": 15,
        "typical_section": "1245",
        "typical_recovery": "5-year",
        "notes": "Decorative fixtures typically Section 1245. Recessed/structural may vary.",
    },
    "electrical_panel": {
        "industry_standards": ["NEC", "UL", "NEMA"],
        "typical_lifespan_years": 25,
        "typical_section": "1245",
        "typical_recovery": "7-year",
        "notes": "Distribution equipment typically Section 1245.",
    },
    "plumbing_fixture": {
        "industry_standards": ["IPC", "UPC", "ASME"],
        "typical_lifespan_years": 20,
        "typical_section": "1245",
        "typical_recovery": "5-year",
        "notes": "Fixtures (sinks, toilets) typically Section 1245. Piping may be 1250.",
    },
    "carpet": {
        "industry_standards": ["CRI", "NSF/ANSI"],
        "typical_lifespan_years": 10,
        "typical_section": "1245",
        "typical_recovery": "5-year",
        "notes": "Carpet is typically Section 1245, 5-year recovery.",
    },
    "flooring": {
        "industry_standards": ["TCNA", "NWFA", "RFCI"],
        "typical_lifespan_years": 15,
        "typical_section": "1245",
        "typical_recovery": "5-year",
        "notes": "Most flooring is Section 1245. Structural subfloor is 1250.",
    },
    "cabinet": {
        "industry_standards": ["KCMA", "ANSI A161.1"],
        "typical_lifespan_years": 20,
        "typical_section": "1245",
        "typical_recovery": "5-year",
        "notes": "Kitchen/bath cabinets typically Section 1245 if not structural.",
    },
    "appliance": {
        "industry_standards": ["ENERGY STAR", "UL", "AHAM"],
        "typical_lifespan_years": 10,
        "typical_section": "1245",
        "typical_recovery": "5-year",
        "notes": "Appliances are Section 1245 personal property.",
    },
    "fire_sprinkler": {
        "industry_standards": ["NFPA 13", "UL", "FM"],
        "typical_lifespan_years": 50,
        "typical_section": "1245",
        "typical_recovery": "15-year",
        "notes": "Fire suppression systems typically Section 1245, 15-year.",
    },
    "security_system": {
        "industry_standards": ["UL", "NFPA", "ASIS"],
        "typical_lifespan_years": 10,
        "typical_section": "1245",
        "typical_recovery": "5-year",
        "notes": "Security and access systems typically Section 1245.",
    },
    "elevator": {
        "industry_standards": ["ASME A17.1", "ADA", "IBC"],
        "typical_lifespan_years": 25,
        "typical_section": "1250",
        "typical_recovery": "39-year",
        "notes": "Elevators are typically Section 1250 as part of building.",
    },
    "window": {
        "industry_standards": ["NFRC", "AAMA", "ENERGY STAR"],
        "typical_lifespan_years": 25,
        "typical_section": "1250",
        "typical_recovery": "39-year",
        "notes": "Windows are typically Section 1250 as structural components.",
    },
    "door": {
        "industry_standards": ["BHMA", "SDI", "NFPA"],
        "typical_lifespan_years": 25,
        "typical_section": "mixed",
        "typical_recovery": "varies",
        "notes": "Exterior doors typically 1250. Interior decorative may be 1245.",
    },
}

# Similar components mapping for suggestions
SIMILAR_COMPONENTS = {
    "hvac": ["hvac_unit", "air_handler", "furnace", "boiler", "heat_pump", "thermostat", "duct"],
    "lighting": ["light_fixture", "recessed_light", "pendant", "chandelier", "sconce", "emergency_light"],
    "plumbing": ["sink", "faucet", "toilet", "shower", "tub", "water_heater", "pipe"],
    "electrical": ["electrical_panel", "outlet", "switch", "wiring", "conduit", "generator"],
    "flooring": ["carpet", "tile", "hardwood", "vinyl", "laminate", "concrete"],
    "kitchen": ["cabinet", "countertop", "appliance", "range", "refrigerator", "dishwasher"],
    "fire_safety": ["fire_sprinkler", "fire_alarm", "fire_extinguisher", "smoke_detector"],
    "security": ["security_system", "camera", "access_control", "alarm"],
    "exterior": ["window", "door", "siding", "roofing", "gutter", "fence"],
}

# Component specifications
COMPONENT_SPECIFICATIONS = {
    "hvac_unit": {
        "typical_sizes": ["1.5-ton", "2-ton", "3-ton", "4-ton", "5-ton"],
        "efficiency_ratings": ["SEER 14-21", "EER 10-14"],
        "installation_requirements": ["Dedicated circuit", "Refrigerant lines", "Condensate drain"],
        "cost_factors": ["Size", "Efficiency", "Installation complexity"],
    },
    "light_fixture": {
        "typical_types": ["Recessed", "Surface mount", "Pendant", "Track", "Emergency"],
        "efficiency_ratings": ["LED", "Fluorescent", "Incandescent"],
        "installation_requirements": ["Junction box", "Proper circuit", "Height clearance"],
        "cost_factors": ["Type", "Finish", "Smart features"],
    },
    "carpet": {
        "typical_grades": ["Commercial", "Residential", "Heavy traffic", "Standard"],
        "specifications": ["Face weight", "Pile height", "Density"],
        "installation_requirements": ["Subfloor prep", "Pad", "Seaming"],
        "cost_factors": ["Quality", "Pad type", "Room complexity"],
    },
    "cabinet": {
        "typical_grades": ["Stock", "Semi-custom", "Custom"],
        "specifications": ["Wood type", "Construction", "Finish"],
        "installation_requirements": ["Level walls", "Scribing", "Hardware"],
        "cost_factors": ["Material", "Configuration", "Hardware"],
    },
}


@tool
def search_component_standards(component_name: str) -> dict:
    """
    Search for industry standards applicable to a component.

    Provides information about relevant codes, standards, and typical
    classifications for a building component.

    Args:
        component_name: Name of the component to search for

    Returns:
        Industry standards, typical lifespan, and IRS classification hints
    """
    component_lower = component_name.lower().replace(" ", "_")

    # Try exact match
    if component_lower in COMPONENT_STANDARDS:
        standards = COMPONENT_STANDARDS[component_lower]
        return {
            "component": component_name,
            "found": True,
            **standards,
        }

    # Try partial match
    for key, standards in COMPONENT_STANDARDS.items():
        if key in component_lower or component_lower in key:
            return {
                "component": component_name,
                "matched_to": key,
                "found": True,
                **standards,
            }

    # Try category match
    for category, components in SIMILAR_COMPONENTS.items():
        if any(comp in component_lower for comp in components):
            # Return generic info for category
            return {
                "component": component_name,
                "category": category,
                "found": False,
                "industry_standards": ["Search IRS guidance for specific standards"],
                "typical_lifespan_years": None,
                "typical_section": "varies",
                "notes": f"Component appears related to {category}. Search IRS guidance for specifics.",
            }

    return {
        "component": component_name,
        "found": False,
        "industry_standards": [],
        "typical_section": "unknown",
        "notes": "No specific standards found. Search IRS Cost Segregation ATG for guidance.",
    }


@tool
def find_similar_components(component_name: str) -> dict:
    """
    Find components similar to the given one.

    Useful for understanding related components that may have similar
    IRS treatment or for ensuring complete coverage in a room.

    Args:
        component_name: Name of the component

    Returns:
        List of similar components and their category
    """
    component_lower = component_name.lower().replace(" ", "_")

    # Check which category this component belongs to
    for category, components in SIMILAR_COMPONENTS.items():
        for comp in components:
            if comp in component_lower or component_lower in comp:
                return {
                    "component": component_name,
                    "category": category,
                    "found": True,
                    "similar_components": components,
                    "hint": f"Consider also checking for these related components: {', '.join(components[:5])}",
                }

    # Try keyword match
    for category, components in SIMILAR_COMPONENTS.items():
        if category in component_lower:
            return {
                "component": component_name,
                "category": category,
                "found": True,
                "similar_components": components,
                "hint": f"Category: {category}. Related components: {', '.join(components[:5])}",
            }

    return {
        "component": component_name,
        "found": False,
        "similar_components": [],
        "hint": "No similar components found. This may be a unique or specialized component.",
    }


@tool
def get_component_specifications(component_name: str) -> dict:
    """
    Get technical specifications for a component type.

    Provides typical sizes, grades, installation requirements,
    and cost factors for the component.

    Args:
        component_name: Name of the component

    Returns:
        Technical specifications and cost factors
    """
    component_lower = component_name.lower().replace(" ", "_")

    # Try exact match
    if component_lower in COMPONENT_SPECIFICATIONS:
        specs = COMPONENT_SPECIFICATIONS[component_lower]
        return {
            "component": component_name,
            "found": True,
            **specs,
        }

    # Try partial match
    for key, specs in COMPONENT_SPECIFICATIONS.items():
        if key in component_lower or component_lower in key:
            return {
                "component": component_name,
                "matched_to": key,
                "found": True,
                **specs,
            }

    return {
        "component": component_name,
        "found": False,
        "typical_types": [],
        "specifications": [],
        "installation_requirements": [],
        "cost_factors": [],
        "hint": "No detailed specifications found. Refer to RSMeans for cost data.",
    }


# =============================================================================
# Input/Output Schemas
# =============================================================================


class ObjectInput(BaseModel):
    """Input for object context enrichment."""

    detection_id: str = Field(..., description="Detection identifier from vision layer")
    label: str = Field(..., description="Object label from detection (e.g., 'refrigerator')")
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Detection confidence"
    )
    room_type: Optional[str] = Field(
        default=None, description="Room type where detected"
    )
    indoor_outdoor: Optional[str] = Field(
        default=None, description="Indoor or outdoor"
    )
    bbox_area: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Bounding box area (normalized)"
    )


class ObjectContext(BaseModel):
    """Enriched object context for asset classification."""

    component_name: str = Field(..., description="Normalized component name")
    component_category: str = Field(
        ...,
        description="Category: 'fixture', 'equipment', 'improvement', 'structural', 'decorative'",
    )
    attachment_type: str = Field(
        ...,
        description="Attachment: 'permanent', 'removable', 'built_in', 'freestanding'",
    )
    function_type: str = Field(
        ...,
        description="Function: 'utility', 'aesthetic', 'structural', 'safety', 'convenience'",
    )
    likely_section: str = Field(
        ...,
        pattern="^(1245|1250|unknown)$",
        description="Likely IRS section: 1245 (personal), 1250 (real), or unknown",
    )
    likely_recovery: Optional[str] = Field(
        default=None,
        description="Likely recovery period: '5-year', '7-year', '15-year', etc.",
    )
    requires_inspection: bool = Field(
        default=False,
        description="Whether physical inspection is recommended",
    )
    inspection_reason: Optional[str] = Field(
        default=None,
        description="Reason for requiring inspection",
    )
    irs_note: str = Field(
        ..., description="Brief IRS context for this component type"
    )
    citation_refs: list[str] = Field(
        default_factory=list,
        description="Referenced chunk_ids or table_ids",
    )


# =============================================================================
# Object Context Agent
# =============================================================================


class ObjectContextAgent(BaseStageAgent[ObjectInput, ObjectContext]):
    """
    Agent for enriching object detections with IRS context.

    Takes vision layer detections and provides attachment type,
    function, and likely classification hints for asset classification.

    Phase 4 Enhancement: Domain-specific tools for component analysis.
    """

    def __init__(self):
        super().__init__(stage_name="object_context")

    def get_tools(self) -> list[BaseTool]:
        """
        Return tools including domain-specific component tools.

        Phase 4: Adds specialized component analysis tools.
        """
        from ..mcp_server.server import get_all_evidence_tools

        # Get base search tools
        base_tools = get_all_evidence_tools()

        # Add domain-specific object tools
        object_tools = [
            search_component_standards,
            find_similar_components,
            get_component_specifications,
        ]

        return base_tools + object_tools

    def get_system_prompt(self) -> str:
        return """You are a cost segregation expert determining IRS context for detected objects.

Your task: Analyze detected objects and provide IRS-relevant context for asset classification.

## WORKFLOW

1. **Get Component Standards**: Use search_component_standards for industry info
2. **Find Similar Components**: Use find_similar_components to understand category
3. **Get Specifications**: Use get_component_specifications for technical details
4. **Search IRS Guidance**: Search for specific IRS guidance
5. **Return Enriched Context**: Combine all information

## DOMAIN-SPECIFIC TOOLS (USE THESE FIRST)

- search_component_standards(component_name): Get industry standards and typical IRS treatment
- find_similar_components(component_name): Find related components in same category
- get_component_specifications(component_name): Get technical specs and cost factors

## SEARCH STRATEGY (AFTER DOMAIN TOOLS)

1. Use domain tools first to get baseline information
2. Then search IRS guidance for specifics:
   - hybrid_search(doc_id="IRS_IRS_COST_SEG_ATG__2024", query="<component_name> depreciation")
   - bm25_search(doc_id="IRS_REV_PROC_87_56", query="<component_name>") for asset class

## COMPONENT CATEGORIES

- fixture: Permanently attached items (light fixtures, plumbing fixtures)
- equipment: Functional equipment (appliances, HVAC units)
- improvement: Building improvements (flooring, wall coverings)
- structural: Part of building structure (walls, foundation, roof)
- decorative: Aesthetic elements (artwork, decorative molding)

## ATTACHMENT TYPES

- permanent: Cannot be removed without damage to building
- removable: Can be removed without significant damage
- built_in: Integrated into building structure
- freestanding: Not attached to building

## SECTION DETERMINATION

- Section 1245: Tangible personal property, shorter recovery
- Section 1250: Real property, longer recovery (27.5 or 39 years)
- Key factors: Attachment method, ease of removal, function

## OUTPUT FORMAT

Return a JSON object:
{
    "component_name": "<normalized name>",
    "component_category": "fixture|equipment|improvement|structural|decorative",
    "attachment_type": "permanent|removable|built_in|freestanding",
    "function_type": "utility|aesthetic|structural|safety|convenience",
    "likely_section": "1245|1250|unknown",
    "likely_recovery": "5-year|7-year|15-year|27.5-year|39-year",
    "requires_inspection": true/false,
    "inspection_reason": "<reason if true>",
    "irs_note": "<Brief IRS context>",
    "citation_refs": ["<chunk_id>", ...]
}

## DOCUMENT IDS

- IRS_IRS_COST_SEG_ATG__2024: Cost Segregation Audit Techniques Guide
- IRS_REV_PROC_87_56: Asset class definitions
- IRS_IRS_PUB_946__2024: How To Depreciate Property

Always use corpus="reference" for IRS documents."""

    def get_output_schema(self) -> type[ObjectContext]:
        return ObjectContext

    def parse_output(
        self,
        response: str,
        tool_calls: list[dict],
    ) -> ObjectContext:
        """Parse agent response into structured object context."""
        json_patterns = [
            r'\{[^{}]*"component_name"[^{}]*\}',
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

                    if "component_name" in data:
                        return ObjectContext(
                            component_name=data["component_name"],
                            component_category=data.get("component_category", "equipment"),
                            attachment_type=data.get("attachment_type", "removable"),
                            function_type=data.get("function_type", "utility"),
                            likely_section=data.get("likely_section", "unknown"),
                            likely_recovery=data.get("likely_recovery"),
                            requires_inspection=data.get("requires_inspection", False),
                            inspection_reason=data.get("inspection_reason"),
                            irs_note=data.get("irs_note", "Component context from IRS guidance"),
                            citation_refs=data.get("citation_refs", []),
                        )
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        raise ValueError(f"Could not parse object context from response: {response[:500]}")


# =============================================================================
# Convenience Functions
# =============================================================================


async def enrich_object_context(
    detection_id: str,
    label: str,
    context: StageContext,
    confidence: float = 0.5,
    room_type: Optional[str] = None,
    indoor_outdoor: Optional[str] = None,
) -> dict:
    """
    Convenience function to enrich a detected object.

    Args:
        detection_id: Detection identifier
        label: Object label from detection
        context: Study context
        confidence: Detection confidence
        room_type: Room type where detected
        indoor_outdoor: Indoor/outdoor classification

    Returns:
        Enriched object context with IRS relevance
    """
    agent = ObjectContextAgent()

    input_data = ObjectInput(
        detection_id=detection_id,
        label=label,
        confidence=confidence,
        room_type=room_type,
        indoor_outdoor=indoor_outdoor,
    )

    result = await agent.run(context, input_data)

    return {
        "detection_id": detection_id,
        "original_label": label,
        "context": result.result.model_dump() if result.result else None,
        "citations": [c.model_dump() for c in result.citations],
        "confidence": result.confidence,
        "needs_review": result.needs_review,
    }


async def enrich_objects_batch(
    detections: list[dict],
    context: StageContext,
    room_type: Optional[str] = None,
    max_concurrent: int = 1,  # Sequential for rate limit
) -> list[dict]:
    """
    Enrich multiple detections IN PARALLEL.

    Args:
        detections: List of detection dicts with 'detection_id' and 'label'
        context: Study context
        room_type: Room type (if known)
        max_concurrent: Maximum concurrent enrichments (default: 3)

    Returns:
        List of enriched object contexts
    """
    from ..utils.parallel import parallel_map

    if not detections:
        return []

    async def enrich_single_detection(det: dict) -> dict:
        """Enrich a single detection."""
        result = await enrich_object_context(
            detection_id=det.get("detection_id", det.get("id", "")),
            label=det.get("label", ""),
            context=context,
            confidence=det.get("confidence", 0.5),
            room_type=det.get("room_type", room_type),
            indoor_outdoor=det.get("indoor_outdoor"),
        )
        result["original_detection"] = det
        return result

    # PARALLEL: Enrich all objects concurrently
    results = await parallel_map(
        items=detections,
        async_fn=enrich_single_detection,
        max_concurrent=max_concurrent,
        desc=f"Enriching {len(detections)} objects",
    )

    return results
