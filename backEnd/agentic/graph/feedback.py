"""
Bidirectional feedback system for workflow stages.

Allows downstream stages to send feedback to upstream stages when
issues are detected, enabling iterative refinement.

Phase 5: Workflow Reliability Implementation
"""

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Feedback Types
# =============================================================================


class FeedbackType(str, Enum):
    """Types of feedback that can be sent between stages."""

    # Cost stage → Classification
    RSMEANS_NOT_FOUND = "rsmeans_not_found"  # No matching RSMeans line item
    COST_OUTLIER = "cost_outlier"  # Cost outside expected range

    # Takeoff stage → Classification
    UNIT_MISMATCH = "unit_mismatch"  # Takeoff unit doesn't match classification

    # Verification stage → Classification/Takeoff
    VERIFICATION_FAILED = "verification_failed"  # Self-verification found issues
    CLASSIFICATION_SUSPECT = "classification_suspect"  # Classification may be wrong

    # Cross-validation → Any stage
    VALIDATION_WARNING = "validation_warning"  # Cross-validation warning
    VALIDATION_ERROR = "validation_error"  # Cross-validation error

    # Engineer → Any stage
    ENGINEER_CORRECTION = "engineer_correction"  # Manual correction by engineer
    ENGINEER_OVERRIDE = "engineer_override"  # Engineer overrode AI decision

    # System
    STALE_DATA = "stale_data"  # Data marked as stale, needs recalculation


class SuggestedAction(str, Enum):
    """Suggested actions for handling feedback."""

    RECLASSIFY = "reclassify"  # Re-run classification
    RECALCULATE_TAKEOFF = "recalculate_takeoff"  # Re-run takeoff
    RECALCULATE_COST = "recalculate_cost"  # Re-run cost estimation
    FLAG_FOR_REVIEW = "flag_for_review"  # Mark for engineer review
    NO_ACTION = "no_action"  # Informational only
    CASCADE = "cascade"  # Propagate to dependent stages


# =============================================================================
# Feedback Models
# =============================================================================


class FeedbackEvent(BaseModel):
    """
    A feedback event from one stage to another.

    Represents a signal that something may need attention or correction
    in an upstream stage.
    """

    feedback_type: FeedbackType = Field(
        ..., description="Type of feedback being sent"
    )
    source_stage: str = Field(
        ..., description="Stage that generated the feedback (e.g., 'cost', 'takeoff')"
    )
    target_stage: str = Field(
        ..., description="Stage that should receive the feedback (e.g., 'classification')"
    )
    component_id: str = Field(
        ..., description="ID of the component this feedback relates to"
    )
    study_id: str = Field(
        ..., description="Study this feedback belongs to"
    )
    message: str = Field(
        ..., description="Human-readable description of the issue"
    )
    suggested_action: Optional[SuggestedAction] = Field(
        default=None, description="Recommended action to take"
    )
    details: dict[str, Any] = Field(
        default_factory=dict, description="Additional context"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the feedback was created",
    )
    processed: bool = Field(
        default=False, description="Whether this feedback has been processed"
    )
    processed_at: Optional[datetime] = Field(
        default=None, description="When the feedback was processed"
    )


class FeedbackHistory(BaseModel):
    """Collection of feedback events for a study/component."""

    study_id: str
    component_id: str
    events: list[FeedbackEvent] = Field(default_factory=list)

    def add_event(self, event: FeedbackEvent) -> None:
        """Add a feedback event."""
        self.events.append(event)

    def get_unprocessed(self) -> list[FeedbackEvent]:
        """Get all unprocessed feedback events."""
        return [e for e in self.events if not e.processed]


# =============================================================================
# Feedback Processor
# =============================================================================


class FeedbackProcessor:
    """
    Processes feedback events and triggers appropriate actions.

    Coordinates between stages to handle feedback from downstream
    stages that affects upstream data.
    """

    def __init__(self):
        from ..firestore.client import FirestoreClient
        self._client = FirestoreClient()

    async def process_feedback(self, event: FeedbackEvent) -> dict[str, Any]:
        """
        Process a single feedback event.

        Routes the feedback to the appropriate handler based on type.

        Args:
            event: The feedback event to process

        Returns:
            Result of processing the feedback
        """
        logger.info(
            f"Processing feedback: {event.feedback_type.value} "
            f"from {event.source_stage} → {event.target_stage} "
            f"for component {event.component_id}"
        )

        result = {
            "feedback_type": event.feedback_type.value,
            "component_id": event.component_id,
            "action_taken": None,
            "jobs_enqueued": [],
        }

        # Route to appropriate handler
        if event.feedback_type == FeedbackType.RSMEANS_NOT_FOUND:
            result = await self._handle_rsmeans_not_found(event)

        elif event.feedback_type == FeedbackType.COST_OUTLIER:
            result = await self._handle_cost_outlier(event)

        elif event.feedback_type == FeedbackType.ENGINEER_CORRECTION:
            result = await self._handle_engineer_correction(event)

        elif event.feedback_type == FeedbackType.VERIFICATION_FAILED:
            result = await self._handle_verification_failed(event)

        elif event.feedback_type == FeedbackType.CLASSIFICATION_SUSPECT:
            result = await self._handle_classification_suspect(event)

        elif event.feedback_type in (FeedbackType.VALIDATION_WARNING, FeedbackType.VALIDATION_ERROR):
            result = await self._handle_validation_issue(event)

        elif event.feedback_type == FeedbackType.STALE_DATA:
            result = await self._handle_stale_data(event)

        else:
            # Default: flag for review
            result = await self._flag_for_review(event)

        # Mark as processed
        event.processed = True
        event.processed_at = datetime.now(timezone.utc)

        # Store feedback event in history
        await self._store_feedback(event)

        return result

    async def _handle_rsmeans_not_found(self, event: FeedbackEvent) -> dict[str, Any]:
        """
        Handle feedback when RSMeans line item not found.

        Flags the classification for review, as the component description
        may need adjustment for better RSMeans matching.
        """
        # Update component to flag for review
        study = self._client.get_study(event.study_id)
        if study:
            objects = study.get("objects", [])
            for obj in objects:
                if obj.get("id") == event.component_id:
                    obj["needs_review"] = True
                    existing_reason = obj.get("review_reason", "")
                    if "RSMeans" not in (existing_reason or ""):
                        obj["review_reason"] = (
                            f"{existing_reason}; {event.message}"
                            if existing_reason else event.message
                        )
                    break
            self._client.update_study(event.study_id, {"objects": objects})

        return {
            "feedback_type": event.feedback_type.value,
            "component_id": event.component_id,
            "action_taken": "flagged_for_review",
            "jobs_enqueued": [],
        }

    async def _handle_cost_outlier(self, event: FeedbackEvent) -> dict[str, Any]:
        """
        Handle feedback when cost is outside expected range.

        Flags for review but doesn't automatically reclassify.
        """
        return await self._flag_for_review(event)

    async def _handle_engineer_correction(self, event: FeedbackEvent) -> dict[str, Any]:
        """
        Handle engineer correction feedback.

        Cascades the correction to dependent stages via the job queue.
        """
        from .corrections import CorrectionCascade
        from ..firestore.job_queue import JobQueue

        cascade = CorrectionCascade()
        job_queue = JobQueue()

        result = await cascade.apply_correction(
            study_id=event.study_id,
            correction_type=event.details.get("correction_type", "unknown"),
            component_id=event.component_id,
            new_value=event.details.get("new_value"),
            job_queue=job_queue,
        )

        return {
            "feedback_type": event.feedback_type.value,
            "component_id": event.component_id,
            "action_taken": "correction_cascaded",
            "jobs_enqueued": result.get("jobs_enqueued", []),
        }

    async def _handle_verification_failed(self, event: FeedbackEvent) -> dict[str, Any]:
        """Handle verification failure."""
        return await self._flag_for_review(event)

    async def _handle_classification_suspect(self, event: FeedbackEvent) -> dict[str, Any]:
        """Handle suspect classification."""
        return await self._flag_for_review(event)

    async def _handle_validation_issue(self, event: FeedbackEvent) -> dict[str, Any]:
        """Handle cross-validation warnings/errors."""
        return await self._flag_for_review(event)

    async def _handle_stale_data(self, event: FeedbackEvent) -> dict[str, Any]:
        """
        Handle stale data by enqueuing recalculation jobs.
        """
        from ..firestore.job_queue import JobQueue

        job_queue = JobQueue()
        jobs_enqueued = []

        # Determine what needs recalculation
        stale_stages = event.details.get("stale_stages", [])

        if "takeoff" in stale_stages:
            # No direct recalculate takeoff job type yet, would need full process_assets
            pass

        if "cost" in stale_stages:
            job_id = await job_queue.enqueue(
                job_type="recalculate_costs",
                study_id=event.study_id,
                input_data={
                    "component_ids": [event.component_id],
                },
                timeout_seconds=120,
                priority=4,
            )
            jobs_enqueued.append(job_id)

        return {
            "feedback_type": event.feedback_type.value,
            "component_id": event.component_id,
            "action_taken": "recalculation_enqueued",
            "jobs_enqueued": jobs_enqueued,
        }

    async def _flag_for_review(self, event: FeedbackEvent) -> dict[str, Any]:
        """
        Default handler: flag the component for engineer review.
        """
        study = self._client.get_study(event.study_id)
        if study:
            objects = study.get("objects", [])
            for obj in objects:
                if obj.get("id") == event.component_id:
                    obj["needs_review"] = True
                    existing_reason = obj.get("review_reason", "")
                    if event.message not in (existing_reason or ""):
                        obj["review_reason"] = (
                            f"{existing_reason}; {event.message}"
                            if existing_reason else event.message
                        )
                    break
            self._client.update_study(event.study_id, {"objects": objects})

        return {
            "feedback_type": event.feedback_type.value,
            "component_id": event.component_id,
            "action_taken": "flagged_for_review",
            "jobs_enqueued": [],
        }

    async def _store_feedback(self, event: FeedbackEvent) -> None:
        """Store feedback event in Firestore for audit trail."""
        # Store in a subcollection under the study
        doc_ref = (
            self._client.db.collection("studies")
            .document(event.study_id)
            .collection("feedback_history")
            .document()
        )

        doc_ref.set({
            "feedback_type": event.feedback_type.value,
            "source_stage": event.source_stage,
            "target_stage": event.target_stage,
            "component_id": event.component_id,
            "message": event.message,
            "suggested_action": event.suggested_action.value if event.suggested_action else None,
            "details": event.details,
            "created_at": event.created_at,
            "processed": event.processed,
            "processed_at": event.processed_at,
        })


# =============================================================================
# Convenience Functions
# =============================================================================


async def emit_feedback(
    feedback_type: FeedbackType,
    source_stage: str,
    target_stage: str,
    component_id: str,
    study_id: str,
    message: str,
    suggested_action: Optional[SuggestedAction] = None,
    details: Optional[dict[str, Any]] = None,
    process_immediately: bool = True,
) -> FeedbackEvent:
    """
    Emit a feedback event.

    Args:
        feedback_type: Type of feedback
        source_stage: Stage generating the feedback
        target_stage: Stage that should receive the feedback
        component_id: Related component ID
        study_id: Study ID
        message: Human-readable message
        suggested_action: Recommended action
        details: Additional context
        process_immediately: Whether to process the feedback right away

    Returns:
        The created feedback event
    """
    event = FeedbackEvent(
        feedback_type=feedback_type,
        source_stage=source_stage,
        target_stage=target_stage,
        component_id=component_id,
        study_id=study_id,
        message=message,
        suggested_action=suggested_action,
        details=details or {},
    )

    if process_immediately:
        processor = FeedbackProcessor()
        await processor.process_feedback(event)

    return event
