"""What ``GET /v1/ops/health`` reads, each read bounded and failing on its own.

Every reader returns an :class:`Outcome`: the value, or a sanitized error category (never
the exception text), with when it was checked and how long it took. One failing read leaves
the others' evidence intact, so the response is partial rather than an error.

* :func:`read_state`: one session, sequential queries: the heartbeat rows, the outbox
  backlog, the latest published reference corpus, the cost index store.
* :func:`read_ledger`: the latest successful paid call per provider, kind, activity and
  model (``llm_calls``): evidence of inference, reported apart from the metadata probes.
* :func:`probe_storage`: a metadata lookup of a reserved object name no study uses.
* :func:`read_pollers`: Temporal's ``DescribeTaskQueue`` for the study task queue (workflow
  and activity pollers seen recently), which a namespace check does not show.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.models import CorpusVersion, CostIndexRun, CostIndexValue, LlmCall, ServiceHeartbeat
from basis.storage import ObjectNotFound, Storage

STATE_TIMEOUT_S: Final = 2.5
LEDGER_TIMEOUT_S: Final = 2.5
STORAGE_TIMEOUT_S: Final = 3.0
POLLERS_TIMEOUT_S: Final = 2.0
# A poller Temporal saw within this long is polling now (Temporal lists pollers for a few
# minutes after their last poll; a long poll lasts up to a minute).
POLLER_RECENT: Final = timedelta(minutes=2)
# The object the storage probe looks up: under no study's prefix (studies/<id>/...).
STORAGE_PROBE_OBJECT: Final = "_ops/health-probe"


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Outcome[T]:
    value: T | None
    error: str | None
    checked_at: datetime
    latency_ms: float

    @property
    def ok(self) -> bool:
        return self.error is None


async def bounded[T](
    read: Callable[[], Awaitable[T]], timeout_s: float, *, clock: Callable[[], datetime] = utcnow
) -> Outcome[T]:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_s):
            value = await read()
        error = None
    except TimeoutError:
        value, error = None, f"timed out after {timeout_s:g} s"
    except Exception as exc:  # sanitized: the type only, never the message
        value, error = None, f"failed ({type(exc).__name__})"
    return Outcome(value, error, clock(), round((time.perf_counter() - started) * 1000, 2))


# ---------------------------------------------------------------------------- the state


@dataclass(frozen=True, slots=True)
class Heartbeat:
    service: str
    instance_id: str
    build_sha: str
    state: str
    started_at: datetime
    heartbeat_at: datetime
    report: dict[str, Any]

    @property
    def interval_s(self) -> float:
        value = self.report.get("heartbeat_interval_s")
        return float(value) if isinstance(value, int | float) and value > 0 else 15.0

    @property
    def valid_for(self) -> timedelta:
        """How long the heartbeat stays evidence: three missed beats, at least 45 s."""
        return timedelta(seconds=max(3 * self.interval_s, 45.0))


@dataclass(frozen=True, slots=True)
class Backlog:
    pending: int
    due: int
    oldest_due_s: float | None
    dead: int


@dataclass(frozen=True, slots=True)
class Reference:
    version: int
    embedding_model: str
    published_at: datetime


@dataclass(frozen=True, slots=True)
class CostIndexState:
    values: int
    latest_year: int | None
    latest_runs: list[dict[str, Any]]  # per source: status, trigger, finished_at


@dataclass(frozen=True, slots=True)
class State:
    heartbeats: list[Heartbeat]
    backlog: Backlog
    reference: Reference | None
    cost_indices: CostIndexState


async def _state(sessionmaker: async_sessionmaker[AsyncSession]) -> State:
    async with sessionmaker() as session:
        rows = (await session.execute(select(ServiceHeartbeat))).scalars().all()
        heartbeats = [
            Heartbeat(
                service=row.service,
                instance_id=str(row.id),
                build_sha=row.build_sha,
                state=row.state,
                started_at=row.started_at,
                heartbeat_at=row.heartbeat_at,
                report=row.report if isinstance(row.report, dict) else {},
            )
            for row in rows
        ]
        backlog_row = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE status = 'pending'), "
                    "count(*) FILTER (WHERE status = 'pending' AND next_attempt_at <= now()), "
                    "extract(epoch FROM now() - min(next_attempt_at) "
                    "  FILTER (WHERE status = 'pending' AND next_attempt_at <= now())), "
                    "count(*) FILTER (WHERE status = 'dead') "
                    "FROM outbox WHERE status IN ('pending', 'dead')"
                )
            )
        ).one()
        backlog = Backlog(
            pending=int(backlog_row[0]),
            due=int(backlog_row[1]),
            oldest_due_s=None if backlog_row[2] is None else round(float(backlog_row[2]), 1),
            dead=int(backlog_row[3]),
        )
        corpus = (
            await session.execute(
                select(CorpusVersion)
                .where(CorpusVersion.corpus == "reference", CorpusVersion.published_at.is_not(None))
                .order_by(CorpusVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        reference = (
            None
            if corpus is None or corpus.published_at is None
            else Reference(corpus.version, corpus.embedding_model, corpus.published_at)
        )
        count, latest_year = (
            await session.execute(
                select(
                    func.count(CostIndexValue.id),
                    func.max(CostIndexValue.year).filter(CostIndexValue.series == "historical"),
                )
            )
        ).one()
        runs = (
            await session.execute(
                select(
                    CostIndexRun.source,
                    CostIndexRun.status,
                    CostIndexRun.trigger,
                    CostIndexRun.finished_at,
                )
                .distinct(CostIndexRun.source)
                .order_by(CostIndexRun.source, CostIndexRun.started_at.desc())
            )
        ).all()
        return State(
            heartbeats=heartbeats,
            backlog=backlog,
            reference=reference,
            cost_indices=CostIndexState(
                values=int(count),
                latest_year=None if latest_year is None else int(latest_year),
                latest_runs=[
                    {"source": r[0], "status": r[1], "trigger": r[2], "finished_at": r[3]}
                    for r in runs
                ],
            ),
        )


async def read_state(sessionmaker: async_sessionmaker[AsyncSession]) -> Outcome[State]:
    return await bounded(lambda: _state(sessionmaker), STATE_TIMEOUT_S)


# ---------------------------------------------------------------------------- the ledger


@dataclass(frozen=True, slots=True)
class LastCall:
    provider: str
    kind: str
    activity: str | None
    model: str
    at: datetime


@dataclass(frozen=True, slots=True)
class Ledger:
    calls: list[LastCall] = field(default_factory=list)

    def last(
        self,
        *,
        providers: tuple[str, ...] | None = None,
        kinds: tuple[str, ...] | None = None,
        activities: tuple[str, ...] | None = None,
    ) -> LastCall | None:
        matching = [
            c
            for c in self.calls
            if (providers is None or c.provider in providers)
            and (kinds is None or c.kind in kinds)
            and (activities is None or c.activity in activities)
        ]
        return max(matching, key=lambda c: c.at, default=None)


async def _ledger(sessionmaker: async_sessionmaker[AsyncSession]) -> Ledger:
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    LlmCall.provider,
                    LlmCall.kind,
                    LlmCall.activity,
                    LlmCall.model,
                    func.max(LlmCall.created_at),
                )
                .where(LlmCall.status == "ok")
                .group_by(LlmCall.provider, LlmCall.kind, LlmCall.activity, LlmCall.model)
            )
        ).all()
    return Ledger([LastCall(r[0], r[1], r[2], r[3], r[4]) for r in rows])


async def read_ledger(sessionmaker: async_sessionmaker[AsyncSession]) -> Outcome[Ledger]:
    return await bounded(lambda: _ledger(sessionmaker), LEDGER_TIMEOUT_S)


# ---------------------------------------------------------------------------- storage


async def _storage(storage: Storage, bucket: str) -> str:
    try:
        await storage.stat(f"gs://{bucket}/{STORAGE_PROBE_OBJECT}")
    except ObjectNotFound:
        return "answered: the reserved probe object is absent, as expected"
    return "answered: the reserved probe object exists"


async def probe_storage(storage: Storage, bucket: str) -> Outcome[str]:
    return await bounded(lambda: _storage(storage, bucket), STORAGE_TIMEOUT_S)


# ---------------------------------------------------------------------------- Temporal pollers


@dataclass(frozen=True, slots=True)
class Pollers:
    workflow: int
    activity: int


PollerReader = Callable[[], Awaitable[Pollers]]


def temporal_pollers(client: Any, task_queue: str) -> PollerReader:
    """``DescribeTaskQueue`` for both task types on the real client; pollers seen within
    :data:`POLLER_RECENT` count."""
    from temporalio.api.enums.v1 import TaskQueueType
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

    async def describe(kind: int) -> int:
        response = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=client.namespace,
                task_queue=TaskQueue(name=task_queue),
                task_queue_type=kind,  # type: ignore[arg-type]
            ),
            timeout=timedelta(seconds=POLLERS_TIMEOUT_S),
        )
        cutoff = utcnow() - POLLER_RECENT
        return sum(
            1
            for poller in response.pollers
            if poller.HasField("last_access_time")
            and poller.last_access_time.ToDatetime(tzinfo=UTC) >= cutoff
        )

    async def read() -> Pollers:
        workflow, activity = await asyncio.gather(
            describe(TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
            describe(TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
        )
        return Pollers(workflow=workflow, activity=activity)

    return read


async def read_pollers(reader: PollerReader) -> Outcome[Pollers]:
    return await bounded(reader, POLLERS_TIMEOUT_S)
