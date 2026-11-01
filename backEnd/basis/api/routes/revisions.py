"""``/v1/studies/{id}/reopen``, ``/revisions``, ``/revisions/{a}/diff/{b}`` and
``/revisions/{n}/restore`` (docs/api/REVISIONS.md).

* ``POST .../reopen`` (engineer): a ``completed`` study goes back to a review gate on a new
  revision copied from the completed one; the steps after the gate recompute. ``dry_run``
  answers the plan (200) without acting.
* ``GET .../revisions`` (viewer): one item per revision, who made it, at which gate, with which
  corrections, exported, signed.
* ``GET .../revisions/{a}/diff/{b}`` (viewer): rooms, components and assets added, removed and
  changed between two revisions; the changed list is paginated.
* ``POST .../revisions/{n}/restore`` (engineer): a new revision copied from revision ``n``,
  opened at the last gate ``n`` reached.

Reopen and restore share one sequence (:func:`_resume`): the study is read (role, archived,
workflow, completed), the execution is described with no transaction open (``RUNNING`` is
409), then one short transaction moves the row (:meth:`StudyRepository.begin_revision_at`,
under the row lock, reusing an earlier reset whose start did not happen), and after it commits
a new execution starts under the study's id (:meth:`WorkflowService.resume_study`,
``ALLOW_DUPLICATE`` with conflict ``FAIL``: of two concurrent requests one is admitted and the
other is 409 ``workflow-running``). Temporal unreachable after the reset: 503 with the row
moved; repeating reuses it. Every write here answers 503 when Temporal is not configured.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Final

from fastapi import Depends, Query, Request, status
from fastapi.responses import JSONResponse

from basis.api.errors import Conflict, NotFound
from basis.api.routes.common import problems, read_router, write_router
from basis.api.routes.workflow import (
    DETAIL_NO_WORKFLOW,
    WorkflowServiceDep,
    as_problem,
    ensure_not_archived,
    workflow_input,
)
from basis.api.schemas.revisions import (
    DiffChangeOut,
    DiffCountsOut,
    DiffRowOut,
    FieldChangeOut,
    ReopenIn,
    RestoreIn,
    ResumeAccepted,
    ResumePlanOut,
    RevisionDecisionOut,
    RevisionDiffOut,
    RevisionExportOut,
    RevisionOriginOut,
    RevisionOut,
    RevisionPath,
    RevisionSignatureOut,
    RevisionsOut,
)
from basis.api.users import (
    CurrentUser,
    CurrentUserDep,
    SessionmakerDep,
    StudyIdPath,
    require_study_access,
    study_access,
)
from basis.config import Settings
from basis.db.enums import OrgRole, Stage, StudyStatus
from basis.db.errors import StaleStateError
from basis.db.models import Study
from basis.db.repositories import StudyRepository, VisibleStudy
from basis.db.repositories.revision import (
    MAX_DIFF_PAGE,
    RevisionItem,
    RevisionRepository,
    page_of_changes,
)
from basis.db.session import SessionDep, session_scope
from basis.errors.codes import E
from basis.temporal.service import (
    ResumeRefusedError,
    WorkflowNotFoundError,
    WorkflowService,
    WorkflowServiceError,
)
from basis.workflow.contracts import (
    ResumeInput,
    ResumePlan,
    ResumeReason,
    ReviewStage,
    resume_plan,
)

logger = logging.getLogger(__name__)

RESUME_ROLE: Final = OrgRole.ENGINEER
DEFAULT_DIFF_PAGE: Final = 100
MAX_DIFF_CURSOR_LENGTH: Final = 256

reads = read_router(tags=["revisions"])
writes = write_router(tags=["revisions"])

ViewerStudy = Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))]


def _not_completed(study: Study) -> Conflict:
    return Conflict(
        f"The study is at {study.current_stage.value} revision {study.revision} "
        f"({study.status.value}), not completed. Only a completed study is reopened or has a "
        "revision restored: reject at the gate it waits at, retry a failed stage, or restart a "
        "cancelled study.",
        type_suffix="study-not-completed",
        code=E.STUDY_NOT_COMPLETED,
    )


def _running(status_name: str) -> Conflict:
    return Conflict(
        f"An execution of the study is {status_name.lower()}; a study is reopened or restored "
        "only while none runs.",
        type_suffix="workflow-running",
        code=E.STUDY_EXECUTION_RUNNING,
    )


def _plan_out(plan: ResumePlan, *, from_revision: int, revision: int) -> ResumePlanOut:
    return ResumePlanOut(
        stage=plan.stage,
        from_revision=from_revision,
        revision=revision,
        recomputed=[Stage(s) for s in plan.recomputed],
        reviews=list(plan.reviews),
        carried=list(plan.carried),
    )


def _plan_detail(plan: ResumePlan) -> dict[str, Any]:
    return {
        "stage": plan.stage,
        "recomputed": list(plan.recomputed),
        "reviews": list(plan.reviews),
        "carried": list(plan.carried),
    }


async def _resume(
    *,
    study_id: Any,
    request: Request,
    user: CurrentUser,
    sessionmaker: Any,
    workflows: WorkflowService,
    stage: ReviewStage,
    source_revision: int | None,
    dry_run: bool,
) -> JSONResponse | ResumeAccepted:
    """The shared reopen/restore sequence (module doc); ``source_revision`` None reopens the
    completed revision, else restores that one (checked by the caller)."""
    settings: Settings = request.app.state.settings
    reason: ResumeReason = "reopen" if source_revision is None else "restore"
    plan = resume_plan(stage)
    async with session_scope(sessionmaker) as session:
        visible = await StudyRepository(session).get_visible(study_id, user.id)
        if visible is None:
            raise _missing()
        study = visible.study
        ensure_not_archived(study)
        if study.workflow_id is None:
            raise NotFound(DETAIL_NO_WORKFLOW, code=E.STUDY_NO_WORKFLOW)
        completed = study.current_stage is Stage.COMPLETED and study.status is StudyStatus.COMPLETED
        pending = None
        if not completed:
            # A reset this same request already made (its start answered 503, or is in
            # flight in a concurrent request): reused below, under the lock.
            pending = await StudyRepository(session).pending_resume(study.id, study.revision)
            if pending is None or not pending.matches(Stage(stage), source_revision):
                raise _not_completed(study)
        workflow_id = study.workflow_id
        if pending is not None:
            from_revision, new_revision = pending.source_revision, study.revision
        else:
            from_revision = study.revision if source_revision is None else source_revision
            new_revision = study.revision + 1
    # The execution's state, before anything is written (no transaction open).
    try:
        described = await workflows.describe(workflow_id)
    except WorkflowNotFoundError:
        described = None  # retention took the completed execution: nothing is running
    except WorkflowServiceError as exc:
        raise as_problem(exc) from exc
    if described is not None and described.status == "RUNNING":
        raise _running(described.status)
    if dry_run:
        out = _plan_out(plan, from_revision=from_revision, revision=new_revision)
        return JSONResponse(out.model_dump(mode="json"), status_code=status.HTTP_200_OK)
    async with session_scope(sessionmaker) as session:
        try:
            record = await StudyRepository(session).begin_revision_at(
                study_id,
                Stage(stage),
                actor_id=user.id,
                source_revision=source_revision,
                plan=_plan_detail(plan),
            )
        except StaleStateError as exc:
            current = await StudyRepository(session).get(study_id)
            if current is not None and current.status is StudyStatus.ARCHIVED:
                ensure_not_archived(current)
            raise (_not_completed(current) if current is not None else _missing()) from exc
        started_input = workflow_input(
            record.study,
            settings,
            resume=ResumeInput(stage=stage, from_revision=record.source_revision, reason=reason),
        )
    try:
        ref = await workflows.resume_study(started_input)
    except ResumeRefusedError as exc:
        raise _running(exc.status) from exc
    except WorkflowServiceError as exc:
        logger.warning(
            "workflow resume failed",
            extra={"study_id": str(study_id), "exception_type": type(exc).__name__},
        )
        raise as_problem(exc) from exc
    logger.info(
        "workflow resumed",
        extra={
            "study_id": str(study_id),
            "workflow_id": ref.workflow_id,
            "run_id": ref.run_id,
            "revision": record.study.revision,
            "stage": stage,
            "reason": reason,
            "reused_reset": record.reused,
        },
    )
    return ResumeAccepted(
        **_plan_out(
            plan, from_revision=record.source_revision, revision=record.study.revision
        ).model_dump(),
        workflow_id=ref.workflow_id,
        run_id=ref.run_id,
        reason=reason,
    )


def _missing() -> NotFound:
    return NotFound("Study not found.", code=E.STUDY_NOT_FOUND)


def _revision_not_found(study: Study, number: int) -> NotFound:
    problem = NotFound(
        f"The study has revisions 1 to {study.revision}; {number} is not one of them.",
        code=E.STUDY_REVISION_NOT_FOUND,
    )
    problem.type_suffix = "revision-not-found"
    return problem


@writes.post(
    "/studies/{study_id}/reopen",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ResumeAccepted,
    summary="Reopen a completed study at a review gate (engineer)",
    description="The study goes back to `stage` on a new revision copied from the completed one "
    "(the completed revision, its export, report and signature stay as they are) and a new "
    "execution of its workflow opens the gate; every step after the gate recomputes "
    "(`recomputed`). `dry_run: true` answers 200 with the plan and changes nothing. 409 "
    "`study-not-completed`, `workflow-running`, `study-archived`; 404 with no workflow "
    "(docs/api/REVISIONS.md).",
    responses={
        200: {"model": ResumePlanOut, "description": "`dry_run`: the plan; nothing was done."},
        **problems(403, 404),
    },
)
async def reopen_study(
    study_id: StudyIdPath,
    body: ReopenIn,
    request: Request,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    workflows: WorkflowServiceDep,
) -> JSONResponse | ResumeAccepted:
    async with session_scope(sessionmaker) as session:
        await require_study_access(session, user, study_id, RESUME_ROLE)
    return await _resume(
        study_id=study_id,
        request=request,
        user=user,
        sessionmaker=sessionmaker,
        workflows=workflows,
        stage=body.stage,
        source_revision=None,
        dry_run=body.dry_run,
    )


@writes.post(
    "/studies/{study_id}/revisions/{revision}/restore",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ResumeAccepted,
    summary="Restore an earlier revision as a new one (engineer)",
    description="A new revision copies revision `revision`'s rows and opens at the last gate that "
    "revision reached, so the engineer reviews what they restored; nothing before the gate "
    "reruns, everything after it does. Never deletes or rewrites a revision. 404 "
    "`revision-not-found`; 409 `revision-current`, `revision-not-restorable`, "
    "`study-not-completed`, `workflow-running`, `study-archived` (docs/api/REVISIONS.md).",
    responses={
        200: {"model": ResumePlanOut, "description": "`dry_run`: the plan; nothing was done."},
        **problems(403, 404),
    },
)
async def restore_revision(
    study_id: StudyIdPath,
    revision: RevisionPath,
    request: Request,
    user: CurrentUserDep,
    sessionmaker: SessionmakerDep,
    workflows: WorkflowServiceDep,
    body: RestoreIn | None = None,
) -> JSONResponse | ResumeAccepted:
    async with session_scope(sessionmaker) as session:
        study = (await require_study_access(session, user, study_id, RESUME_ROLE)).study
        if revision > study.revision:
            raise _revision_not_found(study, revision)
        if revision == study.revision:
            raise Conflict(
                f"Revision {revision} is the study's current revision; reopen the study at a "
                "gate instead.",
                type_suffix="revision-current",
                code=E.STUDY_REVISION_CURRENT,
            )
        gate = await RevisionRepository(session).gate_reached(study, revision)
        if gate is None:
            raise Conflict(
                f"Revision {revision} never reached a review gate; there is no reviewed state "
                "to restore.",
                type_suffix="revision-not-restorable",
                code=E.STUDY_REVISION_NOT_RESTORABLE,
            )
    return await _resume(
        study_id=study_id,
        request=request,
        user=user,
        sessionmaker=sessionmaker,
        workflows=workflows,
        stage=gate.value,  # type: ignore[arg-type]
        source_revision=revision,
        dry_run=(body or RestoreIn()).dry_run,
    )


# --------------------------------------------------------------------------- reads


def _item_out(item: RevisionItem) -> RevisionOut:
    origin = item.origin
    return RevisionOut(
        revision=item.revision,
        started_at=item.started_at,
        ended_at=item.ended_at,
        origin=RevisionOriginOut(
            kind=origin.kind,
            stage=origin.stage,
            actor_id=origin.actor_id,
            at=origin.at,
            review_id=origin.review_id,
            from_revision=origin.from_revision,
        ),
        corrections=dict(item.corrections),
        decisions=[
            RevisionDecisionOut(
                stage=d.stage,
                decision=d.decision,
                reviewer_id=d.reviewer_id,
                review_id=d.review_id,
                at=d.at,
            )
            for d in item.decisions
        ],
        stage_reached=item.stage_reached,
        export=(
            None
            if item.export is None
            else RevisionExportOut(
                uri=item.export.uri, sha256=item.export.sha256, at=item.export.at
            )
        ),
        signature=(
            None
            if item.signature is None
            else RevisionSignatureOut(
                id=item.signature.id,
                signed_by=item.signature.signed_by,
                signed_at=item.signature.signed_at,
            )
        ),
        current=item.current,
    )


@reads.get(
    "/studies/{study_id}/revisions",
    summary="The study's revisions: who made each, at which gate, with what, exported, signed",
    responses=problems(404),
)
async def list_revisions(access: ViewerStudy, session: SessionDep) -> RevisionsOut:
    items = await RevisionRepository(session).history(access.study)
    return RevisionsOut(
        study_id=access.study.id,
        current_revision=access.study.revision,
        items=[_item_out(item) for item in items],
    )


@reads.get(
    "/studies/{study_id}/revisions/{from_revision}/diff/{to_revision}",
    summary="Rooms, components and assets added, removed and changed between two revisions",
    description="Rows are paired through their lineage (`carried_from`; an asset through its "
    "component). `changed` is one page (`limit`, `cursor`); `added` and `removed` are complete. "
    "`from_revision` above `to_revision` reads as the reverse diff. 404 `revision-not-found` "
    "(docs/api/REVISIONS.md).",
    responses=problems(404),
)
async def diff_revisions(
    access: ViewerStudy,
    session: SessionDep,
    from_revision: RevisionPath,
    to_revision: RevisionPath,
    limit: Annotated[int, Query(ge=1, le=MAX_DIFF_PAGE, description="Changes per page.")] = (
        DEFAULT_DIFF_PAGE
    ),
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=MAX_DIFF_CURSOR_LENGTH,
            description="Opaque `next_cursor` of the previous page.",
        ),
    ] = None,
) -> RevisionDiffOut:
    study = access.study
    for number in (from_revision, to_revision):
        if number > study.revision:
            raise _revision_not_found(study, number)
    diff = await RevisionRepository(session).diff(study.id, from_revision, to_revision)
    changes, next_cursor = page_of_changes(diff, limit=limit, cursor=cursor)
    return RevisionDiffOut(
        study_id=study.id,
        from_revision=from_revision,
        to_revision=to_revision,
        counts={kind: DiffCountsOut(**values) for kind, values in diff.counts().items()},  # type: ignore[misc]
        added=[DiffRowOut(kind=r.kind, id=r.id, label=r.label) for r in diff.added],
        removed=[DiffRowOut(kind=r.kind, id=r.id, label=r.label) for r in diff.removed],
        changed=[
            DiffChangeOut(
                kind=c.kind,
                from_id=c.from_id,
                to_id=c.to_id,
                label=c.label,
                changes={
                    k: FieldChangeOut(before=v.before, after=v.after) for k, v in c.changes.items()
                },
            )
            for c in changes
        ],
        next_cursor=next_cursor,
    )
