"""Shared activity plumbing: errors, idempotency keys, and the claim/complete runner."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Final

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.exceptions import ApplicationError

from basis.activities.deps import Deps
from basis.db.errors import NotVisibleError, StaleStateError
from basis.db.repositories.activity_result import COMPLETED, ActivityResultRepository
from basis.db.repositories.run import RunRepository
from basis.db.session import session_scope
from basis.errors.codes import E, code_in, tag
from basis.workflow.activity_options import BUDGET_EXCEEDED_ERROR, VALIDATION_ERROR

logger = logging.getLogger("basis.activities")

STUB_EMBEDDING_MODEL: Final = "stub"


class _NoOutput(BaseModel):
    """Stored for activities whose contract returns nothing."""


NO_OUTPUT: Final = _NoOutput()


def validation_error(message: str, code: str = E.WF_ACTIVITY_FAILED) -> ApplicationError:
    """Bad input: never retried (S2_SPEC section 4). ``code`` is the catalogue code
    (docs/ERROR_CODES.md), carried at the end of the message (:func:`basis.errors.codes.tag`)
    so it reaches ``activity_results.error``, the study's failure reason and the logs."""
    return ApplicationError(tag(code, message), type=VALIDATION_ERROR, non_retryable=True)


def budget_exceeded(message: str, code: str = E.BUDGET_PHOTO) -> ApplicationError:
    """The study's LLM budget cannot cover this call: never retried."""
    return ApplicationError(tag(code, message), type=BUDGET_EXCEEDED_ERROR, non_retryable=True)


def parse_uuid(value: str | None, what: str) -> uuid.UUID:
    if value is None:
        raise validation_error(f"{what} is required", E.WF_BAD_UUID)
    try:
        return uuid.UUID(value)
    except ValueError:
        raise validation_error(f"{what} {value!r} is not a UUID", E.WF_BAD_UUID) from None


def activity_key(explicit: str | None = None) -> str:
    """``{workflow_id}:{run_id}:{activity_id}``; an explicit key from the input wins."""
    if explicit:
        return explicit
    workflow_id, run_id = workflow_execution()
    return f"{workflow_id}:{run_id}:{activity.info().activity_id}"


def workflow_execution() -> tuple[str, str]:
    """``(workflow_id, run_id)`` of the workflow that scheduled this activity."""
    info = activity.info()
    if info.workflow_id is None or info.workflow_run_id is None:
        raise validation_error(
            "these activities only run on behalf of a workflow", E.WF_NOT_IN_WORKFLOW
        )
    return info.workflow_id, info.workflow_run_id


def heartbeat(*details: object) -> None:
    """Heartbeat (the SDK throttles sends to a fraction of the heartbeat timeout). Outside
    an activity (the reprice preview runs the same carry and pricing code in the API, in a
    transaction it rolls back) there is nothing to heartbeat to."""
    if activity.in_activity():
        activity.heartbeat(*details)


async def run_once[Out: BaseModel](
    deps: Deps,
    *,
    name: str,
    key: str,
    output_type: type[Out],
    work: Callable[[AsyncSession], Awaitable[Out]],
) -> Out:
    """Run ``work`` at most once to completion for ``key`` (claim -> run -> complete).

    See ``basis.db.repositories.activity_result`` for the protocol. ``work`` receives the
    session of the transaction that also marks the key completed, so its rows and the
    completion commit together. Repository errors that mean "this input can never work"
    (missing or stale study) become non-retryable ``ValidationError``; anything else is
    raised as is and retried by Temporal. Cancellation propagates after the transaction
    rolls back.
    """
    workflow_id, run_id = workflow_execution()
    async with session_scope(deps.sessionmaker) as session:
        run = await RunRepository(session).get_by_execution(workflow_id, run_id)
        if run is None:
            raise validation_error(
                f"no runs row for {workflow_id}/{run_id}; record_run_start must run first",
                E.WF_NO_RUN_ROW,
            )
        claimed = await ActivityResultRepository(session).claim(run.id, name, key)
        if claimed.status == COMPLETED:
            logger.info("activity already completed; returning stored output")
            return _stored(claimed.output_ref, output_type)
    try:
        async with session_scope(deps.sessionmaker) as session:
            results = ActivityResultRepository(session)
            locked = await results.lock(key)
            if locked is not None and locked.status == COMPLETED:
                return _stored(locked.output_ref, output_type)
            output = await work(session)
            await results.complete(key, output.model_dump_json())
            return output
    except asyncio.CancelledError:
        logger.info("activity cancelled; its transaction was rolled back")
        raise
    except StaleStateError as exc:
        await _record_failure(deps, key, exc)
        raise validation_error(str(exc), E.WF_STALE_TRANSITION) from exc
    except NotVisibleError as exc:
        await _record_failure(deps, key, exc)
        raise validation_error(str(exc), E.WF_STUDY_GONE) from exc
    except Exception as exc:
        await _record_failure(deps, key, exc)
        raise


async def run_once_no_output(
    deps: Deps, *, name: str, key: str, work: Callable[[AsyncSession], Awaitable[None]]
) -> None:
    """:func:`run_once` for activities whose contract returns ``None``."""

    async def wrapped(session: AsyncSession) -> _NoOutput:
        await work(session)
        return NO_OUTPUT

    await run_once(deps, name=name, key=key, output_type=_NoOutput, work=wrapped)


def _stored[Out: BaseModel](output_ref: str | None, output_type: type[Out]) -> Out:
    return output_type.model_validate_json(output_ref or "{}")


def failure_text(exc: BaseException) -> str:
    """``<ExceptionType>: <message> [code]`` for ``activity_results.error`` and the events:
    the code the message carries, else the one for an exception that is not an
    ApplicationError (a bug, a database error on an established connection)."""
    text = f"{type(exc).__name__}: {exc}"
    return text if code_in(text) else tag(E.WF_UNEXPECTED_ERROR, text)


async def _record_failure(deps: Deps, key: str, exc: BaseException) -> None:
    try:
        async with session_scope(deps.sessionmaker) as session:
            await ActivityResultRepository(session).fail(key, failure_text(exc))
    except Exception:  # the original error matters more than the bookkeeping
        logger.warning("could not record the activity failure", exc_info=True)
