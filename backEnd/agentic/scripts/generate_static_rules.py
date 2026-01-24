#!/usr/bin/env python3
"""
Generate Static Classification Rules from RAG Pipeline.

Phase 3 Optimization: This script generates pre-verified IRS classification
rules by running the existing LLM + RAG pipeline on common components and
capturing high-confidence results.

IMPORTANT: This is a ONE-TIME script to bootstrap the static rules.
Run it once to populate static_classification_rules.py, then the rules
are used for all subsequent classifications without LLM calls.

Usage:
    cd backEnd
    python3 -m agentic.scripts.generate_static_rules

    # Or with options:
    python3 -m agentic.scripts.generate_static_rules --confidence 0.85 --dry-run

Output:
    - Prints results to stdout
    - Optionally updates static_classification_rules.py with new rules
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Common Components to Pre-compute
# =============================================================================

COMMON_COMPONENTS = [
    # Flooring
    "carpet",
    "tile",
    "hardwood_floor",
    "vinyl_flooring",
    "laminate_flooring",
    # Lighting
    "light_fixture",
    "ceiling_fan",
    "recessed_light",
    "chandelier",
    # Cabinets/Storage
    "kitchen_cabinet",
    "bathroom_vanity",
    "closet_shelving",
    # Counters
    "countertop",
    "kitchen_counter",
    "bathroom_counter",
    # Appliances
    "appliance",
    "refrigerator",
    "dishwasher",
    "stove",
    "oven",
    "microwave",
    "washer",
    "dryer",
    "garbage_disposal",
    "water_heater",
    # Bathroom
    "toilet",
    "sink",
    "bathtub",
    "shower",
    "mirror",
    # Windows
    "blinds",
    "curtains",
    "window_treatment",
    # Safety
    "smoke_detector",
    "fire_alarm",
    "thermostat",
    "doorbell",
    # Land Improvements
    "parking_lot",
    "sidewalk",
    "landscaping",
    "fence",
    "signage",
    "outdoor_lighting",
    "retaining_wall",
    "driveway",
    "pool",
    "patio",
    "deck",
    "sprinkler_system",
    # Furniture (7-year)
    "furniture",
    "desk",
    "chair",
    "filing_cabinet",
]


async def classify_component_via_rag(
    component_name: str,
    property_type: str = "residential",
) -> Optional[dict]:
    """
    Classify a single component using the existing LLM + RAG pipeline.

    Args:
        component_name: Name of the component
        property_type: "residential" or "commercial"

    Returns:
        Classification result dict with citations, or None if failed
    """
    try:
        from ..agents.asset_agent import classify_component
        from ..agents.base_agent import StageContext

        # Create context with IRS reference documents
        context = StageContext(
            study_id="static_rules_generation",
            property_name="Static Rules Generator",
            reference_doc_ids=[
                "IRS_IRS_PUB_946__2024",
                "IRS_REV_PROC_87_56",
                "IRS_IRS_COST_SEG_ATG__2024",
                "IRS_IRS_PUB_527__2024",
            ],
            study_doc_ids=[],
        )

        # Run classification
        result = await classify_component(
            component=component_name,
            context=context,
            space_type=None,
            indoor_outdoor=None,
            attachment_type=None,
            function_type=None,
        )

        return result

    except Exception as e:
        logger.error(f"Failed to classify '{component_name}': {e}")
        return None


def format_rule_for_output(component_name: str, result: dict) -> dict:
    """
    Format a classification result as a static rule entry.

    Args:
        component_name: Normalized component name
        result: Classification result from RAG pipeline

    Returns:
        Dict formatted for ASSET_CLASSIFICATION_RULES
    """
    classification = result.get("classification", {})
    citations = result.get("citations", [])

    # Extract section from classification
    section = classification.get("section") or classification.get("irs_section", "")

    # Extract bucket
    bucket = classification.get("bucket") or classification.get("depreciation_bucket", "")

    # Extract life years
    life_years = classification.get("life_years") or classification.get(
        "recovery_period_years", 0
    )

    # Get asset class
    asset_class = classification.get("asset_class")

    # Get IRS note
    irs_note = classification.get("irs_note", "") or result.get("irs_note", "")

    # Format citations for static rules
    formatted_citations = []
    for citation in citations:
        if isinstance(citation, dict):
            formatted_citations.append({
                "doc_id": citation.get("doc_id", citation.get("document_id", "")),
                "page": citation.get("page", citation.get("page_number", 0)),
                "excerpt": citation.get("excerpt", citation.get("text", ""))[:100],
            })

    return {
        "section": section,
        "bucket": bucket,
        "life_years": life_years,
        "asset_class": asset_class,
        "irs_note": irs_note[:200] if irs_note else f"{component_name} classification",
        "citations": formatted_citations,
    }


def normalize_component_name(name: str) -> str:
    """Normalize component name for use as dict key."""
    return name.lower().strip().replace(" ", "_").replace("-", "_")


async def generate_rules(
    components: list[str],
    min_confidence: float = 0.90,
    property_type: str = "residential",
    dry_run: bool = False,
) -> dict[str, dict]:
    """
    Generate static rules for a list of components.

    Args:
        components: List of component names to classify
        min_confidence: Minimum confidence threshold for inclusion
        property_type: "residential" or "commercial"
        dry_run: If True, don't save results

    Returns:
        Dict of component_name -> rule_dict for successful classifications
    """
    rules = {}
    skipped = []
    errors = []

    logger.info(f"Generating static rules for {len(components)} components...")
    logger.info(f"Minimum confidence threshold: {min_confidence}")

    for i, component in enumerate(components, 1):
        normalized = normalize_component_name(component)
        logger.info(f"[{i}/{len(components)}] Processing: {component}")

        try:
            result = await classify_component_via_rag(component, property_type)

            if not result:
                errors.append((component, "No result returned"))
                logger.warning(f"  -> ERROR: No result")
                continue

            confidence = result.get("confidence", 0)
            citations = result.get("citations", [])

            # Check confidence threshold
            if confidence < min_confidence:
                skipped.append((component, f"Low confidence: {confidence:.2f}"))
                logger.info(f"  -> SKIPPED: confidence={confidence:.2f} < {min_confidence}")
                continue

            # Check for citations (required for IRS defensibility)
            if not citations:
                skipped.append((component, "No citations"))
                logger.info(f"  -> SKIPPED: no citations")
                continue

            # Format as static rule
            rule = format_rule_for_output(normalized, result)

            # Validate rule has required fields
            if not rule.get("section") or not rule.get("bucket"):
                skipped.append((component, "Missing section or bucket"))
                logger.info(f"  -> SKIPPED: missing section/bucket")
                continue

            rules[normalized] = rule
            bucket = rule.get("bucket", "unknown")
            logger.info(f"  -> SUCCESS: {bucket} (confidence={confidence:.2f})")

        except Exception as e:
            errors.append((component, str(e)))
            logger.error(f"  -> ERROR: {e}")

    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info("GENERATION COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total components: {len(components)}")
    logger.info(f"Successfully generated: {len(rules)}")
    logger.info(f"Skipped (low confidence/no citations): {len(skipped)}")
    logger.info(f"Errors: {len(errors)}")

    if skipped:
        logger.info("\nSkipped components:")
        for comp, reason in skipped:
            logger.info(f"  - {comp}: {reason}")

    if errors:
        logger.info("\nFailed components:")
        for comp, reason in errors:
            logger.info(f"  - {comp}: {reason}")

    return rules


def save_rules_to_file(rules: dict[str, dict], output_path: Optional[Path] = None):
    """
    Save generated rules to a Python file.

    Args:
        rules: Dict of component_name -> rule_dict
        output_path: Path to save file (default: static_classification_rules.py)
    """
    if output_path is None:
        output_path = (
            Path(__file__).parent.parent / "agents" / "static_classification_rules.py"
        )

    # Read existing file to preserve structure
    if output_path.exists():
        logger.info(f"Reading existing rules from {output_path}")

    # Format rules as Python code
    rules_code = []
    for component, rule in sorted(rules.items()):
        rule_str = json.dumps(rule, indent=8)
        # Convert JSON to Python dict format
        rule_str = rule_str.replace("null", "None")
        rules_code.append(f'    "{component}": {rule_str},')

    rules_block = "\n".join(rules_code)

    logger.info(f"\n{'=' * 60}")
    logger.info("GENERATED RULES (paste into ASSET_CLASSIFICATION_RULES):")
    logger.info("=" * 60)
    print(rules_block)

    logger.info(f"\n\nTotal rules ready for static_classification_rules.py: {len(rules)}")


async def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Generate static classification rules from RAG pipeline"
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.90,
        help="Minimum confidence threshold (default: 0.90)",
    )
    parser.add_argument(
        "--property-type",
        choices=["residential", "commercial"],
        default="residential",
        help="Property type (default: residential)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't save results, just print",
    )
    parser.add_argument(
        "--component",
        type=str,
        help="Classify a single component (for testing)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file path",
    )

    args = parser.parse_args()

    # Single component mode
    if args.component:
        result = await classify_component_via_rag(args.component, args.property_type)
        if result:
            print(json.dumps(result, indent=2, default=str))
            confidence = result.get("confidence", 0)
            if confidence >= args.confidence and result.get("citations"):
                rule = format_rule_for_output(
                    normalize_component_name(args.component), result
                )
                print("\nFormatted rule:")
                print(json.dumps(rule, indent=2))
        else:
            print(f"Failed to classify: {args.component}")
        return

    # Full generation mode
    rules = await generate_rules(
        components=COMMON_COMPONENTS,
        min_confidence=args.confidence,
        property_type=args.property_type,
        dry_run=args.dry_run,
    )

    if not args.dry_run and rules:
        output_path = Path(args.output) if args.output else None
        save_rules_to_file(rules, output_path)


if __name__ == "__main__":
    asyncio.run(main())
