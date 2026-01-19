"""Workflow trigger endpoints."""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Depends
from pydantic import BaseModel, Field

from ...firestore.client import FirestoreClient
from ...graph.workflow import run_workflow, resume_workflow
from ...graph.state import WorkflowState
from ..auth import CurrentUser, get_current_user, verify_study_ownership
from ..exceptions import WorkflowError
from ..validators import ValidStudyId
from ..rate_limit import limiter, WORKFLOW_LIMIT, STATUS_LIMIT


router = APIRouter(prefix="/workflow", tags=["workflow"])


# =============================================================================
# Request/Response Models
# =============================================================================


class StartWorkflowRequest(BaseModel):
    """Request to start a workflow."""

    study_id: ValidStudyId = Field(..., description="Study document ID")
    reference_doc_ids: list[str] = Field(
        default_factory=list,
        description="Available IRS/RSMeans document IDs",
    )
    study_doc_ids: list[str] = Field(
        default_factory=list,
        description="Available study document IDs (appraisals, etc.)",
    )


class ResumeWorkflowRequest(BaseModel):
    """Request to resume a workflow after engineer review."""

    study_id: ValidStudyId = Field(..., description="Study document ID")
    engineer_approved: bool = Field(
        default=True,
        description="Whether engineer approved the current stage",
    )
    corrections: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Optional corrections made by engineer",
    )


class TriggerStageRequest(BaseModel):
    """Request to trigger a specific stage."""

    study_id: ValidStudyId = Field(..., description="Study document ID")
    stage: str = Field(..., description="Stage to trigger")
    reference_doc_ids: list[str] = Field(default_factory=list)
    study_doc_ids: list[str] = Field(default_factory=list)


class WorkflowResponse(BaseModel):
    """Workflow execution response."""

    study_id: str
    status: str
    current_stage: str
    needs_review: bool = False
    items_needing_review: list[str] = Field(default_factory=list)
    message: str = ""


class WorkflowStatusResponse(BaseModel):
    """Workflow status response."""

    study_id: str
    current_stage: str
    rooms_count: int = 0
    objects_count: int = 0
    classifications_count: int = 0
    needs_review: bool = False
    items_needing_review: list[str] = Field(default_factory=list)


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/start", response_model=WorkflowResponse)
@limiter.limit(WORKFLOW_LIMIT)
async def start_workflow(
    request_obj: Request,
    body: StartWorkflowRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Start a new workflow for a study.

    This begins the stage-gated workflow from the beginning.
    The workflow will pause at review checkpoints for engineer approval.
    """
    # Store user in request state for rate limiting
    request_obj.state.user = user

    # Verify study exists and user has access
    verify_study_ownership(body.study_id, user)

    # Run workflow
    try:
        final_state = await run_workflow(
            study_id=body.study_id,
            reference_doc_ids=body.reference_doc_ids,
            study_doc_ids=body.study_doc_ids,
        )

        return WorkflowResponse(
            study_id=body.study_id,
            status="paused" if final_state.get("needs_review") else "running",
            current_stage=final_state.get("current_stage", "unknown"),
            needs_review=final_state.get("needs_review", False),
            items_needing_review=final_state.get("items_needing_review", []),
            message="Workflow started successfully",
        )
    except Exception as e:
        raise WorkflowError(internal_message=str(e))


@router.post("/resume", response_model=WorkflowResponse)
@limiter.limit(WORKFLOW_LIMIT)
async def resume_workflow_endpoint(
    request_obj: Request,
    body: ResumeWorkflowRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Resume a workflow after engineer review.

    Call this after the engineer has reviewed and approved (or corrected)
    the current stage results.
    """
    # Store user in request state for rate limiting
    request_obj.state.user = user

    # Verify study exists and user has access
    verify_study_ownership(body.study_id, user)

    try:
        final_state = await resume_workflow(
            study_id=body.study_id,
            engineer_approved=body.engineer_approved,
            corrections=body.corrections,
        )

        return WorkflowResponse(
            study_id=body.study_id,
            status="completed" if final_state.get("current_stage") == "completed" else "running",
            current_stage=final_state.get("current_stage", "unknown"),
            needs_review=final_state.get("needs_review", False),
            items_needing_review=final_state.get("items_needing_review", []),
            message="Workflow resumed successfully",
        )
    except Exception as e:
        raise WorkflowError(internal_message=str(e))


@router.post("/stage/{stage}", response_model=WorkflowResponse)
@limiter.limit(WORKFLOW_LIMIT)
async def trigger_stage(
    stage: str,
    request_obj: Request,
    body: TriggerStageRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Trigger a specific workflow stage.

    Use this to manually run a specific stage (e.g., re-run classification).
    """
    # Store user in request state for rate limiting
    request_obj.state.user = user

    # Verify study exists and user has access
    verify_study_ownership(body.study_id, user)

    valid_stages = [
        "analyze_rooms",
        "analyze_objects",
        "analyze_takeoffs",
        "classify_assets",
        "verify_assets",
    ]

    if stage not in valid_stages:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stage: {stage}. Must be one of: {valid_stages}",
        )

    # For now, we just run the full workflow
    # In the future, we could add logic to run a specific stage
    try:
        final_state = await run_workflow(
            study_id=body.study_id,
            reference_doc_ids=body.reference_doc_ids,
            study_doc_ids=body.study_doc_ids,
        )

        return WorkflowResponse(
            study_id=body.study_id,
            status="running",
            current_stage=final_state.get("current_stage", stage),
            needs_review=final_state.get("needs_review", False),
            items_needing_review=final_state.get("items_needing_review", []),
            message=f"Stage '{stage}' triggered",
        )
    except Exception as e:
        raise WorkflowError(internal_message=str(e))


@router.get("/{study_id}/status", response_model=WorkflowStatusResponse)
@limiter.limit(STATUS_LIMIT)
async def get_workflow_status(
    study_id: ValidStudyId,
    request_obj: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Get the current workflow status for a study.
    """
    # Store user in request state for rate limiting
    request_obj.state.user = user

    # Verify study exists and user has access
    study = verify_study_ownership(study_id, user)

    # Count items needing review
    objects = study.get("objects", [])
    items_needing_review = [
        obj.get("component", obj.get("name", "unknown"))
        for obj in objects
        if obj.get("needs_review")
    ]

    return WorkflowStatusResponse(
        study_id=study_id,
        current_stage=study.get("workflowStatus", "uploading_documents"),
        rooms_count=len(study.get("rooms", [])),
        objects_count=len(objects),
        classifications_count=len([o for o in objects if o.get("asset_classification")]),
        needs_review=len(items_needing_review) > 0,
        items_needing_review=items_needing_review,
    )


@router.get("/{study_id}/evidence")
@limiter.limit(STATUS_LIMIT)
async def get_workflow_evidence(
    study_id: ValidStudyId,
    request_obj: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Get all evidence citations for a study's classifications.
    """
    # Store user in request state for rate limiting
    request_obj.state.user = user

    # Verify study exists and user has access
    study = verify_study_ownership(study_id, user)

    # Collect all citations from objects
    citations = []
    for obj in study.get("objects", []):
        obj_citations = obj.get("citations", [])
        for citation in obj_citations:
            citations.append({
                "component": obj.get("component", obj.get("name")),
                **citation,
            })

    return {
        "study_id": study_id,
        "total_citations": len(citations),
        "citations": citations,
    }
