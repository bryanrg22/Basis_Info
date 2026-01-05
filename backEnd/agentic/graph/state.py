"""
Workflow state definition for LangGraph.

Defines the typed state that persists across workflow nodes.
Matches frontend WorkflowStatus values exactly.
"""

from typing import Any, Literal, Optional, TypedDict


class WorkflowState(TypedDict, total=False):
    """
    State that persists across workflow nodes.

    Stage-gated workflow with 3 pause points:
    1. resource_extraction - Engineer reviews appraisal data
    2. reviewing_rooms - Engineer approves room classifications
    3. engineering_takeoff - Engineer reviews all asset data (objects, takeoffs, classification, costs)

    Matches frontend WorkflowStatus:
    uploading_documents → analyzing_rooms → resource_extraction → reviewing_rooms → engineering_takeoff → completed
    """

    # Study identification
    study_id: str
    user_id: str
    property_name: str

    # Current stage (matches frontend WorkflowStatus exactly)
    current_stage: Literal[
        "uploading_documents",
        "analyzing_rooms",
        "resource_extraction",  # PAUSE #1: Engineer reviews appraisal data
        "reviewing_rooms",      # PAUSE #2: Engineer approves room classifications
        "processing_assets",    # Backend processing (no pause)
        "engineering_takeoff",  # PAUSE #3: Engineer reviews all asset data
        "completed",
    ]

    # Stage results (accumulated as workflow progresses)
    rooms: list[dict[str, Any]]
    objects: list[dict[str, Any]]
    takeoffs: list[dict[str, Any]]
    asset_classifications: list[dict[str, Any]]
    cost_estimates: list[dict[str, Any]]

    # Appraisal/resource extraction data
    appraisal_resources: dict[str, Any]

    # Evidence tracking
    evidence_pack: list[dict[str, Any]]

    # Document references
    reference_doc_ids: list[str]  # IRS/RSMeans documents
    study_doc_ids: list[str]  # Property-specific documents

    # Review flags
    needs_review: bool
    review_reasons: list[str]
    items_needing_review: list[str]

    # Engineer actions
    engineer_approved: bool
    engineer_corrections: list[dict[str, Any]]

    # Parallel branch tracking (for staggered pauses)
    appraisal_approved: bool  # True when engineer approves appraisal at PAUSE #1
    rooms_ready: bool         # True when analyze_rooms completes

    # Error tracking
    errors: list[dict[str, Any]]
    last_error: Optional[str]


def create_initial_state(
    study_id: str,
    user_id: str = "",
    property_name: str = "",
    reference_doc_ids: Optional[list[str]] = None,
    study_doc_ids: Optional[list[str]] = None,
) -> WorkflowState:
    """
    Create initial workflow state for a new study.

    Args:
        study_id: Study document ID
        user_id: User ID
        property_name: Property name
        reference_doc_ids: Available IRS/RSMeans document IDs
        study_doc_ids: Available study document IDs

    Returns:
        Initial workflow state
    """
    return WorkflowState(
        study_id=study_id,
        user_id=user_id,
        property_name=property_name,
        current_stage="uploading_documents",
        rooms=[],
        objects=[],
        takeoffs=[],
        asset_classifications=[],
        cost_estimates=[],
        appraisal_resources={},
        evidence_pack=[],
        reference_doc_ids=reference_doc_ids or [],
        study_doc_ids=study_doc_ids or [],
        needs_review=False,
        review_reasons=[],
        items_needing_review=[],
        engineer_approved=False,
        engineer_corrections=[],
        appraisal_approved=False,
        rooms_ready=False,
        errors=[],
        last_error=None,
    )
