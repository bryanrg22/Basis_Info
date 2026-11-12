"""``POST /v1/demo-requests``: the public site's "Book a demo" form; ``GET /v1/ops/demo-requests``:
operators read the requests (docs/api/DEMO_REQUESTS.md, EVAL_REPORT section 95).

The one ``/v1`` write without a token: a visitor to the public site is nobody's user. What
stands in for authentication:

* a per-address rate limit, :func:`demo_request_rate_limit` (``DEMO_REQUESTS_PER_HOUR``,
  through the API's rate limiter, so the Postgres store holds it across instances), run
  before the body is read;
* the ``website`` spam trap: a field the page hides from people and always sends empty;
  filled in, the request is answered exactly as a stored one (202 with an id) and stored
  nowhere, so a bot learns nothing;
* the duplicate window: the same email within ten minutes is answered with the first
  request's id, stores nothing and emails nothing (``DemoRequestRepository.record``);
* CORS for this path only: the site's origins (``DEMO_REQUEST_ALLOWED_ORIGINS``) may post
  here and nowhere else (:mod:`basis.api.cors`);
* the API's 1 MiB body limit, and the schema's lengths (``basis.api.schemas.demo_requests``).

202, not 201: the visitor cannot read the request back (there is no public read and no
``Location``), the email is sent later by the relay, and the trap and a duplicate must look
the same as a new request (``client_errors`` answers 202 for the same reasons). The body is
the id only; nothing about any other request.

Nothing the visitor typed is logged: the log lines carry the request's id, its role, and the
client address for the trap.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, Request

from basis.api.errors import TooManyRequests
from basis.api.operators import require_operator
from basis.api.ratelimit import UNKNOWN_CLIENT, RateLimiter, get_rate_limiter
from basis.api.routes.activity import Cursor
from basis.api.routes.common import API_PREFIX, problems, read_router, seq_cursor
from basis.api.schemas.demo_requests import (
    DemoRequestAcceptedOut,
    DemoRequestIn,
    DemoRequestOut,
    DemoRequestPageOut,
    Notification,
)
from basis.config import Settings
from basis.db.models import DemoRequest
from basis.db.repositories.demo_request import (
    MAX_DEMO_REQUEST_PAGE,
    DemoRequestRepository,
    NewDemoRequest,
)
from basis.db.repositories.study_event import request_id
from basis.db.session import SessionDep
from basis.errors.codes import E

PATH: Final = f"{API_PREFIX}/demo-requests"
RATE_WINDOW: Final = timedelta(hours=1)
MAX_USER_AGENT: Final = 500
DEFAULT_PAGE: Final = 50

logger = logging.getLogger(__name__)

# 400 is the body parser's (bytes that are not UTF-8 JSON); the shared 400 text is about
# Idempotency-Key, which this route does not take.
_PUBLIC_PROBLEMS = problems(400, 413, 422, 429, 503)
_PUBLIC_PROBLEMS[400] = {
    **_PUBLIC_PROBLEMS[400],
    "description": "The body could not be parsed (not UTF-8 JSON).",
}
public = APIRouter(prefix=API_PREFIX, tags=["demo-requests"], responses=_PUBLIC_PROBLEMS)
reads = read_router(tags=["ops"])


def client_address(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


async def demo_request_rate_limit(
    request: Request, limiter: Annotated[RateLimiter, Depends(get_rate_limiter)]
) -> None:
    """429 past ``DEMO_REQUESTS_PER_HOUR`` from one client address (trapped and duplicate
    requests count: they are requests)."""
    settings: Settings = request.app.state.settings
    limit = settings.demo_requests_per_hour
    host = client_address(request) or UNKNOWN_CLIENT
    decision = await limiter.hit(f"demo-requests:ip:{host}", limit, RATE_WINDOW)
    if not decision.allowed:
        raise TooManyRequests(
            f"Rate limit of {limit} demo requests per hour exceeded.",
            retry_after=math.ceil(decision.retry_after),
        )


@public.post(
    "/demo-requests",
    status_code=202,
    response_model=DemoRequestAcceptedOut,
    summary="Ask for a demo (the public site's form; no authentication)",
    description="Name, work email, company and role, an optional note and the page the form "
    "was on. No token: the route is rate limited per client address (`DEMO_REQUESTS_PER_HOUR`, "
    "10 by default, then 429). The answer is 202 with the request's id. The same email again "
    "within ten minutes gets the first request's id and nothing new is stored or emailed; a "
    "filled-in `website` (the page's hidden spam trap) gets an id and nothing is stored. Each "
    "new request is emailed once to `DEMO_REQUEST_NOTIFY_EMAIL` by the outbox relay, so a slow "
    "mail server never fails the request. Browsers may call it from "
    "`DEMO_REQUEST_ALLOWED_ORIGINS` (docs/api/DEMO_REQUESTS.md).",
    dependencies=[Depends(demo_request_rate_limit)],
)
async def post_demo_request(
    body: DemoRequestIn, request: Request, session: SessionDep
) -> DemoRequestAcceptedOut:
    remote = client_address(request)
    if body.website:
        logger.info(
            "demo request trapped",
            extra={"error_code": E.DEMO_REQUEST_TRAPPED, "remote_addr": remote},
        )
        return DemoRequestAcceptedOut(id=uuid.uuid4())
    user_agent = request.headers.get("user-agent")
    recorded = await DemoRequestRepository(session).record(
        NewDemoRequest(
            name=body.name,
            email=body.email,
            company=body.company,
            role=body.role,
            note=body.note,
            page=body.page,
            remote_addr=remote,
            user_agent=None if not user_agent else user_agent[:MAX_USER_AGENT],
            request_id=request_id(),
        )
    )
    row = recorded.request
    if recorded.created:
        logger.info(
            "demo request recorded",
            extra={
                "error_code": E.DEMO_REQUEST_RECORDED,
                "demo_request_id": str(row.id),
                "role": row.role,
            },
        )
    else:
        logger.info(
            "demo request duplicate",
            extra={"error_code": E.DEMO_REQUEST_DUPLICATE, "demo_request_id": str(row.id)},
        )
    return DemoRequestAcceptedOut(id=row.id)


def notification_of(row: DemoRequest) -> Notification:
    if row.notified_at is not None:
        return "sent"
    if row.notify_error is not None:
        return "failed"
    return "queued"


def _out(row: DemoRequest) -> DemoRequestOut:
    return DemoRequestOut(
        id=row.id,
        seq=row.seq,
        received_at=row.created_at,
        name=row.name,
        email=row.email,
        company=row.company,
        role=row.role,  # type: ignore[arg-type]  # the table's check is the Literal's values
        note=row.note,
        page=row.page,
        remote_addr=row.remote_addr,
        user_agent=row.user_agent,
        request_id=row.request_id,
        notification=notification_of(row),
        notified_at=row.notified_at,
        notify_error=row.notify_error,
    )


@reads.get(
    "/ops/demo-requests",
    summary="Demo requests from the public site, newest first (platform operators)",
    description="Every stored request with what the visitor sent, when, from which address, "
    "and whether its email went out (`notification`). `next_cursor` continues with older "
    "requests. Operators only (`OPS_OPERATOR_UIDS`); a firm role never grants it "
    "(docs/api/DEMO_REQUESTS.md).",
    dependencies=[Depends(require_operator)],
    responses=problems(403),
)
async def list_demo_requests(
    session: SessionDep,
    limit: Annotated[
        int, Query(ge=1, le=MAX_DEMO_REQUEST_PAGE, description="Requests per page.")
    ] = DEFAULT_PAGE,
    cursor: Cursor = None,
) -> DemoRequestPageOut:
    page = await DemoRequestRepository(session).list(before_seq=seq_cursor(cursor), limit=limit)
    return DemoRequestPageOut(
        items=[_out(row) for row in page.items],
        next_cursor=None if page.next_seq is None else str(page.next_seq),
    )
