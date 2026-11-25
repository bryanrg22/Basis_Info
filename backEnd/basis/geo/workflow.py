"""``PublicGeoWorkflow``: one durable execution per gather, on its own task queue
(``GEO_TASK_QUEUE``), so neither the study workflow nor the capture workflow changes.

It runs the gather activity (retried with backoff: the public servers' own failures are handled
per request inside it, so an activity retry means the worker died or the step broke); when the
activity fails after its retries, ``fail_public_geo`` marks the gather failed with
``E.GEO_GATHER_FAILED`` so nothing is left ``running``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from basis.errors.codes import E
    from basis.geo.contracts import (
        FAIL_ACTIVITY,
        GATHER_ACTIVITY,
        GEO_WORKFLOW,
        FailInput,
        GatherInput,
        GatherResult,
    )

GATHER_TIMEOUT: Final = timedelta(minutes=30)
GATHER_HEARTBEAT: Final = timedelta(minutes=5)
GATHER_RETRY: Final = RetryPolicy(
    initial_interval=timedelta(seconds=30), backoff_coefficient=2.0, maximum_attempts=3
)
FAIL_RETRY: Final = RetryPolicy(initial_interval=timedelta(seconds=5), maximum_attempts=10)


@workflow.defn(name=GEO_WORKFLOW)
class PublicGeoWorkflow:
    @workflow.run
    async def run(self, request: GatherInput) -> GatherResult:
        try:
            result: GatherResult = await workflow.execute_activity(
                GATHER_ACTIVITY,
                request,
                result_type=GatherResult,
                start_to_close_timeout=GATHER_TIMEOUT,
                heartbeat_timeout=GATHER_HEARTBEAT,
                retry_policy=GATHER_RETRY,
            )
            return result
        except ActivityError as exc:
            await workflow.execute_activity(
                FAIL_ACTIVITY,
                FailInput(
                    gather_id=request.gather_id,
                    code=E.GEO_GATHER_FAILED,
                    detail=f"the gather step failed: {exc.cause!r}"[:500],
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=FAIL_RETRY,
            )
            return GatherResult(status="failed")
