"""Shared by the activities that call providers (``extract_fields``, ``analyze_photo``).

* :func:`heartbeating` keeps heartbeating while a long provider call is awaited (an Azure
  DI analysis or a vision call can take longer than the 30 s heartbeat timeout).
* :func:`provider_failure` turns provider and storage errors into Temporal errors:
  transient ones (429, 5xx, timeouts) stay retryable; a bad response from Azure DI (4xx)
  or a missing file is a non-retryable ``ValidationError``; no provider configured is a
  non-retryable ``ProviderNotConfigured``.
* :func:`run_pk` finds the ``runs`` row so ``llm_calls`` can reference it.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.exceptions import ApplicationError

from basis.activities._support import heartbeat, validation_error, workflow_execution
from basis.db.repositories.run import RunRepository
from basis.llm.errors import ProviderBadResponse, ProviderError, ProviderNotConfigured

PROVIDER_UNAVAILABLE: Final = "ProviderUnavailable"
PROVIDER_NOT_CONFIGURED: Final = "ProviderNotConfigured"
HEARTBEAT_EVERY_S: Final = 5.0


@contextlib.asynccontextmanager
async def heartbeating(detail: str, every_s: float = HEARTBEAT_EVERY_S) -> AsyncIterator[None]:
    async def beat() -> None:
        while True:
            await asyncio.sleep(every_s)
            heartbeat(detail)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def provider_failure(error: ProviderError, what: str) -> ApplicationError:
    if isinstance(error, ProviderNotConfigured):
        return ApplicationError(
            f"{what}: {error}", type=PROVIDER_NOT_CONFIGURED, non_retryable=True
        )
    if isinstance(error, ProviderBadResponse):
        return validation_error(f"{what}: {error.provider} rejected the input: {error}")
    return ApplicationError(f"{what}: {error}", type=PROVIDER_UNAVAILABLE)


async def run_pk(session: AsyncSession) -> uuid.UUID | None:
    workflow_id, run_id = workflow_execution()
    run = await RunRepository(session).get_by_execution(workflow_id, run_id)
    return None if run is None else run.id
