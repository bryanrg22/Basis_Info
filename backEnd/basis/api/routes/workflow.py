"""The study's Temporal workflow: start, status, cancel (S2_SPEC section 7).

* ``POST /v1/studies/{id}/start`` (engineer). Needs at least one appraisal document whose
  upload is confirmed. In one short transaction the study moves ``uploading ->
  extracting`` and records ``workflow_id = study-{id}``; after that transaction commits,
  and with no transaction open, the workflow is started with conflict policy
  ``USE_EXISTING`` and reuse policy ``REJECT_DUPLICATE``. The answer is 202
  ``{workflow_id, run_id}``, and the same 202 on every repeat, with or without the same
  ``Idempotency-Key``, because Temporal returns the existing execution. Once the study
  is past ``extracting`` a repeat only describes the execution: if Temporal no longer
  knows it (for example after namespace retention), the answer is 409
  ``workflow-missing`` rather than a second execution that would redo finished work.

  Why commit first: the database is the source of truth the workflow's activities read,
  so the study must be in ``extracting`` before any workflow can run against it, and no
  row lock or connection is held during the Temporal call. If Temporal is unreachable the
  answer is 503 with the study already in ``extracting``; repeating the request (any key)
  skips straight to the start, so the two converge. Concurrent starts converge too
  (``StudyRepository.mark_started``).
* ``GET /v1/studies/{id}/workflow`` (viewer): Temporal's execution status, the workflow's
  ``status`` query (null when it cannot answer, e.g. no worker is polling) and the
  study's ``runs`` rows. 404 when the study has no workflow.
* ``POST /v1/studies/{id}/cancel`` (owner): sends the ``cancel_study`` signal; the
  workflow cancels its activities and records the outcome. 404 when there is no
  workflow, 409 when it has already closed.

Every route answers 503 when Temporal is not configured or unreachable. Decisions do not
come through here: ``POST .../decisions`` writes an outbox row that ``basis.relay``
delivers, so they keep working while Temporal is down.
"""

from __future__ import annotations

import logging
from typing import Annotated, Final

from fastapi import Depends, Request, status

from basis.api.errors import Conflict, NotFound, ServiceUnavailable
from basis.api.routes.common import problems, read_router, write_router
from basis.api.schemas.workflow import (
    CancelIn,
    CancelRequested,
    StudyWorkflowOut,
    WorkflowProgress,
    WorkflowRunOut,
    WorkflowStarted,
)
from basis.api.users import CurrentUserDep, SessionmakerDep, StudyIdPath, require_study_access
from basis.config import Settings
from basis.db.enums import DocumentKind, OrgRole, Stage, StudyStatus
from basis.db.models import Study
from basis.db.repositories import DocumentRepository, StudyRepository
from basis.db.session import session_scope
from basis.temporal.names import study_workflow_id
from basis.temporal.service import (
    WorkflowNotFoundError,
    WorkflowNotRunningError,
    WorkflowRef,
    WorkflowService,
    WorkflowServiceError,
    WorkflowUnavailableError,
)
from basis.workflow.contracts import StudyWorkflowInput

START_ROLE: Final = OrgRole.ENGINEER
READ_ROLE: Final = OrgRole.VIEWER
CANCEL_ROLE: Final = OrgRole.OWNER
TEMPORAL_RETRY_AFTER_S: Final = 5
DETAIL_NOT_CONFIGURED: Final = "Temporal is not configured."
DETAIL_NO_WORKFLOW: Final = "The study has no workflow; start it first."

logger = logging.getLogger(__name__)

reads = read_router(tags=["workflow"])
writes = write_router(tags=["workflow"])


def get_workflow_service(request: Request) -> WorkflowService:
    workflows: WorkflowService | None = getattr(request.app.state, "workflows", None)
    if workflows is None:
        raise ServiceUnavailable(DETAIL_NOT_CONFIGURED, retry_after=TEMPORAL_RETRY_AFTER_S)
    return workflows


WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]


def as_problem(exc: WorkflowServiceError) -> Exception:
    if isinstance(exc, WorkflowNotFoundError):
        return NotFound(DETAIL_NO_WORKFLOW)
    if isinstance(exc, WorkflowNotRunningError):
        return Conflict(str(exc), type_suffix="workflow-closed")
    if isinstance(exc, WorkflowUnavailableError):
        return ServiceUnavailable(str(exc), retry_after=TEMPORAL_RETRY_AFTER_S)
    return ServiceUnavailable("Temporal returned an unexpected error.")  # pragma: no cover


async def existing_workflow(workflows: WorkflowService, workflow_id: str) -> WorkflowRef:
    try:
        described = await workflows.describe(workflow_id)
    except WorkflowNotFoundError as exc:
        raise Conflict(
            f"Temporal has no record of {workflow_id}, although the study has progressed "
            "past its first stage; it is not started again automatically.",
            type_suffix="workflow-missing",
        ) from exc
    return WorkflowRef(workflow_id=workflow_id, run_id=described.run_id)


def ensure_not_archived(study: Study) -> None:
    if study.status is StudyStatus.ARCHIVED:
        raise Conflict("The study is archived.", type_suffix="study-archived")


def workflow_input(study: Study, settings: Settings) -> StudyWorkflowInput:
    return StudyWorkflowInput(
        study_id=str(study.id),
        org_id=str(study.org_id),
        input_revision=study.revision,
        workflow_version=settings.git_sha,
        budget_usd=settings.study_budget_usd,
        photo_concurrency=settings.photo_concurrency,
        review_reminder_days=settings.review_reminder_days,
        review_deadline_days=settings.review_deadline_days,
    )


@writes.post(
    "/studies/{study_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start the study's workflow (idempotent)",
    responses=problems(403, 404),
)
async def start_study(
    study_id: StudyIdPath,
    request: Request,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    workflows: WorkflowServiceDep,
) -> WorkflowStarted:
    settings: Settings = request.app.state.settings
    workflow_id = study_workflow_id(study_id)
    async with session_scope(sessionmaker) as session:
        study = (await require_study_access(session, user, study_id, START_ROLE)).study
        ensure_not_archived(study)
        if study.workflow_id is None:
            has_appraisal = await DocumentRepository(session).has_uploaded(
                study.id, DocumentKind.APPRAISAL
            )
            if not has_appraisal:
                raise Conflict(
                    "Upload and confirm at least one appraisal document before starting.",
                    type_suffix="appraisal-required",
                )
            study = await StudyRepository(session).mark_started(
                study.id, workflow_id, study.revision
            )
        past_first_stage = study.current_stage is not Stage.EXTRACTING
        started_input = workflow_input(study, settings)
    try:
        if past_first_stage:
            # The workflow has moved the study on, so it was started. Never start it
            # again: if Temporal has lost it, a new execution would redo finished work.
            ref = await existing_workflow(workflows, workflow_id)
        else:
            ref = await workflows.start_study(started_input)
    except WorkflowServiceError as exc:
        logger.warning(
            "workflow start failed",
            extra={"study_id": str(study_id), "exception_type": type(exc).__name__},
        )
        raise as_problem(exc) from exc
    logger.info(
        "workflow started",
        extra={"study_id": str(study_id), "workflow_id": ref.workflow_id, "run_id": ref.run_id},
    )
    return WorkflowStarted(workflow_id=ref.workflow_id, run_id=ref.run_id)


@reads.get(
    "/studies/{study_id}/workflow",
    summary="The study's workflow: execution status, progress and runs",
    responses=problems(404),
)
async def get_workflow(
    study_id: StudyIdPath,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    workflows: WorkflowServiceDep,
) -> StudyWorkflowOut:
    async with session_scope(sessionmaker) as session:
        study = (await require_study_access(session, user, study_id, READ_ROLE)).study
        if study.workflow_id is None:
            raise NotFound(DETAIL_NO_WORKFLOW)
        workflow_id = study.workflow_id
        rows = await StudyRepository(session).runs(study.id)
        runs = [WorkflowRunOut.model_validate(row) for row in rows]
    # No transaction is open during the Temporal calls.
    try:
        described = await workflows.describe(workflow_id)
    except WorkflowServiceError as exc:
        raise as_problem(exc) from exc
    progress: WorkflowProgress | None
    try:
        view = await workflows.query_status(workflow_id)
        progress = WorkflowProgress.model_validate(view.model_dump())
    except WorkflowServiceError as exc:
        # The execution exists (describe answered) but nothing answered the query, e.g.
        # no worker is polling. The rest of the answer is still useful.
        logger.info(
            "workflow status query unanswered",
            extra={"study_id": str(study_id), "exception_type": type(exc).__name__},
        )
        progress = None
    return StudyWorkflowOut(
        workflow_id=described.workflow_id,
        run_id=described.run_id,
        execution_status=described.status,
        status=progress,
        runs=runs,
    )


@writes.post(
    "/studies/{study_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask the study's workflow to cancel (owner only)",
    responses=problems(403, 404),
)
async def cancel_study(
    study_id: StudyIdPath,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    workflows: WorkflowServiceDep,
    body: CancelIn | None = None,
) -> CancelRequested:
    async with session_scope(sessionmaker) as session:
        study = (await require_study_access(session, user, study_id, CANCEL_ROLE)).study
        if study.workflow_id is None:
            raise NotFound(DETAIL_NO_WORKFLOW)
        workflow_id = study.workflow_id
    reason = (body or CancelIn()).reason
    try:
        await workflows.cancel(workflow_id, reason)
    except WorkflowServiceError as exc:
        raise as_problem(exc) from exc
    logger.info(
        "workflow cancel requested", extra={"study_id": str(study_id), "workflow_id": workflow_id}
    )
    return CancelRequested(workflow_id=workflow_id)
