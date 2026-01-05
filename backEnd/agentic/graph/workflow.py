"""
LangGraph workflow for Basis cost segregation study.

PARALLEL STAGE-GATED WORKFLOW with staggered pauses:

                         load_study
                               │
                  ┌────────────┴────────────┐
                  ▼                         ▼
            resource_extraction       analyze_rooms
            (ingest appraisal PDF)    (52 images, 2 workers)
            ~30 seconds               ~2-3 minutes (BACKGROUND)
                  │                         │
                  ▼                         │
            PAUSE #1 ◄──────────────────────┤ (vision continues in background)
            (engineer reviews               │
             appraisal data)                │
                  │                         │
                  └────────────┬────────────┘
                               ▼
                          PAUSE #2
                          (engineer reviews rooms)
                               │
                               ▼
                        process_assets
                               │
                               ▼
                    engineering_takeoff ←── PAUSE #3
                               │
                               ▼
                          completed

Key behavior:
1. resource_extraction finishes fast (~30s) → PAUSE #1 immediately
2. analyze_rooms runs as BACKGROUND TASK while engineer reviews appraisal
3. When engineer approves PAUSE #1 AND analyze_rooms completes → PAUSE #2
4. Engineer reviews room classifications

Matches frontend WorkflowStatus exactly:
uploading_documents → analyzing_rooms → resource_extraction → reviewing_rooms → engineering_takeoff → completed
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
    resource_extraction_node,
    reviewing_rooms_node,
    process_assets_node,
    complete_workflow_node,
    error_handler_node,
)
from .edges import (
    check_for_errors,
    route_after_rooms,
    route_after_resource_extraction,
    route_after_assets,
    route_after_engineering_takeoff,
)


def create_workflow(
    checkpointer: Optional[Union[MemorySaver, FirestoreCheckpointer]] = None,
):
    """
    Build the LangGraph workflow with 3 pause points.

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

    # ==========================================================================
    # Add nodes
    # ==========================================================================
    workflow.add_node("load_study", load_study_node)
    workflow.add_node("analyze_rooms", analyze_rooms_node)
    workflow.add_node("resource_extraction", resource_extraction_node)
    workflow.add_node("reviewing_rooms", reviewing_rooms_node)
    workflow.add_node("process_assets", process_assets_node)
    workflow.add_node("complete", complete_workflow_node)
    workflow.add_node("error_handler", error_handler_node)

    # ==========================================================================
    # Set entry point
    # ==========================================================================
    workflow.set_entry_point("load_study")

    # ==========================================================================
    # Stage 0: Load Study → Resource Extraction (or error)
    # Resource extraction runs first (fast), starts analyze_rooms in background
    # ==========================================================================
    workflow.add_conditional_edges(
        "load_study",
        check_for_errors,
        {
            "error": "error_handler",
            "continue": "resource_extraction",  # Start with resource extraction
        },
    )

    # ==========================================================================
    # Stage 1: Resource Extraction → PAUSE #1 (Engineer reviews appraisal)
    # analyze_rooms runs as background task while engineer reviews
    # ==========================================================================
    workflow.add_conditional_edges(
        "resource_extraction",
        route_after_resource_extraction,
        {
            "wait_for_review": END,  # PAUSE #1: Engineer reviews appraisal data
        },
    )

    # ==========================================================================
    # Stage 2: Analyze Rooms (runs as background, updates rooms_ready)
    # This node is invoked asynchronously by resource_extraction_node
    # When it completes, it sets rooms_ready=True in Firestore
    # ==========================================================================
    workflow.add_conditional_edges(
        "analyze_rooms",
        route_after_rooms,
        {
            "wait_for_review": END,  # Sets rooms_ready, then ENDs
        },
    )

    # ==========================================================================
    # Stage 3: Reviewing Rooms → Process Assets (no pause)
    # (Entered when workflow resumes after engineer approves rooms)
    # ==========================================================================
    workflow.add_edge("reviewing_rooms", "process_assets")

    # ==========================================================================
    # Stage 4: Process Assets → PAUSE at engineering_takeoff
    # ==========================================================================
    workflow.add_conditional_edges(
        "process_assets",
        route_after_assets,
        {
            "wait_for_review": END,  # PAUSE #3: Engineer reviews all asset data
        },
    )

    # ==========================================================================
    # Stage 5: Engineering Takeoff → Complete (when approved)
    # (Entered when workflow resumes after engineer approves)
    # Note: This edge is not directly in the graph - completion happens via resume_workflow
    # ==========================================================================

    # Complete goes to END
    workflow.add_edge("complete", END)

    # Error handler goes to END
    workflow.add_edge("error_handler", END)

    # ==========================================================================
    # Select checkpointer
    # ==========================================================================
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

    The workflow will pause at the first review checkpoint (resource_extraction).
    Use resume_workflow() to continue after engineer approval.

    Args:
        study_id: Study document ID
        reference_doc_ids: Available IRS/RSMeans document IDs
        study_doc_ids: Available study document IDs
        resume_from: Optional thread ID to resume from

    Returns:
        Workflow state (paused at first review checkpoint)
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

    # Execute workflow (will pause at first review checkpoint)
    final_state = await app.ainvoke(initial_state, config)

    return final_state


async def resume_workflow(
    study_id: str,
    engineer_approved: bool = True,
    corrections: Optional[list[dict]] = None,
) -> WorkflowState:
    """
    Resume workflow after engineer review.

    Based on current stage, resumes to the appropriate next node:
    - resource_extraction approved:
        - If rooms_ready (background analyze_rooms done): → PAUSE #2 (reviewing_rooms)
        - If not rooms_ready: wait for analyze_rooms to complete
    - reviewing_rooms approved → runs process_assets → pauses at engineering_takeoff
    - engineering_takeoff approved → runs complete → done

    Args:
        study_id: Study document ID
        engineer_approved: Whether engineer approved the current stage
        corrections: Optional list of corrections made by engineer

    Returns:
        Updated workflow state (will pause at next review checkpoint)
    """
    from ..firestore.client import FirestoreClient

    # Get current study state to determine where to resume
    client = FirestoreClient()
    study = client.get_study(study_id)
    current_stage = study.get("workflowStatus", "uploading_documents") if study else "uploading_documents"
    rooms_ready = study.get("roomsReady", False) if study else False

    # Create workflow with same thread ID
    app = create_workflow()

    # Prepare update with engineer input
    update = {
        "study_id": study_id,
        "engineer_approved": engineer_approved,
        "engineer_corrections": corrections or [],
        "needs_review": False,  # Clear review flag
    }

    # Determine which node to resume from
    if current_stage == "resource_extraction":
        # After appraisal approval, set appraisal_approved flag
        update["appraisal_approved"] = True
        client.update_study(study_id, {"appraisalApproved": True})

        # Check if analyze_rooms has completed (background task)
        if rooms_ready:
            # Rooms are ready, advance to reviewing_rooms (PAUSE #2)
            update["current_stage"] = "reviewing_rooms"
        else:
            # Rooms not ready yet, stay at resource_extraction but mark approved
            # Frontend will poll and see rooms_ready when analyze_rooms completes
            update["current_stage"] = "analyzing_rooms"  # Show "Analyzing rooms..." status

    elif current_stage == "reviewing_rooms":
        # After room review approval → run process_assets → pause at engineering_takeoff
        update["current_stage"] = "processing_assets"

    elif current_stage == "engineering_takeoff":
        # After engineering takeoff approval → complete
        update["current_stage"] = "completed"

    # Resume execution
    config = {"configurable": {"thread_id": study_id}}
    final_state = await app.ainvoke(update, config)

    return final_state


async def trigger_next_stage(study_id: str) -> WorkflowState:
    """
    Trigger the next stage of the workflow after engineer approval.

    Simpler version of resume_workflow that just advances to the next stage.

    Args:
        study_id: Study document ID

    Returns:
        Workflow state after running the next stage
    """
    return await resume_workflow(study_id, engineer_approved=True)


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
