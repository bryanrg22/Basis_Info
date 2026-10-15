"""Overall confidence and the review gate (S3_SPEC section 4).

Migrated from ``evidence_layer/src/tiered_extraction/confidence.py``. The gate:

    needs_review = any critical field is missing
                or any critical field's confidence < settings.extraction_review_threshold
                or any validation issue

Overall confidence is the minimum confidence over the critical fields (legacy
``aggregate_confidence``), and 0 when any critical field is missing: the legacy version
averaged only the fields it found, so a document missing its GLA could still score 0.99.
The 0.90 default threshold comes from the legacy ``CONFIDENCE_THRESHOLDS["critical"]``;
Microsoft publishes no calibration for Azure DI confidences, so the real-data eval picks
the final value (BUILD_PLAN 13.2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from basis.extraction.field_mappings import CRITICAL_FIELDS
from basis.extraction.results import ExtractedField, ValidationIssue


@dataclass(frozen=True, slots=True)
class Gate:
    overall_confidence: float
    needs_review: bool
    reasons: tuple[str, ...]


def aggregate_confidence(fields: Mapping[str, ExtractedField]) -> float:
    """Minimum confidence over the critical fields; 0 when one is missing."""
    values: list[float] = []
    for key in CRITICAL_FIELDS:
        item = fields.get(key)
        if item is None or item.is_empty:
            return 0.0
        values.append(item.confidence)
    return min(values) if values else 0.0


def review_gate(
    fields: Mapping[str, ExtractedField],
    issues: Sequence[ValidationIssue],
    *,
    threshold: float,
) -> Gate:
    reasons: list[str] = []
    for key in CRITICAL_FIELDS:
        item = fields.get(key)
        if item is None or item.is_empty:
            reasons.append(f"missing:{key}")
        elif item.confidence < threshold:
            reasons.append(f"low_confidence:{key}={item.confidence:.2f}<{threshold:.2f}")
    for issue in issues:
        reason = f"{issue.rule}:{','.join(issue.keys)}" if issue.keys else issue.rule
        if reason not in reasons and not (
            issue.rule == "missing_critical" and f"missing:{issue.keys[0]}" in reasons
        ):
            reasons.append(reason)
    return Gate(
        overall_confidence=aggregate_confidence(fields),
        needs_review=bool(reasons),
        reasons=tuple(reasons),
    )
