"""The stale-heartbeat check: one alert per service per stale period.

Runs in the relay (``basis.relay.main``), the always-on process that already polls the
database, every ``ALERT_CHECK_SECONDS``. For each watched service it reads the newest
``service_heartbeats`` row (``basis.ops.heartbeat`` writes one per process); when none is
younger than ``stale_after`` the service is stale and :meth:`AlertRepository.heartbeat_stale`
raises an alert keyed by that last heartbeat. The next check during the same silence finds
the same key and raises nothing (the insert is a no-op); once the service writes again and
later falls silent, the last heartbeat differs and a new alert goes out.

A service with no row at all (never started since the table existed, or its rows pruned)
is stale under the key ``never``: one alert until it writes.

The relay checking itself is a limit, not a bug: a dead relay alerts on nothing. The Cloud
Monitoring policy on the worker pool's instance count (infra/staging/alerts.tf) covers the
whole pool being down, relay included.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.models import Alert, ServiceHeartbeat
from basis.db.repositories.alert import AlertRepository
from basis.db.session import session_scope
from basis.errors.codes import E

DEFAULT_SERVICES: Final[tuple[str, ...]] = ("worker", "relay")

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


class HeartbeatMonitor:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        stale_after: timedelta,
        check_every: float,
        services: Sequence[str] = DEFAULT_SERVICES,
        clock: Clock = utcnow,
    ) -> None:
        if stale_after <= timedelta(0) or check_every <= 0:
            raise ValueError("stale_after and check_every must be positive")
        self.stale_after = stale_after
        self.check_every = check_every
        self.services = tuple(services)
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def check_once(self) -> list[Alert]:
        """Raise an alert for every watched service that is stale now; the alerts raised."""
        now = self._clock()
        raised: list[Alert] = []
        async with session_scope(self._sessionmaker) as session:
            rows = await session.execute(
                select(ServiceHeartbeat.service, func.max(ServiceHeartbeat.heartbeat_at))
                .where(ServiceHeartbeat.service.in_(self.services))
                .group_by(ServiceHeartbeat.service)
            )
            latest: dict[str, datetime] = {service: at for service, at in rows.all()}
            alerts = AlertRepository(session)
            for service in self.services:
                last = latest.get(service)
                stale_for = None if last is None else (now - last).total_seconds()
                if last is not None and now - last <= self.stale_after:
                    continue
                alert = await alerts.heartbeat_stale(
                    service,
                    last_heartbeat_at=last,
                    stale_for_s=stale_for,
                    threshold_s=self.stale_after.total_seconds(),
                    at=now,
                )
                if alert is None:
                    continue  # this stale period was already raised
                raised.append(alert)
                logger.error(
                    "service heartbeat is stale; alert enqueued",
                    extra={
                        "error_code": E.ALERT_HEARTBEAT_STALE,
                        "service": service,
                        "last_heartbeat_at": None if last is None else last.isoformat(),
                        "stale_for_s": stale_for,
                        "alert_key": alert.key,
                    },
                )
        return raised

    async def run(self, stop: asyncio.Event) -> None:
        """Check every ``check_every`` seconds until ``stop`` is set, starting one interval
        after start-up (a fresh deployment's worker has not written yet; alerting on it at
        second zero would be noise). A failing check (database down) is logged and retried."""
        logger.debug(
            "heartbeat monitor started",
            extra={
                "stale_after_s": self.stale_after.total_seconds(),
                "check_every_s": self.check_every,
                "services": list(self.services),
            },
        )
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.check_every)
            if stop.is_set():
                return
            try:
                await self.check_once()
            except Exception:
                logger.exception("heartbeat check failed")
