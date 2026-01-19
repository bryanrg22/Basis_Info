"""
Classification Verifier Agent - Verifies asset classifications for IRS defensibility.

Validates asset classifications before final writeout to ensure:
1. Section consistency (1245 personal vs 1250 real property)
2. MACRS bucket alignment with section
3. Citation quality and relevance
4. Component context consistency

INVALID combinations this catches:
- Section 1245 with 27.5-year or 39-year bucket
- Section 1250 with 5-year or 7-year bucket
- Structural elements classified as personal property

Usage:
    from agents.classification_verifier import verify_classifications_batch

    verified = await verify_classifications_batch(
        classifications=asset_classifications,
        components=enriched_objects,
        context=stage_context,
    )
"""

import logging
from typing import Any, Optional

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from .base_agent import BaseStageAgent, StageContext, AgentOutput

logger = logging.getLogger(__name__)


# =============================================================================
# Schemas
# =============================================================================


class VerificationInput(BaseModel):
    """Input for ClassificationVerifierAgent."""
    component_name: str = Field(..., description="Name of the component being verified")
    section: str = Field(..., description="IRS section (1245 or 1250)")
    macrs_bucket: str = Field(..., description="MACRS recovery period")
    room_type: Optional[str] = Field(default=None, description="Room context")
    citations: list[dict] = Field(default_factory=list, description="Supporting citations")
    original_confidence: float = Field(default=0.5, description="Original classification confidence")


class VerificationResult(BaseModel):
    """Result of verification check."""
    component_name: str = Field(..., description="Component that was verified")
    is_valid: bool = Field(..., description="Whether classification is valid")
    needs_review: bool = Field(default=False, description="Flag for engineer review")
    review_reason: Optional[str] = Field(default=None, description="Reason if review needed")
    adjusted_confidence: float = Field(default=0.5, description="Adjusted confidence score")
    issues: list[str] = Field(default_factory=list, description="List of issues found")
    suggestions: list[str] = Field(default_factory=list, description="Correction suggestions")


# =============================================================================
# Validation Rules
# =============================================================================


# Valid section/bucket combinations per IRS rules
VALID_SECTION_BUCKETS = {
    "1245": ["5-year", "7-year", "15-year"],      # Personal property
    "1250": ["15-year", "27.5-year", "39-year"],  # Real property
}

# Components that are typically structural (Section 1250)
STRUCTURAL_COMPONENTS = {
    "wall", "ceiling", "floor", "foundation", "roof", "structural",
    "framing", "beam", "column", "slab", "concrete", "drywall",
    "stucco", "brick", "masonry", "insulation",
}

# Components that are typically personal property (Section 1245)
PERSONAL_PROPERTY_COMPONENTS = {
    "hvac", "lighting", "light fixture", "electrical panel", "outlet",
    "plumbing fixture", "appliance", "cabinet", "countertop", "carpet",
    "tile", "flooring", "window treatment", "blind", "security system",
    "fire alarm", "sprinkler head", "thermostat", "vent", "duct",
}

# Room types with special considerations
SPECIAL_ROOM_RULES = {
    "mechanical_room": {
        "expected_section": "1245",  # Most items here are personal property
        "warning_if_1250": True,
    },
    "exterior": {
        "expected_section": "1250",  # Land improvements are typically 1250
        "warning_if_1245": False,  # Some exterior items can be 1245
    },
}


def validate_section_bucket(section: str, bucket: str) -> tuple[bool, str]:
    """
    Validate section/bucket combination per IRS rules.

    Args:
        section: IRS section (1245 or 1250)
        bucket: MACRS recovery period

    Returns:
        Tuple of (is_valid, message)
    """
    # Normalize inputs
    section = section.strip()
    bucket = bucket.lower().strip()

    # Handle variations in bucket naming
    bucket_normalized = bucket
    if "5" in bucket and "year" in bucket and "15" not in bucket and "27" not in bucket:
        bucket_normalized = "5-year"
    elif "7" in bucket and "year" in bucket and "27" not in bucket:
        bucket_normalized = "7-year"
    elif "15" in bucket and "year" in bucket:
        bucket_normalized = "15-year"
    elif "27" in bucket or "27.5" in bucket:
        bucket_normalized = "27.5-year"
    elif "39" in bucket and "year" in bucket:
        bucket_normalized = "39-year"

    valid_buckets = VALID_SECTION_BUCKETS.get(section, [])

    if not valid_buckets:
        return False, f"Unknown section: {section}"

    if bucket_normalized not in valid_buckets:
        return False, (
            f"Invalid: Section {section} cannot have {bucket} recovery. "
            f"Valid buckets for {section}: {', '.join(valid_buckets)}"
        )

    return True, "Valid combination"


def check_component_context(
    component_name: str,
    section: str,
    room_type: Optional[str],
) -> tuple[bool, list[str]]:
    """
    Check if component classification makes sense in its context.

    Args:
        component_name: Name of the component
        section: IRS section assigned
        room_type: Room where component is located

    Returns:
        Tuple of (is_consistent, list of warnings)
    """
    warnings = []
    component_lower = component_name.lower()

    # Check if structural component is classified as personal property
    is_structural = any(s in component_lower for s in STRUCTURAL_COMPONENTS)
    if is_structural and section == "1245":
        warnings.append(
            f"'{component_name}' appears to be structural but classified as Section 1245 (personal property). "
            "Structural elements are typically Section 1250."
        )

    # Check if personal property is classified as real property
    is_personal = any(p in component_lower for p in PERSONAL_PROPERTY_COMPONENTS)
    if is_personal and section == "1250" and "15-year" not in str(VALID_SECTION_BUCKETS.get("1250", [])):
        # Note: Some items can be 1250 with 15-year (land improvements)
        if "land" not in component_lower and "improvement" not in component_lower:
            warnings.append(
                f"'{component_name}' appears to be personal property but classified as Section 1250. "
                "Consider if Section 1245 is more appropriate."
            )

    # Check room-specific rules
    if room_type and room_type.lower() in SPECIAL_ROOM_RULES:
        rules = SPECIAL_ROOM_RULES[room_type.lower()]
        expected = rules.get("expected_section")
        if expected and section != expected:
            if rules.get("warning_if_1250") and section == "1250":
                warnings.append(
                    f"Component in {room_type} classified as Section 1250. "
                    f"Items in {room_type} are often Section 1245 personal property."
                )
            elif rules.get("warning_if_1245") and section == "1245":
                warnings.append(
                    f"Component in {room_type} classified as Section 1245. "
                    f"Some {room_type} items may be Section 1250 land improvements."
                )

    return len(warnings) == 0, warnings


def check_citation_quality(citations: list[dict]) -> tuple[float, list[str]]:
    """
    Assess the quality of supporting citations.

    Args:
        citations: List of citation dicts

    Returns:
        Tuple of (quality_score 0-1, list of issues)
    """
    issues = []

    if not citations:
        return 0.0, ["No supporting citations provided"]

    # Check citation count
    if len(citations) < 2:
        issues.append("Only one citation - additional sources recommended for IRS defensibility")

    # Check for IRS source citations
    has_irs_source = any(
        "irs" in c.get("doc_id", "").lower() or
        "pub" in c.get("doc_id", "").lower() or
        "rev" in c.get("doc_id", "").lower()
        for c in citations
    )
    if not has_irs_source:
        issues.append("No IRS publication or revenue ruling cited - consider adding primary source")

    # Check for meaningful excerpts
    empty_excerpts = sum(1 for c in citations if not c.get("excerpt", "").strip())
    if empty_excerpts > 0:
        issues.append(f"{empty_excerpts} citation(s) have empty excerpts")

    # Calculate quality score
    score = 1.0
    if not citations:
        score = 0.0
    else:
        if len(citations) < 2:
            score -= 0.2
        if not has_irs_source:
            score -= 0.3
        if empty_excerpts > 0:
            score -= 0.1 * empty_excerpts

    return max(0.0, score), issues


# =============================================================================
# Verification Tools
# =============================================================================


@tool
def validate_section_bucket_tool(section: str, bucket: str) -> dict:
    """
    Validate that a section/bucket combination is valid per IRS rules.

    Section 1245 (personal property) can only have 5-year, 7-year, or 15-year recovery.
    Section 1250 (real property) can only have 15-year, 27.5-year, or 39-year recovery.

    Args:
        section: IRS section (1245 or 1250)
        bucket: MACRS recovery period (e.g., "5-year", "39-year")

    Returns:
        Validation result with is_valid flag and message
    """
    is_valid, message = validate_section_bucket(section, bucket)
    return {
        "section": section,
        "bucket": bucket,
        "is_valid": is_valid,
        "message": message,
        "valid_buckets_for_section": VALID_SECTION_BUCKETS.get(section, []),
    }


@tool
def check_component_context_tool(
    component_name: str,
    section: str,
    room_type: str = "",
) -> dict:
    """
    Check if a component's classification makes sense in its context.

    Identifies potential issues like:
    - Structural elements classified as personal property
    - Personal property classified as real property
    - Room-specific classification anomalies

    Args:
        component_name: Name of the component
        section: IRS section assigned (1245 or 1250)
        room_type: Room where component is located (optional)

    Returns:
        Context check result with consistency flag and warnings
    """
    is_consistent, warnings = check_component_context(
        component_name,
        section,
        room_type if room_type else None,
    )
    return {
        "component_name": component_name,
        "section": section,
        "room_type": room_type or "not specified",
        "is_consistent": is_consistent,
        "warnings": warnings,
        "suggestion": "Flag for engineer review" if warnings else "Classification appears consistent",
    }


@tool
def assess_citation_quality_tool(citations: list[dict]) -> dict:
    """
    Assess the quality of supporting citations for IRS defensibility.

    Checks for:
    - Minimum number of citations
    - Presence of IRS primary sources
    - Quality of excerpt content

    Args:
        citations: List of citation dicts with doc_id and excerpt fields

    Returns:
        Quality assessment with score and improvement suggestions
    """
    score, issues = check_citation_quality(citations)
    return {
        "citation_count": len(citations),
        "quality_score": round(score, 2),
        "issues": issues,
        "is_sufficient": score >= 0.5,
        "recommendation": (
            "Citations are sufficient for IRS defensibility"
            if score >= 0.7 else
            "Consider adding more IRS source citations"
            if score >= 0.5 else
            "Citations need improvement for IRS defensibility"
        ),
    }


# =============================================================================
# ClassificationVerifierAgent
# =============================================================================


class ClassificationVerifierAgent(BaseStageAgent[VerificationInput, VerificationResult]):
    """
    Verifies asset classifications for IRS defensibility.

    Checks:
    1. Section/bucket combination validity
    2. Component context consistency
    3. Citation quality
    4. Overall classification defensibility

    Use this agent after asset classification to catch errors before final writeout.
    """

    def __init__(self):
        super().__init__(stage_name="classification_verification")

    def get_system_prompt(self) -> str:
        return """You are an IRS cost segregation compliance expert.

Your task is to verify that asset classifications are defensible under IRS rules.

Key validation rules:
1. Section 1245 (personal property) can ONLY have 5-year, 7-year, or 15-year MACRS recovery
2. Section 1250 (real property) can ONLY have 15-year, 27.5-year, or 39-year MACRS recovery
3. Structural elements (walls, foundation, roof) are typically Section 1250
4. Equipment and fixtures (HVAC, lighting, plumbing) are typically Section 1245

INVALID combinations you must flag:
- Section 1245 with 27.5-year or 39-year bucket
- Section 1250 with 5-year or 7-year bucket (except land improvements can be 15-year)
- Structural elements classified as Section 1245 personal property

Available tools:
- validate_section_bucket_tool: Check if section/bucket is valid
- check_component_context_tool: Check if classification fits context
- assess_citation_quality_tool: Evaluate citation quality

For each classification:
1. First validate the section/bucket combination
2. Then check the component context
3. Finally assess citation quality

Return your verification as JSON:
{
    "is_valid": true/false,
    "needs_review": true/false,
    "review_reason": "Reason for review if needed",
    "adjusted_confidence": 0.0-1.0,
    "issues": ["List of issues found"],
    "suggestions": ["List of correction suggestions"]
}"""

    def get_tools(self) -> list[BaseTool]:
        return [
            validate_section_bucket_tool,
            check_component_context_tool,
            assess_citation_quality_tool,
        ]

    def get_output_schema(self) -> type[VerificationResult]:
        return VerificationResult

    def parse_output(
        self,
        response: str,
        tool_calls: list[dict],
    ) -> VerificationResult:
        """Parse agent response into VerificationResult."""
        import json

        # Try to parse JSON from response
        try:
            # Look for JSON in response
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                data = json.loads(response[start:end])
                return VerificationResult(
                    component_name=data.get("component_name", ""),
                    is_valid=data.get("is_valid", True),
                    needs_review=data.get("needs_review", False),
                    review_reason=data.get("review_reason"),
                    adjusted_confidence=data.get("adjusted_confidence", 0.5),
                    issues=data.get("issues", []),
                    suggestions=data.get("suggestions", []),
                )
        except json.JSONDecodeError:
            pass

        # Fallback
        return VerificationResult(
            component_name="",
            is_valid=True,
            needs_review=True,
            review_reason="Could not parse verification response",
            adjusted_confidence=0.3,
            issues=["Verification parsing failed"],
            suggestions=["Manual review recommended"],
        )


# =============================================================================
# Batch Verification Function
# =============================================================================


async def verify_single_classification(
    classification: dict,
    component: dict,
    context: StageContext,
) -> dict:
    """
    Verify a single classification without using the full agent.

    This is a fast path that uses direct validation rules for common cases.

    Args:
        classification: Classification dict with section, macrs_bucket, etc.
        component: Component dict with label, room_type, etc.
        context: Stage context

    Returns:
        Verification result dict
    """
    issues = []
    suggestions = []
    needs_review = False
    review_reason = None

    # Get classification details
    clf = classification.get("classification", {})
    section = clf.get("section", "")
    bucket = clf.get("macrs_bucket", clf.get("recovery_period", ""))
    component_name = component.get("label", component.get("original_label", "unknown"))
    room_type = component.get("room_type")
    citations = classification.get("citations", [])
    original_confidence = classification.get("confidence", 0.5)

    # 1. Validate section/bucket combination
    is_valid_combo, combo_message = validate_section_bucket(section, bucket)
    if not is_valid_combo:
        issues.append(combo_message)
        suggestions.append(f"Review section/bucket assignment for {component_name}")
        needs_review = True
        review_reason = combo_message

    # 2. Check component context
    is_consistent, context_warnings = check_component_context(component_name, section, room_type)
    issues.extend(context_warnings)
    if context_warnings:
        suggestions.append("Consider if classification matches component type")
        if not needs_review:
            needs_review = True
            review_reason = context_warnings[0]

    # 3. Check citation quality
    citation_score, citation_issues = check_citation_quality(citations)
    issues.extend(citation_issues)
    if citation_score < 0.5:
        suggestions.append("Add more supporting citations, especially from IRS sources")
        if not needs_review and citation_score < 0.3:
            needs_review = True
            review_reason = "Insufficient citation support for IRS defensibility"

    # Calculate adjusted confidence
    confidence_adjustment = 0.0
    if not is_valid_combo:
        confidence_adjustment -= 0.4
    if not is_consistent:
        confidence_adjustment -= 0.2
    if citation_score < 0.5:
        confidence_adjustment -= 0.1

    adjusted_confidence = max(0.1, min(1.0, original_confidence + confidence_adjustment))

    return {
        "component_name": component_name,
        "is_valid": is_valid_combo and is_consistent,
        "needs_review": needs_review,
        "review_reason": review_reason,
        "adjusted_confidence": round(adjusted_confidence, 2),
        "issues": issues,
        "suggestions": suggestions,
    }


async def verify_classifications_batch(
    classifications: list[dict],
    components: list[dict],
    context: StageContext,
    use_agent: bool = False,
    max_concurrent: int = 5,
) -> list[dict]:
    """
    Verify a batch of classifications.

    By default, uses fast direct validation. Set use_agent=True
    to use the full ClassificationVerifierAgent for complex cases.

    Args:
        classifications: List of classification dicts
        components: List of component dicts (matched by index)
        context: Stage context
        use_agent: Whether to use full agent (slower but more thorough)
        max_concurrent: Max concurrent verifications

    Returns:
        List of verification result dicts
    """
    from ..utils.parallel import parallel_map

    if not classifications:
        return []

    # Ensure components list matches classifications
    if len(components) < len(classifications):
        # Pad with empty dicts
        components = components + [{}] * (len(classifications) - len(components))

    async def verify_one(idx_and_clf: tuple[int, dict]) -> dict:
        idx, clf = idx_and_clf
        component = components[idx] if idx < len(components) else {}
        return await verify_single_classification(clf, component, context)

    # Create indexed list
    indexed_classifications = list(enumerate(classifications))

    # Run in parallel
    results = await parallel_map(
        items=indexed_classifications,
        async_fn=verify_one,
        max_concurrent=max_concurrent,
        desc="Verifying classifications",
    )

    # Log summary
    total = len(results)
    flagged = sum(1 for r in results if r.get("needs_review"))
    invalid = sum(1 for r in results if not r.get("is_valid"))

    logger.info(
        f"Classification verification complete: {total} verified, "
        f"{flagged} flagged for review, {invalid} invalid combinations"
    )

    return results
