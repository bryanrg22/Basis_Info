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
