"""Activity idempotency records (``activity_results``, S2_SPEC section 1).

One row per idempotency key ``{workflow_id}:{run_id}:{activity_id}``. The protocol an
activity follows (``basis.activities._idempotent.run_once``):

1. :meth:`ActivityResultRepository.claim` in its own short transaction: insert the key
   with status ``running`` (or find the existing row). A ``completed`` row means the work
   is already done and its stored output is returned without running anything.
2. In the work transaction: :meth:`lock` the row (``SELECT ... FOR UPDATE``), re-check
   ``completed`` (another attempt may have finished while this one waited for the lock),
   do the work, then :meth:`complete` with the output. The work and the completion commit
   together, so a crash or cancellation leaves neither partial rows nor a false
   ``completed``.
3. On failure, :meth:`fail` records the error in a separate transaction for diagnosis;
   the next attempt claims the same row and runs again.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import ActivityResult

RUNNING: Final = "running"
COMPLETED: Final = "completed"
FAILED: Final = "failed"
MAX_ERROR_LENGTH: Final = 2000


class ActivityResultRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, run_pk: uuid.UUID, activity: str, key: str) -> ActivityResult:
        """Insert ``key`` as ``running`` unless it exists; return the row either way."""
        await self._session.execute(
            pg_insert(ActivityResult)
            .values(run_id=run_pk, activity=activity, idempotency_key=key, status=RUNNING)
            .on_conflict_do_nothing(index_elements=[ActivityResult.idempotency_key])
        )
        row = await self.get(key)
        if row is None:  # pragma: no cover - the row was just inserted or already existed
            raise RuntimeError(f"activity result {key!r} vanished after claim")
        return row

    async def get(self, key: str) -> ActivityResult | None:
        stmt = (
            select(ActivityResult)
            .where(ActivityResult.idempotency_key == key)
            .execution_options(populate_existing=True)
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def lock(self, key: str) -> ActivityResult | None:
        """The row for ``key`` locked until the transaction ends (waits for other holders)."""
        stmt = (
            select(ActivityResult)
            .where(ActivityResult.idempotency_key == key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def complete(self, key: str, output_ref: str | None) -> None:
        await self._session.execute(
            update(ActivityResult)
            .where(ActivityResult.idempotency_key == key)
            .values(status=COMPLETED, output_ref=output_ref, error=None)
            .execution_options(synchronize_session=False)
        )

    async def fail(self, key: str, error: str) -> None:
        """Record a failed attempt; a completed row is never downgraded."""
        await self._session.execute(
            update(ActivityResult)
            .where(ActivityResult.idempotency_key == key, ActivityResult.status != COMPLETED)
            .values(status=FAILED, error=error[:MAX_ERROR_LENGTH])
            .execution_options(synchronize_session=False)
        )

    async def list_for_run(self, run_pk: uuid.UUID) -> list[ActivityResult]:
        stmt = (
            select(ActivityResult)
            .where(ActivityResult.run_id == run_pk)
            .order_by(ActivityResult.created_at, ActivityResult.idempotency_key)
        )
        return list((await self._session.scalars(stmt)).all())
