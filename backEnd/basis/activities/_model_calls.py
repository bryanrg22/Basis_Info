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
* :func:`run_pk` finds the ``runs`` row so ``llm_calls`` can reference it;
  :func:`call_origin` adds the study's stage and the activity attempt, which the ledger
  records with every paid call (:class:`CallOrigin`).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.exceptions import ApplicationError

from basis.activities._support import heartbeat, validation_error, workflow_execution
from basis.db.repositories.run import RunRepository
from basis.db.repositories.study import StudyRepository
from basis.errors.codes import E, tag
from basis.llm.call import CallContext
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderConfigError,
    ProviderError,
    ProviderRequestRejected,
)
from basis.llm.provider import Budget
from basis.llm.recorder import Recorder
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


@dataclass(frozen=True, slots=True)
class CallOrigin:
    """Where a paid call is made from, for the ledger: the ``runs`` row, the study's stage
    at the time and the activity attempt (:class:`basis.llm.call.CallContext`)."""

    run_id: uuid.UUID | None
    stage: str | None
    attempt: int | None

    def context(
        self,
        study_id: uuid.UUID,
        activity_name: str,
        budget: Budget,
        recorder: Recorder,
        *,
        revision: int | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
    ) -> CallContext:
        return CallContext(
            study_id=study_id,
            run_id=self.run_id,
            activity=activity_name,
            budget=budget,
            recorder=recorder,
            stage=self.stage,
            revision=revision,
            activity_attempt=self.attempt,
            entity_type=entity_type,
            entity_id=entity_id,
        )


def activity_attempt() -> int | None:
    """The Temporal attempt of the running activity (``None`` outside one)."""
    return activity.info().attempt if activity.in_activity() else None


async def call_origin(session: AsyncSession, study_id: uuid.UUID) -> CallOrigin:
    """:func:`run_pk`, the study's current stage and the attempt, in one read. Outside an
    activity (the reprice preview, which makes no call) there is no run."""
    study = await StudyRepository(session).get(study_id)
    return CallOrigin(
        run_id=await run_pk(session) if activity.in_activity() else None,
        stage=None if study is None else str(study.current_stage.value),
        attempt=activity_attempt(),
    )
