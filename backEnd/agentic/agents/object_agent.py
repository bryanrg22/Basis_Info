"""
Object Context Agent - IRS-relevant context for detected objects.

Takes vision layer detections and provides context about attachment type,
function, and other IRS-relevant properties for asset classification.
"""

import json
import re
from typing import Optional

from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext


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
    """

    def __init__(self):
        super().__init__(stage_name="object_context")

    def get_system_prompt(self) -> str:
        return """You are a cost segregation expert determining IRS context for detected objects.

Your task: Analyze detected objects and provide IRS-relevant context for asset classification.

## CRITICAL RULES

1. **Evidence Required**: Search IRS guidance to determine:
   - Whether this component is Section 1245 (personal property) or 1250 (real property)
   - The likely attachment method and whether it affects classification
   - The component's function and its impact on depreciation

2. **Search Strategy**:
   - hybrid_search(doc_id="IRS_IRS_COST_SEG_ATG__2024", query="<component_name> depreciation")
   - bm25_search(doc_id="IRS_REV_PROC_87_56", query="<component_name>") for asset class

3. **Component Categories**:
   - fixture: Permanently attached items (light fixtures, plumbing fixtures)
   - equipment: Functional equipment (appliances, HVAC units)
   - improvement: Building improvements (flooring, wall coverings)
   - structural: Part of building structure (walls, foundation, roof)
   - decorative: Aesthetic elements (artwork, decorative molding)

4. **Attachment Types**:
   - permanent: Cannot be removed without damage to building
   - removable: Can be removed without significant damage
   - built_in: Integrated into building structure
   - freestanding: Not attached to building

5. **Section Determination**:
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
