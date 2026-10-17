"""The lifecycle test harness: a time-skipping Temporal server, real Postgres, real workers.

One :class:`Harness` per test. It owns a unique task queue, so workers of other tests
never pick up its workflows, and it terminates every workflow it started at teardown.
Decisions are delivered with ``handle.execute_update("decide", Decision(...))`` directly
(the outbox relay is tested on its own).

Time: the test server skips time automatically only while a client waits for a workflow
result; while a test polls the ``status`` query or sends updates, time stands still, so a
review gate never times out by accident. ``env.sleep(...)`` moves time on purpose.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from lifecycletest_support import DEFAULT_APPRAISAL, DEFAULT_PHOTOS, SeededStudy, seed_study
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from basis.activities import Deps, build_activities
from basis.config import Settings
from basis.db.models import ActivityResult, Run, Study
from basis.db.session import session_scope
from basis.worker.main import build_worker
from basis.workflow.contracts import (
    CorrectionInput,
    Decision,
    DecisionAck,
    ReviewStage,
    StudyStatusView,
    StudyWorkflowInput,
    StudyWorkflowResult,
)
from basis.workflow.study_workflow import StudyWorkflow, workflow_id_for

POLL_INTERVAL_S: Final = 0.05
WAIT_TIMEOUT_S: Final = 30.0


@dataclass
class Started:
    study: SeededStudy
    handle: WorkflowHandle[StudyWorkflow, StudyWorkflowResult]

    @property
    def study_id(self) -> uuid.UUID:
        return self.study.study_id


@dataclass
class Harness:
    env: WorkflowEnvironment
    sessionmaker: async_sessionmaker[AsyncSession]
    deps: Deps
    settings: Settings
    task_queue: str = field(default_factory=lambda: f"lifecycle-{uuid.uuid4().hex[:12]}")
    handles: list[WorkflowHandle[Any, Any]] = field(default_factory=list)

    @property
    def client(self) -> Client:
        return self.env.client

    # ------------------------------------------------------------------ workers

    def activities(self, **overrides: Callable[..., Any]) -> dict[str, Callable[..., Any]]:
        """The real activities with some replaced by mocks (keyed by activity name)."""
        activities = build_activities(self.deps)
        unknown = set(overrides) - set(activities)
        if unknown:
            raise KeyError(f"unknown activities: {sorted(unknown)}")
        activities.update(overrides)
        return activities

    def build_worker(
        self, worker_kwargs: dict[str, Any] | None = None, **overrides: Callable[..., Any]
    ) -> Worker:
        """A worker on this test's task queue; ``overrides`` replace activities by name."""
        return build_worker(
            self.client,
            self.settings,
            self.deps,
            task_queue=self.task_queue,
            activities=self.activities(**overrides),
            **(worker_kwargs or {}),
        )

    @asynccontextmanager
    async def worker(
        self, worker_kwargs: dict[str, Any] | None = None, **overrides: Callable[..., Any]
    ) -> AsyncIterator[Worker]:
        async with self.build_worker(worker_kwargs, **overrides) as worker:
            yield worker

    # ------------------------------------------------------------------ studies

    async def seed(
        self, *, photos: Sequence[str] = DEFAULT_PHOTOS, appraisal: str | None = DEFAULT_APPRAISAL
    ) -> SeededStudy:
        return await seed_study(
            self.sessionmaker, photos=photos, appraisal=appraisal, storage=self.deps.storage
        )

    def workflow_input(self, study: SeededStudy, **overrides: Any) -> StudyWorkflowInput:
        values: dict[str, Any] = {
            "study_id": str(study.study_id),
            "org_id": str(study.org_id),
            "input_revision": 1,
            "workflow_version": "test",
            "budget_usd": 25.0,
        }
        values.update(overrides)
        return StudyWorkflowInput(**values)

    async def start(self, study: SeededStudy | None = None, **overrides: Any) -> Started:
        study = study or await self.seed()
        handle = await self.client.start_workflow(
            StudyWorkflow.run,
            self.workflow_input(study, **overrides),
            id=workflow_id_for(str(study.study_id)),
            task_queue=self.task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        self.handles.append(handle)
        return Started(study=study, handle=handle)

    # ------------------------------------------------------------------ waiting

    async def status(self, handle: WorkflowHandle[Any, Any]) -> StudyStatusView:
        result: StudyStatusView = await handle.query(StudyWorkflow.status)
        return result

    async def wait_for(
        self,
        handle: WorkflowHandle[Any, Any],
        predicate: Callable[[StudyStatusView], bool],
        what: str,
        timeout: float = WAIT_TIMEOUT_S,
    ) -> StudyStatusView:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last: StudyStatusView | None = None
        while loop.time() < deadline:
            last = await self.status(handle)
            if predicate(last):
                return last
            await asyncio.sleep(POLL_INTERVAL_S)
        raise AssertionError(f"timed out waiting for {what}; last status: {last}")

    async def wait_awaiting(
        self, handle: WorkflowHandle[Any, Any], stage: ReviewStage, revision: int | None = None
    ) -> StudyStatusView:
        return await self.wait_for(
            handle,
            lambda s: (
                s.awaiting == stage
                and s.stage == stage
                and (revision is None or s.revision == revision)
            ),
            f"{stage} (revision {revision})",
        )

    async def wait_photos(self, handle: WorkflowHandle[Any, Any], count: int) -> StudyStatusView:
        """The photo stage done: ``count`` photos completed and none pending. Since v0.9
        (patch ``appraisal-gate-early-v1``) the appraisal review opens before it is."""
        return await self.wait_for(
            handle,
            lambda s: s.completed_photos == count and s.pending_photos == 0,
            f"{count} photos done",
        )

    # ------------------------------------------------------------------ decisions

    @staticmethod
    def decision(
        stage: ReviewStage,
        revision: int,
        decision: str = "approve",
        *,
        corrections: Sequence[CorrectionInput] = (),
        review_id: str | None = None,
    ) -> Decision:
        return Decision(
            review_id=review_id or str(uuid.uuid4()),
            stage=stage,
            revision=revision,
            decision=decision,  # type: ignore[arg-type]
            reviewer_id=str(uuid.uuid4()),
            corrections=tuple(corrections),
            submitted_at=datetime.now(UTC),
        )

    async def decide(
        self, handle: WorkflowHandle[Any, Any], decision: Decision, *, relay_id: bool = True
    ) -> DecisionAck:
        """Deliver like the outbox relay: update ``decide`` with id ``decision-<review_id>``.

        ``relay_id=False`` uses a fresh update id, so the server cannot deduplicate and the
        workflow's own ``review_id`` idempotency is what is exercised.
        """
        update_id = f"decision-{decision.review_id}" if relay_id else str(uuid.uuid4())
        ack: DecisionAck = await handle.execute_update(StudyWorkflow.decide, decision, id=update_id)
        return ack

    async def approve(
        self, handle: WorkflowHandle[Any, Any], stage: ReviewStage, revision: int = 1
    ) -> DecisionAck:
        await self.wait_awaiting(handle, stage, revision)
        return await self.decide(handle, self.decision(stage, revision))

    async def approve_all(self, handle: WorkflowHandle[Any, Any], revision: int = 1) -> None:
        for stage in ("appraisal_review", "rooms_review", "assets_review"):
            await self.approve(handle, stage, revision)  # type: ignore[arg-type]

    async def result(self, handle: WorkflowHandle[Any, StudyWorkflowResult]) -> StudyWorkflowResult:
        return await asyncio.wait_for(handle.result(), WAIT_TIMEOUT_S)

    # ------------------------------------------------------------------ database

    async def study_row(self, study_id: uuid.UUID) -> Study:
        async with session_scope(self.sessionmaker) as session:
            study = await session.get(Study, study_id, populate_existing=True)
            assert study is not None
            return study

    async def runs(self, study_id: uuid.UUID) -> list[Run]:
        async with session_scope(self.sessionmaker) as session:
            rows = await session.scalars(select(Run).where(Run.study_id == study_id))
            return list(rows.all())

    async def activity_results(self, workflow_id: str) -> list[ActivityResult]:
        async with session_scope(self.sessionmaker) as session:
            rows = await session.scalars(
                select(ActivityResult)
                .where(ActivityResult.idempotency_key.startswith(f"{workflow_id}:"))
                .order_by(ActivityResult.created_at)
            )
            return list(rows.all())

    async def activity_sequence(self, handle: WorkflowHandle[Any, Any]) -> list[str]:
        """Activity types in the order they were scheduled, from the workflow history."""
        history = await handle.fetch_history()
        return [
            event.activity_task_scheduled_event_attributes.activity_type.name
            for event in history.events
            if event.HasField("activity_task_scheduled_event_attributes")
        ]

    async def cleanup(self) -> None:
        for handle in self.handles:
            try:
                await handle.terminate("test finished")
            except Exception:  # already completed, failed or terminated
                pass


async def skip(env: WorkflowEnvironment, delta: timedelta) -> None:
    """Advance the test server's clock by ``delta`` (timers due in between fire)."""
    await env.sleep(delta)
