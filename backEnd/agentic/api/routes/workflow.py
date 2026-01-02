"""Workflow trigger endpoints."""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from ...firestore.client import FirestoreClient
from ...graph.workflow import run_workflow, resume_workflow
from ...graph.state import WorkflowState


router = APIRouter(prefix="/workflow", tags=["workflow"])


# =============================================================================
# Request/Response Models
# =============================================================================


class StartWorkflowRequest(BaseModel):
    """Request to start a workflow."""

    study_id: str = Field(..., description="Study document ID")
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

    study_id: str = Field(..., description="Study document ID")
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

    study_id: str = Field(..., description="Study document ID")
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
async def start_workflow(
    request: StartWorkflowRequest,
    background_tasks: BackgroundTasks,
):
    """
    Start a new workflow for a study.

    This begins the stage-gated workflow from the beginning.
    The workflow will pause at review checkpoints for engineer approval.
    """
    # Verify study exists
    client = FirestoreClient()
    study = client.get_study(request.study_id)

    if not study:
        raise HTTPException(status_code=404, detail=f"Study not found: {request.study_id}")

    # Run workflow
    try:
        final_state = await run_workflow(
            study_id=request.study_id,
            reference_doc_ids=request.reference_doc_ids,
            study_doc_ids=request.study_doc_ids,
        )

        return WorkflowResponse(
            study_id=request.study_id,
            status="paused" if final_state.get("needs_review") else "running",
            current_stage=final_state.get("current_stage", "unknown"),
            needs_review=final_state.get("needs_review", False),
            items_needing_review=final_state.get("items_needing_review", []),
            message="Workflow started successfully",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resume", response_model=WorkflowResponse)
async def resume_workflow_endpoint(request: ResumeWorkflowRequest):
    """
    Resume a workflow after engineer review.

    Call this after the engineer has reviewed and approved (or corrected)
    the current stage results.
    """
    try:
        final_state = await resume_workflow(
            study_id=request.study_id,
            engineer_approved=request.engineer_approved,
            corrections=request.corrections,
        )

        return WorkflowResponse(
            study_id=request.study_id,
            status="completed" if final_state.get("current_stage") == "completed" else "running",
            current_stage=final_state.get("current_stage", "unknown"),
            needs_review=final_state.get("needs_review", False),
            items_needing_review=final_state.get("items_needing_review", []),
            message="Workflow resumed successfully",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stage/{stage}", response_model=WorkflowResponse)
async def trigger_stage(stage: str, request: TriggerStageRequest):
    """
    Trigger a specific workflow stage.

    Use this to manually run a specific stage (e.g., re-run classification).
    """
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
            study_id=request.study_id,
            reference_doc_ids=request.reference_doc_ids,
            study_doc_ids=request.study_doc_ids,
        )

        return WorkflowResponse(
            study_id=request.study_id,
            status="running",
            current_stage=final_state.get("current_stage", stage),
            needs_review=final_state.get("needs_review", False),
            items_needing_review=final_state.get("items_needing_review", []),
            message=f"Stage '{stage}' triggered",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{study_id}/status", response_model=WorkflowStatusResponse)
async def get_workflow_status(study_id: str):
    """
    Get the current workflow status for a study.
    """
    client = FirestoreClient()
    study = client.get_study(study_id)

    if not study:
        raise HTTPException(status_code=404, detail=f"Study not found: {study_id}")

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
async def get_workflow_evidence(study_id: str):
    """
    Get all evidence citations for a study's classifications.
    """
    client = FirestoreClient()
    study = client.get_study(study_id)

    if not study:
        raise HTTPException(status_code=404, detail=f"Study not found: {study_id}")

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
