"""``GET /v1/me/activity`` and ``GET /v1/orgs/{org_id}/activity``: what one user did, and
what happened to a firm (docs/api/TIMELINE_AND_AUDIT.md "Your activity", "Firm activity").

* ``/me/activity``: the caller's own ``study_events`` rows (``actor_id`` is them) across
  every study they can see now, with each study's id and property name, newest first,
  keyset-paginated on ``seq`` (``limit``, ``cursor``), filtered by ``event_type``
  (repeatable), ``since`` and ``until``.

  The cap Bryan asked for (three years of history should not all show up) is two things,
  not one: a page is at most 200 rows, and the window defaults to the last 90 days when
  ``since`` is absent. A page cap alone would let a client walk the whole history page by
  page; a window alone would still answer one large page. The response carries the window
  it applied (``since``, ``until``), so a client pins it on the next page (the default is
  evaluated per request, so without pinning the window drifts by the time between pages;
  with ``seq`` as the cursor nothing is skipped or repeated, the oldest rows only drop out
  of the window). ``since`` after ``until`` is an empty window: an empty page, not an
  error, as an interval with nothing in it (Stripe's ``created[gte]``/``[lte]``, GitHub's
  ``since``/``until`` answer the same way).

  Naive datetimes are read as UTC. A study the caller can no longer see (a firm they were
  removed from) is not listed: the listing is the caller's view, not a personal record.

* ``/orgs/{org_id}/activity`` (owner): the firm's ``org_events`` rows newest first, the same
  pagination and filters.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

from fastapi import Depends, Query

from basis.api.routes.common import MAX_SEQ_CURSOR_LENGTH, problems, read_router, seq_cursor
from basis.api.schemas.activity import ActivityItemOut, ActivityOut, OrgActivityOut, OrgEventOut
from basis.api.schemas.common import NO_NUL_PATTERN
from basis.api.users import CurrentUserDep, org_access
from basis.audit.timeline import event_json
from basis.db.enums import OrgRole
from basis.db.repositories.org_event import MAX_ORG_ACTIVITY_PAGE, OrgEventRepository
from basis.db.repositories.study_event import MAX_ACTIVITY_PAGE, StudyEventRepository
from basis.db.session import SessionDep

DEFAULT_PAGE: Final = 50
DEFAULT_WINDOW: Final = timedelta(days=90)
MAX_EVENT_TYPE_LENGTH: Final = 100

reads = read_router(tags=["activity"])

Cursor = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=MAX_SEQ_CURSOR_LENGTH,
        description="Opaque `next_cursor` of the previous page.",
    ),
]
EventTypes = Annotated[
    list[
        Annotated[
            str, Query(min_length=1, max_length=MAX_EVENT_TYPE_LENGTH, pattern=NO_NUL_PATTERN)
        ]
    ]
    | None,
    Query(description="Only these event types (repeat the parameter)."),
]
Since = Annotated[
    datetime | None,
    Query(description="Only events at or after this time (RFC 3339; a naive time is UTC)."),
]
Until = Annotated[
    datetime | None,
    Query(description="Only events at or before this time (RFC 3339; a naive time is UTC)."),
]


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@reads.get(
    "/me/activity",
    tags=["me"],
    summary="Your own actions across the studies you can see, newest first",
    description="Every timeline event you caused (`actor_id` is you) in every study of your "
    "firms, with the study's id and property name. The last 90 days unless `since` is given "
    "(the response says which window it applied); at most 200 per page, `next_cursor` "
    "continues with older events. `since` after `until` is an empty page.",
)
async def get_my_activity(
    user: CurrentUserDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_ACTIVITY_PAGE, description="Events per page.")] = (
        DEFAULT_PAGE
    ),
    cursor: Cursor = None,
    event_type: EventTypes = None,
    since: Since = None,
    until: Until = None,
) -> ActivityOut:
    start = as_utc(since) or datetime.now(UTC) - DEFAULT_WINDOW
    end = as_utc(until)
    page = await StudyEventRepository(session).list_for_actor(
        user.id,
        before_seq=seq_cursor(cursor),
        limit=limit,
        event_types=event_type,
        since=start,
        until=end,
    )
    return ActivityOut(
        items=[
            ActivityItemOut.model_validate(
                {
                    **event_json(item.event),
                    "study_id": item.study_id,
                    "property_name": item.property_name,
                }
            )
            for item in page.items
        ],
        since=start,
        until=end,
        next_cursor=None if page.next_seq is None else str(page.next_seq),
    )


@reads.get(
    "/orgs/{org_id}/activity",
    tags=["firms"],
    summary="The firm's own history: invitations, members, roles, the profile (owner only)",
    description="Every recorded action on the firm that is not about a study, newest first "
    "(the vocabulary is in docs/api/TIMELINE_AND_AUDIT.md); `next_cursor` continues with "
    "older events. `since` after `until` is an empty page.",
    responses=problems(403, 404),
)
async def get_org_activity(
    org_id: Annotated[uuid.UUID, Depends(org_access(OrgRole.OWNER))],
    session: SessionDep,
    limit: Annotated[
        int, Query(ge=1, le=MAX_ORG_ACTIVITY_PAGE, description="Events per page.")
    ] = DEFAULT_PAGE,
    cursor: Cursor = None,
    event_type: EventTypes = None,
    since: Since = None,
    until: Until = None,
) -> OrgActivityOut:
    page = await OrgEventRepository(session).list_for_org(
        org_id,
        before_seq=seq_cursor(cursor),
        limit=limit,
        event_types=event_type,
        since=as_utc(since),
        until=as_utc(until),
    )
    return OrgActivityOut(
        org_id=org_id,
        items=[
            OrgEventOut(
                seq=event.seq,
                at=event.created_at,
                event_type=event.event_type,
                actor_type=event.actor_type,  # type: ignore[arg-type]
                actor_id=event.actor_id,
                subject_user_id=event.subject_user_id,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                request_id=event.request_id,
                before=event.before,
                after=event.after,
                detail=event.detail,
            )
            for event in page.items
        ],
        next_cursor=None if page.next_seq is None else str(page.next_seq),
    )
