"""Operator alerts, raised once per occurrence (docs/api/RETENTION_AND_ALERTS.md).

An alert is two rows written in the caller's transaction: a ``notification`` outbox message
of ``type: alert`` (the relay renders it and hands it to the email sender) and an ``alerts``
row that names the occurrence with a unique ``key``. The key is what makes the alert
exactly-once at the source: a retried ``record_failure`` activity, or the next heartbeat
check during the same outage, computes the same key, the ``alerts`` insert conflicts, and
nothing is enqueued. Both inserts run in a SAVEPOINT so the conflict leaves the caller's
transaction usable (the pattern of ``DocumentRepository.create``).

Keys:

* ``study-failed:<activity key>``: the failure's ``record_failure`` activity key
  (``{workflow_id}:{run_id}:record_failure:{stage}:r{revision}:{n}``), one per failure the
  workflow recorded, stable across the activity's retries;
* ``heartbeat-stale:<service>:<last heartbeat, ISO 8601>``: one stale period of one
  service, named by the last heartbeat seen before the silence (``never`` when the service
  has no row at all).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.errors import constraint_name
from basis.db.models import Alert
from basis.db.repositories.outbox import NOTIFICATION, OutboxRepository

ALERT_NOTIFICATION: Final = "alert"
STUDY_FAILED: Final = "study_failed"
HEARTBEAT_STALE: Final = "heartbeat_stale"
STUDY_FAILED_PREFIX: Final = "study-failed:"
HEARTBEAT_STALE_PREFIX: Final = "heartbeat-stale:"
MAX_REASON: Final = 2000
_KEY_CONSTRAINT: Final = "uq_alerts_key"


def study_failed_key(activity_key: str) -> str:
    return f"{STUDY_FAILED_PREFIX}{activity_key}"


def heartbeat_stale_key(service: str, last_heartbeat_at: datetime | None) -> str:
    when = "never" if last_heartbeat_at is None else last_heartbeat_at.astimezone(UTC).isoformat()
    return f"{HEARTBEAT_STALE_PREFIX}{service}:{when}"


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def raise_once(
        self,
        *,
        key: str,
        kind: str,
        payload: dict[str, Any],
        study_id: uuid.UUID | None = None,
        service: str | None = None,
    ) -> Alert | None:
        """Enqueue the alert unless ``key`` was raised before; None means it was."""
        body = {"type": ALERT_NOTIFICATION, "kind": kind, "key": key, **payload}
        try:
            async with self._session.begin_nested():
                message = await OutboxRepository(self._session).enqueue(NOTIFICATION, body)
                alert = Alert(
                    key=key,
                    kind=kind,
                    study_id=study_id,
                    service=service,
                    outbox_id=message.id,
                    payload=body,
                )
                self._session.add(alert)
                await self._session.flush()
        except IntegrityError as exc:
            if constraint_name(exc) == _KEY_CONSTRAINT:
                return None
            raise
        await self._session.refresh(alert)
        return alert

    async def study_failed(
        self,
        study_id: uuid.UUID,
        *,
        key: str,
        stage: str,
        revision: int,
        reason: str,
        code: str | None,
        marked: bool,
        at: datetime | None = None,
    ) -> Alert | None:
        return await self.raise_once(
            key=key,
            kind=STUDY_FAILED,
            study_id=study_id,
            payload={
                "study_id": str(study_id),
                "stage": stage,
                "revision": revision,
                "reason": reason[:MAX_REASON],
                "code": code,
                "marked": marked,
                "at": (at or datetime.now(UTC)).isoformat(),
            },
        )

    async def heartbeat_stale(
        self,
        service: str,
        *,
        last_heartbeat_at: datetime | None,
        stale_for_s: float | None,
        threshold_s: float,
        at: datetime,
    ) -> Alert | None:
        return await self.raise_once(
            key=heartbeat_stale_key(service, last_heartbeat_at),
            kind=HEARTBEAT_STALE,
            service=service,
            payload={
                "service": service,
                "last_heartbeat_at": (
                    None if last_heartbeat_at is None else last_heartbeat_at.isoformat()
                ),
                "stale_for_s": None if stale_for_s is None else round(stale_for_s, 1),
                "threshold_s": threshold_s,
                "at": at.isoformat(),
            },
        )
