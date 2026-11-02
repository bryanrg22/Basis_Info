"""``POST /v1/client-errors``: the web app reports the errors a user hit in the browser;
``GET /v1/ops/client-errors`` and ``.../{client_error_id}``: operators read them
(docs/api/CLIENT_ERRORS.md).

The ingest route is an ordinary write route (a Firebase token, any firm role, the mutation
rate limit, an ``Idempotency-Key`` per batch), with two limits of its own:

* the batch is at most :data:`MAX_BATCH_BYTES` (256 KiB, under the API's 1 MiB body limit),
  checked before the body is parsed, 413 ``E.API_CLIENT_ERROR_BATCH_TOO_LARGE``;
* at most :data:`BATCHES_PER_MIN` batches a minute per user (429), so a render loop that
  throws on every frame costs the API nothing past the first batch: the capture module caps
  itself at 30 events a minute and 20 events a batch, and this is the server's own floor.

What the server adds and verifies: the reporter is the token's user (``user_id``), never the
``user`` the event claims; the firm is stored in ``org_id`` only when the reporter is a
member of it (one firm: that one, when the event names none), the study only when the
reporter can see it (``StudyRepository.get_visible``: a member of its firm), else both stay
inside the event as the client's claim and the columns are NULL, so a browser cannot write a
timeline row into another firm's study. Keys that look like secrets (the logging redaction
pattern: key, token, secret, password, authorization) are masked anywhere in the event
before it is stored. An event that names a visible study also appends ``client.error`` to the
study's timeline in the same transaction (``actor`` the engineer, ``request_id`` this
request's, ``code`` the failed request's, ``entity_id`` the record), so the error appears in
``GET .../timeline``, ``GET .../audit`` is unchanged (it lists the engineer's actions, and an
error is not one) and ``GET /v1/me/activity`` lists it under the user.

202, not 201: the response says how many were stored and how many were duplicates (the
client's ``event_id`` is unique), and the client never reads the records back.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Final

from fastapi import Depends, Path, Query, Request

from basis.api.errors import NotFound, PayloadTooLarge, TooManyRequests
from basis.api.operators import require_operator
from basis.api.ratelimit import WINDOW, RateLimiter, get_rate_limiter
from basis.api.routes.activity import Cursor, Since, Until, as_utc
from basis.api.routes.common import problems, read_router, seq_cursor, write_router
from basis.api.schemas.client_errors import (
    ClientErrorBatchIn,
    ClientErrorDetailOut,
    ClientErrorIn,
    ClientErrorKind,
    ClientErrorOut,
    ClientErrorPageOut,
    ClientErrorsAcceptedOut,
)
from basis.api.users import CurrentUserDep
from basis.config import Settings
from basis.db.enums import OrgRole
from basis.db.models import ClientError
from basis.db.repositories import (
    ClientErrorFilters,
    ClientErrorRepository,
    NewClientError,
    StudyRepository,
    UserRepository,
)
from basis.db.repositories.client_error import MAX_CLIENT_ERROR_PAGE
from basis.db.repositories.study_event import StudyEventRepository, request_id
from basis.db.session import SessionDep
from basis.errors.codes import CODE_PATTERN, E
from basis.obs.logging import redact

MAX_BATCH_BYTES: Final = 256 * 1024
BATCHES_PER_MIN: Final = 30
CLIENT_ERROR_EVENT: Final = "client.error"
DEFAULT_PAGE: Final = 50
# What the timeline row keeps of the message: enough to recognise the error, not the record.
TIMELINE_MESSAGE_CHARS: Final = 500
DETAIL_TOO_LARGE: Final = (
    f"A batch of browser error events is at most {MAX_BATCH_BYTES} bytes; send fewer events."
)
DETAIL_NOT_FOUND: Final = "Client error record not found."

logger = logging.getLogger(__name__)

writes = write_router(tags=["client-errors"])
reads = read_router(tags=["ops"])


async def batch_size_limit(request: Request) -> None:
    """413 before the body is parsed when the batch is over :data:`MAX_BATCH_BYTES`."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.strip().isdigit() and int(declared) > MAX_BATCH_BYTES:
        raise PayloadTooLarge(DETAIL_TOO_LARGE, code=E.API_CLIENT_ERROR_BATCH_TOO_LARGE)
    body = await request.body()  # cached on the request; the route's parser reads the same
    if len(body) > MAX_BATCH_BYTES:
        raise PayloadTooLarge(DETAIL_TOO_LARGE, code=E.API_CLIENT_ERROR_BATCH_TOO_LARGE)


async def batch_rate_limit(
    user: CurrentUserDep, limiter: Annotated[RateLimiter, Depends(get_rate_limiter)]
) -> None:
    decision = await limiter.hit(f"client-errors:uid:{user.principal.uid}", BATCHES_PER_MIN, WINDOW)
    if not decision.allowed:
        raise TooManyRequests(
            f"Rate limit of {BATCHES_PER_MIN} error batches per minute exceeded.",
            retry_after=int(decision.retry_after) + 1,
        )


def resolve_org(event: ClientErrorIn, memberships: dict[uuid.UUID, OrgRole]) -> uuid.UUID | None:
    """The firm column: the claimed firm when the reporter belongs to it, else the one firm
    they belong to, else NULL (the claim stays inside the event)."""
    claimed = event.firm.org_id if event.firm is not None else None
    if claimed is not None and claimed in memberships:
        return claimed
    if claimed is None and len(memberships) == 1:
        return next(iter(memberships))
    return None


def stored_event(
    event: ClientErrorIn,
    *,
    request: Request,
    received_at: datetime,
    sent_at: datetime | None,
    user_id: uuid.UUID,
    uid: str,
    org_verified: bool,
    study_visible: bool,
) -> dict[str, Any]:
    """The JSON to keep: the event as sent, secrets masked, plus what the server knows."""
    settings: Settings = request.app.state.settings
    body: dict[str, Any] = redact(event.model_dump(mode="json"))
    body["_server"] = {
        "received_at": received_at.isoformat(),
        "sent_at": None if sent_at is None else sent_at.isoformat(),
        # The browser's clock minus the server's at send time (null when the batch has no
        # sent_at): add it to occurred_at to place the event on the server's timeline.
        "clock_offset_ms": None
        if sent_at is None
        else round((sent_at - received_at).total_seconds() * 1000),
        "api_request_id": request_id(),
        "api_git_sha": settings.git_sha,
        "api_env": settings.env,
        "reporter": {"user_id": str(user_id), "uid": uid},
        "org_verified": org_verified,
        "study_visible": study_visible,
        "remote_addr": request.client.host if request.client is not None else None,
    }
    return body


def _code_of(event: ClientErrorIn) -> str | None:
    code = event.http.code if event.http is not None else None
    if code is None or not CODE_PATTERN.fullmatch(code):
        return None
    return code


@writes.post(
    "/client-errors",
    status_code=202,
    response_model=ClientErrorsAcceptedOut,
    summary="Report errors users hit in the browser (the web app's capture module)",
    description="A batch of up to 20 events (at most 256 KiB). Any firm role; the reporter is "
    "the token's user whatever the event claims. The firm and study columns are set only when "
    "the reporter belongs to the firm and can see the study (the claims stay inside the stored "
    "event otherwise); an event that names a visible study also appends `client.error` to the "
    "study's timeline. Keys that look like secrets are masked before storing. 30 batches a "
    "minute per user, then 429. The same `event_id` is stored once "
    "(docs/api/CLIENT_ERRORS.md).",
    dependencies=[Depends(batch_size_limit), Depends(batch_rate_limit)],
    responses=problems(413, 429),
)
async def post_client_errors(
    body: ClientErrorBatchIn, request: Request, user: CurrentUserDep, session: SessionDep
) -> ClientErrorsAcceptedOut:
    received_at = datetime.now(UTC)
    memberships = await UserRepository(session).memberships(user.id)
    records = ClientErrorRepository(session)
    studies = StudyRepository(session)
    events = StudyEventRepository(session)
    stored = 0
    duplicates = 0
    ids: list[uuid.UUID] = []
    for event in body.events:
        org_id = resolve_org(event, memberships)
        claimed_study = event.study.study_id if event.study is not None else None
        visible = (
            await studies.get_visible(claimed_study, user.id) if claimed_study is not None else None
        )
        code = _code_of(event)
        new = NewClientError(
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            user_id=user.id,
            org_id=org_id,
            study_id=None if visible is None else visible.study.id,
            revision=event.study.revision if event.study is not None else None,
            stage=event.study.stage if event.study is not None else None,
            route=event.page.hash if event.page is not None else None,
            screen=event.page.screen if event.page is not None else None,
            kind=event.kind,
            error_type=event.error_type,
            message=event.message,
            code=code,
            http_status=event.http.status if event.http is not None else None,
            request_id=event.http.request_id if event.http is not None else None,
            build_sha=event.build.api_git_sha if event.build is not None else None,
            web_version=event.build.web_version if event.build is not None else None,
            user_agent=event.client.user_agent if event.client is not None else None,
            event=stored_event(
                event,
                request=request,
                received_at=received_at,
                sent_at=body.sent_at,
                user_id=user.id,
                uid=user.principal.uid,
                org_verified=org_id is not None,
                study_visible=visible is not None,
            ),
        )
        row = await records.record(new)
        if row is None:
            duplicates += 1
            continue
        stored += 1
        ids.append(row.id)
        if visible is not None:
            await events.record(
                visible.study.id,
                CLIENT_ERROR_EVENT,
                actor="engineer",
                actor_id=user.id,
                stage=visible.study.current_stage,
                revision=visible.study.revision,
                entity_type="client_error",
                entity_id=row.id,
                code=code,
                detail={
                    "kind": event.kind,
                    "level": event.level,
                    "error_type": event.error_type,
                    "message": event.message[:TIMELINE_MESSAGE_CHARS],
                    "occurred_at": event.occurred_at.isoformat(),
                    "route": new.route,
                    "screen": new.screen,
                    "client_revision": new.revision,
                    "client_stage": new.stage,
                    "http": None
                    if event.http is None
                    else {
                        "method": event.http.method,
                        "route": event.http.route,
                        "status": event.http.status,
                        "code": code,
                        "request_id": event.http.request_id,
                    },
                    "build_sha": new.build_sha,
                    "event_id": str(event.event_id),
                },
            )
        logger.info(
            "client error recorded",
            extra={
                "error_code": E.CLIENT_ERROR_RECORDED,
                "client_error_id": str(row.id),
                "client_event_id": str(event.event_id),
                "kind": event.kind,
                "client_code": code,
                "client_request_id": new.request_id,
                "client_http_status": new.http_status,
                "org_id": None if org_id is None else str(org_id),
                "study_id": None if visible is None else str(visible.study.id),
                "screen": new.screen,
                "build_sha": new.build_sha,
            },
        )
    return ClientErrorsAcceptedOut(
        accepted=len(body.events), stored=stored, duplicates=duplicates, ids=ids
    )


def _out(row: ClientError) -> dict[str, Any]:
    return {
        "id": row.id,
        "seq": row.seq,
        "event_id": row.event_id,
        "occurred_at": row.occurred_at,
        "received_at": row.created_at,
        "user_id": row.user_id,
        "org_id": row.org_id,
        "study_id": row.study_id,
        "revision": row.revision,
        "stage": row.stage,
        "route": row.route,
        "screen": row.screen,
        "kind": row.kind,
        "error_type": row.error_type,
        "message": row.message,
        "code": row.code,
        "http_status": row.http_status,
        "request_id": row.request_id,
        "build_sha": row.build_sha,
        "web_version": row.web_version,
        "user_agent": row.user_agent,
    }


Code = Annotated[
    str | None,
    Query(pattern=r"^BASIS-[A-Z]+-\d{3}$", description="Only this catalogue code."),
]
RequestId = Annotated[
    str | None,
    Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:\-]+$"),
]


@reads.get(
    "/ops/client-errors",
    summary="Browser error records, newest first (platform operators)",
    description="Every error the web app reported, filtered by firm, user, study, code, kind, "
    "the failed request's id and the time received (`since`/`until`, RFC 3339; a naive time "
    "is UTC); `next_cursor` continues with older records. Operators only "
    "(`OPS_OPERATOR_UIDS`); a firm role never grants it (docs/api/CLIENT_ERRORS.md).",
    dependencies=[Depends(require_operator)],
    responses=problems(403),
)
async def list_client_errors(
    session: SessionDep,
    limit: Annotated[
        int, Query(ge=1, le=MAX_CLIENT_ERROR_PAGE, description="Records per page.")
    ] = DEFAULT_PAGE,
    cursor: Cursor = None,
    org_id: Annotated[uuid.UUID | None, Query()] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    study_id: Annotated[uuid.UUID | None, Query()] = None,
    code: Code = None,
    kind: Annotated[ClientErrorKind | None, Query()] = None,
    request_id: RequestId = None,
    since: Since = None,
    until: Until = None,
) -> ClientErrorPageOut:
    page = await ClientErrorRepository(session).list(
        filters=ClientErrorFilters(
            org_id=org_id,
            user_id=user_id,
            study_id=study_id,
            code=code,
            kind=kind,
            request_id=request_id,
            since=as_utc(since),
            until=as_utc(until),
        ),
        before_seq=seq_cursor(cursor),
        limit=limit,
    )
    return ClientErrorPageOut(
        items=[ClientErrorOut.model_validate(_out(row)) for row in page.items],
        next_cursor=None if page.next_seq is None else str(page.next_seq),
    )


@reads.get(
    "/ops/client-errors/{client_error_id}",
    summary="One browser error record with the whole event (platform operators)",
    dependencies=[Depends(require_operator)],
    responses=problems(403, 404),
)
async def get_client_error(
    client_error_id: Annotated[uuid.UUID, Path(description="The record's id.")],
    session: SessionDep,
) -> ClientErrorDetailOut:
    row = await ClientErrorRepository(session).get(client_error_id)
    if row is None:
        raise NotFound(DETAIL_NOT_FOUND)
    return ClientErrorDetailOut.model_validate({**_out(row), "event": row.event})
