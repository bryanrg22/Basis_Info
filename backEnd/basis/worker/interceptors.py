"""Structured-logging context and the timeline's step timings for activities.

Every log record emitted while an activity runs carries ``workflow_id``, ``run_id``,
``activity`` (type), ``activity_id`` and ``attempt``, bound through
``basis.obs.logging.bound`` (a ``ContextVar``, so concurrent activities never see each
other's fields). Workflow code logs through ``workflow.logger``, which the SDK already
enriches with the workflow id, run id and type and suppresses during replay.

Step timings (docs/api/TIMELINE_AND_AUDIT.md): the same interceptor records one
``study_events`` row per attempt that ends, ``activity.completed``, ``activity.failed`` (with
the error code, docs/ERROR_CODES.md) or ``activity.cancelled``, with the attempt's start on
the worker's clock and its duration measured monotonically, so a step's duration never
depends on two clocks. It is recorded here and not in ``run_once`` because several
activities fail before ``run_once`` runs (a provider error in ``analyze_photo``, an input
that does not parse) and those must be on the timeline too. Temporal's history cannot give
this: ``ActivityTaskStarted`` is written only when the attempts end, so per-attempt timing
exists nowhere else. The row is written in its own short transaction after the attempt,
never inside the activity's; a write that fails is logged and never fails the activity.

The study is the ``study_id`` of the activity's input (every activity input carries one);
an activity without it (none today) is timed in the log only.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
)

from basis.db.repositories.study_event import MAX_ERROR_CHARS, StudyEventRepository
from basis.db.session import session_scope
from basis.errors.codes import E, code_in
from basis.obs.logging import bound

logger = logging.getLogger(__name__)


class LoggingInterceptor(Interceptor):
    """Bind the activity's identity to the log context and record its timing."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._sessionmaker = sessionmaker

    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _ActivityLogging(next, self._sessionmaker)


def study_id_of(input: ExecuteActivityInput) -> uuid.UUID | None:
    """The ``study_id`` of the activity's (pydantic) input, when it has one."""
    for arg in input.args:
        value = getattr(arg, "study_id", None)
        if isinstance(value, str):
            try:
                return uuid.UUID(value)
            except ValueError:
                return None
    return None


class _ActivityLogging(ActivityInboundInterceptor):
    def __init__(
        self,
        next: ActivityInboundInterceptor,
        sessionmaker: async_sessionmaker[AsyncSession] | None,
    ) -> None:
        super().__init__(next)
        self._sessionmaker = sessionmaker

    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        info = activity.info()
        with bound(
            workflow_id=info.workflow_id,
            run_id=info.workflow_run_id,
            activity=info.activity_type,
            activity_id=info.activity_id,
            attempt=info.attempt,
        ):
            started_at = datetime.now(UTC)
            clock = time.monotonic()
            try:
                result = await self.next.execute_activity(input)
            except asyncio.CancelledError:
                await self._record(
                    input, "activity.cancelled", started_at, clock, code=E.WF_ACTIVITY_CANCELLED
                )
                raise
            except BaseException as exc:
                message = str(exc)
                code = code_in(message) or E.WF_UNEXPECTED_ERROR
                error = f"{type(exc).__name__}: {message}"[:MAX_ERROR_CHARS]
                await self._record(input, "activity.failed", started_at, clock, code, error)
                raise
            await self._record(input, "activity.completed", started_at, clock)
            return result

    async def _record(
        self,
        input: ExecuteActivityInput,
        event_type: str,
        started_at: datetime,
        clock: float,
        code: str | None = None,
        error: str | None = None,
    ) -> None:
        duration_ms = int((time.monotonic() - clock) * 1000)
        logger.info("activity %s", event_type.split(".", 1)[1], extra={"duration_ms": duration_ms})
        if self._sessionmaker is None:
            return
        study_id = study_id_of(input)
        if study_id is None:
            return
        info = activity.info()
        try:
            async with session_scope(self._sessionmaker) as session:
                await StudyEventRepository(session).record(
                    study_id,
                    event_type,
                    actor="system",
                    workflow_id=info.workflow_id,
                    run_id=info.workflow_run_id,
                    activity=info.activity_type,
                    activity_id=info.activity_id,
                    attempt=info.attempt,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    code=code,
                    detail={"error": error} if error else None,
                )
        except Exception:  # the activity's outcome matters more than the bookkeeping
            logger.warning("could not record the activity event", exc_info=True)
