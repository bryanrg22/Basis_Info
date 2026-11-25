"""The capture workflow's activities: run the capture (:class:`~basis.capture.runner.
CaptureRunner`) and mark a capture failed when the workflow gives up on it."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from temporalio import activity

from basis.capture import messages
from basis.capture.contracts import (
    CAPTURE_ACTIVITY,
    FAIL_ACTIVITY,
    CaptureInput,
    CaptureStep,
    FailInput,
)
from basis.capture.runner import CaptureRunner, Done, Failed, Waiting
from basis.capture.store import CaptureStore, Incident

RunnerFactory = Callable[[Callable[[str], None]], CaptureRunner]


class CaptureActivities:
    def __init__(self, runner: RunnerFactory, store: CaptureStore) -> None:
        self._runner = runner
        self._store = store

    @activity.defn(name=CAPTURE_ACTIVITY)
    async def capture_property(self, request: CaptureInput) -> CaptureStep:
        runner = self._runner(lambda step: activity.heartbeat(step))
        outcome = await runner.run(uuid.UUID(request.capture_id))
        if isinstance(outcome, Done):
            return CaptureStep(status="done", views=outcome.views)
        if isinstance(outcome, Waiting):
            return CaptureStep(status="waiting", resume_at=outcome.until, code=outcome.code)
        assert isinstance(outcome, Failed)
        return CaptureStep(status="failed", code=outcome.code)

    @activity.defn(name=FAIL_ACTIVITY)
    async def fail_capture(self, request: FailInput) -> None:
        capture_id = uuid.UUID(request.capture_id)
        row = await self._store.capture(capture_id)
        if row is None or row.status in ("succeeded", "failed"):
            return
        await self._store.fail(
            capture_id,
            reason=messages.failed(request.code),
            incident=Incident(
                code=request.code,
                at=datetime.now(UTC),
                study_id=row.study_id,
                capture_id=capture_id,
                detail=request.detail,
            ),
        )
