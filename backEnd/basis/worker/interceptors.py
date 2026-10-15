"""Structured-logging context for activities.

Every log record emitted while an activity runs carries ``workflow_id``, ``run_id``,
``activity`` (type), ``activity_id`` and ``attempt``, bound through
``basis.obs.logging.bound`` (a ``ContextVar``, so concurrent activities never see each
other's fields). Workflow code logs through ``workflow.logger``, which the SDK already
enriches with the workflow id, run id and type and suppresses during replay.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
)

from basis.obs.logging import bound


class LoggingInterceptor(Interceptor):
    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _ActivityLogging(next)


class _ActivityLogging(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        info = activity.info()
        with bound(
            workflow_id=info.workflow_id,
            run_id=info.workflow_run_id,
            activity=info.activity_type,
            activity_id=info.activity_id,
            attempt=info.attempt,
        ):
            return await self.next.execute_activity(input)
