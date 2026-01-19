"""
LLM cost tracking for workflow budget management.

Phase 6: Tracks LLM usage and costs across all agent calls,
enabling cost visibility and optimization.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..firestore.client import FirestoreClient

logger = logging.getLogger(__name__)


# =============================================================================
# Cost Configuration
# =============================================================================

# Model costs per 1K tokens (as of 2025)
MODEL_COSTS = {
    # OpenAI models
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-4": {"input": 0.03, "output": 0.06},
    # Azure OpenAI (same pricing as OpenAI)
    "gpt-4o-mini-azure": {"input": 0.00015, "output": 0.0006},
    "gpt-4o-azure": {"input": 0.005, "output": 0.015},
    # GPT-5 family
    "gpt-5-nano": {"input": 0.0001, "output": 0.0003},
    "gpt-5-mini": {"input": 0.0003, "output": 0.001},
    # Default fallback
    "default": {"input": 0.001, "output": 0.003},
}


def get_model_costs(model: str) -> dict[str, float]:
    """
    Get cost per 1K tokens for a model.

    Args:
        model: Model name or deployment name

    Returns:
        Dict with "input" and "output" costs per 1K tokens
    """
    # Normalize model name
    model_lower = model.lower()

    # Check for exact match
    if model_lower in MODEL_COSTS:
        return MODEL_COSTS[model_lower]

    # Check for partial matches
    for known_model, costs in MODEL_COSTS.items():
        if known_model in model_lower or model_lower in known_model:
            return costs

    # Return default
    logger.warning(f"Unknown model '{model}', using default costs")
    return MODEL_COSTS["default"]


# =============================================================================
# Models
# =============================================================================


class LLMUsageRecord(BaseModel):
    """
    A single LLM usage record.

    Captures all relevant information about an LLM call for
    cost tracking and analysis.
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique record ID",
    )
    study_id: str = Field(
        ...,
        description="Study this usage belongs to",
    )
    agent: str = Field(
        ...,
        description="Agent that made the call (e.g., 'room_agent', 'classifier')",
    )
    model: str = Field(
        ...,
        description="Model name or deployment name",
    )
    input_tokens: int = Field(
        ...,
        ge=0,
        description="Number of input tokens",
    )
    output_tokens: int = Field(
        ...,
        ge=0,
        description="Number of output tokens",
    )
    estimated_cost_usd: float = Field(
        ...,
        ge=0.0,
        description="Estimated cost in USD",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the call was made",
    )
    stage: Optional[str] = Field(
        default=None,
        description="Workflow stage (e.g., 'room', 'classification')",
    )
    component_id: Optional[str] = Field(
        default=None,
        description="Component being processed (if applicable)",
    )
    latency_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description="Call latency in milliseconds",
    )

    def to_firestore_dict(self) -> dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        return {
            "id": self.id,
            "study_id": self.study_id,
            "agent": self.agent,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "timestamp": self.timestamp,
            "stage": self.stage,
            "component_id": self.component_id,
            "latency_ms": self.latency_ms,
        }

    @classmethod
    def from_firestore_dict(cls, data: dict[str, Any]) -> "LLMUsageRecord":
        """Create record from Firestore document."""
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
            model=data["model"],
            input_tokens=data["input_tokens"],
            output_tokens=data["output_tokens"],
            estimated_cost_usd=data["estimated_cost_usd"],
            timestamp=timestamp,
            stage=data.get("stage"),
            component_id=data.get("component_id"),
            latency_ms=data.get("latency_ms"),
        )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None,
        }


class StudyCostSummary(BaseModel):
    """
    Aggregated cost summary for a study.

    Provides high-level cost metrics and breakdowns.
    """

    study_id: str = Field(
        ...,
        description="Study ID",
    )
    total_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Total estimated cost in USD",
    )
    total_input_tokens: int = Field(
        default=0,
        ge=0,
        description="Total input tokens across all calls",
    )
    total_output_tokens: int = Field(
        default=0,
        ge=0,
        description="Total output tokens across all calls",
    )
    total_calls: int = Field(
        default=0,
        ge=0,
        description="Total number of LLM calls",
    )
    calls_by_agent: dict[str, int] = Field(
        default_factory=dict,
        description="Number of calls per agent",
    )
    cost_by_agent: dict[str, float] = Field(
        default_factory=dict,
        description="Cost breakdown by agent",
    )
    cost_by_stage: dict[str, float] = Field(
        default_factory=dict,
        description="Cost breakdown by workflow stage",
    )
    avg_latency_ms: Optional[float] = Field(
        default=None,
        description="Average call latency in milliseconds",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this summary was last updated",
    )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None,
        }


# =============================================================================
# Cost Tracker
# =============================================================================


class CostTracker:
    """
    Tracks LLM usage and costs across workflow execution.

    Stores usage records in Firestore and provides cost summaries.

    Usage:
        tracker = CostTracker()

        # Record usage after LLM call
        await tracker.record_usage(
            study_id="study_123",
            agent="room_agent",
            model="gpt-4o-mini",
            input_tokens=1500,
            output_tokens=500,
            stage="room",
            latency_ms=1200,
        )

        # Get cost summary
        summary = await tracker.get_study_summary("study_123")
    """

    COLLECTION_NAME = "llm_usage"

    def __init__(self):
        self._client = FirestoreClient()

    @property
    def _collection(self):
        """Get the LLM usage collection reference."""
        return self._client.db.collection(self.COLLECTION_NAME)

    async def record_usage(
        self,
        study_id: str,
        agent: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        stage: Optional[str] = None,
        component_id: Optional[str] = None,
        latency_ms: Optional[int] = None,
    ) -> LLMUsageRecord:
        """
        Record an LLM usage event.

        Calculates estimated cost and stores the record.

        Args:
            study_id: Study ID
            agent: Agent that made the call
            model: Model name or deployment name
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            stage: Workflow stage (optional)
            component_id: Component ID (optional)
            latency_ms: Call latency in milliseconds (optional)

        Returns:
            The created usage record
        """
        # Calculate cost
        costs = get_model_costs(model)
        input_cost = (input_tokens / 1000) * costs["input"]
        output_cost = (output_tokens / 1000) * costs["output"]
        total_cost = input_cost + output_cost

        # Create record
        record = LLMUsageRecord(
            study_id=study_id,
            agent=agent,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=round(total_cost, 6),
            stage=stage,
            component_id=component_id,
            latency_ms=latency_ms,
        )

        # Store in Firestore
        self._collection.document(record.id).set(record.to_firestore_dict())

        logger.debug(
            f"Recorded LLM usage: {agent} on {model}, "
            f"{input_tokens}+{output_tokens} tokens, ${total_cost:.6f}"
        )

        return record

    async def get_study_records(
        self,
        study_id: str,
        limit: int = 100,
    ) -> list[LLMUsageRecord]:
        """
        Get usage records for a study.

        Args:
            study_id: Study ID
            limit: Maximum records to return

        Returns:
            List of usage records, newest first
        """
        query = (
            self._collection
            .where("study_id", "==", study_id)
            .order_by("timestamp", direction="DESCENDING")
            .limit(limit)
        )

        records = []
        for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            records.append(LLMUsageRecord.from_firestore_dict(data))

        return records

    async def get_study_summary(self, study_id: str) -> StudyCostSummary:
        """
        Get aggregated cost summary for a study.

        Args:
            study_id: Study ID

        Returns:
            StudyCostSummary with totals and breakdowns
        """
        # Get all records for study
        records = await self.get_study_records(study_id, limit=1000)

        if not records:
            return StudyCostSummary(study_id=study_id)

        # Aggregate
        total_cost = 0.0
        total_input = 0
        total_output = 0
        calls_by_agent: dict[str, int] = {}
        cost_by_agent: dict[str, float] = {}
        cost_by_stage: dict[str, float] = {}
        latencies = []

        for record in records:
            total_cost += record.estimated_cost_usd
            total_input += record.input_tokens
            total_output += record.output_tokens

            # By agent
            calls_by_agent[record.agent] = calls_by_agent.get(record.agent, 0) + 1
            cost_by_agent[record.agent] = (
                cost_by_agent.get(record.agent, 0.0) + record.estimated_cost_usd
            )

            # By stage
            if record.stage:
                cost_by_stage[record.stage] = (
                    cost_by_stage.get(record.stage, 0.0) + record.estimated_cost_usd
                )

            # Latency
            if record.latency_ms is not None:
                latencies.append(record.latency_ms)

        # Calculate average latency
        avg_latency = sum(latencies) / len(latencies) if latencies else None

        return StudyCostSummary(
            study_id=study_id,
            total_cost_usd=round(total_cost, 6),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_calls=len(records),
            calls_by_agent=calls_by_agent,
            cost_by_agent={k: round(v, 6) for k, v in cost_by_agent.items()},
            cost_by_stage={k: round(v, 6) for k, v in cost_by_stage.items()},
            avg_latency_ms=round(avg_latency, 1) if avg_latency else None,
        )

    async def get_total_cost(self, study_id: str) -> float:
        """
        Quick method to get just the total cost for a study.

        Args:
            study_id: Study ID

        Returns:
            Total estimated cost in USD
        """
        summary = await self.get_study_summary(study_id)
        return summary.total_cost_usd


# Global instance for convenience
_cost_tracker: Optional[CostTracker] = None


def get_cost_tracker() -> CostTracker:
    """Get the global cost tracker instance."""
    global _cost_tracker
    if _cost_tracker is None:
        _cost_tracker = CostTracker()
    return _cost_tracker
