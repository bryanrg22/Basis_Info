"""``GET /v1/studies/{id}/timeline``, ``.../timeline/steps`` and ``.../audit``: the study's
history (docs/api/TIMELINE_AND_AUDIT.md). Viewer access.

* ``/timeline``: the ``study_events`` rows in order, keyset-paginated on ``seq`` (``limit``,
  ``cursor``; ``event_type`` filters, repeatable).
* ``/timeline/steps``: derived from the whole timeline (:mod:`basis.audit.timeline`): one
  step per activity id with its attempts and duration, one entry per stage visit with its
  duration and review wait.
* ``/audit``: every engineer action in order (:mod:`basis.db.repositories.audit`).
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends, Query

from basis.api.errors import Unprocessable
from basis.api.routes.common import problems, read_router
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
from basis.db.repositories.audit import AuditRepository
from basis.db.repositories.study_event import MAX_TIMELINE_PAGE, StudyEventRepository
from basis.db.session import SessionDep
from basis.errors.codes import E

DEFAULT_PAGE: Final = 200

reads = read_router(tags=["timeline"])

ViewerStudy = Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))]


MAX_SEQ: Final = 2**63 - 1  # ``study_events.seq`` is a bigint


def _after_seq(cursor: str | None) -> int | None:
    """The ``seq`` a cursor names: ASCII digits within the bigint range, else 422
    ``invalid-cursor`` (a wider number would reach the database and fail there)."""
    if cursor is None:
        return None
    if not (cursor.isascii() and cursor.isdigit()) or int(cursor) > MAX_SEQ:
        raise Unprocessable(
            "invalid pagination cursor", type_suffix="invalid-cursor", code=E.API_INVALID_CURSOR
        )
    return int(cursor)


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
            min_length=1, max_length=32, description="Opaque `next_cursor` of the previous page."
        ),
    ] = None,
    event_type: Annotated[
        list[str] | None,
        Query(description="Only these event types (repeat the parameter)."),
    ] = None,
) -> TimelinePageOut:
    page = await StudyEventRepository(session).list_for_study(
        access.study.id, after_seq=_after_seq(cursor), limit=limit, event_types=event_type
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
    responses=problems(404),
)
async def get_audit(access: ViewerStudy, session: SessionDep) -> AuditOut:
    items = await AuditRepository(session).list_for_study(access.study.id)
    return AuditOut(
        study_id=access.study.id,
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
            for item in items
        ],
    )
