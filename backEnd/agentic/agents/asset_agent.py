"""
Asset Classification Agent - IRS-grounded MACRS classification.

Uses hybrid search over IRS reference corpus to classify components
with proper citations and evidence backing.

Phase 4 Enhancement: Self-verification using validation tools.
The agent now validates its own classifications before returning,
and can reconsider if validation fails.

Phase 2 Optimization: Classification cache for engineer-approved classifications.
Checks verified_classifications cache before LLM calls to reduce costs.

Phase 3 Optimization: Static rules engine for common components.
Pre-verified IRS classifications for ~50 common components eliminate LLM calls
for 70-80% of classifications on typical studies.
"""

import json
import logging
import re
from typing import Optional

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext
from .classification_verifier import (
    validate_section_bucket,
    check_component_context,
    VALID_SECTION_BUCKETS,
    validate_section_bucket_tool,
    check_component_context_tool,
)
from ..firestore.classification_cache import get_cached_classification
from .static_classification_rules import get_static_classification

logger = logging.getLogger(__name__)


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
# Self-Verification Tools for Classification
# =============================================================================


@tool
def reconsider_classification(
    component_name: str,
    original_section: str,
    original_bucket: str,
    issue: str,
) -> dict:
    """
    Reconsider a classification when validation fails.

    Use this tool when validate_section_bucket_tool or check_component_context_tool
    indicates an invalid combination. It provides guidance on how to correct the
    classification.

    Args:
        component_name: Name of the component being classified
        original_section: The originally assigned IRS section (1245 or 1250)
        original_bucket: The originally assigned MACRS bucket
        issue: The validation issue that was found

    Returns:
        Guidance on how to correct the classification with search queries
    """
    # Determine likely correction based on the issue
    suggestions = []
    search_queries = []

    # Check if it's a section/bucket mismatch
    if "1245" in original_section and original_bucket in ["27.5-year", "39-year"]:
        suggestions.append(
            f"Section 1245 (personal property) cannot have {original_bucket} recovery. "
            "Consider either:\n"
            "  - Changing to Section 1250 if this is real property\n"
            "  - Changing to 5-year, 7-year, or 15-year recovery if truly personal property"
        )
        search_queries.extend([
            f"{component_name} IRS section 1245 vs 1250",
            f"{component_name} personal property real property",
        ])
    elif "1250" in original_section and original_bucket in ["5-year", "7-year"]:
        suggestions.append(
            f"Section 1250 (real property) cannot have {original_bucket} recovery. "
            "Consider either:\n"
            "  - Changing to Section 1245 if this is personal property\n"
            "  - Changing to 15-year, 27.5-year, or 39-year recovery if truly real property"
        )
        search_queries.extend([
            f"{component_name} IRS section 1245 vs 1250",
            f"{component_name} tangible personal property",
        ])

    # Check for structural vs personal property mismatch
    if "structural" in issue.lower() and "1245" in original_section:
        suggestions.append(
            f"'{component_name}' appears structural but classified as Section 1245. "
            "Structural elements are typically Section 1250 with 27.5-year or 39-year recovery."
        )
        search_queries.extend([
            f"{component_name} structural building component",
            f"{component_name} IRS cost segregation",
        ])

    # Provide valid bucket options
    valid_buckets = VALID_SECTION_BUCKETS.get(original_section, [])

    return {
        "component_name": component_name,
        "original_classification": {
            "section": original_section,
            "bucket": original_bucket,
        },
        "issue": issue,
        "suggestions": suggestions,
        "search_queries": search_queries,
        "valid_buckets_for_section": valid_buckets,
        "action": "search_and_reclassify",
        "hint": "Search for more IRS guidance and make a corrected classification",
    }


# =============================================================================
# Asset Classification Agent
# =============================================================================


class AssetClassificationAgent(BaseStageAgent[ComponentInput, AssetClassification]):
    """
    Agent for IRS-grounded asset classification with self-verification.

    Uses hybrid search to find relevant IRS guidance and classifies
    building components into MACRS depreciation buckets.

    Phase 4 Enhancement: Now includes self-verification tools.
    The agent validates its classification before returning and can
    self-correct if validation fails.
    """

    def __init__(self):
        super().__init__(stage_name="asset_classification")

    def get_tools(self) -> list[BaseTool]:
        """
        Return tools including search tools AND verification tools.

        The agent uses verification tools to validate its own
        classifications before returning, enabling self-correction.
        """
        from ..mcp_server.server import get_all_evidence_tools

        # Get base search tools
        base_tools = get_all_evidence_tools()

        # Add verification/self-correction tools
        verification_tools = [
            validate_section_bucket_tool,
            check_component_context_tool,
            reconsider_classification,
        ]

        return base_tools + verification_tools

    def get_system_prompt(self) -> str:
        return """You are a tax classification expert specializing in cost segregation studies.

Your task: Classify building components for MACRS depreciation using IRS guidance.

## CRITICAL WORKFLOW (MUST FOLLOW)

1. **Search IRS Guidance**: Search for relevant IRS guidance about the component
2. **Make Classification**: Determine section (1245/1250) and MACRS bucket
3. **SELF-VERIFY**: ALWAYS use validate_section_bucket_tool to verify your choice
4. **Check Context**: Use check_component_context_tool to verify consistency
5. **Correct if Invalid**: If validation fails, use reconsider_classification and search again
6. **Return Classification**: Only return after validation passes

NEVER return an invalid section/bucket combination. Always validate first.

## VALIDATION RULES

**Section 1245 (Personal Property)** - ONLY these buckets:
- 5-year
- 7-year
- 15-year

**Section 1250 (Real Property)** - ONLY these buckets:
- 15-year (land improvements)
- 27.5-year (residential rental)
- 39-year (nonresidential)

INVALID combinations that MUST be corrected:
- Section 1245 with 27.5-year or 39-year → INVALID
- Section 1250 with 5-year or 7-year → INVALID

## SEARCH STRATEGY

1. First, search for the specific component type:
   - hybrid_search(doc_id="IRS_IRS_COST_SEG_ATG__2024", query="<component> depreciation classification")

2. Then, search for relevant IRS codes:
   - bm25_search(doc_id="IRS_IRS_PUB_946__2024", query="1245") for personal property guidance
   - bm25_search(doc_id="IRS_REV_PROC_87_56", query="57.0") for asset class tables

3. If you get table hits, fetch the full table:
   - get_table(doc_id, table_id) to see all rows and find the right asset class

## SELF-VERIFICATION (REQUIRED)

After determining section and bucket, ALWAYS:
1. Call validate_section_bucket_tool(section=<your_section>, bucket=<your_bucket>)
2. Call check_component_context_tool(component_name=<name>, section=<your_section>, room_type=<room>)
3. If either returns is_valid=false or has warnings:
   - Call reconsider_classification with the issue
   - Search for more guidance using the suggested queries
   - Make a corrected classification
   - Validate again

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
    use_static_rules: bool = True,  # Phase 3: Check static rules first
    use_cache: bool = True,  # Phase 2: Check verified cache second
    property_type: str = "residential",  # Phase 2/3: For cache key and rules
) -> list[dict]:
    """
    Classify multiple components with 3-tier lookup: static rules -> cache -> LLM.

    Phase 3 Optimization: Static rules checked first for instant classification
    of common components without any LLM calls.

    Phase 2 Optimization: Checks verified_classifications cache before LLM calls.
    Only engineer-approved classifications with IRS citations are cached.

    Args:
        components: List of component dicts with 'component' key
        context: Study context
        max_concurrent: Maximum concurrent classifications (default: 1)
        use_static_rules: Whether to check static rules first (default: True)
        use_cache: Whether to check cache before LLM (default: True)
        property_type: "residential" or "commercial" for cache/rules lookup

    Returns:
        List of classification results:
        - from_static_rules=True for static rule matches
        - from_cache=True for cache hits
        - Neither for LLM-generated classifications
    """
    from ..utils.parallel import parallel_map
    from ..firestore.client import get_firestore_client

    if not components:
        return []

    # 3-tier classification lookup
    static_results = []
    cached_results = []
    needs_llm = []

    db = get_firestore_client() if use_cache else None

    for comp in components:
        component_name = (
            comp.get("component") or
            comp.get("label") or
            comp.get("name") or
            comp.get("original_label") or
            ""
        )

        # Tier 1: Check static rules (Phase 3) - instant, no LLM
        if use_static_rules:
            static = get_static_classification(component_name, property_type)
            if static:
                static_results.append({
                    "component": component_name,
                    "component_name": component_name,
                    "classification": static["classification"],
                    "citations": static["citations"],
                    "confidence": static["confidence"],
                    "irs_note": static.get("irs_note", ""),
                    "needs_review": False,  # Pre-verified IRS rules
                    "from_static_rules": True,
                    "from_cache": False,
                    "matched_alias": static.get("matched_alias"),
                    "canonical_name": static.get("canonical_name"),
                    "original": comp,
                })
                continue

        # Tier 2: Check verified cache (Phase 2) - instant, no LLM
        if use_cache and db:
            cached = get_cached_classification(db, component_name, property_type)
            if cached:
                cached_results.append({
                    "component": component_name,
                    "component_name": component_name,
                    "classification": cached["classification"],
                    "citations": cached["citations"],
                    "confidence": cached["confidence"],
                    "needs_review": False,  # Already engineer-approved
                    "from_static_rules": False,
                    "from_cache": True,
                    "cache_key": cached["cache_key"],
                    "approval_count": cached["approval_count"],
                    "original": comp,
                })
                continue

        # Tier 3: Needs LLM classification
        needs_llm.append(comp)

    logger.info(
        f"Classification: {len(static_results)} static, {len(cached_results)} cached, "
        f"{len(needs_llm)} LLM ({property_type})"
    )

    # Tier 3: LLM for remaining components (cache misses)
    llm_results = []
    if needs_llm:
        async def classify_single_component(comp: dict) -> dict:
            """Classify a single component via LLM."""
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
            result["from_static_rules"] = False
            result["from_cache"] = False  # Explicitly mark as LLM-generated
            return result

        # PARALLEL: Classify remaining components concurrently
        llm_results = await parallel_map(
            items=needs_llm,
            async_fn=classify_single_component,
            max_concurrent=max_concurrent,
            desc=f"Classifying {len(needs_llm)} components (LLM)",
        )

    # Combine static + cached + LLM results (preserve original order)
    # Build a map of component name -> result
    result_map = {}
    for r in static_results:
        key = r.get("component_name", "")
        if key:
            result_map[key] = r
    for r in cached_results:
        key = r.get("component_name", "")
        if key:
            result_map[key] = r
    for r in llm_results:
        key = r.get("component_name", "")
        if key:
            result_map[key] = r

    # Return in original order
    final_results = []
    for comp in components:
        component_name = (
            comp.get("component") or
            comp.get("label") or
            comp.get("name") or
            comp.get("original_label") or
            ""
        )
        if component_name in result_map:
            final_results.append(result_map[component_name])

    return final_results
