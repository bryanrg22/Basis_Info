"""How the API starts a gather's workflow, and the hook that starts one beside every capture.

:class:`TemporalGeoStarter` starts ``PublicGeoWorkflow`` with the API's Temporal client; API
tests pass a fake to ``create_app``.

:class:`CaptureStarterWithGeo` wraps the property capture's starter (``basis.capture.starter``):
after the capture's workflow has started, when ``PUBLIC_GEO`` is on and the pin has a point, it
queues a gather of the public sources for the same study and pin (``requested_by`` the capture's
requester). The capture route itself is untouched (it belongs to the capture work), and a gather
that cannot be queued or started never fails the capture: it is logged with its code and the
engineer can ask again with ``POST .../property/public``. A pin with only an address has no
point until the capture's search places it, so no gather is queued for it then.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Any, Protocol

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from basis.errors.codes import E
from basis.geo.contracts import GEO_WORKFLOW, GatherInput, geo_workflow_id

logger = logging.getLogger(__name__)


class GeoStartError(RuntimeError):
    """Temporal did not take the start (unreachable, timed out)."""


class GeoStarter(Protocol):
    async def start(self, gather_id: str) -> str:
        """Start the gather's workflow; its workflow id."""
        ...


class TemporalGeoStarter:
    def __init__(self, client: Client, *, task_queue: str, call_timeout_s: float = 5.0) -> None:
        self._client = client
        self._task_queue = task_queue
        self._timeout = call_timeout_s

    async def start(self, gather_id: str) -> str:
        workflow_id = geo_workflow_id(gather_id)
        try:
            async with asyncio.timeout(self._timeout):
                await self._client.start_workflow(
                    GEO_WORKFLOW,
                    GatherInput(gather_id=gather_id),
                    id=workflow_id,
                    task_queue=self._task_queue,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    rpc_timeout=timedelta(seconds=self._timeout),
                )
        except Exception as exc:
            raise GeoStartError(type(exc).__name__) from exc
        return workflow_id


async def queue_and_start(
    app_state: Any, study_id: uuid.UUID, *, user_id: uuid.UUID | None
) -> uuid.UUID | None:
    """Queue a gather for the study's pin and start it; the gather id, or ``None`` when there is
    no point or one is already active. Raises :class:`GeoStartError` (the row is then failed)."""
    from basis.db.repositories.public_geo import GeoActiveError, PublicGeoRepository
    from basis.db.session import SESSIONMAKER_STATE_ATTR, session_scope

    sessionmaker = getattr(app_state, SESSIONMAKER_STATE_ATTR)
    async with session_scope(sessionmaker) as session:
        repo = PublicGeoRepository(session)
        pin = await repo.pin(study_id)
        if pin is None or pin.lat is None or pin.lon is None:
            return None
        try:
            row = await repo.create_gather(study_id, user_id=user_id, pin=pin)
        except GeoActiveError:
            return None
        gather_id = row.id
    starter: GeoStarter | None = getattr(app_state, "geo_starter", None)
    try:
        if starter is None:
            raise GeoStartError("no workflow service")
        await starter.start(str(gather_id))
    except GeoStartError:
        async with session_scope(sessionmaker) as session:
            await PublicGeoRepository(session).mark_unstarted(gather_id, code=E.GEO_UNAVAILABLE)
        raise
    return gather_id


class CaptureStarterWithGeo:
    """See the module docstring. ``inner`` is the capture's own starter."""

    def __init__(self, inner: Any, app_state: Any) -> None:
        self._inner = inner
        self._state = app_state

    async def start(self, capture_id: str) -> str:
        workflow_id: str = await self._inner.start(capture_id)
        settings = getattr(self._state, "settings", None)
        if settings is None or not settings.public_geo:
            return workflow_id
        try:
            from basis.db.models import PropertyCapture
            from basis.db.session import SESSIONMAKER_STATE_ATTR, session_scope

            async with session_scope(getattr(self._state, SESSIONMAKER_STATE_ATTR)) as session:
                capture = await session.get(PropertyCapture, uuid.UUID(capture_id))
                if capture is None:
                    return workflow_id
                study_id, user_id = capture.study_id, capture.requested_by
            gather_id = await queue_and_start(self._state, study_id, user_id=user_id)
            logger.info(
                "public geo queued with the capture",
                extra={
                    "capture_id": capture_id,
                    "gather_id": str(gather_id) if gather_id else None,
                },
            )
        except Exception:
            logger.warning(
                "public geo not started with the capture",
                extra={"error_code": E.GEO_UNAVAILABLE, "capture_id": capture_id},
                exc_info=True,
            )
        return workflow_id


def with_geo(starter: Any, app_state: Any) -> Any:
    """``starter`` wrapped in :class:`CaptureStarterWithGeo` (``None`` stays ``None``)."""
    return None if starter is None else CaptureStarterWithGeo(starter, app_state)
