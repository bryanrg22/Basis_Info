"""
Checkpoint history endpoints.

Phase 6: Provides REST endpoints for viewing and comparing checkpoint history.
"""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field

from ...firestore.checkpointer import FirestoreCheckpointer
from ...firestore.checkpoint_history import (
    CheckpointHistoryEntry,
    CheckpointHistoryList,
    CheckpointDiff,
)
from ..auth import CurrentUser, get_current_user, verify_study_ownership
from ..validators import ValidStudyId
from ..rate_limit import limiter, STATUS_LIMIT


router = APIRouter(prefix="/workflow", tags=["checkpoints"])


# =============================================================================
# Response Models
# =============================================================================


class CheckpointHistoryEntryResponse(BaseModel):
    """Response for a single checkpoint history entry."""

    id: str
    thread_id: str
    parent_id: Optional[str]
    v: int
    from_stage: Optional[str]
    to_stage: Optional[str]
    trigger: str
    summary: dict[str, Any]
    created_at: str


class CheckpointHistoryListResponse(BaseModel):
    """Response for checkpoint history listing."""

    thread_id: str
    entries: list[CheckpointHistoryEntryResponse]
    total_count: int
    latest_stage: Optional[str]


class CheckpointDiffResponse(BaseModel):
    """Response for checkpoint comparison."""

    checkpoint_a_id: str
    checkpoint_b_id: str
    checkpoint_a_stage: Optional[str]
    checkpoint_b_stage: Optional[str]
    fields_changed: list[str]
    additions_count: int
    deletions_count: int
    modifications_count: int
    summary: dict[str, Any]


class CheckpointDetailResponse(BaseModel):
    """Response for a checkpoint with full channel values."""

    id: str
    thread_id: str
    parent_id: Optional[str]
    v: int
    from_stage: Optional[str]
    to_stage: Optional[str]
    trigger: str
    summary: dict[str, Any]
    channel_values: dict[str, Any]
    metadata: dict[str, Any]
    created_at: str


# =============================================================================
# Helper Functions
# =============================================================================


def _entry_to_response(entry: CheckpointHistoryEntry) -> CheckpointHistoryEntryResponse:
    """Convert CheckpointHistoryEntry to response model."""
    return CheckpointHistoryEntryResponse(
        id=entry.id,
        thread_id=entry.thread_id,
        parent_id=entry.parent_id,
        v=entry.v,
        from_stage=entry.from_stage,
        to_stage=entry.to_stage,
        trigger=entry.trigger,
        summary=entry.summary,
        created_at=entry.created_at.isoformat(),
    )


def _entry_to_detail_response(
    entry: CheckpointHistoryEntry,
) -> CheckpointDetailResponse:
    """Convert CheckpointHistoryEntry to detailed response model."""
    return CheckpointDetailResponse(
        id=entry.id,
        thread_id=entry.thread_id,
        parent_id=entry.parent_id,
        v=entry.v,
        from_stage=entry.from_stage,
        to_stage=entry.to_stage,
        trigger=entry.trigger,
        summary=entry.summary,
        channel_values=entry.channel_values,
        metadata=entry.metadata,
        created_at=entry.created_at.isoformat(),
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/{study_id}/checkpoints", response_model=CheckpointHistoryListResponse)
@limiter.limit(STATUS_LIMIT)
async def list_checkpoint_history(
    study_id: ValidStudyId,
    request_obj: Request,
    limit: int = 50,
    user: CurrentUser = Depends(get_current_user),
):
    """
    List checkpoint history for a study workflow.

    Phase 6: Returns chronological history of all checkpoints,
    enabling debugging of what happened between pauses.

    Query Parameters:
        limit: Maximum number of entries to return (default: 50)
    """
    request_obj.state.user = user
    verify_study_ownership(study_id, user)

    # Thread ID is the study ID for workflow checkpoints
    thread_id = study_id

    checkpointer = FirestoreCheckpointer()
    history = checkpointer.get_history(thread_id, limit=limit)

    return CheckpointHistoryListResponse(
        thread_id=history.thread_id,
        entries=[_entry_to_response(e) for e in history.entries],
        total_count=history.total_count,
        latest_stage=history.latest_stage,
    )


@router.get(
    "/{study_id}/checkpoints/{entry_id}",
    response_model=CheckpointDetailResponse,
)
@limiter.limit(STATUS_LIMIT)
async def get_checkpoint_entry(
    study_id: ValidStudyId,
    entry_id: str,
    request_obj: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Get a specific checkpoint history entry with full details.

    Phase 6: Returns the complete checkpoint state including
    channel values for detailed inspection.
    """
    request_obj.state.user = user
    verify_study_ownership(study_id, user)

    thread_id = study_id

    checkpointer = FirestoreCheckpointer()
    entry = checkpointer.get_history_entry(thread_id, entry_id)

    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"Checkpoint entry not found: {entry_id}",
        )

    return _entry_to_detail_response(entry)


@router.get(
    "/{study_id}/checkpoints/{entry_id_a}/diff/{entry_id_b}",
    response_model=CheckpointDiffResponse,
)
@limiter.limit(STATUS_LIMIT)
async def compare_checkpoints(
    study_id: ValidStudyId,
    entry_id_a: str,
    entry_id_b: str,
    request_obj: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Compare two checkpoint entries and show differences.

    Phase 6: Computes and returns the diff between two checkpoints,
    showing what fields changed, were added, or were removed.

    Path Parameters:
        entry_id_a: First (earlier) checkpoint entry ID
        entry_id_b: Second (later) checkpoint entry ID
    """
    request_obj.state.user = user
    verify_study_ownership(study_id, user)

    thread_id = study_id

    checkpointer = FirestoreCheckpointer()
    diff = checkpointer.diff_checkpoints(thread_id, entry_id_a, entry_id_b)

    if not diff:
        raise HTTPException(
            status_code=404,
            detail=f"Could not compute diff: one or both entries not found",
        )

    return CheckpointDiffResponse(
        checkpoint_a_id=diff.checkpoint_a_id,
        checkpoint_b_id=diff.checkpoint_b_id,
        checkpoint_a_stage=diff.checkpoint_a_stage,
        checkpoint_b_stage=diff.checkpoint_b_stage,
        fields_changed=diff.fields_changed,
        additions_count=len(diff.additions),
        deletions_count=len(diff.deletions),
        modifications_count=len(diff.modifications),
        summary=diff.summary,
    )


@router.get("/{study_id}/checkpoints/transitions")
@limiter.limit(STATUS_LIMIT)
async def get_stage_transitions(
    study_id: ValidStudyId,
    request_obj: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Get a summary of all stage transitions for a workflow.

    Phase 6: Returns a timeline of stage transitions for easy
    visualization of workflow progress.
    """
    request_obj.state.user = user
    verify_study_ownership(study_id, user)

    thread_id = study_id

    checkpointer = FirestoreCheckpointer()
    history = checkpointer.get_history(thread_id)

    transitions = history.get_stage_transitions()

    return {
        "study_id": study_id,
        "total_transitions": len(transitions),
        "transitions": [
            {
                "from_stage": t[0],
                "to_stage": t[1],
                "timestamp": t[2].isoformat(),
            }
            for t in transitions
        ],
        "current_stage": history.latest_stage,
    }
