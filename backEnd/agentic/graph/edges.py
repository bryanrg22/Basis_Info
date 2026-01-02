"""
Conditional edge functions for workflow routing.

Determines next node based on workflow state.
"""

from typing import Literal

from .state import WorkflowState


def route_after_rooms(
    state: WorkflowState,
) -> Literal["wait_for_review", "analyze_objects"]:
    """
    Route after room analysis.

    If rooms need review, pause for engineer.
    Otherwise, continue to object analysis.
    """
    if state.get("needs_review") and not state.get("engineer_approved"):
        return "wait_for_review"
    return "analyze_objects"


def route_after_takeoffs(
    state: WorkflowState,
) -> Literal["wait_for_review", "classify_assets"]:
    """
    Route after takeoff analysis.

    If takeoffs need review, pause for engineer.
    Otherwise, continue to asset classification.
    """
    if state.get("needs_review") and not state.get("engineer_approved"):
        return "wait_for_review"
    return "classify_assets"


def route_after_classification(
    state: WorkflowState,
) -> Literal["wait_for_review", "estimate_costs"]:
    """
    Route after asset classification.

    If any items need review, pause for engineer.
    Otherwise, continue to cost estimation.
    """
    if state.get("needs_review") and not state.get("engineer_approved"):
        return "wait_for_review"
    return "estimate_costs"


def route_after_costs(
    state: WorkflowState,
) -> Literal["wait_for_review", "verify_assets"]:
    """
    Route after cost estimation.

    If any items need review, pause for engineer.
    Otherwise, continue to verification.
    """
    if state.get("needs_review") and not state.get("engineer_approved"):
        return "wait_for_review"
    return "verify_assets"


def route_after_verification(
    state: WorkflowState,
) -> Literal["complete", "wait_for_review"]:
    """
    Route after verification.

    If engineer has approved, complete workflow.
    Otherwise, wait for approval.
    """
    if state.get("engineer_approved"):
        return "complete"
    return "wait_for_review"


def check_for_errors(
    state: WorkflowState,
) -> Literal["error", "continue"]:
    """
    Check if there are errors to handle.
    """
    if state.get("last_error"):
        return "error"
    return "continue"


def determine_next_stage(state: WorkflowState) -> str:
    """
    Determine next stage based on current stage.

    Used when resuming workflow after engineer approval.
    """
    stage_flow = {
        "uploading_documents": "analyze_rooms",
        "analyzing_rooms": "reviewing_rooms",
        "reviewing_rooms": "analyze_objects",
        "analyzing_takeoffs": "reviewing_takeoffs",
        "reviewing_takeoffs": "viewing_report",
        "viewing_report": "classify_assets",
        "reviewing_assets": "estimate_costs",
        "estimating_costs": "verify_assets",
        "verifying_assets": "complete",
    }

    current = state.get("current_stage", "uploading_documents")
    return stage_flow.get(current, "complete")
