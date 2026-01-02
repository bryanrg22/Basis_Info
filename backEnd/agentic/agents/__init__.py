"""
Basis Stage Agents.

Each agent handles a specific workflow stage with evidence-backed outputs.
"""

from .base_agent import (
    BaseStageAgent,
    AgentOutput,
    Citation,
    StageContext,
)
from .asset_agent import (
    AssetClassificationAgent,
    AssetClassification,
    ComponentInput,
    classify_component,
    classify_components_batch,
)
from .room_agent import (
    RoomContextAgent,
    RoomContext,
    RoomInput,
    enrich_room_context,
)
from .object_agent import (
    ObjectContextAgent,
    ObjectContext,
    ObjectInput,
    enrich_object_context,
    enrich_objects_batch,
)
from .takeoff_agent import (
    TakeoffAgent,
    TakeoffResult,
    TakeoffInput,
    calculate_takeoff,
    calculate_takeoffs_batch,
)
from .cost_agent import (
    CostEstimationAgent,
    CostEstimate,
    CostInput,
    estimate_cost,
    estimate_costs_batch,
    aggregate_costs,
)

__all__ = [
    # Base
    "BaseStageAgent",
    "AgentOutput",
    "Citation",
    "StageContext",
    # Asset Classification
    "AssetClassificationAgent",
    "AssetClassification",
    "ComponentInput",
    "classify_component",
    "classify_components_batch",
    # Room Context
    "RoomContextAgent",
    "RoomContext",
    "RoomInput",
    "enrich_room_context",
    # Object Context
    "ObjectContextAgent",
    "ObjectContext",
    "ObjectInput",
    "enrich_object_context",
    "enrich_objects_batch",
    # Takeoff
    "TakeoffAgent",
    "TakeoffResult",
    "TakeoffInput",
    "calculate_takeoff",
    "calculate_takeoffs_batch",
    # Cost Estimation
    "CostEstimationAgent",
    "CostEstimate",
    "CostInput",
    "estimate_cost",
    "estimate_costs_batch",
    "aggregate_costs",
]
