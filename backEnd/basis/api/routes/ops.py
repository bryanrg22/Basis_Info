"""``GET /v1/ops/health``: internal system health for platform operators
(docs/api/OPS_HEALTH.md).

* Access: a verified Firebase ID token whose uid is in ``OPS_OPERATOR_UIDS``. A firm role,
  owner included, never grants it. 401 without a usable token, 403 ``not-operator``
  otherwise; neither carries diagnostics. The user is not upserted (no database write), so
  the route answers while the database is down.
* Rate limits: the read limit by uid (every ``/v1`` read), then
  :data:`OPS_RATE_LIMIT_PER_MIN` per operator.
* The document is computed at most once per :data:`CACHE_TTL_S` per process (concurrent
  requests share one evaluation); a cached document is served as it was, its ``checked_at``
  values unchanged. Every check is bounded and fails on its own: the answer is partial,
  never an error, when one dependency fails.
* 200, or 503 with the same body when a required service is ``down``.
* Read-only and free: no model inference, Replicate prediction or Azure DI analysis. The
  provider evidence comes from the worker's heartbeat (its metadata probes) and the ledger.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.auth import CurrentPrincipal
from basis.api.errors import Forbidden, TooManyRequests
from basis.api.health import check_database, check_temporal
from basis.api.ratelimit import RateLimiter, get_rate_limiter, rate_limited
from basis.api.routes.common import API_PREFIX, problems
from basis.api.schemas.ops import OpsHealthOut, ServiceHealthOut
from basis.auth.firebase import EMULATOR_ENV_VAR, Principal
from basis.config import Settings
from basis.db.session import SESSIONMAKER_STATE_ATTR
from basis.errors.codes import E
from basis.ops import evidence
from basis.ops.services import Inputs, compose, overall_failed
from basis.storage import GcsStorage, Storage

CACHE_TTL_S: Final = 10.0
OPS_RATE_LIMIT_PER_MIN: Final = 30
OPS_POLLERS_STATE_ATTR: Final = "ops_pollers"
DETAIL_NOT_OPERATOR: Final = "This account is not a platform operator."

logger = logging.getLogger(__name__)

router = APIRouter(prefix=API_PREFIX, tags=["ops"])


async def require_operator(request: Request, principal: CurrentPrincipal) -> Principal:
    settings: Settings = request.app.state.settings
    if principal.uid not in settings.ops_operator_uids:
        logger.info("ops health refused: not an operator")
        raise Forbidden(DETAIL_NOT_OPERATOR, type_suffix="not-operator", code=E.API_NOT_OPERATOR)
    return principal


async def ops_rate_limit(
    operator: Annotated[Principal, Depends(require_operator)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> Principal:
    decision = await limiter.hit(
        f"ops:uid:{operator.uid}", OPS_RATE_LIMIT_PER_MIN, timedelta(minutes=1)
    )
    if not decision.allowed:
        raise TooManyRequests(
            f"Rate limit of {OPS_RATE_LIMIT_PER_MIN} health reads per minute exceeded.",
            retry_after=int(decision.retry_after) + 1,
        )
    return operator


class OpsHealthCache:
    """The last document for :data:`CACHE_TTL_S`; one evaluation at a time."""

    def __init__(self, ttl_s: float = CACHE_TTL_S, clock: Callable[[], float] = time.monotonic):
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self._value: tuple[float, dict[str, Any], int] | None = None
        self.evaluations = 0

    async def get(
        self, compute: Callable[[], Awaitable[tuple[dict[str, Any], int]]]
    ) -> tuple[dict[str, Any], int]:
        async with self._lock:
            if self._value is not None and self._clock() - self._value[0] < self._ttl_s:
                return self._value[1], self._value[2]
            body, status = await compute()
            self.evaluations += 1
            self._value = (self._clock(), body, status)
            return body, status


def ops_cache(request: Request) -> OpsHealthCache:
    cache: OpsHealthCache | None = getattr(request.app.state, "ops_health_cache", None)
    if cache is None:
        cache = OpsHealthCache()
        request.app.state.ops_health_cache = cache
    return cache


def _as_outcome(result: Any, error: str) -> evidence.Outcome[bool]:
    ok = result.status == "ok"
    return evidence.Outcome(ok, None if ok else error, datetime.now(UTC), result.latency_ms)


async def _none() -> None:
    return None


async def evaluate(request: Request) -> tuple[dict[str, Any], int]:
    app = request.app
    settings: Settings = app.state.settings
    sessionmaker: async_sessionmaker[AsyncSession] | None = getattr(
        app.state, SESSIONMAKER_STATE_ATTR, None
    )
    workflows = getattr(app.state, "workflows", None)
    pollers = getattr(app.state, OPS_POLLERS_STATE_ATTR, None)
    client = getattr(app.state, "temporal", None)
    if pollers is None and client is not None:
        pollers = evidence.temporal_pollers(client, settings.temporal_task_queue)
    storage: Storage = app.state.storage
    verified_at = datetime.now(UTC)
    db, temporal, poller_outcome, state, ledger, stored = await asyncio.gather(
        check_database(sessionmaker) if sessionmaker is not None else _none(),
        check_temporal(workflows) if workflows is not None else _none(),
        evidence.read_pollers(pollers) if pollers is not None else _none(),
        evidence.read_state(sessionmaker) if sessionmaker is not None else _none(),
        evidence.read_ledger(sessionmaker) if sessionmaker is not None else _none(),
        evidence.probe_storage(storage, app.state.storage_bucket),
    )
    services = compose(
        Inputs(
            now=datetime.now(UTC),
            api_build=settings.git_sha,
            api_env=settings.env,
            db=None if db is None else _as_outcome(db, "SELECT 1 failed or timed out"),
            temporal=None
            if temporal is None
            else _as_outcome(temporal, "DescribeNamespace failed or timed out"),
            pollers=poller_outcome,
            state=state,
            ledger=ledger,
            storage=stored,
            storage_adapter="Google Cloud Storage" if isinstance(storage, GcsStorage) else "local",
            firebase_adapter="emulator" if os.environ.get(EMULATOR_ENV_VAR) else "Firebase Auth",
            firebase_project=settings.firebase_project_id,
            token_verified_at=verified_at,
            task_queue=settings.temporal_task_queue,
        )
    )
    body = OpsHealthOut(services=[ServiceHealthOut.of(s) for s in services])
    return body.model_dump(mode="json"), 503 if overall_failed(services) else 200


@router.get(
    "/ops/health",
    response_model=OpsHealthOut,
    summary="Internal system health for platform operators",
    description="Every infrastructure dependency, provider and processing capability with "
    "the evidence behind its status. Platform operators only (`OPS_OPERATOR_UIDS`); a firm "
    "role never grants access. Read-only: no inference, prediction or document analysis. "
    "Cached for 10 s per API process. See docs/api/OPS_HEALTH.md.",
    responses={
        503: {
            "model": OpsHealthOut,
            "description": "The same document, when a required service is `down`.",
        },
        **problems(401, 403, 422, 429),
    },
    dependencies=[Depends(rate_limited("read"))],
)
async def ops_health(
    request: Request, operator: Annotated[Principal, Depends(ops_rate_limit)]
) -> JSONResponse:
    body, status = await ops_cache(request).get(lambda: evaluate(request))
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})
