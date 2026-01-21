"""Workflow trigger endpoints."""

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Depends
from pydantic import BaseModel, Field

from ...firestore.client import FirestoreClient
from ...firestore.job_queue import JobQueue
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
# Job Queue Models (Phase 5)
# =============================================================================


class JobResponse(BaseModel):
    """Response for a single job."""

    id: str
    study_id: str
    job_type: str
    status: Literal["pending", "claimed", "running", "completed", "failed", "retry"]
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    progress: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class JobListResponse(BaseModel):
    """Response for listing jobs."""

    study_id: str
    jobs: list[JobResponse]
    total: int
    pending_count: int = 0


class CancelJobRequest(BaseModel):
    """Request to cancel a job."""

    reason: str = Field(default="Cancelled by user", description="Cancellation reason")


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/start", response_model=WorkflowResponse)
@limiter.limit(WORKFLOW_LIMIT)
async def start_workflow(
    request: Request,
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
    request.state.user = user

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
    request: Request,
    body: ResumeWorkflowRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Resume a workflow after engineer review.

    Call this after the engineer has reviewed and approved (or corrected)
    the current stage results.
    """
    # Store user in request state for rate limiting
    request.state.user = user

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
    request: Request,
    body: TriggerStageRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Trigger a specific workflow stage.

    Use this to manually run a specific stage (e.g., re-run classification).
    """
    # Store user in request state for rate limiting
    request.state.user = user

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
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Get the current workflow status for a study.
    """
    # Store user in request state for rate limiting
    request.state.user = user

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


class EvidenceFilterParams(BaseModel):
    """Query parameters for evidence filtering."""

    stage: Optional[str] = Field(default=None, description="Filter by workflow stage")
    component_id: Optional[str] = Field(default=None, description="Filter by component ID")
    doc_id: Optional[str] = Field(default=None, description="Filter by source document")


class EvidenceResponse(BaseModel):
    """Response for evidence queries."""

    study_id: str
    total_citations: int
    entries: list[dict[str, Any]]
    summary: dict[str, Any]
    filters_applied: dict[str, Optional[str]]


@router.get("/{study_id}/evidence", response_model=EvidenceResponse)
@limiter.limit(STATUS_LIMIT)
async def get_workflow_evidence(
    study_id: ValidStudyId,
    request: Request,
    stage: Optional[str] = None,
    component_id: Optional[str] = None,
    doc_id: Optional[str] = None,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Get evidence citations for a study with optional filtering.

    Phase 6: Returns organized evidence pack with filtering by stage,
    component, or source document.

    Query Parameters:
        stage: Filter by workflow stage (e.g., "room", "object", "classification", "takeoff", "cost")
        component_id: Filter by component ID
        doc_id: Filter by source document ID
    """
    # Store user in request state for rate limiting
    request.state.user = user

    # Verify study exists and user has access
    study = verify_study_ownership(study_id, user)

    # Get evidence pack from study
    evidence_pack = study.get("evidence_pack", {})
    entries = evidence_pack.get("entries", [])
    summary = evidence_pack.get("summary", {})

    # Apply filters
    filtered_entries = entries

    if stage:
        stage_entry_ids = set(evidence_pack.get("by_stage", {}).get(stage, []))
        filtered_entries = [e for e in filtered_entries if e.get("id") in stage_entry_ids]

    if component_id:
        component_entry_ids = set(evidence_pack.get("by_component", {}).get(component_id, []))
        filtered_entries = [e for e in filtered_entries if e.get("id") in component_entry_ids]

    if doc_id:
        doc_entry_ids = set(evidence_pack.get("by_document", {}).get(doc_id, []))
        filtered_entries = [e for e in filtered_entries if e.get("id") in doc_entry_ids]

    # If no evidence pack exists, fall back to legacy object citations
    if not entries:
        citations = []
        for obj in study.get("objects", []):
            obj_citations = obj.get("citations", [])
            for citation in obj_citations:
                citations.append({
                    "component": obj.get("component", obj.get("name")),
                    "stage": "classification",
                    **citation,
                })
        filtered_entries = citations
        summary = {"total_citations": len(citations), "legacy_format": True}

    return EvidenceResponse(
        study_id=study_id,
        total_citations=len(filtered_entries),
        entries=filtered_entries,
        summary=summary,
        filters_applied={
            "stage": stage,
            "component_id": component_id,
            "doc_id": doc_id,
        },
    )


# =============================================================================
# Cost Tracking Endpoints (Phase 6)
# =============================================================================


class CostSummaryResponse(BaseModel):
    """Response for cost summary endpoint."""

    study_id: str
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_calls: int
    calls_by_agent: dict[str, int]
    cost_by_agent: dict[str, float]
    cost_by_stage: dict[str, float]
    avg_latency_ms: Optional[float]


@router.get("/{study_id}/costs", response_model=CostSummaryResponse)
@limiter.limit(STATUS_LIMIT)
async def get_workflow_costs(
    study_id: ValidStudyId,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Get LLM cost summary for a study workflow.

    Phase 6: Returns aggregated cost metrics including totals,
    breakdowns by agent and stage, and latency statistics.
    """
    request.state.user = user
    verify_study_ownership(study_id, user)

    from ...observability.cost_tracker import get_cost_tracker

    cost_tracker = get_cost_tracker()
    summary = await cost_tracker.get_study_summary(study_id)

    return CostSummaryResponse(
        study_id=summary.study_id,
        total_cost_usd=summary.total_cost_usd,
        total_input_tokens=summary.total_input_tokens,
        total_output_tokens=summary.total_output_tokens,
        total_calls=summary.total_calls,
        calls_by_agent=summary.calls_by_agent,
        cost_by_agent=summary.cost_by_agent,
        cost_by_stage=summary.cost_by_stage,
        avg_latency_ms=summary.avg_latency_ms,
    )


# =============================================================================
# Decision Logging Endpoints (Phase 6)
# =============================================================================


class DecisionResponse(BaseModel):
    """Response for a single decision."""

    id: str
    agent: str
    component_id: Optional[str]
    component_name: Optional[str]
    decision_type: str
    decision: Any
    reasoning: str
    confidence: float
    evidence_used: list[str]
    timestamp: str


class DecisionListResponse(BaseModel):
    """Response for decision listing."""

    study_id: str
    decisions: list[DecisionResponse]
    total: int


class DecisionSummaryResponse(BaseModel):
    """Response for decision summary."""

    study_id: str
    total_decisions: int
    decisions_by_agent: dict[str, int]
    decisions_by_type: dict[str, int]
    avg_confidence: float
    low_confidence_count: int


@router.get("/{study_id}/decisions", response_model=DecisionListResponse)
@limiter.limit(STATUS_LIMIT)
async def get_workflow_decisions(
    study_id: ValidStudyId,
    request: Request,
    agent: Optional[str] = None,
    decision_type: Optional[str] = None,
    limit: int = 100,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Get agent decisions for a study workflow.

    Phase 6: Returns logged decisions with filtering options
    for debugging and audit trail.

    Query Parameters:
        agent: Filter by agent name (optional)
        decision_type: Filter by decision type (optional)
        limit: Maximum decisions to return (default: 100)
    """
    request.state.user = user
    verify_study_ownership(study_id, user)

    from ...observability.decision_log import get_decision_logger

    decision_logger = get_decision_logger()
    decisions = await decision_logger.get_all_decisions(
        study_id,
        agent_filter=agent,
        decision_type_filter=decision_type,
        limit=limit,
    )

    return DecisionListResponse(
        study_id=study_id,
        decisions=[
            DecisionResponse(
                id=d.id,
                agent=d.agent,
                component_id=d.component_id,
                component_name=d.component_name,
                decision_type=d.decision_type,
                decision=d.decision,
                reasoning=d.reasoning,
                confidence=d.confidence,
                evidence_used=d.evidence_used,
                timestamp=d.timestamp.isoformat(),
            )
            for d in decisions
        ],
        total=len(decisions),
    )


@router.get("/{study_id}/decisions/summary", response_model=DecisionSummaryResponse)
@limiter.limit(STATUS_LIMIT)
async def get_decisions_summary(
    study_id: ValidStudyId,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Get decision summary statistics for a study.

    Phase 6: Returns aggregate metrics about agent decisions.
    """
    request.state.user = user
    verify_study_ownership(study_id, user)

    from ...observability.decision_log import get_decision_logger

    decision_logger = get_decision_logger()
    summary = await decision_logger.get_decision_summary(study_id)

    return DecisionSummaryResponse(
        study_id=summary.study_id,
        total_decisions=summary.total_decisions,
        decisions_by_agent=summary.decisions_by_agent,
        decisions_by_type=summary.decisions_by_type,
        avg_confidence=summary.avg_confidence,
        low_confidence_count=summary.low_confidence_count,
    )


# =============================================================================
# Job Queue Endpoints (Phase 5)
# =============================================================================


def _job_to_response(job) -> JobResponse:
    """Convert Job model to JobResponse."""
    return JobResponse(
        id=job.id,
        study_id=job.study_id,
        job_type=job.job_type,
        status=job.status,
        created_at=job.created_at.isoformat() if job.created_at else "",
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        retry_count=job.retry_count,
        max_retries=job.max_retries,
        progress=job.progress,
        result=job.result,
        error=job.error,
    )


@router.get("/{study_id}/jobs", response_model=JobListResponse)
@limiter.limit(STATUS_LIMIT)
async def list_study_jobs(
    study_id: ValidStudyId,
    request: Request,
    status: Optional[str] = None,
    user: CurrentUser = Depends(get_current_user),
):
    """
    List background jobs for a study.

    Phase 5: Provides visibility into durable job queue for the frontend.
    """
    request.state.user = user
    verify_study_ownership(study_id, user)

    job_queue = JobQueue()
    jobs = await job_queue.get_jobs_by_study(study_id, status=status)
    pending_count = len([j for j in jobs if j.status in ["pending", "retry"]])

    return JobListResponse(
        study_id=study_id,
        jobs=[_job_to_response(j) for j in jobs],
        total=len(jobs),
        pending_count=pending_count,
    )


@router.get("/{study_id}/jobs/{job_id}", response_model=JobResponse)
@limiter.limit(STATUS_LIMIT)
async def get_job_status(
    study_id: ValidStudyId,
    job_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Get status of a specific job.

    Phase 5: Allows frontend to poll for job completion.
    """
    request.state.user = user
    verify_study_ownership(study_id, user)

    job_queue = JobQueue()
    job = await job_queue.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    if job.study_id != study_id:
        raise HTTPException(status_code=403, detail="Job does not belong to this study")

    return _job_to_response(job)


@router.post("/{study_id}/jobs/{job_id}/cancel")
@limiter.limit(WORKFLOW_LIMIT)
async def cancel_job(
    study_id: ValidStudyId,
    job_id: str,
    request: Request,
    body: CancelJobRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Cancel a pending job.

    Phase 5: Allows engineers to cancel stuck or unwanted jobs.
    """
    request.state.user = user
    verify_study_ownership(study_id, user)

    job_queue = JobQueue()
    job = await job_queue.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    if job.study_id != study_id:
        raise HTTPException(status_code=403, detail="Job does not belong to this study")

    cancelled = await job_queue.cancel_job(job_id, body.reason)

    if not cancelled:
        raise HTTPException(
            status_code=400,
            detail="Job cannot be cancelled (already running or completed)",
        )

    return {"success": True, "job_id": job_id, "message": "Job cancelled"}


# =============================================================================
# Debug Endpoints
# =============================================================================


class TestAlertRequest(BaseModel):
    """Request to send a test alert."""

    severity: Literal["warning", "error", "critical"] = Field(
        default="warning",
        description="Alert severity level",
    )
    message: str = Field(
        default="This is a test alert from the debug endpoint",
        description="Custom test message",
    )


class TestAlertResponse(BaseModel):
    """Response from test alert."""

    success: bool
    message: str
    channels_notified: list[str]


@router.post("/debug/test-alert", response_model=TestAlertResponse)
async def send_test_alert(
    body: TestAlertRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Send a test alert to verify Slack webhook configuration.

    This endpoint sends a test alert through all configured channels
    (Slack, webhook) to verify the alerting system is working.

    Requires authentication. Use this to test your ALERT_SLACK_WEBHOOK
    environment variable is configured correctly.
    """
    from ...observability.alerts import send_alert, get_alert_manager

    # Send test alert
    try:
        result = await send_alert(
            study_id="TEST_ALERT",
            stage="debug_endpoint",
            error_type="TEST_ALERT",
            error_message=body.message,
            severity=body.severity,
            context={
                "triggered_by": user.uid,
                "purpose": "Testing alert configuration",
            },
        )

        # Check which channels are configured
        manager = get_alert_manager()
        channels_notified = []
        for channel in manager.channels:
            channel_name = type(channel).__name__.replace("AlertChannel", "")
            channels_notified.append(channel_name)

        if not channels_notified:
            return TestAlertResponse(
                success=False,
                message="No alert channels configured. Set ALERT_SLACK_WEBHOOK in .env",
                channels_notified=[],
            )

        return TestAlertResponse(
            success=result,
            message="Test alert sent successfully" if result else "Alert was throttled or failed",
            channels_notified=channels_notified,
        )

    except Exception as e:
        return TestAlertResponse(
            success=False,
            message=f"Failed to send alert: {str(e)}",
            channels_notified=[],
        )
