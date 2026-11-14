"""``demo_requests``: the requests for a demo sent from the public site (docs/api/DEMO_REQUESTS.md;
migration ``0035_demo_requests``).

Written by ``POST /v1/demo-requests`` in the request's transaction, together with the
``notification`` outbox row (``type: demo_request``) the relay turns into one email to
``DEMO_REQUEST_NOTIFY_EMAIL``. Read by ``GET /v1/ops/demo-requests`` newest first,
keyset-paginated on ``seq``. The relay records the email's outcome (:meth:`record_notified`,
:meth:`record_notify_error`); the retention purge deletes old rows; nothing else writes.

Duplicates: a request with the same email as one received within :data:`DUPLICATE_WINDOW`
is not stored again and asks for no second email; :meth:`record` returns the first one with
``created=False``. Two identical requests at once (a double click, a retry racing the first)
are serialized by a transaction-scoped advisory lock on the email, taken before the check, so
the second waits for the first to commit and then sees it. The lock is released with the
request's transaction; a hash collision between two emails only makes them queue briefly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import DemoRequest
from basis.db.repositories.outbox import NOTIFICATION, OutboxRepository

# ``notification`` payload type: the relay renders the request (``basis.email.demo_request``).
DEMO_REQUEST_NOTIFICATION: Final = "demo_request"
# The same email again within this long is the same request: a double click, the back button
# and a resubmit, a client retry after a lost response. Long enough for those, short enough
# that someone writing again later with something new is heard (EVAL_REPORT section 95).
DUPLICATE_WINDOW: Final = timedelta(minutes=10)
MAX_DEMO_REQUEST_PAGE: Final = 200
MAX_NOTIFY_ERROR_LENGTH: Final = 2000
PURGE_BATCH: Final = 1000
# Seed for the email's advisory lock (hashtextextended), so it never shares a key with the
# uid lock in ``basis.db.repositories.user``.
_EMAIL_LOCK_SEED: Final = 9_351_002


@dataclass(frozen=True, slots=True)
class NewDemoRequest:
    """One request to store, already validated and trimmed; ``email`` lower-cased."""

    name: str
    email: str
    company: str
    role: str
    note: str | None = None
    page: str | None = None
    remote_addr: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class Recorded:
    request: DemoRequest
    created: bool  # False: a duplicate inside the window, answered with the first request


@dataclass(frozen=True, slots=True)
class DemoRequestPage:
    items: list[DemoRequest]
    next_seq: int | None


class DemoRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self, new: NewDemoRequest, *, window: timedelta = DUPLICATE_WINDOW
    ) -> Recorded:
        """Store the request and enqueue its email, unless the same email was received within
        ``window``: then the earlier request, ``created=False``, and nothing is written.
        Flushed, not committed: the caller's transaction decides."""
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(new.email, _EMAIL_LOCK_SEED)))
        )
        earlier = await self._session.scalar(
            select(DemoRequest)
            .where(
                DemoRequest.email == new.email,
                DemoRequest.created_at > func.now() - window,
            )
            .order_by(DemoRequest.seq.desc())
            .limit(1)
        )
        if earlier is not None:
            return Recorded(earlier, created=False)
        row = DemoRequest(
            name=new.name,
            email=new.email,
            company=new.company,
            role=new.role,
            note=new.note,
            page=new.page,
            remote_addr=new.remote_addr,
            user_agent=new.user_agent,
            request_id=new.request_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        # Only the id: the relay reads the row, so the retention purge leaves nothing
        # personal behind in the outbox.
        await OutboxRepository(self._session).enqueue(
            NOTIFICATION, {"type": DEMO_REQUEST_NOTIFICATION, "demo_request_id": str(row.id)}
        )
        return Recorded(row, created=True)

    async def get(self, demo_request_id: uuid.UUID) -> DemoRequest | None:
        return await self._session.get(DemoRequest, demo_request_id)

    async def list(self, *, before_seq: int | None = None, limit: int = 50) -> DemoRequestPage:
        """Newest first (``seq`` descending), below ``before_seq`` (keyset); ``next_seq`` is
        the cursor for the next (older) page, None on the last."""
        if not 1 <= limit <= MAX_DEMO_REQUEST_PAGE:
            raise ValueError(f"limit must be between 1 and {MAX_DEMO_REQUEST_PAGE}")
        stmt = select(DemoRequest)
        if before_seq is not None:
            stmt = stmt.where(DemoRequest.seq < before_seq)
        stmt = stmt.order_by(DemoRequest.seq.desc()).limit(limit + 1)
        rows = list((await self._session.scalars(stmt)).all())
        items = rows[:limit]
        return DemoRequestPage(items=items, next_seq=items[-1].seq if len(rows) > limit else None)

    async def record_notified(self, demo_request_id: uuid.UUID) -> None:
        """The relay handed the email to the sender: ``notified_at`` now, the error cleared."""
        await self._session.execute(
            update(DemoRequest)
            .where(DemoRequest.id == demo_request_id)
            .values(notified_at=func.now(), notify_error=None)
            .execution_options(synchronize_session=False)
        )

    async def record_notify_error(self, demo_request_id: uuid.UUID, error: str) -> None:
        """Why the email has not gone (yet): no recipient, the sender failed or refused."""
        await self._session.execute(
            update(DemoRequest)
            .where(DemoRequest.id == demo_request_id, DemoRequest.notified_at.is_(None))
            .values(notify_error=error[:MAX_NOTIFY_ERROR_LENGTH])
            .execution_options(synchronize_session=False)
        )

    async def delete_received_before(self, cutoff: datetime, *, limit: int = PURGE_BATCH) -> int:
        """Delete up to ``limit`` of the oldest rows received before ``cutoff``; the number
        deleted (the retention purge calls it until it returns fewer than ``limit``)."""
        if limit < 1:
            raise ValueError("limit must be positive")
        oldest = (
            select(DemoRequest.id)
            .where(DemoRequest.created_at < cutoff)
            .order_by(DemoRequest.seq)
            .limit(limit)
        )
        result = await self._session.execute(
            delete(DemoRequest).where(DemoRequest.id.in_(oldest)).returning(DemoRequest.id)
        )
        return len(result.all())

    async def count_received_before(self, cutoff: datetime) -> int:
        stmt = select(func.count()).select_from(DemoRequest).where(DemoRequest.created_at < cutoff)
        return int(await self._session.scalar(stmt) or 0)
