"""Per-caller rate limits (S1_SPEC section 5): by default 240 reads and 60 mutations per minute.

Algorithm: sliding-window log. Each key keeps the timestamps of its accepted requests
in the last window; a request is accepted when fewer than ``limit`` remain. Chosen over
the alternatives because:

* it is exact: never more than ``limit`` requests in any ``window``-long interval. A
  fixed window admits up to 2x at a boundary, and a token bucket sized to the limit
  admits a full burst and then a steady refill, also above the stated "per minute";
* ``Retry-After`` is exact: the moment the oldest logged request leaves the window;
* the cost (one timestamp per accepted request, at most ``limit`` per key) is trivial at
  60 and 240 per minute. At limits in the thousands a GCRA or sliding-window counter
  (O(1) per key) would be the better trade.

Rejected requests are not logged, so a client that keeps hammering is not locked out
longer than the window.

Keys: ``<kind>:uid:<uid>`` for authenticated callers, ``<kind>:ip:<address>`` otherwise.
Reads and mutations have separate budgets. The address is ``request.client.host``; behind
Cloud Run's front end this is the proxy unless uvicorn runs with ``--proxy-headers`` and
``--forwarded-allow-ips`` (X-Forwarded-For is never parsed here, since clients can forge
it). Only a verified uid is used, so a forged token cannot spend someone else's budget.

The in-memory limiter is per process; the Postgres-backed one (S1-C) implements the same
:class:`RateLimiter` protocol.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Final, Literal, Protocol

from fastapi import Depends, Request

from basis.api.auth import OptionalPrincipal
from basis.api.errors import TooManyRequests
from basis.config import Settings

Kind = Literal["read", "mutation"]
WINDOW: Final = timedelta(minutes=1)
UNKNOWN_CLIENT: Final = "unknown"


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: float  # seconds until a request would be accepted; 0 when allowed


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window: timedelta) -> RateLimitDecision:
        """Count one request against ``key`` if it fits in the window."""
        ...


class InMemoryRateLimiter:
    """Sliding-window log, per process. Safe within one event loop (no awaits inside)."""

    SWEEP_INTERVAL_S: Final = 60.0

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._longest_window = 0.0
        self._next_sweep = clock() + self.SWEEP_INTERVAL_S

    def __len__(self) -> int:
        return len(self._hits)

    def _sweep(self, now: float) -> None:
        if now < self._next_sweep:
            return
        self._next_sweep = now + self.SWEEP_INTERVAL_S
        stale = [k for k, q in self._hits.items() if not q or q[-1] <= now - self._longest_window]
        for k in stale:
            del self._hits[k]

    async def hit(self, key: str, limit: int, window: timedelta) -> RateLimitDecision:
        if limit <= 0:
            raise ValueError("limit must be positive")
        now = self._clock()
        span = window.total_seconds()
        self._longest_window = max(self._longest_window, span)
        self._sweep(now)
        log = self._hits.setdefault(key, deque())
        while log and log[0] <= now - span:
            log.popleft()
        if len(log) >= limit:
            retry_after = log[len(log) - limit] + span - now
            return RateLimitDecision(False, limit, 0, max(retry_after, 0.0))
        log.append(now)
        return RateLimitDecision(True, limit, limit - len(log), 0.0)


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def limit_for(kind: Kind, settings: Settings) -> int:
    if kind == "read":
        return settings.rate_limit_reads_per_min
    return settings.rate_limit_mutations_per_min


def client_key(kind: Kind, uid: str | None, request: Request) -> str:
    if uid is not None:
        return f"{kind}:uid:{uid}"
    host = request.client.host if request.client is not None else UNKNOWN_CLIENT
    return f"{kind}:ip:{host}"


def rate_limited(kind: Kind) -> Callable[..., Awaitable[None]]:
    """Dependency factory: ``Depends(rate_limited("read"))`` or ``("mutation")``."""
    if kind not in ("read", "mutation"):
        raise ValueError(f"unknown rate limit kind: {kind!r}")

    async def dependency(
        request: Request,
        principal: OptionalPrincipal,
        limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    ) -> None:
        settings: Settings = request.app.state.settings
        limit = limit_for(kind, settings)
        key = client_key(kind, principal.uid if principal else None, request)
        decision = await limiter.hit(key, limit, WINDOW)
        if not decision.allowed:
            raise TooManyRequests(
                f"Rate limit of {limit} {kind} requests per minute exceeded.",
                retry_after=math.ceil(decision.retry_after),
            )

    dependency.__name__ = f"rate_limited_{kind}"
    return dependency
