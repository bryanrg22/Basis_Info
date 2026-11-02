"""``POST /v1/client-errors`` (the web app's error events) and the operator reads
``GET /v1/ops/client-errors[/{id}]`` (docs/api/CLIENT_ERRORS.md).

The event is what the browser knew when the error happened. Its shape follows Sentry's
event payload where the names fit (``event_id``, ``timestamp`` as ``occurred_at``, ``level``,
the exception's type, value and stack with a source location, ``breadcrumbs``, ``user``,
``release`` as ``build``, the request that failed) and OpenTelemetry's names for the rest
(``exception.type``/``message``/``stacktrace``; ``browser.language``, ``browser.platform``,
``user_agent.original``; ``http.request.method``, ``http.response.status_code``), with the
Basis ids on top: the firm, the study, its revision and stage, the screen, the problem's
``code`` and ``request_id``. Every string is bounded and free of NUL (PostgreSQL text cannot
carry one), every list is bounded, the free-form parts (``context``, a breadcrumb's ``data``)
are bounded by their serialized size, so one event is at most about 40 KB and a batch of 20
stays under the route's 256 KiB cap.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, Field

from basis.api.schemas.common import INT32_MAX, NO_NUL_PATTERN, RequestModel, ResponseModel

MAX_BATCH: Final = 20
MAX_BREADCRUMBS: Final = 50
MAX_MESSAGE: Final = 4000
MAX_STACK: Final = 16000
MAX_SHORT: Final = 500
MAX_USER_AGENT: Final = 1000
MAX_BODY_FIELDS: Final = 100
MAX_LANGUAGES: Final = 10
MAX_BREADCRUMB_DATA_CHARS: Final = 2000
MAX_CONTEXT_CHARS: Final = 16000

ClientErrorKind = Literal["error", "unhandledrejection", "api", "console", "resource", "report"]
BreadcrumbKind = Literal["click", "navigation", "api", "decision", "console", "error", "custom"]
Level = Literal["error", "warning", "info"]

Short = Annotated[str, Field(max_length=MAX_SHORT, pattern=NO_NUL_PATTERN)]
Message = Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE, pattern=NO_NUL_PATTERN)]
Stack = Annotated[str, Field(max_length=MAX_STACK, pattern=NO_NUL_PATTERN)]
Int32 = Annotated[int, Field(ge=0, le=INT32_MAX)]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("must carry a UTC offset (ISO 8601 with offset)")
    return value


AwareDateTime = Annotated[datetime, AfterValidator(_aware)]


def _bounded_json(limit: int) -> Any:
    def check(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        text = json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
        if len(text) > limit:
            raise ValueError(f"serializes to more than {limit} characters")
        if "\x00" in text:
            raise ValueError("must not contain NUL")
        return value

    return AfterValidator(check)


class SourceIn(RequestModel):
    """Where the error was thrown (the first frame the browser reported)."""

    file: Short | None = None
    line: Int32 | None = None
    column: Int32 | None = None
    function: Short | None = None


class PageIn(RequestModel):
    hash: Short | None = Field(default=None, description="The hash route, e.g. `#rooms/{id}`.")
    screen: Short | None = Field(default=None, description="The screen name the app rendered.")
    path: Short | None = Field(default=None, description="The document's path (no query).")


class UserIn(RequestModel):
    uid: Short | None = None
    email: Short | None = None
    role: Short | None = Field(default=None, description="The role in the current firm.")


class FirmIn(RequestModel):
    org_id: uuid.UUID | None = None
    org_name: Short | None = None


class StudyIn(RequestModel):
    study_id: uuid.UUID | None = None
    revision: Int32 | None = None
    stage: Short | None = None
    status: Short | None = None


class HttpIn(RequestModel):
    """The `/v1` request that failed, or the last one before the error."""

    method: Short | None = None
    route: Short | None = Field(
        default=None, description="The path with ids replaced by `{id}`; never a query value."
    )
    status: Annotated[int, Field(ge=0, le=999)] | None = Field(
        default=None, description="0 when no response arrived."
    )
    code: Short | None = Field(default=None, description="The problem's `BASIS-...` code.")
    request_id: Short | None = None
    problem_type: Short | None = None
    problem_title: Short | None = None
    problem_detail: Message | None = None
    latency_ms: Annotated[float, Field(ge=0)] | None = None
    attempt: Annotated[int, Field(ge=1, le=INT32_MAX)] | None = None
    retry: bool | None = Field(default=None, description="The same idempotency key sent again.")
    body_fields: Annotated[list[Short], Field(max_length=MAX_BODY_FIELDS)] | None = Field(
        default=None, description="The request body's field names; never its values."
    )


class BreadcrumbIn(RequestModel):
    at: AwareDateTime
    since_load_ms: Annotated[float, Field(ge=0)] | None = None
    kind: BreadcrumbKind
    message: Short | None = None
    data: Annotated[dict[str, Any] | None, _bounded_json(MAX_BREADCRUMB_DATA_CHARS)] = None


class BuildIn(RequestModel):
    api_git_sha: Short | None = Field(default=None, description="What `/health` reported.")
    web_version: Short | None = Field(default=None, description="The capture module's `?v=`.")


class ViewportIn(RequestModel):
    width: Int32 | None = None
    height: Int32 | None = None


class ClientIn(RequestModel):
    user_agent: Annotated[str, Field(max_length=MAX_USER_AGENT, pattern=NO_NUL_PATTERN)] | None = (
        None
    )
    platform: Short | None = None
    language: Short | None = None
    languages: Annotated[list[Short], Field(max_length=MAX_LANGUAGES)] | None = None
    viewport: ViewportIn | None = None
    device_pixel_ratio: Annotated[float, Field(ge=0, le=100)] | None = None
    online: bool | None = None
    timezone: Short | None = None
    connection: Short | None = Field(default=None, description="`navigator.connection` type.")
    visibility: Short | None = None
    cookies_enabled: bool | None = None


class ClientErrorIn(RequestModel):
    event_id: uuid.UUID = Field(description="Minted by the browser when the error happened.")
    occurred_at: AwareDateTime = Field(description="The browser's clock, ISO 8601 with offset.")
    since_load_ms: Annotated[float, Field(ge=0)] | None = Field(
        default=None, description="`performance.now()` when it happened."
    )
    kind: ClientErrorKind
    level: Level = "error"
    message: Message
    error_type: Short | None = Field(default=None, description="`error.name` (`TypeError`).")
    stack: Stack | None = None
    source: SourceIn | None = None
    page: PageIn | None = None
    user: UserIn | None = None
    firm: FirmIn | None = None
    study: StudyIn | None = None
    http: HttpIn | None = None
    breadcrumbs: Annotated[list[BreadcrumbIn], Field(max_length=MAX_BREADCRUMBS)] = Field(
        default_factory=list, description="The last user actions, oldest first."
    )
    build: BuildIn | None = None
    client: ClientIn | None = None
    context: Annotated[dict[str, Any] | None, _bounded_json(MAX_CONTEXT_CHARS)] = Field(
        default=None, description="What `Basis.reportError(err, context)` was given."
    )
    repeats: Annotated[int, Field(ge=1, le=INT32_MAX)] = Field(
        default=1, description="How many identical errors this event stands for."
    )
    dropped: Annotated[int, Field(ge=0, le=INT32_MAX)] = Field(
        default=0, description="Events the client dropped under its per-minute cap before this one."
    )
    capture_version: Short | None = Field(default=None, description="The capture module's version.")


class ClientErrorBatchIn(RequestModel):
    events: Annotated[list[ClientErrorIn], Field(min_length=1, max_length=MAX_BATCH)]
    sent_at: AwareDateTime | None = Field(
        default=None, description="When the batch left the browser (its clock)."
    )


class ClientErrorsAcceptedOut(ResponseModel):
    accepted: int = Field(description="Events in the batch.")
    stored: int = Field(description="Events stored now.")
    duplicates: int = Field(description="Events stored before (same `event_id`).")
    ids: list[uuid.UUID] = Field(description="The records stored now, in batch order.")


IssueStatus = Literal["open", "in_progress", "resolved", "ignored", "regressed"]
SettableIssueStatus = Literal["open", "in_progress", "resolved", "ignored"]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]


class IssueStatusIn(RequestModel):
    """A person's call on an issue (docs/api/CLIENT_ERRORS.md section 5.2)."""

    status: SettableIssueStatus = Field(
        description="`regressed` is set by the ingest, never by hand; set `in_progress` or "
        "`resolved` to take it back."
    )
    note: Annotated[str, Field(max_length=500, pattern=NO_NUL_PATTERN)] | None = None


class ClientErrorIssueOut(ResponseModel):
    fingerprint: str = Field(description="Sixteen hex characters naming the bug.")
    kind: ClientErrorKind
    error_type: str | None
    title: str = Field(description="The first occurrence's message.")
    route: str | None
    code: str | None
    http_status: int | None
    status: IssueStatus
    note: str | None
    status_set_by: uuid.UUID | None
    status_set_at: datetime | None
    status_build_sha: str | None = Field(
        description="The build the issue had last been seen on when the status was set; a "
        "resolved issue seen on another build regresses."
    )
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    last_build_sha: str | None


class ClientErrorIssuePageOut(ResponseModel):
    items: list[ClientErrorIssueOut] = Field(description="Most recently seen first.")
    next_cursor: str | None


class ClientErrorOut(ResponseModel):
    id: uuid.UUID
    seq: int = Field(description="Newest first; the keyset cursor.")
    event_id: uuid.UUID
    occurred_at: datetime = Field(description="The browser's clock.")
    received_at: datetime = Field(description="The database clock when it was stored.")
    user_id: uuid.UUID
    org_id: uuid.UUID | None = Field(description="Verified against the reporter's memberships.")
    study_id: uuid.UUID | None = Field(description="Set only when the reporter can see the study.")
    revision: int | None
    stage: str | None
    route: str | None
    screen: str | None
    kind: ClientErrorKind
    error_type: str | None
    message: str
    code: str | None = Field(description="The failed request's `BASIS-...` code.")
    http_status: int | None
    request_id: str | None = Field(description="The failed request's `X-Request-Id`.")
    build_sha: str | None
    web_version: str | None
    user_agent: str | None
    fingerprint: str = Field(description="The issue this record belongs to.")
    issue_status: IssueStatus = Field(description="The issue's status now.")


class ClientErrorDetailOut(ClientErrorOut):
    event: dict[str, Any] = Field(
        description="The whole event as received, redacted, plus `_server` (what the API "
        "added: when it arrived, the clock offset, its own request id and build)."
    )


class ClientErrorPageOut(ResponseModel):
    items: list[ClientErrorOut] = Field(description="Newest first (`seq` descending).")
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next (older) page; null on the last page."
    )
