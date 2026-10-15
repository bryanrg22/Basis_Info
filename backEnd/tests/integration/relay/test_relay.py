"""The outbox relay against PostgreSQL and a real Temporal dev server (S2_SPEC 6, 9).

The workflow is ``fake_study_workflow.FakeStudyWorkflow`` (same names and validator
rules as the real one). Decisions are recorded through ``ReviewRepository`` exactly as
the API records them, so the relay reads real ``workflow_signal`` rows.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.client import Client
from workflowtest_support import (
    RecordingGateway,
    applied_decisions,
    due_in,
    make_due,
    outbox_row,
    outbox_rows,
    place,
    record_decision,
    seed_study,
    start_fake,
    unreachable_client,
    wait_for_stage,
)

from basis.db.enums import Decision as DecisionKind
from basis.db.enums import OutboxStatus, Stage
from basis.db.models import OutboxMessage
from basis.db.repositories import CorrectionInput, OutboxRepository
from basis.db.session import session_scope
from basis.relay.delivery import MAX_ATTEMPTS, Relay, TemporalWorkflowGateway

pytestmark = pytest.mark.integration


def relay_for(sm: async_sessionmaker[AsyncSession], client: Client, **kwargs: object) -> Relay:
    return Relay(sm, TemporalWorkflowGateway(client), **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- delivers


async def test_delivers_a_decision_with_every_field(
    sm: async_sessionmaker[AsyncSession], fake_workflows: Client, task_queue: str
) -> None:
    study = await seed_study(sm)
    handle = await start_fake(fake_workflows, study, task_queue)
    await wait_for_stage(handle, "appraisal_review")
    entity = uuid.uuid4()
    record = await record_decision(
        sm,
        study,
        corrections=[
            CorrectionInput("extraction_field", entity, {"year_built": 1998}, None, "deed")
        ],
    )

    report = await relay_for(sm, fake_workflows).run_once()

    assert (report.claimed, report.delivered) == (1, 1)
    row = await outbox_row(sm, record.signal.id)
    assert row.status is OutboxStatus.DELIVERED
    assert row.delivered_at is not None and row.last_error is None and row.attempts == 0
    view = await wait_for_stage(handle, "rooms_review")
    assert view.decisions_applied == (str(record.review.id),)
    (decision,) = await applied_decisions(handle)
    assert decision.review_id == str(record.review.id)
    assert (decision.stage, decision.revision, decision.decision) == (
        "appraisal_review",
        1,
        "approve",
    )
    assert decision.reviewer_id == str(study.created_by)
    assert decision.submitted_at == record.review.created_at
    assert len(decision.corrections) == 1
    correction = decision.corrections[0]
    assert (correction.entity_type, correction.entity_id) == ("extraction_field", str(entity))
    assert (correction.after, correction.reason) == ({"year_built": 1998}, "deed")


async def test_redelivery_is_applied_once(
    sm: async_sessionmaker[AsyncSession], fake_workflows: Client, task_queue: str
) -> None:
    """A delivery whose commit was lost is delivered again; the workflow applies it once."""
    study = await seed_study(sm)
    handle = await start_fake(fake_workflows, study, task_queue)
    await wait_for_stage(handle, "appraisal_review")
    record = await record_decision(sm, study)
    relay = relay_for(sm, fake_workflows)
    assert (await relay.run_once()).delivered == 1

    # Simulate the commit of the first delivery being lost: the row is pending again.
    async with session_scope(sm) as session:
        await session.execute(
            update(OutboxMessage).values(status=OutboxStatus.PENDING, delivered_at=None)
        )
    report = await relay.run_once()

    assert (report.delivered, report.refused) == (1, 0)
    assert (await outbox_row(sm, record.signal.id)).last_error is None
    assert len(await applied_decisions(handle)) == 1
    assert (await wait_for_stage(handle, "rooms_review")).decisions_applied == (
        str(record.review.id),
    )


async def test_a_studys_decisions_are_delivered_in_order(
    sm: async_sessionmaker[AsyncSession], fake_workflows: Client, task_queue: str
) -> None:
    """Two decisions queued while the relay was down: appraisal then rooms, in one batch."""
    study = await seed_study(sm)
    handle = await start_fake(fake_workflows, study, task_queue)
    await wait_for_stage(handle, "appraisal_review")
    first = await record_decision(sm, study)
    second = await record_decision(sm, study, stage=Stage.ROOMS_REVIEW)

    report = await relay_for(sm, fake_workflows).run_once()

    assert (report.claimed, report.delivered) == (2, 2)
    view = await wait_for_stage(handle, "assets_review")
    assert view.decisions_applied == (str(first.review.id), str(second.review.id))


# --------------------------------------------------------------------------- refused


async def test_validator_rejection_is_final(
    sm: async_sessionmaker[AsyncSession], fake_workflows: Client, task_queue: str
) -> None:
    """The workflow is past the decision's stage: refused, delivered with last_error."""
    study = await seed_study(sm)
    handle = await start_fake(fake_workflows, study, task_queue, stage="rooms_review")
    await wait_for_stage(handle, "rooms_review")
    record = await record_decision(sm, study)  # an appraisal_review decision
    relay = relay_for(sm, fake_workflows)

    report = await relay.run_once()

    assert (report.claimed, report.refused) == (1, 1)
    row = await outbox_row(sm, record.signal.id)
    assert row.status is OutboxStatus.DELIVERED and row.delivered_at is not None
    assert row.attempts == 0
    assert row.last_error is not None
    assert "wrong stage" in row.last_error and "rooms_review" in row.last_error
    assert (await relay.run_once()).claimed == 0  # never retried
    assert await applied_decisions(handle) == []


async def test_stale_revision_is_refused(
    sm: async_sessionmaker[AsyncSession], fake_workflows: Client, task_queue: str
) -> None:
    study = await seed_study(sm)
    handle = await start_fake(fake_workflows, study, task_queue)
    await place(handle, "appraisal_review", 3)
    await wait_for_stage(handle, "appraisal_review")
    record = await record_decision(sm, study)  # revision 1 < 3: the workflow is ahead

    assert (await relay_for(sm, fake_workflows).run_once()).refused == 1
    row = await outbox_row(sm, record.signal.id)
    assert row.last_error is not None and "stale revision" in row.last_error


# --------------------------------------------------------------------------- retried


async def test_a_workflow_that_is_behind_is_retried_not_refused(
    sm: async_sessionmaker[AsyncSession], fake_workflows: Client, task_queue: str
) -> None:
    """The decision is recorded, but the workflow has not reached the review stage yet."""
    study = await seed_study(sm)
    handle = await start_fake(fake_workflows, study, task_queue, stage="extracting")
    await wait_for_stage(handle, "extracting")
    record = await record_decision(sm, study)
    relay = relay_for(sm, fake_workflows)

    report = await relay.run_once()

    assert (report.claimed, report.retried, report.refused) == (1, 1, 0)
    row = await outbox_row(sm, record.signal.id)
    assert row.status is OutboxStatus.PENDING and row.attempts == 1
    assert row.last_error is not None and "has not reached appraisal_review" in row.last_error
    assert 0 < await due_in(sm, row.id) <= 2

    await place(handle, "appraisal_review", 1)
    await make_due(sm)
    assert (await relay.run_once()).delivered == 1
    assert (await wait_for_stage(handle, "rooms_review")).decisions_applied == (
        str(record.review.id),
    )


async def test_transient_errors_retry_with_exponential_backoff(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    study = await seed_study(sm)
    record = await record_decision(sm, study)
    relay = relay_for(sm, await unreachable_client())

    for attempt in (1, 2, 3):
        report = await relay.run_once()
        assert (report.claimed, report.retried) == (1, 1)
        row = await outbox_row(sm, record.signal.id)
        assert row.status is OutboxStatus.PENDING and row.attempts == attempt
        assert row.last_error is not None and "connect" in row.last_error
        # min(2**attempts, 300) s, measured from the database's now()
        assert 2**attempt - 1.5 <= await due_in(sm, row.id) <= 2**attempt
        assert (await relay.run_once()).claimed == 0  # not due yet
        await make_due(sm)


async def test_an_unknown_workflow_is_retried(
    sm: async_sessionmaker[AsyncSession], fake_workflows: Client
) -> None:
    """NOT_FOUND (no workflow yet, e.g. a start that has not happened) is not final."""
    study = await seed_study(sm)
    record = await record_decision(sm, study)

    assert (await relay_for(sm, fake_workflows).run_once()).retried == 1
    row = await outbox_row(sm, record.signal.id)
    assert row.status is OutboxStatus.PENDING and row.attempts == 1
    assert row.last_error is not None and "not found" in row.last_error.lower()


async def test_a_delivery_that_hangs_is_bounded_by_the_timeout(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    study = await seed_study(sm)
    record = await record_decision(sm, study)
    relay = Relay(sm, RecordingGateway(delay=5.0), delivery_timeout=0.2)

    started = asyncio.get_running_loop().time()
    assert (await relay.run_once()).retried == 1
    assert asyncio.get_running_loop().time() - started < 2
    row = await outbox_row(sm, record.signal.id)
    assert row.attempts == 1 and row.last_error == "timed out after 0.2 s"


# --------------------------------------------------------------------------- dead


async def test_dead_letters_after_max_attempts(sm: async_sessionmaker[AsyncSession]) -> None:
    study = await seed_study(sm)
    record = await record_decision(sm, study)
    relay = relay_for(sm, await unreachable_client(), max_attempts=3)

    for _ in range(3):
        await relay.run_once()
        await make_due(sm)

    row = await outbox_row(sm, record.signal.id)
    assert row.status is OutboxStatus.DEAD and row.attempts == 3
    assert row.delivered_at is None and row.last_error is not None
    assert (await relay.run_once()).claimed == 0  # a dead row is never claimed again


async def test_the_fiftieth_failure_is_dead(sm: async_sessionmaker[AsyncSession]) -> None:
    study = await seed_study(sm)
    record = await record_decision(sm, study)
    await make_due(sm, attempts=MAX_ATTEMPTS - 2)
    relay = relay_for(sm, await unreachable_client())  # the default threshold

    await relay.run_once()
    row = await outbox_row(sm, record.signal.id)
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, MAX_ATTEMPTS - 1)
    assert 299 < await due_in(sm, row.id) <= 300  # capped at 300 s

    await make_due(sm)
    await relay.run_once()
    row = await outbox_row(sm, record.signal.id)
    assert (row.status, row.attempts) == (OutboxStatus.DEAD, MAX_ATTEMPTS)


async def test_malformed_payload_and_unknown_kind_are_dead_at_once(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(sm) as session:
        outbox = OutboxRepository(session)
        malformed = await outbox.enqueue("workflow_signal", {"study_id": str(uuid.uuid4())})
        unknown = await outbox.enqueue("carrier_pigeon", {"study_id": str(uuid.uuid4())})
        note = await outbox.enqueue("notification", {"study_id": str(uuid.uuid4()), "n": 1})
    gateway = RecordingGateway()

    report = await Relay(sm, gateway).run_once()

    assert (report.claimed, report.dead, report.delivered) == (3, 2, 1)
    bad = await outbox_row(sm, malformed.id)
    assert bad.status is OutboxStatus.DEAD and bad.attempts == 1
    assert bad.last_error is not None and bad.last_error.startswith("malformed payload")
    odd = await outbox_row(sm, unknown.id)
    assert (
        odd.status is OutboxStatus.DEAD and odd.last_error == "unknown outbox kind 'carrier_pigeon'"
    )
    assert (await outbox_row(sm, note.id)).status is OutboxStatus.DELIVERED
    assert gateway.decided == []


# --------------------------------------------------------------------------- concurrency


async def test_two_relays_never_deliver_the_same_message(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    studies = [await seed_study(sm) for _ in range(12)]
    records = [await record_decision(sm, study) for study in studies]
    gateway = RecordingGateway(delay=0.05)  # slow enough that the two relays overlap
    relays = [Relay(sm, gateway, batch_size=4), Relay(sm, gateway, batch_size=4)]

    async def drain(relay: Relay) -> int:
        claimed = 0
        while (report := await relay.run_once()).claimed:
            claimed += report.claimed
        return claimed

    claimed = await asyncio.gather(*(drain(relay) for relay in relays))

    assert sum(claimed) == 12 and all(count > 0 for count in claimed)  # both did work
    decided = [decision.review_id for _, decision in gateway.decided]
    assert sorted(decided) == sorted(str(record.review.id) for record in records)
    assert len(decided) == len(set(decided))  # nobody delivered twice
    assert {row.status for row in await outbox_rows(sm)} == {OutboxStatus.DELIVERED}


# --------------------------------------------------------------------------- loop


async def test_run_polls_until_stopped(sm: async_sessionmaker[AsyncSession]) -> None:
    gateway = RecordingGateway()
    relay = Relay(sm, gateway, poll_seconds=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(relay.run(stop))
    await asyncio.sleep(0.1)  # running and idle

    study = await seed_study(sm)
    record = await record_decision(sm, study)
    async with asyncio.timeout(5):
        while not gateway.decided:
            await asyncio.sleep(0.02)

    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert gateway.decided[0][1].review_id == str(record.review.id)
    assert relay.last_poll is not None


async def test_run_survives_a_failing_poll(
    sm: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    relay = Relay(sm, RecordingGateway(), poll_seconds=0.01)
    calls = 0
    original = relay.run_once

    async def flaky() -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("database down")
        return await original()

    monkeypatch.setattr(relay, "run_once", flaky)
    stop = asyncio.Event()
    task = asyncio.create_task(relay.run(stop))
    async with asyncio.timeout(5):
        while calls < 3:
            await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert relay.last_poll is not None


async def test_backoff_window_is_respected_by_claims(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """A failed row is invisible to claims until its next_attempt_at passes."""
    study = await seed_study(sm)
    await record_decision(sm, study)
    async with session_scope(sm) as session:
        (message,) = await OutboxRepository(session).claim_batch(20)
        await OutboxRepository(session).mark_failed(message.id, "x", timedelta(seconds=1))
    async with session_scope(sm) as session:
        assert await OutboxRepository(session).claim_batch(20) == []
    await asyncio.sleep(1.1)
    async with session_scope(sm) as session:
        assert len(await OutboxRepository(session).claim_batch(20)) == 1


async def test_decisions_reject_path_round_trip(
    sm: async_sessionmaker[AsyncSession], fake_workflows: Client, task_queue: str
) -> None:
    """A rejection: the workflow keeps the stage and moves to the next revision."""
    study = await seed_study(sm)
    handle = await start_fake(fake_workflows, study, task_queue)
    await wait_for_stage(handle, "appraisal_review")
    await record_decision(sm, study, decision=DecisionKind.REJECT)

    assert (await relay_for(sm, fake_workflows).run_once()).delivered == 1
    async with asyncio.timeout(5):
        while (await wait_for_stage(handle, "appraisal_review")).revision != 2:
            await asyncio.sleep(0.05)


async def test_a_batch_cancelled_at_shutdown_rolls_back(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """Cancelling an in-flight batch (the SIGTERM drain timeout) counts no attempt."""
    study = await seed_study(sm)
    record = await record_decision(sm, study)
    gateway = RecordingGateway(delay=30.0)
    task = asyncio.create_task(Relay(sm, gateway).run_once())
    await asyncio.sleep(0.3)  # claimed and delivering

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    row = await outbox_row(sm, record.signal.id)
    assert (row.status, row.attempts, row.last_error) == (OutboxStatus.PENDING, 0, None)
    assert (await Relay(sm, RecordingGateway()).run_once()).delivered == 1  # lock released
