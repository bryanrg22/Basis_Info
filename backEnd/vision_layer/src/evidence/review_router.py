"""Review routing for low-confidence vision artifacts.

Routes artifacts that need engineer review based on:
1. Low confidence scores
2. Ungrounded VLM claims
3. Consistency check failures
4. Component-specific thresholds
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .artifact_store import (
    get_artifacts_needing_review,
    get_vision_artifacts,
    update_artifact,
)

logger = logging.getLogger(__name__)


@dataclass
class ReviewThresholds:
    """Configurable thresholds for review routing."""

    # Global thresholds
    min_confidence: float = 0.5
    require_grounding: bool = True

    # Component-specific thresholds (lower = more review)
    component_thresholds: Dict[str, float] = field(default_factory=lambda: {
        "cabinet": 0.6,
        "appliance": 0.5,
        "flooring": 0.7,
        "lighting": 0.5,
        "hvac": 0.6,
        "plumbing": 0.5,
        "electrical": 0.6,
        "window": 0.6,
        "door": 0.6,
    })

    # High-value components (always reviewed if confidence < high threshold)
    high_value_components: List[str] = field(default_factory=lambda: [
        "hvac",
        "electrical",
        "plumbing",
    ])
    high_value_threshold: float = 0.8


@dataclass
class ReviewItem:
    """An artifact queued for review with priority scoring."""

    artifact: Dict[str, Any]
    priority: float  # Higher = more urgent
    review_reasons: List[str]
    queued_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact": self.artifact,
            "priority": self.priority,
            "review_reasons": self.review_reasons,
            "queued_at": self.queued_at.isoformat(),
        }


def should_review(
    artifact: Dict[str, Any],
    thresholds: Optional[ReviewThresholds] = None,
) -> tuple[bool, List[str]]:
    """Determine if an artifact needs engineer review.

    Args:
        artifact: Vision artifact dictionary.
        thresholds: Optional custom thresholds.

    Returns:
        Tuple of (needs_review, list of reasons).
    """
    if thresholds is None:
        thresholds = ReviewThresholds()

    reasons = []
    confidence = artifact.get("confidence", 0.0)
    grounded = artifact.get("grounded", False)
    component_type = artifact.get("classification", {}).get("component_type", "").lower()

    # Check grounding requirement
    if thresholds.require_grounding and not grounded:
        reasons.append("ungrounded_claim")

    # Check global confidence threshold
    if confidence < thresholds.min_confidence:
        reasons.append(f"low_confidence_{confidence:.2f}")

    # Check component-specific threshold
    component_threshold = thresholds.component_thresholds.get(
        component_type,
        thresholds.min_confidence,
    )
    if confidence < component_threshold:
        reasons.append(f"below_{component_type}_threshold_{component_threshold}")

    # Check high-value component threshold
    if component_type in thresholds.high_value_components:
        if confidence < thresholds.high_value_threshold:
            reasons.append(f"high_value_component_low_confidence")

    # Check if already flagged
    if artifact.get("needs_review", False) and not reasons:
        reasons.append("previously_flagged")

    return len(reasons) > 0, reasons


def calculate_priority(
    artifact: Dict[str, Any],
    reasons: List[str],
) -> float:
    """Calculate review priority score (0-1, higher = more urgent).

    Factors:
    - Lower confidence = higher priority
    - Ungrounded claims = higher priority
    - High-value components = higher priority
    - Multiple issues = higher priority
    """
    priority = 0.0
    confidence = artifact.get("confidence", 0.0)
    component_type = artifact.get("classification", {}).get("component_type", "").lower()

    # Base priority from confidence (inverse)
    priority += (1.0 - confidence) * 0.4

    # Bonus for ungrounded claims
    if "ungrounded_claim" in reasons:
        priority += 0.2

    # Bonus for high-value components
    high_value = ["hvac", "electrical", "plumbing"]
    if component_type in high_value:
        priority += 0.2

    # Bonus for multiple issues
    if len(reasons) > 1:
        priority += 0.1 * min(len(reasons) - 1, 3)

    return min(priority, 1.0)


def route_artifact(
    artifact: Dict[str, Any],
    thresholds: Optional[ReviewThresholds] = None,
) -> Optional[ReviewItem]:
    """Route a single artifact for review if needed.

    Args:
        artifact: Vision artifact dictionary.
        thresholds: Optional custom thresholds.

    Returns:
        ReviewItem if artifact needs review, None otherwise.
    """
    needs_review, reasons = should_review(artifact, thresholds)

    if not needs_review:
        return None

    priority = calculate_priority(artifact, reasons)

    return ReviewItem(
        artifact=artifact,
        priority=priority,
        review_reasons=reasons,
    )


def route_artifacts(
    artifacts: List[Dict[str, Any]],
    thresholds: Optional[ReviewThresholds] = None,
) -> List[ReviewItem]:
    """Route multiple artifacts for review.

    Args:
        artifacts: List of vision artifact dictionaries.
        thresholds: Optional custom thresholds.

    Returns:
        List of ReviewItems sorted by priority (highest first).
    """
    review_items = []

    for artifact in artifacts:
        item = route_artifact(artifact, thresholds)
        if item:
            review_items.append(item)

    # Sort by priority (highest first)
    review_items.sort(key=lambda x: x.priority, reverse=True)

    return review_items


def get_review_queue(
    study_id: str,
    thresholds: Optional[ReviewThresholds] = None,
    include_resolved: bool = False,
) -> List[ReviewItem]:
    """Get the review queue for a study.

    Args:
        study_id: Study document ID.
        thresholds: Optional custom thresholds.
        include_resolved: Include artifacts that were already reviewed.

    Returns:
        List of ReviewItems sorted by priority.
    """
    if include_resolved:
        artifacts = get_vision_artifacts(study_id)
    else:
        artifacts = get_artifacts_needing_review(study_id)

    # Re-route to get current priority and reasons
    return route_artifacts(artifacts, thresholds)


def mark_reviewed(
    study_id: str,
    artifact_id: str,
    reviewed_by: str,
    approved: bool = True,
    notes: Optional[str] = None,
) -> bool:
    """Mark an artifact as reviewed.

    Args:
        study_id: Study document ID.
        artifact_id: Artifact to mark reviewed.
        reviewed_by: Engineer ID or email.
        approved: Whether the artifact was approved as-is.
        notes: Optional review notes.

    Returns:
        True if artifact was updated.
    """
    updates = {
        "needs_review": False,
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime.utcnow().isoformat(),
        "review_approved": approved,
    }

    if notes:
        updates["review_notes"] = notes

    return update_artifact(study_id, artifact_id, updates)


def get_review_stats(study_id: str) -> Dict[str, Any]:
    """Get review statistics for a study.

    Returns:
        Dictionary with review queue stats.
    """
    all_artifacts = get_vision_artifacts(study_id)
    pending_review = get_artifacts_needing_review(study_id)

    # Count reviewed vs pending
    reviewed = [a for a in all_artifacts if a.get("reviewed_by")]
    approved = [a for a in reviewed if a.get("review_approved", True)]

    # Get queue with priorities
    queue = route_artifacts(pending_review)

    # Count by priority level
    high_priority = len([q for q in queue if q.priority >= 0.7])
    medium_priority = len([q for q in queue if 0.4 <= q.priority < 0.7])
    low_priority = len([q for q in queue if q.priority < 0.4])

    # Count by reason
    reason_counts: Dict[str, int] = {}
    for item in queue:
        for reason in item.review_reasons:
            # Normalize reasons (remove numeric suffixes)
            base_reason = reason.split("_")[0] if "_" in reason else reason
            reason_counts[base_reason] = reason_counts.get(base_reason, 0) + 1

    return {
        "total_artifacts": len(all_artifacts),
        "pending_review": len(pending_review),
        "reviewed": len(reviewed),
        "approved": len(approved),
        "rejected": len(reviewed) - len(approved),
        "by_priority": {
            "high": high_priority,
            "medium": medium_priority,
            "low": low_priority,
        },
        "by_reason": reason_counts,
    }
