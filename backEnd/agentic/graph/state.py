"""
Workflow state definition for LangGraph.

Defines the typed state that persists across workflow nodes.
"""

from typing import Any, Literal, Optional, TypedDict


class WorkflowState(TypedDict, total=False):
    """
    State that persists across workflow nodes.

    Matches the frontend workflow stages:
    uploading_documents → analyzing_rooms → reviewing_rooms →
    analyzing_takeoffs → reviewing_takeoffs → viewing_report →
    reviewing_assets → verifying_assets → completed
    """

    # Study identification
    study_id: str
    user_id: str
    property_name: str

    # Current stage
    current_stage: Literal[
        "uploading_documents",
        "analyzing_rooms",
        "reviewing_rooms",
        "analyzing_takeoffs",
        "reviewing_takeoffs",
        "viewing_report",
        "reviewing_assets",
        "estimating_costs",
        "verifying_assets",
        "completed",
    ]

    # Stage results (accumulated as workflow progresses)
    rooms: list[dict[str, Any]]
    objects: list[dict[str, Any]]
    takeoffs: list[dict[str, Any]]
    asset_classifications: list[dict[str, Any]]
    cost_estimates: list[dict[str, Any]]  # RSMeans-backed cost estimates

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
        evidence_pack=[],
        reference_doc_ids=reference_doc_ids or [],
        study_doc_ids=study_doc_ids or [],
        needs_review=False,
        review_reasons=[],
        items_needing_review=[],
        engineer_approved=False,
        engineer_corrections=[],
        errors=[],
        last_error=None,
    )
