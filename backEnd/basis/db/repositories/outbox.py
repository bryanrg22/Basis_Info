"""Transactional outbox (BUILD_PLAN §4).

A message is inserted in the same transaction as the state change it announces, so
either both are committed or neither is. A relay later claims due messages with
``FOR UPDATE SKIP LOCKED`` (several relays never deliver the same message at once),
delivers them (for example as a Temporal signal), and marks them delivered or failed.
Delivery is at-least-once; consumers must be idempotent.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from basis.db.models import OutboxMessage

WORKFLOW_SIGNAL: Final = "workflow_signal"
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

    async def claim_due(self, limit: int = 10) -> list[OutboxMessage]:
        """Lock up to ``limit`` undelivered messages that are due, oldest first.

        The locks last until the caller's transaction ends, so the caller marks each
        message delivered or failed in that same transaction.
        """
        if limit < 1:
            raise ValueError("limit must be positive")
        stmt = (
            select(OutboxMessage)
            .where(
                OutboxMessage.delivered_at.is_(None),
                OutboxMessage.next_attempt_at <= func.now(),
            )
            .order_by(OutboxMessage.next_attempt_at, OutboxMessage.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return list((await self._session.scalars(stmt)).all())

    async def mark_delivered(self, message_id: uuid.UUID) -> None:
        await self._session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == message_id)
            .values(delivered_at=func.now())
            .execution_options(synchronize_session=False)
        )

    async def mark_failed(self, message_id: uuid.UUID, error: str, retry_in: timedelta) -> None:
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

    async def list_for_study(self, study_id: uuid.UUID) -> list[OutboxMessage]:
        """Messages whose payload names ``study_id`` (diagnostics and tests)."""
        stmt = (
            select(OutboxMessage)
            .where(OutboxMessage.payload["study_id"].astext == str(study_id))
            .order_by(OutboxMessage.created_at, OutboxMessage.id)
        )
        return list((await self._session.scalars(stmt)).all())
