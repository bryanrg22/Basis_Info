"""The heartbeat a worker or relay process writes to ``service_heartbeats``.

:class:`HeartbeatWriter` upserts the process's row every ``interval`` seconds and whenever
:meth:`HeartbeatWriter.set_state` changes the lifecycle state, with the report its
``report`` callable returns at that moment. A failed write is logged and retried on the next
beat: health reporting never stops the process it reports on. Each write also deletes the
service's rows whose heartbeat is older than :data:`PRUNE_AFTER` (dead processes).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.models import ServiceHeartbeat
from basis.errors.codes import E

Service = Literal["worker", "relay"]
State = Literal["starting", "warming", "polling", "draining", "stopped", "failed"]
PRUNE_AFTER: Final = timedelta(days=1)
WRITE_TIMEOUT_S: Final = 5.0

logger = logging.getLogger(__name__)

Report = Callable[[], dict[str, Any]]
Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


class HeartbeatWriter:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        service: Service,
        build_sha: str,
        interval_s: float,
        report: Report | None = None,
        clock: Clock = utcnow,
    ) -> None:
        self.instance_id = uuid.uuid4()
        self.service: Service = service
        self.state: State = "starting"
        self._sessionmaker = sessionmaker
        self._build_sha = build_sha
        self._interval_s = interval_s
        self._report = report or dict
        self._clock = clock
        self._started_at = clock()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()

    async def beat(self) -> bool:
        """Write the row now; False (logged) when the write failed."""
        async with self._lock:
            now = self._clock()
            try:
                report = self._report()
            except Exception:  # a broken report must not stop the heartbeat
                logger.warning("heartbeat report failed", exc_info=True)
                report = {"report_error": True}
            report = {**report, "heartbeat_interval_s": self._interval_s}
            values = {
                "id": self.instance_id,
                "service": self.service,
                "build_sha": self._build_sha,
                "state": self.state,
                "started_at": self._started_at,
                "heartbeat_at": now,
                "report": report,
            }
            statement = insert(ServiceHeartbeat).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[ServiceHeartbeat.id],
                set_={
                    "state": statement.excluded.state,
                    "heartbeat_at": statement.excluded.heartbeat_at,
                    "report": statement.excluded.report,
                },
            )
            try:
                async with asyncio.timeout(WRITE_TIMEOUT_S), self._sessionmaker() as session:
                    await session.execute(statement)
                    await session.execute(
                        delete(ServiceHeartbeat).where(
                            ServiceHeartbeat.service == self.service,
                            ServiceHeartbeat.heartbeat_at < now - PRUNE_AFTER,
                        )
                    )
                    await session.commit()
            except Exception as exc:
                logger.warning(
                    "heartbeat write failed",
                    extra={
                        "error_code": E.INF_HEARTBEAT_WRITE,
                        "service": self.service,
                        "exception_type": type(exc).__name__,
                    },
                )
                return False
            return True

    def set_state(self, state: State) -> None:
        """Change the state; the loop writes it at once (never blocks the caller)."""
        self.state = state
        self._wake.set()

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            await self.beat()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._interval_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"{self.service}-heartbeat")

    async def stop(self, final_state: State = "stopped") -> None:
        """Stop the loop and write the final state."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.state = final_state
        await self.beat()
