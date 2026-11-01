"""PostgreSQL-backed :class:`~basis.api.ratelimit.RateLimiter`: one budget per caller across
every API process.

:class:`~basis.api.ratelimit.InMemoryRateLimiter` counts per process, so a deployment with
``n`` instances admits ``n`` times the published limit (Cloud Run scales the API out under
load, exactly when a limit matters). This limiter keeps the same sliding-window log in the
``rate_limit_hits`` table (migration ``0023``), so the decision is the same whichever
instance answers, and it is the default in ``staging`` and ``prod`` (``RATE_LIMIT_STORE``,
:attr:`basis.config.Settings.rate_limit_backend`).

Algorithm, per ``hit``, in one short transaction of its own (never the request's):

1. ``pg_advisory_xact_lock(hashtext(key))`` serializes the key's requests, so two
   concurrent requests cannot both read ``limit - 1`` hits and both be admitted. The lock
   is released with the transaction. A hash collision between two keys only makes them
   queue briefly.
2. ``now()`` is the database clock, so instances with drifting clocks agree.
3. The key's hits older than the window are deleted (the table holds at most ``limit``
   rows per active key), the rest are read in order.
4. Fewer than ``limit`` remain: a row is inserted and the request is admitted. Otherwise it
   is refused with ``Retry-After`` equal to the moment the oldest hit that still counts
   leaves the window, exactly as the in-memory limiter computes it.

Every :data:`SWEEP_INTERVAL_S` a process also deletes rows older than :data:`RETENTION`
(keys that went idle), bounded by the limit times the number of active keys in between.

Degraded mode: when the database cannot be reached (a connection cannot be opened, the pool
is exhausted, a connection drops) the limiter answers from a per-process
:class:`~basis.api.ratelimit.InMemoryRateLimiter` and logs a warning (at most one per
:data:`SWEEP_INTERVAL_S`, with ``E.API_RATE_LIMIT_STORE_UNAVAILABLE``). Failing closed would
turn a database outage into a refusal of every request, including ``GET /v1/ops/health``,
which is built to answer while the database is down; failing to per-process limiting keeps a
flood bounded
while the database is being brought back. A programming error (the table missing because
the migration was not applied) is not caught: it must surface as a 500 in the first
request, not as silent per-process limiting.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import delete, func, select
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.ratelimit import InMemoryRateLimiter, RateLimitDecision, RateLimiter
from basis.db.errors import DatabaseUnavailableError
from basis.db.models import RateLimitHit
from basis.db.session import session_scope
from basis.errors.codes import E

SWEEP_INTERVAL_S: Final = 60.0
RETENTION: Final = timedelta(hours=1)
# The database errors that mean "the store cannot be reached right now", after which the
# per-process fallback answers; anything else (a ProgrammingError, say) propagates.
UNAVAILABLE_ERRORS: Final = (
    DatabaseUnavailableError,
    PoolTimeoutError,
    OperationalError,
    InterfaceError,
    OSError,
)

logger = logging.getLogger(__name__)


class PostgresRateLimiter:
    """Sliding-window log in ``rate_limit_hits``; see the module docstring."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        fallback: RateLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._fallback = fallback if fallback is not None else InMemoryRateLimiter(clock=clock)
        self._clock = clock
        self._next_sweep = clock() + SWEEP_INTERVAL_S
        self._next_warning = clock()
        # How often the store was unreachable and the fallback answered (ops and tests).
        self.fallbacks = 0

    async def hit(self, key: str, limit: int, window: timedelta) -> RateLimitDecision:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if window <= timedelta(0) or window > RETENTION:
            raise ValueError(f"window must be positive and at most {RETENTION}")
        try:
            return await self._hit(key, limit, window)
        except UNAVAILABLE_ERRORS as exc:
            self.fallbacks += 1
            now = self._clock()
            if now >= self._next_warning:
                self._next_warning = now + SWEEP_INTERVAL_S
                logger.warning(
                    "rate limit store unavailable; limiting per process until it is back",
                    extra={
                        "exception_type": type(exc).__name__,
                        "error_code": E.API_RATE_LIMIT_STORE_UNAVAILABLE,
                        "fallbacks": self.fallbacks,
                    },
                )
            return await self._fallback.hit(key, limit, window)

    async def _hit(self, key: str, limit: int, window: timedelta) -> RateLimitDecision:
        async with session_scope(self._sessionmaker) as session:
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(key))))
            now = await session.scalar(select(func.now()))
            assert isinstance(now, datetime)
            since = now - window
            await session.execute(
                delete(RateLimitHit).where(
                    RateLimitHit.key == key, RateLimitHit.created_at <= since
                )
            )
            stamps = list(
                await session.scalars(
                    select(RateLimitHit.created_at)
                    .where(RateLimitHit.key == key)
                    .order_by(RateLimitHit.created_at, RateLimitHit.id)
                )
            )
            if len(stamps) >= limit:
                retry_after = (stamps[len(stamps) - limit] + window - now).total_seconds()
                return RateLimitDecision(False, limit, 0, max(retry_after, 0.0))
            session.add(RateLimitHit(key=key, created_at=now))
            await self._sweep(session, now)
            return RateLimitDecision(True, limit, limit - len(stamps) - 1, 0.0)

    async def _sweep(self, session: AsyncSession, now: datetime) -> None:
        """Drop the rows of keys that went idle, once per :data:`SWEEP_INTERVAL_S`."""
        if self._clock() < self._next_sweep:
            return
        self._next_sweep = self._clock() + SWEEP_INTERVAL_S
        await session.execute(delete(RateLimitHit).where(RateLimitHit.created_at < now - RETENTION))


__all__ = ["PostgresRateLimiter", "RETENTION", "SWEEP_INTERVAL_S", "UNAVAILABLE_ERRORS"]
