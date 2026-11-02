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
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import ClientError

MAX_CLIENT_ERROR_PAGE: Final = 200
PURGE_BATCH: Final = 1000


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


@dataclass(frozen=True, slots=True)
class ClientErrorPage:
    items: list[ClientError]
    next_seq: int | None


class ClientErrorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, new: NewClientError) -> ClientError | None:
        """Store the event; ``None`` when its ``event_id`` was stored before (flushed, not
        committed: the caller's transaction decides)."""
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

    async def get(self, client_error_id: uuid.UUID) -> ClientError | None:
        return await self._session.get(ClientError, client_error_id)

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
        stmt = select(ClientError)
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
        rows = list((await self._session.scalars(stmt)).all())
        items = rows[:limit]
        return ClientErrorPage(items=items, next_seq=items[-1].seq if len(rows) > limit else None)

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
