"""How the API starts a capture's workflow: a protocol of its own (the API's
:class:`~basis.temporal.service.WorkflowService` stays the study's), implemented with the
API's Temporal client; API tests pass a fake to ``create_app``."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Protocol

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from basis.capture.contracts import CAPTURE_WORKFLOW, CaptureInput, capture_workflow_id


class CaptureStartError(RuntimeError):
    """Temporal did not take the start (unreachable, timed out)."""


class CaptureStarter(Protocol):
    async def start(self, capture_id: str) -> str:
        """Start the capture's workflow; its workflow id."""
        ...


class TemporalCaptureStarter:
    def __init__(
        self,
        client: Client,
        *,
        task_queue: str,
        max_wait_hours: float,
        call_timeout_s: float = 5.0,
    ) -> None:
        self._client = client
        self._task_queue = task_queue
        self._max_wait_hours = max_wait_hours
        self._timeout = call_timeout_s

    async def start(self, capture_id: str) -> str:
        workflow_id = capture_workflow_id(capture_id)
        try:
            async with asyncio.timeout(self._timeout):
                await self._client.start_workflow(
                    CAPTURE_WORKFLOW,
                    CaptureInput(capture_id=capture_id, max_wait_hours=self._max_wait_hours),
                    id=workflow_id,
                    task_queue=self._task_queue,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    rpc_timeout=timedelta(seconds=self._timeout),
                )
        except Exception as exc:
            raise CaptureStartError(type(exc).__name__) from exc
        return workflow_id
