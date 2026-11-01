"""Operator alerts on PostgreSQL (docs/api/RETENTION_AND_ALERTS.md section 2).

* A failed study enqueues exactly one alert: ``mark_failed`` writes the ``stage.failed``
  event, one ``alerts`` row and one ``notification`` outbox row of ``type: alert`` in the
  same transaction; calling it again with the same key (a retried ``record_failure``
  activity) adds nothing, and a second failure (another key) adds one more.
* A stale heartbeat raises one alert per service per stale period: the monitor finds the
  worker silent past the threshold and alerts once; checking again during the same silence
  adds nothing; a fresh heartbeat quiets it; a second silence raises a new alert; two stale
  services each get their own; a service with no row at all is alerted once (``never``).
* Nothing is sent twice: the relay delivers the alert through the sender once, and a
  redelivery after a lost commit hands the sender the same idempotency key.
* ``raise_once`` leaves the caller's transaction usable after a duplicate key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dbtest_support import Factory
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import OutboxStatus, Stage, StudyStatus
from basis.db.models import Alert, OutboxMessage, StudyEvent
from basis.db.repositories.alert import (
    HEARTBEAT_STALE,
    STUDY_FAILED,
    AlertRepository,
    study_failed_key,
)
from basis.db.repositories.workflow_state import WorkflowStateRepository
from basis.db.session import session_scope
from basis.notify import EmailMessage, SendResult
from basis.notify.heartbeats import HeartbeatMonitor
from basis.ops.heartbeat import HeartbeatWriter
from basis.relay.delivery import Relay

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
STALE = timedelta(minutes=5)


async def alerts_of(session: AsyncSession) -> list[Alert]:
    return list((await session.scalars(select(Alert).order_by(Alert.created_at))).all())


async def notifications(session: AsyncSession) -> list[OutboxMessage]:
    stmt = select(OutboxMessage).where(OutboxMessage.kind == "notification")
    return list((await session.scalars(stmt.order_by(OutboxMessage.created_at))).all())


# --------------------------------------------------------------------------- study failed


async def test_a_failed_study_enqueues_exactly_one_alert(
    session: AsyncSession, make: Factory
) -> None:
    study = await make.study(stage=Stage.EXTRACTING)
    key = f"study-{study.id}:run1:record_failure:extracting:r1:1"
    repo = WorkflowStateRepository(session)

    marked = await repo.mark_failed(
        study.id,
        Stage.EXTRACTING,
        1,
        "IngestError: boom [BASIS-INGEST-003]",
        "BASIS-INGEST-003",
        alert_key=key,
    )

    assert marked is True
    await session.refresh(study)
    assert study.status is StudyStatus.FAILED
    (alert,) = await alerts_of(session)
    (message,) = await notifications(session)
    assert alert.kind == STUDY_FAILED and alert.key == study_failed_key(key)
    assert alert.study_id == study.id and alert.outbox_id == message.id
    assert message.status is OutboxStatus.PENDING
    assert message.payload["type"] == "alert" and message.payload["kind"] == STUDY_FAILED
    assert message.payload["study_id"] == str(study.id)
    assert (message.payload["stage"], message.payload["revision"]) == ("extracting", 1)
    assert message.payload["code"] == "BASIS-INGEST-003" and message.payload["marked"] is True
    assert alert.payload == message.payload
    events = (
        await session.scalars(select(StudyEvent.event_type).where(StudyEvent.study_id == study.id))
    ).all()
    assert "stage.failed" in events


async def test_a_retried_record_failure_does_not_alert_twice(
    session: AsyncSession, make: Factory
) -> None:
    study = await make.study(stage=Stage.EXTRACTING)
    key = f"study-{study.id}:run1:record_failure:extracting:r1:1"
    repo = WorkflowStateRepository(session)
    await repo.mark_failed(study.id, Stage.EXTRACTING, 1, "boom", "BASIS-WF-007", alert_key=key)
    # The activity's own transaction did not commit the activity_results row; Temporal
    # retries it with the same activity id, so the same key reaches mark_failed again.
    again = await repo.mark_failed(
        study.id, Stage.EXTRACTING, 1, "boom", "BASIS-WF-007", alert_key=key
    )

    assert again is True  # the row is still at that stage (already failed)
    assert len(await alerts_of(session)) == 1
    assert len(await notifications(session)) == 1
    # A later failure (the retry failed again: the workflow numbers it 2) is a new alert.
    await repo.clear_failure(study.id)
    await repo.mark_failed(
        study.id, Stage.EXTRACTING, 1, "boom again", "BASIS-WF-007", alert_key=key[:-1] + "2"
    )
    assert len(await alerts_of(session)) == 2


async def test_without_an_activity_key_the_event_names_the_occurrence(
    session: AsyncSession, make: Factory
) -> None:
    study = await make.study(stage=Stage.ASSETS_PROCESSING)
    await WorkflowStateRepository(session).mark_failed(
        study.id, Stage.ASSETS_PROCESSING, 2, "boom", None
    )
    (alert,) = await alerts_of(session)
    assert alert.key.startswith(f"study-failed:{study.id}:assets_processing:r2:seq")


async def test_the_failure_of_a_study_that_moved_on_still_alerts(
    session: AsyncSession, make: Factory
) -> None:
    study = await make.study(stage=Stage.ROOMS_REVIEW)
    marked = await WorkflowStateRepository(session).mark_failed(
        study.id, Stage.EXTRACTING, 1, "late failure", "BASIS-WF-001", alert_key="k1"
    )
    assert marked is False
    (alert,) = await alerts_of(session)
    assert alert.payload["marked"] is False


async def test_raise_once_leaves_the_transaction_usable(
    session: AsyncSession, make: Factory
) -> None:
    study = await make.study()
    repo = AlertRepository(session)
    first = await repo.raise_once(key="k", kind=STUDY_FAILED, payload={}, study_id=study.id)
    second = await repo.raise_once(key="k", kind=STUDY_FAILED, payload={}, study_id=study.id)
    assert first is not None and second is None
    # The session still works: the duplicate was contained in its SAVEPOINT.
    assert await session.scalar(select(func.count()).select_from(Alert)) == 1
    assert (
        await repo.raise_once(key="k2", kind=HEARTBEAT_STALE, payload={}, service="worker")
    ) is not None


# --------------------------------------------------------------------------- heartbeats


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def beat(
    sm: async_sessionmaker[AsyncSession], service: str, at: datetime, interval: float = 15
) -> HeartbeatWriter:
    writer = HeartbeatWriter(
        sm,
        service=service,  # type: ignore[arg-type]
        build_sha="abc",
        interval_s=interval,
        clock=lambda: at,
    )
    writer.state = "polling"
    assert await writer.beat()
    return writer


def monitor(
    sm: async_sessionmaker[AsyncSession], clock: Clock, services: tuple[str, ...] = ("worker",)
) -> HeartbeatMonitor:
    return HeartbeatMonitor(sm, stale_after=STALE, check_every=60, services=services, clock=clock)


async def test_a_stale_worker_raises_one_alert_per_stale_period(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock(T0)
    await beat(committed, "worker", T0)
    checks = monitor(committed, clock)

    assert await checks.check_once() == []  # fresh
    clock.now = T0 + STALE  # exactly the threshold: not yet stale
    assert await checks.check_once() == []
    clock.now = T0 + STALE + timedelta(seconds=1)
    (alert,) = await checks.check_once()
    assert alert.kind == HEARTBEAT_STALE and alert.service == "worker"
    assert alert.key == f"heartbeat-stale:worker:{T0.isoformat()}"
    assert alert.payload["last_heartbeat_at"] == T0.isoformat()
    assert alert.payload["stale_for_s"] == 301.0 and alert.payload["threshold_s"] == 300.0

    clock.now = T0 + timedelta(hours=3)  # still silent: the same period, nothing new
    assert await checks.check_once() == []
    async with committed() as session:
        assert len(await alerts_of(session)) == 1
        assert len(await notifications(session)) == 1

    # The worker comes back (a new process writes), then falls silent again: a new period.
    recovered = T0 + timedelta(hours=4)
    await beat(committed, "worker", recovered)
    clock.now = recovered + timedelta(minutes=1)
    assert await checks.check_once() == []
    clock.now = recovered + STALE + timedelta(minutes=1)
    (second,) = await checks.check_once()
    assert second.key == f"heartbeat-stale:worker:{recovered.isoformat()}"
    async with committed() as session:
        assert len(await alerts_of(session)) == 2


async def test_each_stale_service_gets_its_own_alert_and_a_missing_one_is_never(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock(T0 + timedelta(hours=1))
    await beat(committed, "worker", T0)
    checks = monitor(committed, clock, services=("worker", "relay"))

    raised = await checks.check_once()

    assert sorted(a.service or "" for a in raised) == ["relay", "worker"]
    by_service = {a.service: a for a in raised}
    assert by_service["relay"].key == "heartbeat-stale:relay:never"
    assert by_service["relay"].payload["last_heartbeat_at"] is None
    assert await checks.check_once() == []
    # The relay starts: its "never" period ends; it is not alerted again until it is silent.
    await beat(committed, "relay", clock.now)
    assert await checks.check_once() == []


async def test_the_newest_heartbeat_of_any_instance_counts(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    """Two worker processes (a deploy): the old one's final heartbeat is old, the new one
    is fresh; the service is not stale."""
    await beat(committed, "worker", T0 - timedelta(hours=1))
    await beat(committed, "worker", T0)
    clock = Clock(T0 + timedelta(minutes=2))
    assert await monitor(committed, clock).check_once() == []


async def test_the_monitor_refuses_bad_limits(committed: async_sessionmaker[AsyncSession]) -> None:
    with pytest.raises(ValueError):
        HeartbeatMonitor(committed, stale_after=timedelta(0), check_every=60)
    with pytest.raises(ValueError):
        HeartbeatMonitor(committed, stale_after=STALE, check_every=0)


# --------------------------------------------------------------------------- delivery


class Sender:
    provider = "fake"

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> SendResult:
        self.sent.append(message)
        return SendResult("fake", f"<{message.idempotency_key}@fake>")


async def test_the_relay_sends_an_alert_once_and_a_redelivery_reuses_the_key(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock(T0 + timedelta(hours=1))
    await beat(committed, "worker", T0)
    (alert,) = await monitor(committed, clock).check_once()
    sender = Sender()
    relay = Relay(committed, None, email_sender=sender, alert_to="ops@example.test")  # type: ignore[arg-type]

    report = await relay.run_once()

    assert (report.claimed, report.delivered) == (1, 1)
    (email,) = sender.sent
    assert email.to == "ops@example.test" and email.idempotency_key == alert.key
    assert "worker heartbeat is stale" in email.subject
    async with committed() as session:
        (message,) = await notifications(session)
        assert message.status is OutboxStatus.DELIVERED and message.id == alert.outbox_id
    assert (await relay.run_once()).claimed == 0  # nothing left to deliver

    # The commit after the send was lost: the row is pending again and is delivered again
    # with the same idempotency key, which is what lets the provider drop the duplicate.
    async with session_scope(committed) as session:
        await session.execute(
            update(OutboxMessage).values(status=OutboxStatus.PENDING, delivered_at=None)
        )
    assert (await relay.run_once()).delivered == 1
    assert [m.idempotency_key for m in sender.sent] == [alert.key, alert.key]


async def test_a_study_failure_alert_reaches_the_sender(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(committed) as session:
        study = await Factory(session).study(stage=Stage.EXTRACTING)
        await WorkflowStateRepository(session).mark_failed(
            study.id, Stage.EXTRACTING, 1, "boom [BASIS-WF-007]", "BASIS-WF-007", alert_key="k"
        )
    sender = Sender()
    relay = Relay(committed, None, email_sender=sender, alert_to="ops@example.test", env="staging")  # type: ignore[arg-type]

    assert (await relay.run_once()).delivered == 1
    (email,) = sender.sent
    assert email.subject == "[Basis] (staging) Study failed at extracting: BASIS-WF-007"
    assert str(study.id) in email.text and email.idempotency_key == "study-failed:k"
    assert uuid.UUID(str(study.id))  # the study id is what the payload carries, nothing more
