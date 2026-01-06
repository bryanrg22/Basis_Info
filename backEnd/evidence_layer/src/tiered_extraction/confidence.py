"""
Confidence Scoring and Result Data Structures

Provides dataclasses for field-level confidence tracking and
utilities for merging results from multiple extraction tiers.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import time

from .field_mappings import CRITICAL_FIELDS, CONFIDENCE_THRESHOLDS


@dataclass
class FieldResult:
    """Result of extracting a single field."""
    value: Any
    confidence: float  # 0.0 to 1.0
    source: str  # "mismo_xml" | "azure_di" | "gpt4o_vision" | "regex"
    bounding_box: Optional[Dict[str, float]] = None  # For vision-based sources

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source,
        }
        if self.bounding_box:
            result["bounding_box"] = self.bounding_box
        return result

    @classmethod
    def empty(cls, source: str = "none") -> "FieldResult":
        """Create an empty field result."""
        return cls(value=None, confidence=0.0, source=source)


@dataclass
class ExtractionResult:
    """Complete extraction result with all sections and metadata."""
    sections: Dict[str, Dict[str, FieldResult]] = field(default_factory=dict)
    overall_confidence: float = 0.0
    needs_review: bool = False
    extraction_time_ms: int = 0
    sources_used: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for Firestore/JSON."""
        sections_dict = {}
        for section_name, fields in self.sections.items():
            sections_dict[section_name] = {}
            for field_name, field_result in fields.items():
                # Store just the value for backwards compatibility
                sections_dict[section_name][field_name] = field_result.value

        # Add metadata
        sections_dict["_metadata"] = {
            "overall_confidence": self.overall_confidence,
            "needs_review": self.needs_review,
            "extraction_time_ms": self.extraction_time_ms,
            "sources_used": self.sources_used,
            "field_confidences": self._get_field_confidences(),
        }

        return sections_dict

    def _get_field_confidences(self) -> Dict[str, Dict[str, float]]:
        """Get confidence scores for all fields."""
        result = {}
        for section_name, fields in self.sections.items():
            result[section_name] = {}
            for field_name, field_result in fields.items():
                result[section_name][field_name] = {
                    "confidence": field_result.confidence,
                    "source": field_result.source,
                }
        return result

    def get_field(self, section: str, field_name: str) -> Optional[FieldResult]:
        """Get a specific field result."""
        return self.sections.get(section, {}).get(field_name)

    def set_field(self, section: str, field_name: str, result: FieldResult) -> None:
        """Set a specific field result."""
        if section not in self.sections:
            self.sections[section] = {}
        self.sections[section][field_name] = result

    def get_low_confidence_fields(
        self, threshold: Optional[float] = None
    ) -> List[tuple]:
        """Get fields below confidence threshold."""
        if threshold is None:
            threshold = CONFIDENCE_THRESHOLDS["standard"]

        low_conf = []
        for section_name, fields in self.sections.items():
            for field_name, field_result in fields.items():
                if field_result.confidence < threshold:
                    low_conf.append((section_name, field_name, field_result))
        return low_conf

    def get_empty_fields(self) -> List[tuple]:
        """Get fields with no value."""
        empty = []
        for section_name, fields in self.sections.items():
            for field_name, field_result in fields.items():
                if field_result.value is None or field_result.value == "":
                    empty.append((section_name, field_name))
        return empty


def aggregate_confidence(result: ExtractionResult) -> float:
    """
    Calculate overall confidence based on critical field confidences.
    Returns the minimum confidence among critical fields.
    """
    critical_confidences = []

    for section_name, fields in result.sections.items():
        for field_name, field_result in fields.items():
            if field_name in CRITICAL_FIELDS:
                if field_result.value is not None and field_result.value != "":
                    critical_confidences.append(field_result.confidence)

    if not critical_confidences:
        return 0.0

    return min(critical_confidences)


def should_flag_for_review(result: ExtractionResult) -> bool:
    """
    Determine if extraction result needs manual review.
    True if any critical field is below threshold or missing.
    """
    critical_threshold = CONFIDENCE_THRESHOLDS["critical"]

    for section_name, fields in result.sections.items():
        for field_name, field_result in fields.items():
            if field_name in CRITICAL_FIELDS:
                # Missing value
                if field_result.value is None or field_result.value == "":
                    return True
                # Low confidence
                if field_result.confidence < critical_threshold:
                    return True

    return False


def merge_results(
    primary: ExtractionResult,
    secondary: Dict[str, FieldResult],
    only_improve: bool = True
) -> ExtractionResult:
    """
    Merge secondary results into primary, optionally only improving low-confidence fields.

    Args:
        primary: The main extraction result
        secondary: Dictionary of field_key -> FieldResult to merge
                   field_key format: "section.field_name"
        only_improve: If True, only replace fields where secondary has higher confidence

    Returns:
        Updated ExtractionResult
    """
    for field_key, new_result in secondary.items():
        parts = field_key.split(".", 1)
        if len(parts) != 2:
            continue

        section_name, field_name = parts

        current = primary.get_field(section_name, field_name)

        should_replace = False
        if current is None:
            should_replace = True
        elif not only_improve:
            should_replace = True
        elif new_result.confidence > current.confidence:
            should_replace = True
        elif current.value in (None, "") and new_result.value not in (None, ""):
            should_replace = True

        if should_replace:
            primary.set_field(section_name, field_name, new_result)

            # Track source
            if new_result.source not in primary.sources_used:
                primary.sources_used.append(new_result.source)

    # Recalculate overall confidence
    primary.overall_confidence = aggregate_confidence(primary)
    primary.needs_review = should_flag_for_review(primary)

    return primary


def create_empty_result() -> ExtractionResult:
    """Create an empty ExtractionResult with all sections initialized."""
    from .field_mappings import URAR_SECTIONS

    sections = {}
    for section_name, field_names in URAR_SECTIONS.items():
        sections[section_name] = {
            field_name: FieldResult.empty()
            for field_name in field_names
        }

    return ExtractionResult(
        sections=sections,
        overall_confidence=0.0,
        needs_review=True,
        extraction_time_ms=0,
        sources_used=[],
    )


class ExtractionTimer:
    """Context manager for timing extraction operations."""

    def __init__(self):
        self.start_time: float = 0
        self.elapsed_ms: int = 0

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, *args):
        self.elapsed_ms = int((time.time() - self.start_time) * 1000)
