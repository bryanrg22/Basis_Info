"""``client_errors``: every error a user hit in the browser, as the web app reported it
(docs/api/CLIENT_ERRORS.md; migration ``0028_client_errors``).

Written by ``POST /v1/client-errors`` in the request's transaction, together with the
``client.error`` timeline event when the error names a study the reporter can see (the
timeline is the study's record; this table is the operator's). Read by
``GET /v1/ops/client-errors`` newest first, keyset-paginated on ``seq``, filtered by firm,
user, study, code, kind, request id and the time received. Deleted only by the retention
purge (:mod:`basis.retention.client_errors`); never updated.

Dedup: ``event_id`` is the client's own id, minted when the error happened, so a batch the
client sent twice (a response lost on the way back, a retry after a 5xx) stores each event
once (``INSERT ... ON CONFLICT DO NOTHING``, the pattern of Sentry's event id). The caller
learns which events were new.

Issues (``client_error_issues``, section 5.2 of the contract): the same bug seen again is one
issue, keyed by :func:`fingerprint` (the kind, the error type, the message with numbers and
ids normalized, the top frame's file and function, the route and status of a failed call;
Sentry groups by stack and fingerprint the same way). The ingest upserts the issue before the
record (the record's foreign key): ``occurrences``, ``last_seen_at`` and ``last_build_sha``
move on every new record; the status is a person's (:meth:`set_status`), except that a
``resolved`` issue seen again on a build other than the one it was resolved on becomes
``regressed`` by itself. A record that turns out to be a duplicate is checked first so it
does not count twice; two identical batches racing could count one occurrence twice, which
is the worst the race can do.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import ClientError, ClientErrorIssue

MAX_CLIENT_ERROR_PAGE: Final = 200
MAX_ISSUE_PAGE: Final = 200
PURGE_BATCH: Final = 1000
MAX_NOTE: Final = 500
MAX_TITLE: Final = 500
# The statuses a person may set; ``regressed`` is the ingest's.
SETTABLE_STATUSES: Final = ("open", "in_progress", "resolved", "ignored")

_UUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_HEX: Final = re.compile(r"\b[0-9a-f]{16,}\b", re.I)
_NUMBER: Final = re.compile(r"\d+")
_QUERY: Final = re.compile(r"\?.*$")


def normalize_message(message: str) -> str:
    """The message with ids and numbers replaced, so one bug has one text."""
    text = _UUID.sub("{id}", message)
    text = _HEX.sub("{id}", text)
    text = _NUMBER.sub("#", text)
    return " ".join(text.split())[:200]


def fingerprint(
    *,
    kind: str,
    error_type: str | None,
    message: str,
    source: dict[str, Any] | None,
    route: str | None,
    http_status: int | None,
) -> str:
    """Sixteen hex characters naming the bug: the same error seen again hashes the same."""
    file = str((source or {}).get("file") or "")
    file = _QUERY.sub("", file)
    function = str((source or {}).get("function") or "")
    parts = [
        kind,
        error_type or "",
        normalize_message(message),
        file,
        function,
        route or "",
        "" if http_status is None else str(http_status),
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class NewClientError:
    """One event to store: the filter columns and the whole event (already redacted)."""

    event_id: uuid.UUID
    occurred_at: datetime
    user_id: uuid.UUID
    kind: str
    message: str
    event: dict[str, Any]
    org_id: uuid.UUID | None = None
    study_id: uuid.UUID | None = None
    revision: int | None = None
    stage: str | None = None
    route: str | None = None
    screen: str | None = None
    error_type: str | None = None
    code: str | None = None
    http_status: int | None = None
    request_id: str | None = None
    build_sha: str | None = None
    web_version: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True, slots=True)
class ClientErrorFilters:
    org_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    study_id: uuid.UUID | None = None
    code: str | None = None
    kind: str | None = None
    request_id: str | None = None
    since: datetime | None = None  # on created_at (received), inclusive
    until: datetime | None = None
    status: str | None = None  # the issue's
    fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class ClientErrorRow:
    """A record with its issue's status."""

    record: ClientError
    issue_status: str


@dataclass(frozen=True, slots=True)
class ClientErrorPage:
    items: list[ClientErrorRow]
    next_seq: int | None


@dataclass(frozen=True, slots=True)
class IssuePage:
    items: list[ClientErrorIssue]
    next_cursor: str | None


def issue_cursor(issue: ClientErrorIssue) -> str:
    return f"{issue.last_seen_at.astimezone(UTC).isoformat()}|{issue.fingerprint}"


def parse_issue_cursor(cursor: str) -> tuple[datetime, str] | None:
    """``(last_seen_at, fingerprint)`` of an issue cursor, None when it is not one."""
    when, sep, fp = cursor.partition("|")
    if not sep or not re.fullmatch(r"[0-9a-f]{16}", fp):
        return None
    try:
        parsed = datetime.fromisoformat(when)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed, fp


def _regression_case(stmt: Any) -> Any:
    """``resolved`` and seen on a build other than the one it was resolved on -> ``regressed``;
    every other status stays."""
    from sqlalchemy import case

    new_build = func.coalesce(stmt.excluded.last_build_sha, "")
    resolved_build = func.coalesce(ClientErrorIssue.status_build_sha, "")
    return case(
        ((ClientErrorIssue.status == "resolved") & (new_build != resolved_build), "regressed"),
        else_=ClientErrorIssue.status,
    )


class ClientErrorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, new: NewClientError) -> ClientError | None:
        """Store the event under its issue (created or bumped first); ``None`` when its
        ``event_id`` was stored before (flushed, not committed: the caller's transaction
        decides)."""
        exists = await self._session.scalar(
            select(ClientError.id).where(ClientError.event_id == new.event_id)
        )
        if exists is not None:
            return None
        fp = fingerprint(
            kind=new.kind,
            error_type=new.error_type,
            message=new.message,
            source=new.event.get("source") if isinstance(new.event.get("source"), dict) else None,
            route=(new.event.get("http") or {}).get("route")
            if isinstance(new.event.get("http"), dict)
            else None,
            http_status=new.http_status,
        )
        await self._touch_issue(fp, new)
        values = {
            "event_id": new.event_id,
            "occurred_at": new.occurred_at,
            "user_id": new.user_id,
            "org_id": new.org_id,
            "study_id": new.study_id,
            "revision": new.revision,
            "stage": new.stage,
            "route": new.route,
            "screen": new.screen,
            "kind": new.kind,
            "error_type": new.error_type,
            "message": new.message,
            "code": new.code,
            "http_status": new.http_status,
            "request_id": new.request_id,
            "build_sha": new.build_sha,
            "web_version": new.web_version,
            "user_agent": new.user_agent,
            "fingerprint": fp,
            "event": new.event,
        }
        stmt = (
            pg_insert(ClientError)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[ClientError.event_id])
            .returning(ClientError)
        )
        row = (await self._session.scalars(stmt)).one_or_none()
        if row is not None:
            await self._session.refresh(row)
        return row

    async def _touch_issue(self, fp: str, new: NewClientError) -> None:
        """Create the issue on first sight; on a repeat move the counts and times, and a
        resolved issue seen on another build than the one it was resolved on regresses."""
        route = None
        http = new.event.get("http")
        if isinstance(http, dict):
            route = http.get("route")
        stmt = pg_insert(ClientErrorIssue).values(
            fingerprint=fp,
            kind=new.kind,
            error_type=new.error_type,
            title=new.message[:MAX_TITLE],
            route=route or new.route,
            code=new.code,
            http_status=new.http_status,
            status="open",
            occurrences=1,
            first_seen_at=new.occurred_at,
            last_seen_at=new.occurred_at,
            last_build_sha=new.build_sha,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[ClientErrorIssue.fingerprint],
                set_={
                    "occurrences": ClientErrorIssue.occurrences + 1,
                    "last_seen_at": func.greatest(
                        ClientErrorIssue.last_seen_at, stmt.excluded.last_seen_at
                    ),
                    "last_build_sha": func.coalesce(
                        stmt.excluded.last_build_sha, ClientErrorIssue.last_build_sha
                    ),
                    "status": _regression_case(stmt),
                },
            )
        )

    async def get(self, client_error_id: uuid.UUID) -> ClientError | None:
        return await self._session.get(ClientError, client_error_id)

    async def get_issue(self, fp: str) -> ClientErrorIssue | None:
        return await self._session.get(ClientErrorIssue, fp)

    async def list_issues(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> IssuePage:
        """Issues by ``last_seen_at`` descending (then fingerprint), keyset-paginated."""
        if not 1 <= limit <= MAX_ISSUE_PAGE:
            raise ValueError(f"limit must be between 1 and {MAX_ISSUE_PAGE}")
        stmt = select(ClientErrorIssue)
        if status is not None:
            stmt = stmt.where(ClientErrorIssue.status == status)
        if kind is not None:
            stmt = stmt.where(ClientErrorIssue.kind == kind)
        if cursor is not None:
            parsed = parse_issue_cursor(cursor)
            if parsed is None:
                raise ValueError("invalid issue cursor")
            seen_at, fp = parsed
            stmt = stmt.where(
                (ClientErrorIssue.last_seen_at < seen_at)
                | ((ClientErrorIssue.last_seen_at == seen_at) & (ClientErrorIssue.fingerprint < fp))
            )
        stmt = stmt.order_by(
            ClientErrorIssue.last_seen_at.desc(), ClientErrorIssue.fingerprint.desc()
        ).limit(limit + 1)
        rows = list((await self._session.scalars(stmt)).all())
        items = rows[:limit]
        return IssuePage(
            items=items, next_cursor=issue_cursor(items[-1]) if len(rows) > limit else None
        )

    async def set_status(
        self, fp: str, *, status: str, note: str | None, user_id: uuid.UUID
    ) -> ClientErrorIssue | None:
        """A person's call on the issue; None when there is no such issue. The build the
        issue was last seen on is kept with the status, so the regression rule can tell a
        new build from the one the fix was made for."""
        if status not in SETTABLE_STATUSES:
            raise ValueError(f"status must be one of {SETTABLE_STATUSES}")
        issue = await self._session.get(ClientErrorIssue, fp, with_for_update=True)
        if issue is None:
            return None
        issue.status = status
        issue.note = None if note is None else note[:MAX_NOTE]
        issue.status_set_by = user_id
        issue.status_set_at = datetime.now(UTC)
        issue.status_build_sha = issue.last_build_sha
        await self._session.flush()
        await self._session.refresh(issue)
        return issue

    async def list(
        self,
        *,
        filters: ClientErrorFilters | None = None,
        before_seq: int | None = None,
        limit: int = 50,
    ) -> ClientErrorPage:
        """Newest first (``seq`` descending), below ``before_seq`` (keyset), under the
        filters; ``next_seq`` is the cursor for the next (older) page, None on the last."""
        if not 1 <= limit <= MAX_CLIENT_ERROR_PAGE:
            raise ValueError(f"limit must be between 1 and {MAX_CLIENT_ERROR_PAGE}")
        f = filters or ClientErrorFilters()
        stmt = select(ClientError, ClientErrorIssue.status).join(
            ClientErrorIssue, ClientErrorIssue.fingerprint == ClientError.fingerprint
        )
        if f.status is not None:
            stmt = stmt.where(ClientErrorIssue.status == f.status)
        if f.fingerprint is not None:
            stmt = stmt.where(ClientError.fingerprint == f.fingerprint)
        if before_seq is not None:
            stmt = stmt.where(ClientError.seq < before_seq)
        if f.org_id is not None:
            stmt = stmt.where(ClientError.org_id == f.org_id)
        if f.user_id is not None:
            stmt = stmt.where(ClientError.user_id == f.user_id)
        if f.study_id is not None:
            stmt = stmt.where(ClientError.study_id == f.study_id)
        if f.code is not None:
            stmt = stmt.where(ClientError.code == f.code)
        if f.kind is not None:
            stmt = stmt.where(ClientError.kind == f.kind)
        if f.request_id is not None:
            stmt = stmt.where(ClientError.request_id == f.request_id)
        if f.since is not None:
            stmt = stmt.where(ClientError.created_at >= f.since)
        if f.until is not None:
            stmt = stmt.where(ClientError.created_at <= f.until)
        stmt = stmt.order_by(ClientError.seq.desc()).limit(limit + 1)
        rows = [
            ClientErrorRow(record=record, issue_status=status)
            for record, status in (await self._session.execute(stmt)).all()
        ]
        items = rows[:limit]
        return ClientErrorPage(
            items=items, next_seq=items[-1].record.seq if len(rows) > limit else None
        )

    async def delete_received_before(self, cutoff: datetime, *, limit: int = PURGE_BATCH) -> int:
        """Delete up to ``limit`` of the oldest rows received before ``cutoff``; the number
        deleted (the retention purge calls it until it returns 0)."""
        if limit < 1:
            raise ValueError("limit must be positive")
        oldest = (
            select(ClientError.id)
            .where(ClientError.created_at < cutoff)
            .order_by(ClientError.seq)
            .limit(limit)
        )
        result = await self._session.execute(
            delete(ClientError).where(ClientError.id.in_(oldest)).returning(ClientError.id)
        )
        return len(result.all())

    async def count_received_before(self, cutoff: datetime) -> int:
        stmt = select(func.count()).select_from(ClientError).where(ClientError.created_at < cutoff)
        return int(await self._session.scalar(stmt) or 0)
