"""``OutboxRepository`` with ``status`` (migration 0002) against PostgreSQL.

Claims return only ``pending`` rows that are due, oldest first; ``mark_delivered`` sets
``delivered_at`` and may keep a final ``last_error``; ``mark_failed`` counts an attempt
and pushes ``next_attempt_at`` back; ``mark_dead`` counts the last attempt and parks the
row. Concurrent claims skip each other's rows (also in ``test_concurrency.py``).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import OutboxStatus
from basis.db.models import OutboxMessage
from basis.db.repositories import OutboxRepository

pytestmark = pytest.mark.integration


async def reload(session: AsyncSession, message_id: uuid.UUID) -> OutboxMessage:
    row = await session.get(OutboxMessage, message_id, populate_existing=True)
    assert row is not None
    return row


async def test_new_messages_are_pending(session: AsyncSession) -> None:
    message = await OutboxRepository(session).enqueue("notification", {"n": 1})
    assert message.status is OutboxStatus.PENDING
    assert (message.attempts, message.delivered_at, message.last_error) == (0, None, None)


async def test_claim_batch_returns_only_due_pending_rows_oldest_first(
    session: AsyncSession,
) -> None:
    repo = OutboxRepository(session)
    rows = [await repo.enqueue("notification", {"n": n}) for n in range(5)]
    await repo.mark_delivered(rows[1].id)
    await repo.mark_dead(rows[2].id, "gave up")
    await repo.mark_failed(rows[3].id, "later", timedelta(minutes=5))
    # rows[4] became due before rows[0]: claims are ordered by next_attempt_at
    await session.execute(
        update(OutboxMessage)
        .where(OutboxMessage.id == rows[4].id)
        .values(next_attempt_at=OutboxMessage.next_attempt_at - timedelta(seconds=10))
    )

    claimed = await repo.claim_batch(20)

    assert [m.id for m in claimed] == [rows[4].id, rows[0].id]
    assert [m.id for m in await repo.claim_batch(1)] == [rows[4].id]


async def test_mark_delivered_with_and_without_a_final_error(session: AsyncSession) -> None:
    repo = OutboxRepository(session)
    ok = await repo.enqueue("workflow_signal", {"n": 1})
    refused = await repo.enqueue("workflow_signal", {"n": 2})

    await repo.mark_delivered(ok.id)
    await repo.mark_delivered(refused.id, last_error="wrong stage " + "x" * 3000)

    first, second = await reload(session, ok.id), await reload(session, refused.id)
    assert first.status is OutboxStatus.DELIVERED and first.delivered_at is not None
    assert first.last_error is None and first.attempts == 0
    assert second.status is OutboxStatus.DELIVERED and second.delivered_at is not None
    assert second.last_error is not None and len(second.last_error) == 2000
    assert second.last_error.startswith("wrong stage")


async def test_mark_failed_counts_and_schedules(session: AsyncSession) -> None:
    repo = OutboxRepository(session)
    message = await repo.enqueue("workflow_signal", {"n": 1})
    before = (await reload(session, message.id)).next_attempt_at

    await repo.mark_failed(message.id, "boom", timedelta(seconds=8))
    await repo.mark_failed(message.id, "boom again", timedelta(seconds=16))

    row = await reload(session, message.id)
    assert row.status is OutboxStatus.PENDING and row.delivered_at is None
    assert (row.attempts, row.last_error) == (2, "boom again")
    # now() is the transaction start, the same instant as the insert in this test
    assert row.next_attempt_at - before == timedelta(seconds=16)


async def test_mark_dead_counts_the_last_attempt_and_is_never_claimed(
    session: AsyncSession,
) -> None:
    repo = OutboxRepository(session)
    message = await repo.enqueue("workflow_signal", {"n": 1})
    await repo.mark_failed(message.id, "one", timedelta(0))

    await repo.mark_dead(message.id, "fifty")

    row = await reload(session, message.id)
    assert (row.status, row.attempts, row.last_error) == (OutboxStatus.DEAD, 2, "fifty")
    assert row.delivered_at is None
    assert await repo.claim_batch(20) == []


async def test_status_and_delivered_at_cannot_disagree(session: AsyncSession) -> None:
    message = await OutboxRepository(session).enqueue("notification", {"n": 1})
    with pytest.raises(IntegrityError, match="ck_outbox_delivered_at_matches_status"):
        async with session.begin_nested():
            await session.execute(
                update(OutboxMessage)
                .where(OutboxMessage.id == message.id)
                .values(status=OutboxStatus.DELIVERED)
            )
