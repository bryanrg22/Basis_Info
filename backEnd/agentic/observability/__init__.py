"""Observability and tracing for Basis agentic layer.

Phase 6: Added cost tracking, decision logging, and alerts.
"""

from .tracing import (
    configure_langsmith,
    get_tracer,
    BasisTracer,
    traced,
)
from .cost_tracker import (
    CostTracker,
    get_cost_tracker,
    LLMUsageRecord,
    StudyCostSummary,
)
from .decision_log import (
    DecisionLogger,
    get_decision_logger,
    DecisionPoint,
    DecisionSummary,
)
from .alerts import (
    AlertManager,
    get_alert_manager,
    AlertPayload,
    send_alert,
)

__all__ = [
    # Tracing
    "configure_langsmith",
    "get_tracer",
    "BasisTracer",
    "traced",
    # Cost tracking
    "CostTracker",
    "get_cost_tracker",
    "LLMUsageRecord",
    "StudyCostSummary",
    # Decision logging
    "DecisionLogger",
    "get_decision_logger",
    "DecisionPoint",
    "DecisionSummary",
    # Alerts
    "AlertManager",
    "get_alert_manager",
    "AlertPayload",
    "send_alert",
]
