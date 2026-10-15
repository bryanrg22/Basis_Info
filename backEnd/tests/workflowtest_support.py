"""Helpers for the Temporal-facing tests: relay, workflow routes, start-to-decision, failure.

Imported as a top-level module (like ``dbtest_support``/``apitest_support``).

* :func:`local_temporal`: a real Temporal dev server via ``WorkflowEnvironment.start_local``
  (the ``temporal`` CLI on ``PATH`` when installed, otherwise the SDK downloads it), with
  the pydantic data converter the application uses.
* :func:`fake_worker`: a worker running :class:`fake_study_workflow.FakeStudyWorkflow`.
* :class:`FakeWorkflowService`: an in-memory :class:`basis.temporal.service.WorkflowService`
  for API tests that need no server.
* :class:`RecordingGateway`: an in-memory relay gateway.
* Database helpers shared by these suites: seeding a study at a stage with its workflow id,
  a confirmed appraisal document, recording a decision, reading and re-arming outbox rows.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from dbtest_support import Factory, db_settings, truncate_all
from fake_study_workflow import FakeStudyWorkflow
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from temporalio.client import Client, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from basis.db.engine import create_engine
from basis.db.enums import Decision as DecisionKind
from basis.db.enums import DocumentKind, OrgRole, Stage
from basis.db.models import OutboxMessage, Study
from basis.db.repositories import (
    CorrectionInput,
    DecisionRecord,
    DocumentRepository,
    ReviewRepository,
)
from basis.db.session import create_sessionmaker, session_scope
from basis.temporal.names import STUDY_WORKFLOW, study_workflow_id
from basis.temporal.service import (
    WorkflowDescription,
    WorkflowNotFoundError,
    WorkflowNotRunningError,
    WorkflowRef,
    WorkflowUnavailableError,
)
from basis.workflow.contracts import (
    Decision,
    DecisionAck,
    StageName,
    StudyStatusView,
    StudyWorkflowInput,
)

TEMPORAL_CLI = shutil.which("temporal")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


async def unreachable_client(port: int | None = None) -> Client:
    """A lazy client for an address where nothing listens (connect is refused)."""
    return await Client.connect(
        f"127.0.0.1:{port or free_port()}", lazy=True, data_converter=pydantic_data_converter
    )


@asynccontextmanager
async def local_temporal(port: int | None = None) -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter,
        port=port,
        dev_server_existing_path=TEMPORAL_CLI,
        dev_server_log_level="error",
    )
    try:
        yield env
    finally:
        await env.shutdown()


@asynccontextmanager
async def fake_worker(client: Client, task_queue: str) -> AsyncIterator[Worker]:
    worker = Worker(client, task_queue=task_queue, workflows=[FakeStudyWorkflow])
    async with worker:
        yield worker


def workflow_input(study: Study, *, git_sha: str = "test-sha") -> StudyWorkflowInput:
    return StudyWorkflowInput(
        study_id=str(study.id),
        org_id=str(study.org_id),
        input_revision=study.revision,
        workflow_version=git_sha,
        budget_usd=25.0,
    )


async def start_fake(
    client: Client, study: Study, task_queue: str, *, stage: StageName | None = None
) -> WorkflowHandle[Any, Any]:
    """Start the fake workflow for ``study``; optionally place it at ``stage``."""
    handle: WorkflowHandle[Any, Any] = await client.start_workflow(
        STUDY_WORKFLOW,
        workflow_input(study),
        id=study_workflow_id(study.id),
        task_queue=task_queue,
    )
    if stage is not None:
        await place(handle, stage, study.revision)
    return handle


async def place(handle: WorkflowHandle[Any, Any], stage: StageName, revision: int) -> None:
    await handle.signal("fake_set_stage", args=[stage, revision])


async def wait_for_stage(
    handle: WorkflowHandle[Any, Any], stage: StageName, timeout: float = 10.0
) -> StudyStatusView:
    async with asyncio.timeout(timeout):
        while True:
            view: StudyStatusView = await handle.query("status", result_type=StudyStatusView)
            if view.stage == stage:
                return view
            await asyncio.sleep(0.05)


async def applied_decisions(handle: WorkflowHandle[Any, Any]) -> list[Decision]:
    decisions: list[Decision] = await handle.query("fake_decisions", result_type=list[Decision])
    return decisions


# --------------------------------------------------------------------------- database


async def seed_study(
    sm: async_sessionmaker[AsyncSession],
    *,
    stage: Stage = Stage.APPRAISAL_REVIEW,
    revision: int = 1,
    started: bool = True,
) -> Study:
    """A committed study at ``stage``; ``started`` also records ``workflow_id``."""
    async with session_scope(sm) as session:
        study = await Factory(session).study(stage=stage, revision=revision)
        if started:
            study.workflow_id = study_workflow_id(study.id)
            await session.flush()
        return study


async def add_member(
    sm: async_sessionmaker[AsyncSession], study: Study, uid: str, role: OrgRole
) -> None:
    async with session_scope(sm) as session:
        factory = Factory(session)
        await factory.member(study.org_id, await factory.user(uid), role)


async def confirmed_document(
    sm: async_sessionmaker[AsyncSession],
    study_id: uuid.UUID,
    kind: DocumentKind = DocumentKind.APPRAISAL,
    *,
    confirmed: bool = True,
) -> None:
    async with session_scope(sm) as session:
        documents = DocumentRepository(session)
        document_id = uuid.uuid4()
        await documents.create(
            study_id=study_id,
            kind=kind,
            filename=f"{kind.value}.pdf",
            content_type="application/pdf",
            size_bytes=10,
            sha256=uuid.uuid4().hex * 2,
            gcs_uri=f"gs://basis-local/studies/{study_id}/{document_id}/{kind.value}.pdf",
            uploaded_by=None,
            document_id=document_id,
        )
        if confirmed:
            await documents.mark_uploaded(study_id, document_id)


async def record_decision(
    sm: async_sessionmaker[AsyncSession],
    study: Study,
    *,
    stage: Stage = Stage.APPRAISAL_REVIEW,
    revision: int = 1,
    decision: DecisionKind = DecisionKind.APPROVE,
    corrections: Sequence[CorrectionInput] = (),
) -> DecisionRecord:
    """Record a decision the way the API does (review + transition + outbox row)."""
    async with session_scope(sm) as session:
        return await ReviewRepository(session).record_decision(
            study_id=study.id,
            stage=stage,
            revision=revision,
            decision=decision,
            reviewer_id=study.created_by,
            idempotency_key=f"k-{uuid.uuid4()}",
            corrections=corrections,
        )


async def outbox_rows(sm: async_sessionmaker[AsyncSession]) -> list[OutboxMessage]:
    async with sm() as session:
        stmt = select(OutboxMessage).order_by(OutboxMessage.created_at, OutboxMessage.id)
        return list((await session.scalars(stmt)).all())


async def outbox_row(sm: async_sessionmaker[AsyncSession], message_id: uuid.UUID) -> OutboxMessage:
    async with sm() as session:
        row = await session.get(OutboxMessage, message_id)
        assert row is not None
        return row


async def make_due(sm: async_sessionmaker[AsyncSession], **values: Any) -> None:
    """Make every row due (skipping the backoff wait), optionally setting columns.

    Uses the database clock, as the claim query does: the host and the container clocks
    differ slightly, so a host timestamp could still lie in the database's future.
    """
    async with session_scope(sm) as session:
        await session.execute(update(OutboxMessage).values(next_attempt_at=func.now(), **values))


async def due_in(sm: async_sessionmaker[AsyncSession], message_id: uuid.UUID) -> float:
    """Seconds until the row is due, by the database clock."""
    async with sm() as session:
        seconds = await session.scalar(
            select(
                func.extract("epoch", OutboxMessage.next_attempt_at - func.clock_timestamp())
            ).where(OutboxMessage.id == message_id)
        )
    return float(seconds)


# --------------------------------------------------------------------------- fakes


@dataclass
class FakeExecution:
    workflow_input: StudyWorkflowInput
    run_id: str
    status: str = "RUNNING"
    view: StudyStatusView | None = None
    cancel_reasons: list[str] = field(default_factory=list)


class FakeWorkflowService:
    """In-memory :class:`~basis.temporal.service.WorkflowService` for API tests.

    ``unavailable`` makes every call raise :class:`WorkflowUnavailableError`;
    ``query_fails`` makes only the status query fail (no worker answering).
    """

    def __init__(self) -> None:
        self.executions: dict[str, FakeExecution] = {}
        self.start_calls: list[StudyWorkflowInput] = []
        self.unavailable = False
        self.query_fails = False

    def _check(self) -> None:
        if self.unavailable:
            raise WorkflowUnavailableError("Temporal is unavailable (fake).")

    def _get(self, workflow_id: str) -> FakeExecution:
        execution = self.executions.get(workflow_id)
        if execution is None:
            raise WorkflowNotFoundError(f"Temporal has no workflow {workflow_id}.")
        return execution

    async def start_study(self, workflow_input: StudyWorkflowInput) -> WorkflowRef:
        self._check()
        self.start_calls.append(workflow_input)
        workflow_id = study_workflow_id(workflow_input.study_id)
        execution = self.executions.setdefault(
            workflow_id, FakeExecution(workflow_input=workflow_input, run_id=str(uuid.uuid4()))
        )
        return WorkflowRef(workflow_id=workflow_id, run_id=execution.run_id)

    async def describe(self, workflow_id: str) -> WorkflowDescription:
        self._check()
        execution = self._get(workflow_id)
        return WorkflowDescription(
            workflow_id=workflow_id, run_id=execution.run_id, status=execution.status
        )

    async def query_status(self, workflow_id: str) -> StudyStatusView:
        self._check()
        execution = self._get(workflow_id)
        if self.query_fails:
            raise WorkflowUnavailableError("The workflow could not answer (fake).")
        return execution.view or StudyStatusView(
            stage="extracting", revision=execution.workflow_input.input_revision
        )

    async def cancel(self, workflow_id: str, reason: str) -> None:
        self._check()
        execution = self._get(workflow_id)
        if execution.status != "RUNNING":
            raise WorkflowNotRunningError(
                f"Workflow {workflow_id} is {execution.status.lower()}, not running."
            )
        execution.cancel_reasons.append(reason)

    async def check(self) -> None:
        self._check()


class RecordingGateway:
    """An in-memory relay gateway whose workflow has always caught up with a decision.

    ``delay`` makes each ``decide`` take that long, so concurrent relays overlap;
    ``fail`` (a callable returning an exception or None) injects errors.
    """

    def __init__(
        self,
        *,
        delay: float = 0.0,
        fail: Callable[[Decision], BaseException | None] | None = None,
    ) -> None:
        self.delay = delay
        self.fail = fail
        self.decided: list[tuple[str, Decision]] = []

    async def status(self, workflow_id: str) -> StudyStatusView:
        # Never "behind" a decision, so the relay always goes on to decide().
        return StudyStatusView(stage="completed", revision=2**31)

    async def decide(self, workflow_id: str, decision: Decision) -> DecisionAck:
        if self.delay:
            await asyncio.sleep(self.delay)
        error = self.fail(decision) if self.fail else None
        if error is not None:
            raise error
        self.decided.append((workflow_id, decision))
        return DecisionAck(accepted=True, applied_revision=decision.revision)


# --------------------------------------------------------------------------- fixtures
# Imported by the conftest.py of tests/integration/relay, tests/integration/api and
# tests/failure (a fixture is registered where it is imported).


@pytest.fixture
async def wf_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """A fresh engine on the session's test database; every table truncated around the test."""
    engine = create_engine(db_settings(database_url))
    await truncate_all(engine)
    try:
        yield engine
    finally:
        await truncate_all(engine)
        await engine.dispose()


@pytest.fixture
def sm(wf_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(wf_engine)


@pytest.fixture
async def temporal_env() -> AsyncIterator[WorkflowEnvironment]:
    async with local_temporal() as env:
        yield env


@pytest.fixture
def task_queue() -> str:
    return f"basis-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def fake_workflows(
    temporal_env: WorkflowEnvironment, task_queue: str
) -> AsyncIterator[Client]:
    """The dev server's client, with a worker running the fake ``StudyWorkflow``."""
    async with fake_worker(temporal_env.client, task_queue):
        yield temporal_env.client
