"""``TemporalWorkflowService`` error mapping and the API's Temporal wiring (no server).

A stub stands in for the ``temporalio`` client so each SDK error can be raised on
demand; the same service runs against a real dev server in
``tests/integration/api/test_start_to_decision.py`` and
``tests/failure/test_temporal_unavailable.py``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowQueryFailedError
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from basis.api.app import create_app
from basis.api.schemas.workflow import WorkflowProgress
from basis.config import Settings
from basis.temporal.client import temporal_configured
from basis.temporal.names import (
    CANCEL_SIGNAL,
    DECIDE_UPDATE,
    STATUS_QUERY,
    STUDY_WORKFLOW,
    decision_update_id,
    study_workflow_id,
)
from basis.temporal.service import (
    TemporalWorkflowService,
    WorkflowNotFoundError,
    WorkflowNotRunningError,
    WorkflowUnavailableError,
)
from basis.workflow.contracts import StudyStatusView, StudyWorkflowInput

INPUT = StudyWorkflowInput(
    study_id="s1", org_id="o1", input_revision=1, workflow_version="sha", budget_usd=25.0
)
NOT_FOUND = RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")
UNAVAILABLE = RPCError("tcp connect error", RPCStatusCode.UNAVAILABLE, b"")


class Handle:
    def __init__(self, client: StubClient, workflow_id: str) -> None:
        self.client = client
        self.workflow_id = workflow_id
        self.run_id = "run-1"
        self.result_run_id = "run-1"

    async def describe(self, **kwargs: Any) -> Any:
        return self.client.act("describe") or SimpleNamespace(
            run_id="run-1", status=self.client.status
        )

    async def query(self, name: str, **kwargs: Any) -> Any:
        self.client.calls.append(("query", name, kwargs.get("result_type")))
        return self.client.act("query") or StudyStatusView(stage="extracting", revision=1)

    async def signal(self, name: str, arg: Any, **kwargs: Any) -> None:
        self.client.calls.append(("signal", name, arg))
        self.client.act("signal")


class StubClient:
    namespace = "default"

    def __init__(self, **errors: BaseException) -> None:
        self.errors = errors
        self.calls: list[tuple[Any, ...]] = []
        self.status: WorkflowExecutionStatus | None = WorkflowExecutionStatus.RUNNING
        self.delay = 0.0
        self.workflow_service = SimpleNamespace(describe_namespace=self.describe_namespace)

    def act(self, name: str) -> Any:
        error = self.errors.get(name)
        if error is not None:
            raise error
        return None

    async def start_workflow(self, workflow: str, arg: Any, **kwargs: Any) -> Handle:
        self.calls.append(("start", workflow, arg, kwargs))
        await asyncio.sleep(self.delay)
        self.act("start")
        return Handle(self, kwargs["id"])

    def get_workflow_handle(self, workflow_id: str) -> Handle:
        return Handle(self, workflow_id)

    async def describe_namespace(self, request: Any, **kwargs: Any) -> None:
        self.calls.append(("describe_namespace", request.namespace))
        self.act("check")


def service(client: StubClient, **kwargs: Any) -> TemporalWorkflowService:
    return TemporalWorkflowService(client, task_queue="basis-studies", **kwargs)  # type: ignore[arg-type]


def test_names() -> None:
    assert (STUDY_WORKFLOW, DECIDE_UPDATE, STATUS_QUERY, CANCEL_SIGNAL) == (
        "StudyWorkflow",
        "decide",
        "status",
        "cancel_study",
    )
    assert study_workflow_id("abc") == "study-abc"
    assert decision_update_id("r1") == "decision-r1"


async def test_start_uses_the_id_policies() -> None:
    client = StubClient()
    ref = await service(client).start_study(INPUT)
    assert (ref.workflow_id, ref.run_id) == ("study-s1", "run-1")
    (_, workflow, arg, kwargs) = client.calls[0]
    assert (workflow, arg) == ("StudyWorkflow", INPUT)
    assert kwargs["id"] == "study-s1" and kwargs["task_queue"] == "basis-studies"
    assert kwargs["id_conflict_policy"] is WorkflowIDConflictPolicy.USE_EXISTING
    assert kwargs["id_reuse_policy"] is WorkflowIDReusePolicy.REJECT_DUPLICATE


async def test_start_of_a_closed_workflow_returns_it() -> None:
    client = StubClient(start=WorkflowAlreadyStartedError("study-s1", "StudyWorkflow"))
    client.status = WorkflowExecutionStatus.COMPLETED
    ref = await service(client).start_study(INPUT)
    assert (ref.workflow_id, ref.run_id) == ("study-s1", "run-1")


@pytest.mark.parametrize(
    "status",
    [
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.TERMINATED,
        WorkflowExecutionStatus.TIMED_OUT,
        WorkflowExecutionStatus.CANCELED,
    ],
)
async def test_start_of_a_workflow_that_ended_unsuccessfully_is_not_running(
    status: WorkflowExecutionStatus,
) -> None:
    """REJECT_DUPLICATE keeps a failed study from being re-run; answering with its run as
    if it had started told the client a dead study was processing (EVAL_REPORT 23)."""
    client = StubClient(start=WorkflowAlreadyStartedError("study-s1", "StudyWorkflow"))
    client.status = status
    with pytest.raises(WorkflowNotRunningError, match=f"ended {status.name.lower()}"):
        await service(client).start_study(INPUT)


@pytest.mark.parametrize("error", [UNAVAILABLE, RuntimeError("Failed client connect"), NOT_FOUND])
async def test_start_errors_are_unavailable(error: BaseException) -> None:
    with pytest.raises(WorkflowUnavailableError, match="unavailable"):
        await service(StubClient(start=error)).start_study(INPUT)


async def test_slow_calls_are_bounded() -> None:
    client = StubClient()
    client.delay = 1.0
    with pytest.raises(WorkflowUnavailableError, match="did not answer in time"):
        await service(client, call_timeout=0.05).start_study(INPUT)


async def test_describe() -> None:
    client = StubClient()
    described = await service(client).describe("study-s1")
    assert (described.run_id, described.status) == ("run-1", "RUNNING")
    client.status = None
    assert (await service(client).describe("study-s1")).status == "UNKNOWN"
    with pytest.raises(WorkflowNotFoundError):
        await service(StubClient(describe=NOT_FOUND)).describe("study-s1")
    with pytest.raises(WorkflowUnavailableError):
        await service(StubClient(describe=UNAVAILABLE)).describe("study-s1")


async def test_query_status() -> None:
    client = StubClient()
    view = await service(client).query_status("study-s1")
    assert view.stage == "extracting"
    assert client.calls[-1] == ("query", "status", StudyStatusView)
    with pytest.raises(WorkflowNotFoundError):
        await service(StubClient(query=NOT_FOUND)).query_status("study-s1")
    failed = WorkflowQueryFailedError("unknown query")
    with pytest.raises(WorkflowUnavailableError, match="could not answer: unknown query"):
        await service(StubClient(query=failed)).query_status("study-s1")


async def test_cancel() -> None:
    client = StubClient()
    await service(client).cancel("study-s1", "withdrawn")
    assert client.calls[-1] == ("signal", "cancel_study", "withdrawn")

    closed = StubClient(signal=NOT_FOUND)
    closed.status = WorkflowExecutionStatus.COMPLETED
    with pytest.raises(WorkflowNotRunningError, match="completed, not running"):
        await service(closed).cancel("study-s1", "x")
    missing = StubClient(signal=NOT_FOUND, describe=NOT_FOUND)
    with pytest.raises(WorkflowNotFoundError):
        await service(missing).cancel("study-s1", "x")
    with pytest.raises(WorkflowUnavailableError):
        await service(StubClient(signal=UNAVAILABLE)).cancel("study-s1", "x")


async def test_check_describes_the_namespace() -> None:
    client = StubClient()
    await service(client).check()
    assert client.calls == [("describe_namespace", "default")]
    with pytest.raises(WorkflowUnavailableError):
        await service(StubClient(check=UNAVAILABLE)).check()


# --------------------------------------------------------------------------- wiring


def build(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_temporal_configured() -> None:
    assert temporal_configured(build())
    assert not temporal_configured(build(temporal_address=""))
    assert not temporal_configured(build(temporal_address="  "))


async def test_lifespan_creates_a_lazy_client_without_a_network_call() -> None:
    # Nothing listens on port 1; a non-lazy connect would fail here.
    app = create_app(build(temporal_address="127.0.0.1:1", temporal_task_queue="q"))
    assert app.state.temporal is None and app.state.workflows is None
    started = datetime.now(UTC)
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.temporal, Client)
        assert isinstance(app.state.workflows, TemporalWorkflowService)
        assert app.state.workflows.task_queue == "q"
    assert (datetime.now(UTC) - started).total_seconds() < 2
    assert app.state.temporal is None and app.state.workflows is None


async def test_lifespan_without_temporal_configures_nothing() -> None:
    app = create_app(build(temporal_address=""))
    async with app.router.lifespan_context(app):
        assert app.state.temporal is None and app.state.workflows is None


async def test_an_injected_service_is_kept() -> None:
    injected = service(StubClient())
    app = create_app(build(), workflows=injected)
    async with app.router.lifespan_context(app):
        assert app.state.workflows is injected and app.state.temporal is None
    assert app.state.workflows is injected


def test_api_progress_mirrors_the_workflow_contract() -> None:
    """GET /workflow's ``status`` is a copy of StudyStatusView: a contract change shows here."""
    assert list(WorkflowProgress.model_fields) == list(StudyStatusView.model_fields)
