"""
LangGraph workflow for Basis cost segregation study.

Orchestrates stage-gated agents with engineer review checkpoints.
Supports both in-memory (development) and Firestore (production) checkpointing.
"""

import os
from typing import Optional, Union

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from ..firestore.checkpointer import FirestoreCheckpointer
from .state import WorkflowState, create_initial_state
from .nodes import (
    load_study_node,
    analyze_rooms_node,
    analyze_objects_node,
    analyze_takeoffs_node,
    classify_assets_node,
    estimate_costs_node,
    verify_assets_node,
    complete_workflow_node,
    error_handler_node,
)
from .edges import (
    route_after_rooms,
    route_after_takeoffs,
    route_after_classification,
    route_after_costs,
    route_after_verification,
    check_for_errors,
)


def create_workflow(
    checkpointer: Optional[Union[MemorySaver, FirestoreCheckpointer]] = None,
):
    """
    Build the LangGraph workflow.

    Args:
        checkpointer: Optional checkpointer for state persistence.
            If None, auto-detects based on environment:
            - Production (GCS_BUCKET_NAME set): Uses FirestoreCheckpointer
            - Development: Uses in-memory MemorySaver

    Returns:
        Compiled workflow graph
    """
    # Create the graph with our state type
    workflow = StateGraph(WorkflowState)

    # Add nodes
    workflow.add_node("load_study", load_study_node)
    workflow.add_node("analyze_rooms", analyze_rooms_node)
    workflow.add_node("analyze_objects", analyze_objects_node)
    workflow.add_node("analyze_takeoffs", analyze_takeoffs_node)
    workflow.add_node("classify_assets", classify_assets_node)
    workflow.add_node("estimate_costs", estimate_costs_node)
    workflow.add_node("verify_assets", verify_assets_node)
    workflow.add_node("complete", complete_workflow_node)
    workflow.add_node("error_handler", error_handler_node)

    # Set entry point
    workflow.set_entry_point("load_study")

    # Add edges from load_study
    workflow.add_conditional_edges(
        "load_study",
        check_for_errors,
        {
            "error": "error_handler",
            "continue": "analyze_rooms",
        },
    )

    # Add edges from analyze_rooms
    workflow.add_conditional_edges(
        "analyze_rooms",
        route_after_rooms,
        {
            "wait_for_review": END,  # Pause for engineer review
            "analyze_objects": "analyze_objects",
        },
    )

    # Add edges from analyze_objects
    workflow.add_edge("analyze_objects", "analyze_takeoffs")

    # Add edges from analyze_takeoffs
    workflow.add_conditional_edges(
        "analyze_takeoffs",
        route_after_takeoffs,
        {
            "wait_for_review": END,  # Pause for engineer review
            "classify_assets": "classify_assets",
        },
    )

    # Add edges from classify_assets
    workflow.add_conditional_edges(
        "classify_assets",
        route_after_classification,
        {
            "wait_for_review": END,  # Pause for engineer review
            "estimate_costs": "estimate_costs",
        },
    )

    # Add edges from estimate_costs
    workflow.add_conditional_edges(
        "estimate_costs",
        route_after_costs,
        {
            "wait_for_review": END,  # Pause for engineer review
            "verify_assets": "verify_assets",
        },
    )

    # Add edges from verify_assets
    workflow.add_conditional_edges(
        "verify_assets",
        route_after_verification,
        {
            "wait_for_review": END,  # Pause for engineer approval
            "complete": "complete",
        },
    )

    # Complete goes to END
    workflow.add_edge("complete", END)

    # Error handler goes to END
    workflow.add_edge("error_handler", END)

    # Select checkpointer based on environment if not provided
    if checkpointer is None:
        if os.getenv("GCS_BUCKET_NAME"):
            # Production: use Firestore for persistence across restarts
            checkpointer = FirestoreCheckpointer()
        else:
            # Development: use in-memory checkpointing
            checkpointer = MemorySaver()

    return workflow.compile(checkpointer=checkpointer)


async def run_workflow(
    study_id: str,
    reference_doc_ids: Optional[list[str]] = None,
    study_doc_ids: Optional[list[str]] = None,
    resume_from: Optional[str] = None,
) -> WorkflowState:
    """
    Run the workflow for a study.

    Args:
        study_id: Study document ID
        reference_doc_ids: Available IRS/RSMeans document IDs
        study_doc_ids: Available study document IDs
        resume_from: Optional thread ID to resume from

    Returns:
        Final workflow state
    """
    # Create workflow
    app = create_workflow()

    # Create initial state
    initial_state = create_initial_state(
        study_id=study_id,
        reference_doc_ids=reference_doc_ids,
        study_doc_ids=study_doc_ids,
    )

    # Run configuration
    config = {"configurable": {"thread_id": resume_from or study_id}}

    # Execute workflow
    final_state = await app.ainvoke(initial_state, config)

    return final_state


async def resume_workflow(
    study_id: str,
    engineer_approved: bool = True,
    corrections: Optional[list[dict]] = None,
) -> WorkflowState:
    """
    Resume workflow after engineer review.

    Args:
        study_id: Study document ID
        engineer_approved: Whether engineer approved the current stage
        corrections: Optional list of corrections made by engineer

    Returns:
        Updated workflow state
    """
    # Create workflow with same thread ID
    app = create_workflow()

    # Get current state and update with approval
    config = {"configurable": {"thread_id": study_id}}

    # Update state with engineer input
    update = {
        "engineer_approved": engineer_approved,
        "engineer_corrections": corrections or [],
        "needs_review": False,  # Clear review flag
    }

    # Resume execution
    final_state = await app.ainvoke(update, config)

    return final_state


def run_workflow_sync(
    study_id: str,
    reference_doc_ids: Optional[list[str]] = None,
    study_doc_ids: Optional[list[str]] = None,
) -> WorkflowState:
    """
    Synchronous wrapper for run_workflow.
    """
    import asyncio
    return asyncio.run(
        run_workflow(study_id, reference_doc_ids, study_doc_ids)
    )
