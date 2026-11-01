"""``GET /v1/studies/{id}/timeline``, ``.../timeline/steps`` and ``.../audit``: the study's
history (docs/api/TIMELINE_AND_AUDIT.md). Viewer access.

* ``/timeline``: the ``study_events`` rows in order, keyset-paginated on ``seq`` (``limit``,
  ``cursor``; ``event_type`` filters, repeatable).
* ``/timeline/steps``: derived from the whole timeline (:mod:`basis.audit.timeline`): one
  step per activity id with its attempts and duration, one entry per stage visit with its
  duration and review wait.
* ``/audit``: every engineer action in order (:mod:`basis.db.repositories.audit`), keyset-
  paginated on the item's sort key (``limit``, ``cursor``).
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends, Query

from basis.api.routes.common import (
    MAX_SEQ_CURSOR_LENGTH,
    EventTypes,
    problems,
    read_router,
    seq_cursor,
)
from basis.api.schemas.timeline import (
    AuditItemOut,
    AuditOut,
    EventOut,
    StageOut,
    StepOut,
    TimelinePageOut,
    TimelineStepsOut,
)
from basis.api.users import study_access
from basis.audit.timeline import event_json, stage_json, stages, step_json, steps
from basis.db.enums import OrgRole
from basis.db.repositories import VisibleStudy
from basis.db.repositories.audit import MAX_AUDIT_PAGE, AuditRepository
from basis.db.repositories.study_event import MAX_TIMELINE_PAGE, StudyEventRepository
from basis.db.session import SessionDep

DEFAULT_PAGE: Final = 200
MAX_AUDIT_CURSOR_LENGTH: Final = 256

reads = read_router(tags=["timeline"])

ViewerStudy = Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))]


@reads.get(
    "/studies/{study_id}/timeline",
    summary="The study's timeline: every recorded event, in order",
    responses=problems(404),
)
async def get_timeline(
    access: ViewerStudy,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_TIMELINE_PAGE, description="Events per page.")] = (
        DEFAULT_PAGE
    ),
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=MAX_SEQ_CURSOR_LENGTH,
            description="Opaque `next_cursor` of the previous page.",
        ),
    ] = None,
    event_type: EventTypes = None,
) -> TimelinePageOut:
    page = await StudyEventRepository(session).list_for_study(
        access.study.id, after_seq=seq_cursor(cursor), limit=limit, event_types=event_type
    )
    return TimelinePageOut(
        study_id=access.study.id,
        items=[EventOut.model_validate(event_json(e)) for e in page.items],
        next_cursor=None if page.next_seq is None else str(page.next_seq),
    )


@reads.get(
    "/studies/{study_id}/timeline/steps",
    summary="The study's steps (per activity, with attempts and durations) and stage visits",
    responses=problems(404),
)
async def get_timeline_steps(access: ViewerStudy, session: SessionDep) -> TimelineStepsOut:
    events = (await StudyEventRepository(session).list_for_study(access.study.id)).items
    return TimelineStepsOut(
        study_id=access.study.id,
        steps=[StepOut.model_validate(step_json(s)) for s in steps(events)],
        stages=[StageOut.model_validate(stage_json(v)) for v in stages(events)],
    )


@reads.get(
    "/studies/{study_id}/audit",
    summary="Every engineer action on the study, in order",
    description="Decisions, corrections, manual photo objects, the intake and the report "
    "answers set, uploads confirmed, the study started, retries and cancels requested, the "
    "study archived or unarchived, the report signed (docs/api/TIMELINE_AND_AUDIT.md). "
    "Oldest first; `cursor` continues from the previous page's `next_cursor`.",
    responses=problems(404),
)
async def get_audit(
    access: ViewerStudy,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_AUDIT_PAGE, description="Items per page.")] = (
        DEFAULT_PAGE
    ),
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=MAX_AUDIT_CURSOR_LENGTH,
            description="Opaque `next_cursor` of the previous page.",
        ),
    ] = None,
) -> AuditOut:
    page = await AuditRepository(session).page_for_study(
        access.study.id, limit=limit, cursor=cursor
    )
    return AuditOut(
        study_id=access.study.id,
        next_cursor=page.next_cursor,
        items=[
            AuditItemOut(
                kind=item.kind,  # type: ignore[arg-type]
                at=item.at,
                by=item.by,
                stage=item.stage,
                revision=item.revision,
                action=item.action,
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                before=item.before,
                after=item.after,
                changed=list(item.changed),
                reason=item.reason,
                review_id=item.review_id,
                correction_id=item.correction_id,
                produced=list(item.produced),
            )
            for item in page.items
        ],
    )
