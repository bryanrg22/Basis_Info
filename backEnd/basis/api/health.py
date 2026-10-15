"""``GET /health``: liveness, build identity, and dependency checks (S1_SPEC section 5).

* No dependency configured (no ``app.state.db_sessionmaker`` and no
  ``app.state.workflows``, e.g. unit tests or a bare local run): 200 with the build
  identity only; there is no ``checks`` member.
* Database configured: ``SELECT 1`` on a pooled connection, bounded by
  :data:`DB_CHECK_TIMEOUT_S`. Success: 200 with ``checks.db = {"status": "ok",
  "latency_ms": ...}``. Failure or timeout: 503 ``application/problem+json`` with the same
  ``checks`` member (``"status": "fail"``) and ``Retry-After``; the cause is logged, not
  returned, since the endpoint is unauthenticated.
* Temporal configured (S2): ``DescribeNamespace`` bounded by
  :data:`TEMPORAL_CHECK_TIMEOUT_S`, reported as ``checks.temporal``. A Temporal failure
  is reported but does **not** make the answer 503: decisions keep working through the
  outbox while Temporal is down (the relay delivers them later), so taking the API out of
  the load balancer would turn a Temporal outage into a full outage. Only the workflow
  routes answer 503 then. The two checks run concurrently.

No auth and no rate limit: load balancers and uptime checks call it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import Response

from basis import __version__
from basis.api.errors import PROBLEM_TYPE_BASE, problem_response
from basis.api.routes.common import problems
from basis.config import Settings
from basis.db.session import SESSIONMAKER_STATE_ATTR
from basis.temporal.service import WorkflowService

SERVICE_NAME: Final = "basis"
DB_CHECK_TIMEOUT_S: Final = 2.0
TEMPORAL_CHECK_TIMEOUT_S: Final = 2.0
RETRY_AFTER_S: Final = 5

logger = logging.getLogger(__name__)


class CheckResult(BaseModel):
    status: Literal["ok", "fail"]
    latency_ms: Annotated[float, Field(ge=0, description="Wall time of the check.")]


class HealthChecks(BaseModel):
    db: CheckResult | None = Field(default=None, description="Absent when no database is set.")
    temporal: CheckResult | None = Field(
        default=None,
        description="Absent when Temporal is not configured. A failure here does not make "
        "the answer 503 (decisions are queued in the outbox while Temporal is down).",
    )


class Health(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    git_sha: str
    env: str
    checks: HealthChecks | None = Field(
        default=None, description="Dependency checks; absent when none is configured."
    )


async def check_database(
    sessionmaker: async_sessionmaker[AsyncSession], timeout: float = DB_CHECK_TIMEOUT_S
) -> CheckResult:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout), sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        ok = True
    except Exception as exc:
        ok = False
        logger.warning("database health check failed", extra={"exception_type": type(exc).__name__})
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return CheckResult(status="ok" if ok else "fail", latency_ms=latency_ms)


async def check_temporal(
    workflows: WorkflowService, timeout: float = TEMPORAL_CHECK_TIMEOUT_S
) -> CheckResult:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout):
            await workflows.check()
        ok = True
    except Exception as exc:
        ok = False
        logger.warning("temporal health check failed", extra={"exception_type": type(exc).__name__})
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return CheckResult(status="ok" if ok else "fail", latency_ms=latency_ms)


async def _no_check() -> None:
    return None


router = APIRouter(tags=["ops"])


@router.get(
    "/health",
    response_model=Health,
    response_model_exclude_none=True,
    responses=problems(503),
    summary="Liveness, build identity, database and Temporal checks",
)
async def health(request: Request) -> Response:
    logger.debug("health check")
    settings: Settings = request.app.state.settings
    body = Health(
        status="ok",
        service=SERVICE_NAME,
        version=__version__,
        git_sha=settings.git_sha,
        env=settings.env,
    )
    sessionmaker: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, SESSIONMAKER_STATE_ATTR, None
    )
    workflows: WorkflowService | None = getattr(request.app.state, "workflows", None)
    if sessionmaker is None and workflows is None:
        return JSONResponse(body.model_dump(mode="json", exclude_none=True))
    db, temporal = await asyncio.gather(
        check_database(sessionmaker, DB_CHECK_TIMEOUT_S) if sessionmaker else _no_check(),
        check_temporal(workflows, TEMPORAL_CHECK_TIMEOUT_S) if workflows else _no_check(),
    )
    body.checks = HealthChecks(db=db, temporal=temporal)
    if db is not None and db.status == "fail":
        return problem_response(
            status=503,
            detail="The database check failed.",
            type_uri=PROBLEM_TYPE_BASE + "service-unavailable",
            instance=request.url.path,
            headers={"Retry-After": str(RETRY_AFTER_S)},
            extensions={"checks": body.checks.model_dump(mode="json", exclude_none=True)},
        )
    return JSONResponse(body.model_dump(mode="json", exclude_none=True))
