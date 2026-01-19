"""
Agent decision logging for audit trail and debugging.

Phase 6: Logs agent decisions with reasoning, evidence used,
and alternatives considered for IRS audit support.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..firestore.client import FirestoreClient

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================


class DecisionPoint(BaseModel):
    """
    A logged agent decision point.

    Captures what decision was made, why, and what evidence supported it.
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique decision ID",
    )
    study_id: str = Field(
        ...,
        description="Study this decision belongs to",
    )
    agent: str = Field(
        ...,
        description="Agent that made the decision",
    )
    component_id: Optional[str] = Field(
        default=None,
        description="Component being processed",
    )
    component_name: Optional[str] = Field(
        default=None,
        description="Component name for readability",
    )
    decision_type: str = Field(
        ...,
        description="Type of decision (e.g., 'classification', 'tool_selection')",
    )
    decision: Any = Field(
        ...,
        description="The actual decision made",
    )
    alternatives_considered: list[Any] = Field(
        default_factory=list,
        description="Other options that were considered",
    )
    reasoning: str = Field(
        default="",
        description="Explanation of why this decision was made",
    )
    evidence_used: list[str] = Field(
        default_factory=list,
        description="Chunk IDs or references to evidence used",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Confidence in the decision",
    )
    input_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Summary of input data that led to decision",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the decision was made",
    )

    def to_firestore_dict(self) -> dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        return {
            "id": self.id,
            "study_id": self.study_id,
            "agent": self.agent,
            "component_id": self.component_id,
            "component_name": self.component_name,
            "decision_type": self.decision_type,
            "decision": self.decision,
            "alternatives_considered": self.alternatives_considered,
            "reasoning": self.reasoning,
            "evidence_used": self.evidence_used,
            "confidence": self.confidence,
            "input_summary": self.input_summary,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_firestore_dict(cls, data: dict[str, Any]) -> "DecisionPoint":
        """Create from Firestore document."""
        timestamp = data.get("timestamp")
        if timestamp and hasattr(timestamp, "seconds"):
            timestamp = datetime.fromtimestamp(timestamp.seconds, tz=timezone.utc)
        elif isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        else:
            timestamp = datetime.now(timezone.utc)

        return cls(
            id=data.get("id", str(uuid.uuid4())),
            study_id=data["study_id"],
            agent=data["agent"],
            component_id=data.get("component_id"),
            component_name=data.get("component_name"),
            decision_type=data["decision_type"],
            decision=data["decision"],
            alternatives_considered=data.get("alternatives_considered", []),
            reasoning=data.get("reasoning", ""),
            evidence_used=data.get("evidence_used", []),
            confidence=data.get("confidence", 0.5),
            input_summary=data.get("input_summary", {}),
            timestamp=timestamp,
        )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None,
        }


class DecisionSummary(BaseModel):
    """
    Summary of decisions for a study or component.
    """

    study_id: str
    total_decisions: int = 0
    decisions_by_agent: dict[str, int] = Field(default_factory=dict)
    decisions_by_type: dict[str, int] = Field(default_factory=dict)
    avg_confidence: float = 0.0
    low_confidence_count: int = 0  # Decisions with confidence < 0.5


# =============================================================================
# Decision Logger
# =============================================================================


class DecisionLogger:
    """
    Logs agent decisions for audit trail and debugging.

    Stores decisions in Firestore for later analysis and IRS audit support.

    Usage:
        logger = DecisionLogger()

        # Log a classification decision
        await logger.log_decision(
            study_id="study_123",
            agent="asset_classifier",
            decision_type="classification",
            decision={"macrs_class": "5-year", "life": 5},
            reasoning="HVAC equipment falls under 5-year property per Rev Proc 87-56",
            evidence_used=["chunk_123", "chunk_456"],
            confidence=0.85,
            component_id="hvac_unit_1",
            component_name="HVAC Unit",
        )

        # Get decisions for a component
        decisions = await logger.get_decisions_for_component("study_123", "hvac_unit_1")
    """

    COLLECTION_NAME = "decision_log"

    def __init__(self):
        self._client = FirestoreClient()

    @property
    def _collection(self):
        """Get the decision log collection reference."""
        return self._client.db.collection(self.COLLECTION_NAME)

    async def log_decision(
        self,
        study_id: str,
        agent: str,
        decision_type: str,
        decision: Any,
        reasoning: str = "",
        evidence_used: Optional[list[str]] = None,
        confidence: float = 0.5,
        component_id: Optional[str] = None,
        component_name: Optional[str] = None,
        alternatives_considered: Optional[list[Any]] = None,
        input_summary: Optional[dict[str, Any]] = None,
    ) -> DecisionPoint:
        """
        Log an agent decision.

        Args:
            study_id: Study ID
            agent: Agent making the decision
            decision_type: Type of decision
            decision: The actual decision
            reasoning: Why this decision was made
            evidence_used: List of chunk IDs or references
            confidence: Confidence score (0-1)
            component_id: Component ID (optional)
            component_name: Component name (optional)
            alternatives_considered: Other options considered (optional)
            input_summary: Summary of inputs (optional)

        Returns:
            The created DecisionPoint
        """
        decision_point = DecisionPoint(
            study_id=study_id,
            agent=agent,
            component_id=component_id,
            component_name=component_name,
            decision_type=decision_type,
            decision=decision,
            alternatives_considered=alternatives_considered or [],
            reasoning=reasoning,
            evidence_used=evidence_used or [],
            confidence=confidence,
            input_summary=input_summary or {},
        )

        # Store in Firestore
        self._collection.document(decision_point.id).set(
            decision_point.to_firestore_dict()
        )

        logger.debug(
            f"Logged decision: {agent}/{decision_type} for {component_name or component_id}, "
            f"confidence={confidence:.2f}"
        )

        return decision_point

    async def get_decisions_for_component(
        self,
        study_id: str,
        component_id: str,
    ) -> list[DecisionPoint]:
        """
        Get all decisions for a specific component.

        Args:
            study_id: Study ID
            component_id: Component ID

        Returns:
            List of decisions, newest first
        """
        query = (
            self._collection
            .where("study_id", "==", study_id)
            .where("component_id", "==", component_id)
            .order_by("timestamp", direction="DESCENDING")
        )

        decisions = []
        for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            decisions.append(DecisionPoint.from_firestore_dict(data))

        return decisions

    async def get_all_decisions(
        self,
        study_id: str,
        agent_filter: Optional[str] = None,
        decision_type_filter: Optional[str] = None,
        limit: int = 100,
    ) -> list[DecisionPoint]:
        """
        Get all decisions for a study with optional filtering.

        Args:
            study_id: Study ID
            agent_filter: Optional agent name filter
            decision_type_filter: Optional decision type filter
            limit: Maximum decisions to return

        Returns:
            List of decisions, newest first
        """
        query = self._collection.where("study_id", "==", study_id)

        if agent_filter:
            query = query.where("agent", "==", agent_filter)

        if decision_type_filter:
            query = query.where("decision_type", "==", decision_type_filter)

        query = query.order_by("timestamp", direction="DESCENDING").limit(limit)

        decisions = []
        for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            decisions.append(DecisionPoint.from_firestore_dict(data))

        return decisions

    async def get_decision_summary(self, study_id: str) -> DecisionSummary:
        """
        Get summary statistics for a study's decisions.

        Args:
            study_id: Study ID

        Returns:
            DecisionSummary with counts and averages
        """
        decisions = await self.get_all_decisions(study_id, limit=1000)

        if not decisions:
            return DecisionSummary(study_id=study_id)

        # Aggregate
        by_agent: dict[str, int] = {}
        by_type: dict[str, int] = {}
        confidences = []
        low_confidence = 0

        for d in decisions:
            by_agent[d.agent] = by_agent.get(d.agent, 0) + 1
            by_type[d.decision_type] = by_type.get(d.decision_type, 0) + 1
            confidences.append(d.confidence)
            if d.confidence < 0.5:
                low_confidence += 1

        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return DecisionSummary(
            study_id=study_id,
            total_decisions=len(decisions),
            decisions_by_agent=by_agent,
            decisions_by_type=by_type,
            avg_confidence=round(avg_confidence, 3),
            low_confidence_count=low_confidence,
        )


# Global instance
_decision_logger: Optional[DecisionLogger] = None


def get_decision_logger() -> DecisionLogger:
    """Get the global decision logger instance."""
    global _decision_logger
    if _decision_logger is None:
        _decision_logger = DecisionLogger()
    return _decision_logger
