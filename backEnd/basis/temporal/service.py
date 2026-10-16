"""What the API asks of Temporal, behind a protocol (S2_SPEC section 7).

:class:`WorkflowService` is the API's only view of Temporal: start a study's workflow,
describe it, query its progress, ask it to cancel, and check that Temporal answers.
:class:`TemporalWorkflowService` implements it with a ``temporalio`` client; API tests use
an in-memory fake (``tests/workflowtest_support.py``), so they run without a server.

Decisions are deliberately not here: the API never sends them to Temporal. It writes the
review and an outbox row in one transaction, and ``basis.relay`` delivers the update.

Errors are reduced to three exceptions the routes map to problems:
:class:`WorkflowNotFoundError` (404), :class:`WorkflowNotRunningError` (409) and
:class:`WorkflowUnavailableError` (503). Every call is bounded by ``call_timeout``: the
SDK retries ``UNAVAILABLE`` internally for about ten seconds (measured against the dev
server), which is too long to hold an API request.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Protocol

from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client, WorkflowHandle, WorkflowQueryFailedError
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from basis.temporal.names import CANCEL_SIGNAL, STATUS_QUERY, STUDY_WORKFLOW, study_workflow_id
from basis.workflow.contracts import StudyStatusView, StudyWorkflowInput

DEFAULT_CALL_TIMEOUT_S: Final = 5.0
# Closed executions that did not complete. REJECT_DUPLICATE never re-runs them, so a start
# must not answer with one as if the study were running (EVAL_REPORT section 22).
ENDED_UNSUCCESSFULLY: Final = frozenset({"FAILED", "TERMINATED", "TIMED_OUT", "CANCELED"})


class WorkflowServiceError(Exception):
    """Base class; the message is safe to show to an API client."""


class WorkflowNotFoundError(WorkflowServiceError):
    """Temporal has no workflow with that id."""


class WorkflowNotRunningError(WorkflowServiceError):
    """The workflow exists but has closed (completed, failed, cancelled, terminated)."""


class WorkflowUnavailableError(WorkflowServiceError):
    """Temporal could not be reached or did not answer in time."""


@dataclass(frozen=True, slots=True)
class WorkflowRef:
    workflow_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class WorkflowDescription:
    workflow_id: str
    run_id: str
    status: str  # RUNNING, COMPLETED, FAILED, CANCELED, TERMINATED, CONTINUED_AS_NEW, TIMED_OUT


def ensure_not_ended_unsuccessfully(described: WorkflowDescription) -> None:
    """Raise :class:`WorkflowNotRunningError` when ``described`` closed without completing:
    starting the study again would otherwise answer 202 with an execution that is over."""
    if described.status in ENDED_UNSUCCESSFULLY:
        raise WorkflowNotRunningError(
            f"Workflow {described.workflow_id} ended {described.status.lower()} and is not "
            "started again; its run is kept for investigation."
        )


class WorkflowService(Protocol):
    async def start_study(self, workflow_input: StudyWorkflowInput) -> WorkflowRef:
        """Start ``study-{id}``; if it already exists (running or completed), return it;
        ``WorkflowNotRunningError`` if it ended any other way (failed, terminated, ...)."""
        ...

    async def describe(self, workflow_id: str) -> WorkflowDescription: ...

    async def query_status(self, workflow_id: str) -> StudyStatusView: ...

    async def cancel(self, workflow_id: str, reason: str) -> None:
        """Signal ``cancel_study``; ``WorkflowNotRunningError`` if it has closed."""
        ...

    async def check(self) -> None:
        """Raise if Temporal does not answer (health check)."""
        ...


class TemporalWorkflowService:
    def __init__(
        self,
        client: Client,
        *,
        task_queue: str,
        call_timeout: float = DEFAULT_CALL_TIMEOUT_S,
    ) -> None:
        self.client = client
        self.task_queue = task_queue
        self.call_timeout = call_timeout

    @property
    def _rpc_timeout(self) -> timedelta:
        return timedelta(seconds=self.call_timeout)

    async def start_study(self, workflow_input: StudyWorkflowInput) -> WorkflowRef:
        workflow_id = study_workflow_id(workflow_input.study_id)
        try:
            async with asyncio.timeout(self.call_timeout):
                handle: WorkflowHandle[object, object] = await self.client.start_workflow(
                    STUDY_WORKFLOW,
                    workflow_input,
                    id=workflow_id,
                    task_queue=self.task_queue,
                    # At most one execution per study: a second start while it runs
                    # returns the running one; a closed one is never re-run by accident.
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    rpc_timeout=self._rpc_timeout,
                )
        except WorkflowAlreadyStartedError:
            # REJECT_DUPLICATE: the study's workflow has already closed. Starting is
            # idempotent, so answer with the execution that exists if it completed.
            described = await self.describe(workflow_id)
            ensure_not_ended_unsuccessfully(described)
            return WorkflowRef(workflow_id=workflow_id, run_id=described.run_id)
        except Exception as exc:
            raise _unavailable(exc) from exc
        run_id = handle.result_run_id or handle.run_id
        if run_id is None:  # pragma: no cover - the SDK always sets it after a start
            raise WorkflowUnavailableError("Temporal did not return a run id.")
        return WorkflowRef(workflow_id=workflow_id, run_id=run_id)

    async def describe(self, workflow_id: str) -> WorkflowDescription:
        handle = self.client.get_workflow_handle(workflow_id)
        try:
            async with asyncio.timeout(self.call_timeout):
                description = await handle.describe(rpc_timeout=self._rpc_timeout)
        except Exception as exc:
            raise _mapped(exc, workflow_id) from exc
        status = description.status.name if description.status is not None else "UNKNOWN"
        return WorkflowDescription(
            workflow_id=workflow_id, run_id=description.run_id, status=status
        )

    async def query_status(self, workflow_id: str) -> StudyStatusView:
        handle = self.client.get_workflow_handle(workflow_id)
        try:
            async with asyncio.timeout(self.call_timeout):
                view: StudyStatusView = await handle.query(
                    STATUS_QUERY, result_type=StudyStatusView, rpc_timeout=self._rpc_timeout
                )
        except WorkflowQueryFailedError as exc:
            raise WorkflowUnavailableError(f"The workflow could not answer: {exc.message}") from exc
        except Exception as exc:
            raise _mapped(exc, workflow_id) from exc
        return view

    async def cancel(self, workflow_id: str, reason: str) -> None:
        handle = self.client.get_workflow_handle(workflow_id)
        try:
            async with asyncio.timeout(self.call_timeout):
                await handle.signal(CANCEL_SIGNAL, reason, rpc_timeout=self._rpc_timeout)
        except Exception as exc:
            mapped = _mapped(exc, workflow_id)
            if not isinstance(mapped, WorkflowNotFoundError):
                raise mapped from exc
            # NOT_FOUND also means "already closed" for a signal; tell the two apart.
            described = await self.describe(workflow_id)
            raise WorkflowNotRunningError(
                f"Workflow {workflow_id} is {described.status.lower()}, not running."
            ) from exc

    async def check(self) -> None:
        try:
            async with asyncio.timeout(self.call_timeout):
                await self.client.workflow_service.describe_namespace(
                    DescribeNamespaceRequest(namespace=self.client.namespace),
                    timeout=self._rpc_timeout,
                )
        except Exception as exc:
            raise _unavailable(exc) from exc


def _unavailable(exc: BaseException) -> WorkflowUnavailableError:
    if isinstance(exc, TimeoutError):
        return WorkflowUnavailableError("Temporal did not answer in time.")
    return WorkflowUnavailableError(f"Temporal is unavailable ({type(exc).__name__}).")


def _mapped(exc: BaseException, workflow_id: str) -> WorkflowServiceError:
    if isinstance(exc, RPCError) and exc.status == RPCStatusCode.NOT_FOUND:
        return WorkflowNotFoundError(f"Temporal has no workflow {workflow_id}.")
    return _unavailable(exc)
