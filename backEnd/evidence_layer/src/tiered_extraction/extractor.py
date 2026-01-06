"""
Tiered Extractor Orchestrator

Main entry point for the tiered extraction system.
Coordinates extraction through multiple tiers:
1. MISMO XML (if available) - confidence: 1.0
2. Azure Document Intelligence - confidence: 0.7-0.95
3. GPT-4o Vision Fallback - confidence: 0.6-0.9
4. Regex Fallback - confidence: 0.5-0.8
5. Validation & Confidence Aggregation
"""

import logging
import os
import sys
from pathlib import Path
from typing import Dict, Optional

from .confidence import (
    ExtractionResult,
    ExtractionTimer,
    FieldResult,
    aggregate_confidence,
    create_empty_result,
    merge_results,
    should_flag_for_review,
)
from .field_mappings import CONFIDENCE_THRESHOLDS, CRITICAL_FIELDS
from .mismo_parser import MISMOParser
from .azure_di_extractor import AzureDocumentExtractor
from .vision_fallback import VisionFallbackExtractor
from .validation import validate_and_flag

logger = logging.getLogger(__name__)


class RegexExtractor:
    """
    Wrapper around existing regex-based extraction.

    Uses the map_appraisal_sections.py module as Tier 4 fallback.
    """

    def __init__(self):
        self._extractor_available = False
        self._check_availability()

    def _check_availability(self) -> None:
        """Check if regex extractor is available."""
        try:
            # Add parent path for importing existing module
            parent_path = Path(__file__).parent.parent
            if str(parent_path) not in sys.path:
                sys.path.insert(0, str(parent_path))

            from map_appraisal_sections import map_appraisal_tables_to_sections
            self._extractor_available = True
        except ImportError as e:
            logger.warning(f"Regex extractor not available: {e}")
            self._extractor_available = False

    def extract(
        self,
        tables_path: str,
        fallback_fields: Optional[Dict] = None
    ) -> Dict[str, FieldResult]:
        """
        Extract using regex patterns from table text.

        Args:
            tables_path: Path to .tables.jsonl file
            fallback_fields: Optional fallback fields

        Returns:
            Dictionary of "section.field_name" -> FieldResult
        """
        results: Dict[str, FieldResult] = {}

        if not self._extractor_available:
            return results

        try:
            from map_appraisal_sections import map_appraisal_tables_to_sections

            sections = map_appraisal_tables_to_sections(
                tables_path,
                fallback_fields or {}
            )

            # Convert to FieldResult format
            for section_name, fields in sections.items():
                if isinstance(fields, dict):
                    for field_name, value in fields.items():
                        if value is not None and value != "":
                            field_key = f"{section_name}.{field_name}"

                            # Assign confidence based on field importance
                            confidence = 0.75
                            if field_name in CRITICAL_FIELDS:
                                confidence = 0.70  # Slightly lower for critical
                            elif value and len(str(value)) > 5:
                                confidence = 0.80  # Higher for substantial values

                            results[field_key] = FieldResult(
                                value=value,
                                confidence=confidence,
                                source="regex"
                            )

        except Exception as e:
            logger.error(f"Regex extraction failed: {e}")

        return results

    def is_available(self) -> bool:
        """Check if regex extractor is available."""
        return self._extractor_available


class TieredExtractor:
    """
    Main orchestrator for tiered appraisal extraction.

    Provides a production-grade extraction pipeline that:
    - Tries multiple extraction methods in order of reliability
    - Tracks confidence scores for each field
    - Flags results that need manual review
    - Provides detailed extraction metadata
    """

    def __init__(self):
        self.mismo_parser = MISMOParser()
        self.azure_extractor = AzureDocumentExtractor()
        self.vision_extractor = VisionFallbackExtractor()
        self.regex_extractor = RegexExtractor()

    async def extract(
        self,
        pdf_path: str,
        mismo_xml: Optional[str] = None,
        tables_path: Optional[str] = None,
        fallback_fields: Optional[Dict] = None
    ) -> ExtractionResult:
        """
        Extract appraisal data using tiered extraction.

        Args:
            pdf_path: Path to appraisal PDF file
            mismo_xml: Optional MISMO XML content (Tier 1)
            tables_path: Optional path to .tables.jsonl for regex fallback
            fallback_fields: Optional fallback field values

        Returns:
            ExtractionResult with all extracted fields and metadata
        """
        with ExtractionTimer() as timer:
            # Start with empty result
            result = create_empty_result()

            # === Tier 1: MISMO XML ===
            if mismo_xml:
                logger.info("Tier 1: Attempting MISMO XML extraction")
                mismo_result = self.mismo_parser.parse(mismo_xml)

                if mismo_result.overall_confidence >= 0.95:
                    # MISMO is authoritative - use it exclusively
                    logger.info("Tier 1: MISMO XML provided complete extraction")
                    mismo_result.extraction_time_ms = timer.elapsed_ms
                    return validate_and_flag(mismo_result)

                # Merge partial MISMO results
                result = mismo_result
                logger.info(f"Tier 1: MISMO XML partial extraction (confidence: {result.overall_confidence:.2f})")

            # === Tier 2: Azure Document Intelligence ===
            if self.azure_extractor.is_available():
                logger.info("Tier 2: Attempting Azure Document Intelligence extraction")
                try:
                    azure_result = await self.azure_extractor.extract(pdf_path)

                    # Merge Azure results, improving low-confidence fields
                    for section_name, fields in azure_result.sections.items():
                        for field_name, field_result in fields.items():
                            if field_result.value is not None and field_result.value != "":
                                field_key = f"{section_name}.{field_name}"
                                current = result.get_field(section_name, field_name)

                                # Replace if better or missing
                                if (current is None or
                                    current.value in (None, "") or
                                    field_result.confidence > current.confidence):
                                    result.set_field(section_name, field_name, field_result)

                    if "azure_di" not in result.sources_used:
                        result.sources_used.append("azure_di")

                    logger.info(f"Tier 2: Azure DI extraction complete")

                except Exception as e:
                    logger.error(f"Tier 2: Azure DI failed: {e}")
            else:
                logger.info("Tier 2: Azure DI not available, skipping")

            # === Tier 3: Vision Fallback ===
            # Get fields that are still missing or low-confidence
            low_conf_fields = self._get_fields_needing_improvement(result)

            if low_conf_fields and self.vision_extractor.is_available():
                logger.info(f"Tier 3: Attempting vision extraction for {len(low_conf_fields)} fields")
                try:
                    vision_results = await self.vision_extractor.extract_fields(
                        pdf_path,
                        low_conf_fields
                    )

                    if vision_results:
                        result = merge_results(result, vision_results, only_improve=True)
                        logger.info(f"Tier 3: Vision extraction improved {len(vision_results)} fields")

                except Exception as e:
                    logger.error(f"Tier 3: Vision fallback failed: {e}")
            elif low_conf_fields:
                logger.info("Tier 3: Vision extractor not available, skipping")

            # === Tier 4: Regex Fallback ===
            empty_fields = self._get_empty_critical_fields(result)

            if empty_fields and tables_path and self.regex_extractor.is_available():
                logger.info(f"Tier 4: Attempting regex extraction for {len(empty_fields)} fields")
                try:
                    regex_results = self.regex_extractor.extract(tables_path, fallback_fields)

                    if regex_results:
                        result = merge_results(result, regex_results, only_improve=True)
                        logger.info(f"Tier 4: Regex extraction provided {len(regex_results)} fields")

                except Exception as e:
                    logger.error(f"Tier 4: Regex fallback failed: {e}")

            # === Tier 5: Validation ===
            logger.info("Tier 5: Running validation")
            result = validate_and_flag(result)

            # Finalize
            result.overall_confidence = aggregate_confidence(result)
            result.needs_review = should_flag_for_review(result)

        result.extraction_time_ms = timer.elapsed_ms
        logger.info(
            f"Extraction complete: confidence={result.overall_confidence:.2f}, "
            f"needs_review={result.needs_review}, time={result.extraction_time_ms}ms"
        )

        return result

    def _get_fields_needing_improvement(
        self,
        result: ExtractionResult
    ) -> list:
        """Get fields that are missing or have low confidence."""
        threshold = CONFIDENCE_THRESHOLDS["standard"]
        fields_to_improve = []

        # Prioritize critical fields
        for field_name in CRITICAL_FIELDS:
            for section_name, fields in result.sections.items():
                if field_name in fields:
                    field_result = fields[field_name]
                    if (field_result.value in (None, "") or
                        field_result.confidence < threshold):
                        fields_to_improve.append((section_name, field_name))
                    break

        # Add other low-confidence fields
        for section_name, fields in result.sections.items():
            for field_name, field_result in fields.items():
                if field_name in CRITICAL_FIELDS:
                    continue  # Already handled
                if field_result.confidence < threshold:
                    fields_to_improve.append((section_name, field_name))

        return fields_to_improve[:20]  # Limit to avoid excessive API calls

    def _get_empty_critical_fields(
        self,
        result: ExtractionResult
    ) -> list:
        """Get critical fields that are still empty."""
        empty = []

        for field_name in CRITICAL_FIELDS:
            for section_name, fields in result.sections.items():
                if field_name in fields:
                    if fields[field_name].value in (None, ""):
                        empty.append((section_name, field_name))
                    break

        return empty


async def extract_appraisal(
    pdf_path: str,
    mismo_xml: Optional[str] = None,
    tables_path: Optional[str] = None,
    fallback_fields: Optional[Dict] = None
) -> ExtractionResult:
    """
    Convenience function for tiered extraction.

    Args:
        pdf_path: Path to appraisal PDF
        mismo_xml: Optional MISMO XML content
        tables_path: Optional path to .tables.jsonl
        fallback_fields: Optional fallback fields

    Returns:
        ExtractionResult with extracted data and metadata
    """
    extractor = TieredExtractor()
    return await extractor.extract(pdf_path, mismo_xml, tables_path, fallback_fields)
