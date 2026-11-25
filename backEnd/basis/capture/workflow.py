"""``PropertyCaptureWorkflow``: one durable execution per capture, on its own task queue
(``CAPTURE_TASK_QUEUE``), so the study workflow is untouched and needs no patch.

It runs the capture activity; when the activity answers ``waiting`` (the breaker is open, a cap
is spent, another capture holds the lease) it sleeps on a durable timer until the time the
activity named, at least 30 seconds, and runs it again. It gives up when the waits together
pass ``max_wait_hours`` (``E.CAPTURE_GAVE_UP``). When the activity itself fails after its
retries (a timeout, a dead worker, an error it did not classify) the ``fail_capture`` activity
marks the capture failed with its code, so nothing is left ``running``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError
from temporalio.exceptions import TimeoutError as ActivityTimeoutError

with workflow.unsafe.imports_passed_through():
    from basis.capture.contracts import (
        CAPTURE_ACTIVITY,
        CAPTURE_WORKFLOW,
        FAIL_ACTIVITY,
        CaptureInput,
        CaptureStep,
        FailInput,
    )
    from basis.errors.codes import E

MIN_WAIT: Final = timedelta(seconds=30)
CAPTURE_TIMEOUT: Final = timedelta(minutes=20)
CAPTURE_HEARTBEAT: Final = timedelta(minutes=3)
CAPTURE_RETRY: Final = RetryPolicy(
    initial_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    maximum_attempts=3,
)
FAIL_RETRY: Final = RetryPolicy(initial_interval=timedelta(seconds=5), maximum_attempts=10)


@workflow.defn(name=CAPTURE_WORKFLOW)
class PropertyCaptureWorkflow:
    @workflow.run
    async def run(self, request: CaptureInput) -> CaptureStep:
        waited = timedelta(0)
        limit = timedelta(hours=request.max_wait_hours)
        while True:
            try:
                step: CaptureStep = await workflow.execute_activity(
                    CAPTURE_ACTIVITY,
                    request,
                    result_type=CaptureStep,
                    start_to_close_timeout=CAPTURE_TIMEOUT,
                    heartbeat_timeout=CAPTURE_HEARTBEAT,
                    retry_policy=CAPTURE_RETRY,
                )
            except ActivityError as exc:
                timed_out = isinstance(exc.cause, ActivityTimeoutError)
                code = E.CAPTURE_TIMEOUT if timed_out else E.CAPTURE_BROWSER_CRASH
                return await self._fail(request, code, f"the capture step failed: {exc.cause!r}")
            if step.status != "waiting":
                return step
            assert step.resume_at is not None
            delay = max(step.resume_at - workflow.now(), MIN_WAIT)
            if waited + delay > limit:
                return await self._fail(
                    request, E.CAPTURE_GAVE_UP, f"waited {waited} and would wait {delay} more"
                )
            await workflow.sleep(delay)
            waited += delay

    async def _fail(self, request: CaptureInput, code: str, detail: str) -> CaptureStep:
        await workflow.execute_activity(
            FAIL_ACTIVITY,
            FailInput(capture_id=request.capture_id, code=code, detail=detail[:500]),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=FAIL_RETRY,
        )
        return CaptureStep(status="failed", code=code)
