"""
Asset Classification Agent - IRS-grounded MACRS classification.

Uses hybrid search over IRS reference corpus to classify components
with proper citations and evidence backing.
"""

import json
import re
from typing import Optional

from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext


# =============================================================================
# Input/Output Schemas
# =============================================================================


class ComponentInput(BaseModel):
    """Input for asset classification."""

    component: str = Field(..., description="Component name to classify")
    space_type: Optional[str] = Field(
        default=None, description="Room/space type (e.g., 'unit_bedroom', 'common_hallway')"
    )
    indoor_outdoor: Optional[str] = Field(
        default=None, description="Location: 'indoor' or 'outdoor'"
    )
    attachment_type: Optional[str] = Field(
        default=None, description="How attached: 'permanent', 'removable', etc."
    )
    function_type: Optional[str] = Field(
        default=None, description="Function: 'structural', 'decorative', 'utility'"
    )


class AssetClassification(BaseModel):
    """Structured asset classification output."""

    bucket: str = Field(
        ...,
        description="MACRS bucket: '5-year', '7-year', '15-year', '27.5-year', '39-year'",
    )
    life_years: int = Field(..., ge=1, le=40, description="Recovery period in years")
    section: str = Field(
        ...,
        pattern="^(1245|1250)$",
        description="IRS section: 1245 (personal property) or 1250 (real property)",
    )
    asset_class: Optional[str] = Field(
        default=None, description="IRS asset class code (e.g., '57.0', '00.11')"
    )
    macrs_system: str = Field(
        default="GDS", description="Depreciation system: 'GDS' or 'ADS'"
    )
    irs_note: str = Field(
        ..., description="Explanation citing specific IRS guidance"
    )
    citation_refs: list[str] = Field(
        default_factory=list,
        description="Referenced chunk_ids or table_ids",
    )


# =============================================================================
# Asset Classification Agent
# =============================================================================


class AssetClassificationAgent(BaseStageAgent[ComponentInput, AssetClassification]):
    """
    Agent for IRS-grounded asset classification.

    Uses hybrid search to find relevant IRS guidance and classifies
    building components into MACRS depreciation buckets.
    """

    def __init__(self):
        super().__init__(stage_name="asset_classification")

    def get_system_prompt(self) -> str:
        return """You are a tax classification expert specializing in cost segregation studies.

Your task: Classify building components for MACRS depreciation using IRS guidance.

## CRITICAL RULES

1. **Evidence Required**: You MUST search the IRS reference corpus before making any classification.
   Use bm25_search for exact codes (e.g., "1245", "57.0") and hybrid_search for component context.

2. **Cite Everything**: Every classification MUST cite at least one chunk_id or table_id from your search results.
   Include the page number in your irs_note.

3. **No Guessing**: If you cannot find supporting evidence, you must set needs_review=true.
   Never guess or infer without IRS documentation.

4. **Use Correct Section**:
   - Section 1245: Tangible personal property (equipment, fixtures, certain improvements)
   - Section 1250: Real property (building structure, land improvements)

## SEARCH STRATEGY

1. First, search for the specific component type:
   - hybrid_search(doc_id="IRS_IRS_COST_SEG_ATG__2024", query="<component> depreciation classification")

2. Then, search for relevant IRS codes:
   - bm25_search(doc_id="IRS_IRS_PUB_946__2024", query="1245") for personal property guidance
   - bm25_search(doc_id="IRS_REV_PROC_87_56", query="57.0") for asset class tables

3. If you get table hits, fetch the full table:
   - get_table(doc_id, table_id) to see all rows and find the right asset class

## OUTPUT FORMAT

Return a JSON object with these fields:
{
    "bucket": "5-year" | "7-year" | "15-year" | "27.5-year" | "39-year",
    "life_years": <integer>,
    "section": "1245" | "1250",
    "asset_class": "<code like 57.0 or 00.11, if applicable>",
    "macrs_system": "GDS",
    "irs_note": "<Brief explanation citing IRS source, page, and chunk_id>",
    "citation_refs": ["<chunk_id_1>", "<table_id_1>", ...]
}

## COMMON CLASSIFICATIONS (use as guidance, but always verify with search)

- Carpeting: Section 1245, 5-year (asset class 57.0 for residential rental)
- Kitchen appliances: Section 1245, 5-year
- HVAC: Can be 1245 (if unit serving specific space) or 1250 (if building-wide)
- Electrical wiring: Usually Section 1250, 39-year (structural)
- Light fixtures (decorative): Section 1245, 5-year or 7-year
- Parking lot: Section 1250, 15-year (land improvement)
- Sidewalks: Section 1250, 15-year (land improvement)

## DOCUMENT IDS TO SEARCH

- IRS_IRS_COST_SEG_ATG__2024: Cost Segregation Audit Techniques Guide (Pub 5653)
- IRS_IRS_PUB_946__2024: How To Depreciate Property
- IRS_REV_PROC_87_56: Asset class definitions and recovery periods
- IRS_IRS_PUB_527__2024: Residential Rental Property

Always use corpus="reference" for IRS documents."""

    def get_output_schema(self) -> type[AssetClassification]:
        return AssetClassification

    def parse_output(
        self,
        response: str,
        tool_calls: list[dict],
    ) -> AssetClassification:
        """
        Parse agent response into structured classification.

        Extracts JSON from the response and validates against schema.
        """
        # Try to find JSON in response
        json_patterns = [
            r'\{[^{}]*"bucket"[^{}]*\}',  # Simple JSON object
            r'```json\s*(.*?)\s*```',      # Markdown code block
            r'```\s*(.*?)\s*```',           # Generic code block
        ]

        for pattern in json_patterns:
            matches = re.findall(pattern, response, re.DOTALL)
            for match in matches:
                try:
                    # Clean up the match
                    json_str = match.strip()
                    if not json_str.startswith("{"):
                        continue

                    data = json.loads(json_str)

                    # Validate required fields
                    if "bucket" in data and "section" in data:
                        # Normalize bucket format
                        bucket = data["bucket"].lower().replace("_", "-")
                        if not bucket.endswith("-year"):
                            bucket = f"{bucket}-year"

                        return AssetClassification(
                            bucket=bucket,
                            life_years=data.get("life_years", self._bucket_to_years(bucket)),
                            section=data["section"],
                            asset_class=data.get("asset_class"),
                            macrs_system=data.get("macrs_system", "GDS"),
                            irs_note=data.get("irs_note", "Classification based on IRS guidance"),
                            citation_refs=data.get("citation_refs", []),
                        )
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        # Fallback: try to extract key information from text
        bucket = self._extract_bucket(response)
        section = self._extract_section(response)

        if bucket and section:
            return AssetClassification(
                bucket=bucket,
                life_years=self._bucket_to_years(bucket),
                section=section,
                irs_note=f"Extracted from response: {response[:200]}...",
            )

        # If we can't parse, raise for needs_review handling
        raise ValueError(f"Could not parse classification from response: {response[:500]}")

    def _bucket_to_years(self, bucket: str) -> int:
        """Convert bucket string to years."""
        bucket_years = {
            "5-year": 5,
            "7-year": 7,
            "15-year": 15,
            "27.5-year": 27,
            "39-year": 39,
        }
        return bucket_years.get(bucket.lower(), 39)

    def _extract_bucket(self, text: str) -> Optional[str]:
        """Try to extract bucket from text."""
        patterns = [
            r'(\d+(?:\.\d+)?)[- ]?year',
            r'bucket[:\s]+["\']?(\d+)["\']?',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                years = match.group(1)
                return f"{years}-year"
        return None

    def _extract_section(self, text: str) -> Optional[str]:
        """Try to extract section from text."""
        if "1245" in text:
            return "1245"
        if "1250" in text:
            return "1250"
        return None


# =============================================================================
# Convenience Functions
# =============================================================================


async def classify_component(
    component: str,
    context: StageContext,
    space_type: Optional[str] = None,
    indoor_outdoor: Optional[str] = None,
    attachment_type: Optional[str] = None,
    function_type: Optional[str] = None,
) -> dict:
    """
    Convenience function to classify a single component.

    Args:
        component: Component name
        context: Study context with available documents
        space_type: Room/space type
        indoor_outdoor: Indoor or outdoor location
        attachment_type: How the component is attached
        function_type: Component function

    Returns:
        Classification result with citations
    """
    agent = AssetClassificationAgent()

    input_data = ComponentInput(
        component=component,
        space_type=space_type,
        indoor_outdoor=indoor_outdoor,
        attachment_type=attachment_type,
        function_type=function_type,
    )

    result = await agent.run(context, input_data)

    return {
        "component": component,
        "classification": result.result.model_dump() if result.result else None,
        "citations": [c.model_dump() for c in result.citations],
        "confidence": result.confidence,
        "needs_review": result.needs_review,
        "review_reason": result.review_reason,
    }


async def classify_components_batch(
    components: list[dict],
    context: StageContext,
    max_concurrent: int = 1,  # Sequential for rate limit
) -> list[dict]:
    """
    Classify multiple components IN PARALLEL.

    Args:
        components: List of component dicts with 'component' key
        context: Study context
        max_concurrent: Maximum concurrent classifications (default: 20)

    Returns:
        List of classification results
    """
    from ..utils.parallel import parallel_map

    if not components:
        return []

    async def classify_single_component(comp: dict) -> dict:
        """Classify a single component."""
        # Get component name from various possible keys
        component_name = (
            comp.get("component") or
            comp.get("label") or
            comp.get("name") or
            comp.get("original_label") or
            ""
        )

        # Get context from enriched object if available
        obj_context = comp.get("context", {}) or {}

        result = await classify_component(
            component=component_name,
            context=context,
            space_type=comp.get("space_type") or comp.get("room_type"),
            indoor_outdoor=comp.get("indoor_outdoor") or obj_context.get("indoor_outdoor"),
            attachment_type=comp.get("attachment_type") or obj_context.get("attachment_type"),
            function_type=comp.get("function_type") or obj_context.get("function_type"),
        )
        result["original"] = comp
        result["component_name"] = component_name
        return result

    # PARALLEL: Classify all components concurrently
    results = await parallel_map(
        items=components,
        async_fn=classify_single_component,
        max_concurrent=max_concurrent,
        desc=f"Classifying {len(components)} components",
    )

    return results
