"""
Conditional edge functions for workflow routing.

PARALLEL workflow with staggered pauses:
1. resource_extraction - PAUSE #1: Engineer reviews appraisal (fast, ~30s)
2. analyze_rooms - runs in BACKGROUND while engineer reviews appraisal
3. reviewing_rooms - PAUSE #2: Engineer reviews rooms (when appraisal approved AND rooms ready)
4. engineering_takeoff - PAUSE #3: Engineer reviews all asset data
"""

from typing import Literal

from .state import WorkflowState


def check_for_errors(
    state: WorkflowState,
) -> Literal["error", "continue"]:
    """
    Check if there are errors to handle.
    """
    if state.get("last_error"):
        return "error"
    return "continue"


def route_after_resource_extraction(
    _state: WorkflowState,
) -> Literal["wait_for_review"]:
    """
    Route after resource extraction (appraisal ingestion).

    ALWAYS pauses for engineer to review appraisal data (PAUSE #1).
    Note: analyze_rooms runs as background task during this pause.
    """
    return "wait_for_review"


def route_after_rooms(
    _state: WorkflowState,
) -> Literal["wait_for_review"]:
    """
    Route after room analysis (background task completion).

    Sets rooms_ready=True and ENDs. The workflow will advance to
    PAUSE #2 (reviewing_rooms) when engineer approves appraisal.
    """
    return "wait_for_review"


def route_after_room_review(
    _state: WorkflowState,
) -> Literal["continue"]:
    """
    Route after room review approval.

    Continues to process_assets (no pause).
    """
    return "continue"


def route_after_assets(
    _state: WorkflowState,
) -> Literal["wait_for_review"]:
    """
    Route after asset processing.

    ALWAYS pauses at engineering_takeoff for engineer to review all asset data.
    """
    return "wait_for_review"


def route_after_engineering_takeoff(
    state: WorkflowState,
) -> Literal["complete", "wait_for_review"]:
    """
    Route after engineering takeoff review.

    Only completes if engineer has explicitly approved.
    """
    if state.get("engineer_approved"):
        return "complete"
    return "wait_for_review"


def determine_next_stage(state: WorkflowState) -> str:
    """
    Determine next stage based on current stage.

    Used when resuming workflow after engineer approval.
    Maps reviewing stages to their next analysis stage.

    PARALLEL FLOW:
    - uploading_documents → resource_extraction (fast, starts analyze_rooms in background)
    - resource_extraction (PAUSE #1) → reviewing_rooms (if rooms_ready) or analyzing_rooms (wait)
    - analyzing_rooms → reviewing_rooms (when done, if appraisal_approved)
    - reviewing_rooms (PAUSE #2) → processing_assets
    - processing_assets → engineering_takeoff
    - engineering_takeoff (PAUSE #3) → completed
    """
    stage_flow = {
        # Initial → resource extraction (fast)
        "uploading_documents": "resource_extraction",
        # Resource extraction (PAUSE #1) → depends on rooms_ready
        "resource_extraction": "reviewing_rooms",  # Default, but resume_workflow checks rooms_ready
        # Analyzing rooms (background) → room review when done
        "analyzing_rooms": "reviewing_rooms",
        # Room review approval → asset processing
        "reviewing_rooms": "processing_assets",
        # Asset processing → engineering takeoff (pause)
        "processing_assets": "engineering_takeoff",
        # Engineering takeoff approval → completed
        "engineering_takeoff": "completed",
    }

    current = state.get("current_stage", "uploading_documents")
    return stage_flow.get(current, "completed")


def get_resume_node(current_stage: str) -> str:
    """
    Get the node to resume from based on current stage.

    When engineer approves a review stage, this determines
    which node the workflow should resume into.
    """
    resume_mapping = {
        # After resource extraction review → still need room review
        "resource_extraction": "resource_extraction_review",
        # After room review → process assets
        "reviewing_rooms": "process_assets",
        # After engineering takeoff → complete
        "engineering_takeoff": "complete",
    }
    return resume_mapping.get(current_stage, "complete")
