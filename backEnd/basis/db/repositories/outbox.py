"""Transactional outbox (BUILD_PLAN §4).

A message is inserted in the same transaction as the state change it announces, so
either both are committed or neither is. A relay (``basis.relay``) later claims due
messages with ``FOR UPDATE SKIP LOCKED`` (several relays never deliver the same message
at once), delivers them (for example as a Temporal update), and in the same transaction
marks each one ``delivered``, failed (``attempts + 1`` and a later ``next_attempt_at``),
or ``dead``. Delivery is at-least-once; consumers must be idempotent.

Status (migration ``0002_outbox_status``): only ``pending`` rows are claimed;
``delivered_at`` is set exactly when a row becomes ``delivered`` (a database check
enforces it). A ``dead`` row is never polled again until an operator requeues it
(``docs/review_2026-10-15/S2_RUNBOOK.md``, section 5).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from basis.db.enums import OutboxStatus
from basis.db.models import OutboxMessage

WORKFLOW_SIGNAL: Final = "workflow_signal"
NOTIFICATION: Final = "notification"
MAX_ERROR_LENGTH: Final = 2000


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, kind: str, payload: dict[str, Any]) -> OutboxMessage:
        message = OutboxMessage(kind=kind, payload=payload)
        self._session.add(message)
        await self._session.flush()
        await self._session.refresh(message)
        return message

    async def claim_batch(self, limit: int) -> list[OutboxMessage]:
        """Lock up to ``limit`` pending messages that are due, oldest first.

        ``SELECT ... WHERE status = 'pending' AND next_attempt_at <= now() ORDER BY
        next_attempt_at, created_at, id LIMIT :limit FOR UPDATE SKIP LOCKED``: rows locked
        by another relay are skipped, not waited for. The locks last until the caller's
        transaction ends, so the caller marks each message in that same transaction.
        ``created_at`` breaks ties so messages enqueued together keep their order.
        """
        if limit < 1:
            raise ValueError("limit must be positive")
        stmt = (
            select(OutboxMessage)
            .where(
                OutboxMessage.status == OutboxStatus.PENDING,
                OutboxMessage.next_attempt_at <= func.now(),
            )
            .order_by(OutboxMessage.next_attempt_at, OutboxMessage.created_at, OutboxMessage.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return list((await self._session.scalars(stmt)).all())

    async def claim_due(self, limit: int = 10) -> list[OutboxMessage]:
        """Stage 1 name of :meth:`claim_batch`."""
        return await self.claim_batch(limit)

    async def mark_delivered(self, message_id: uuid.UUID, *, last_error: str | None = None) -> None:
        """``delivered`` now. ``last_error`` records a final refusal by the consumer."""
        await self._session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == message_id)
            .values(
                status=OutboxStatus.DELIVERED,
                delivered_at=func.now(),
                last_error=None if last_error is None else last_error[:MAX_ERROR_LENGTH],
            )
            .execution_options(synchronize_session=False)
        )

    async def mark_failed(self, message_id: uuid.UUID, error: str, retry_in: timedelta) -> None:
        """One more failed attempt; the message stays ``pending`` and is due in ``retry_in``."""
        if retry_in < timedelta(0):
            raise ValueError("retry_in cannot be negative")
        await self._session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == message_id)
            .values(
                attempts=OutboxMessage.attempts + 1,
                last_error=error[:MAX_ERROR_LENGTH],
                next_attempt_at=func.now() + retry_in,
            )
            .execution_options(synchronize_session=False)
        )

    async def mark_dead(self, message_id: uuid.UUID, error: str) -> None:
        """One more failed attempt, after which the message is ``dead`` (never claimed)."""
        await self._session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == message_id)
            .values(
                status=OutboxStatus.DEAD,
                attempts=OutboxMessage.attempts + 1,
                last_error=error[:MAX_ERROR_LENGTH],
            )
            .execution_options(synchronize_session=False)
        )

    async def list_for_study(self, study_id: uuid.UUID) -> list[OutboxMessage]:
        """Messages whose payload names ``study_id`` (diagnostics and tests)."""
        stmt = (
            select(OutboxMessage)
            .where(OutboxMessage.payload["study_id"].astext == str(study_id))
            .order_by(OutboxMessage.created_at, OutboxMessage.id)
        )
        return list((await self._session.scalars(stmt)).all())
