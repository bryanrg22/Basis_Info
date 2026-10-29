"""Shared by the activities that call providers (``extract_fields``, ``analyze_photo``).

* :func:`heartbeating` keeps heartbeating while a long provider call is awaited (an Azure
  DI analysis or a vision call can take longer than the 30 s heartbeat timeout).
* :func:`provider_failure` turns provider and storage errors into Temporal errors:
  transient ones (429, 5xx, timeouts) stay retryable; a bad response from Azure DI (4xx),
  an input the provider refused (:class:`~basis.llm.errors.ProviderRequestRejected`: an
  image it cannot process, a content-filter block) or a missing file is a non-retryable
  ``ValidationError`` naming the reason, so the workflow fails that item (flags the photo);
  a provider account error (no credit or quota, a bad key) is a non-retryable
  ``ProviderAccountError`` naming the provider, so the workflow fails the stage with that
  reason and waits for a retry (EVAL_REPORT 26.12). A configuration error
  (:class:`~basis.llm.errors.ProviderConfigError`: an unknown model or deployment, a
  parameter the model refuses, a model no configured provider serves) takes the same type
  and the same stop, with a message that names the configuration instead of the account.
  (An activity with no provider at all raises its own non-retryable
  ``ProviderNotConfigured``, which the workflow flags; the worker refuses to start that way
  outside dev and test.)
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
from basis.errors.codes import E, tag
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderConfigError,
    ProviderError,
    ProviderRequestRejected,
)
from basis.workflow.activity_options import PROVIDER_ACCOUNT_ERROR

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
    if isinstance(error, ProviderConfigError):
        # Type ProviderAccountError on purpose: it is the type the workflow stops the study
        # on (provider-account-stop-v1) and never retries, so no workflow change (and no
        # patch) is needed; the message says it is the configuration, not the account.
        return ApplicationError(
            tag(E.PROVIDER_CONFIG, f"{what}: {error}, then retry the study"),
            type=PROVIDER_ACCOUNT_ERROR,
            non_retryable=True,
        )
    if isinstance(error, ProviderAccountError):
        return ApplicationError(
            tag(E.PROVIDER_ACCOUNT, f"{what}: {error}; fix the account, then retry the study"),
            type=PROVIDER_ACCOUNT_ERROR,
            non_retryable=True,
        )
    if isinstance(error, ProviderRequestRejected):
        return validation_error(f"{what}: {error}", E.PROVIDER_REQUEST_REJECTED)
    if isinstance(error, ProviderBadResponse):
        return validation_error(
            f"{what}: {error.provider} rejected the input: {error}", E.PROVIDER_BAD_RESPONSE
        )
    return ApplicationError(
        tag(E.PROVIDER_UNAVAILABLE, f"{what}: {error}"), type=PROVIDER_UNAVAILABLE
    )


async def run_pk(session: AsyncSession) -> uuid.UUID | None:
    workflow_id, run_id = workflow_execution()
    run = await RunRepository(session).get_by_execution(workflow_id, run_id)
    return None if run is None else run.id
