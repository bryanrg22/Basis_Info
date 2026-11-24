"""The public-geo workflow's activities: run the gather (:class:`~basis.geo.gather.Gatherer`)
and mark a gather failed when the workflow gives up on it."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from temporalio import activity

from basis.geo.contracts import FAIL_ACTIVITY, GATHER_ACTIVITY, FailInput, GatherInput, GatherResult
from basis.geo.gather import Gatherer
from basis.geo.store import GeoStore, Incident

GathererFactory = Callable[[Callable[[str], None]], Gatherer]
FAILED_REASON = "The public records could not be gathered; ask for them again."


class GeoActivities:
    def __init__(self, gatherer: GathererFactory, store: GeoStore) -> None:
        self._gatherer = gatherer
        self._store = store

    @activity.defn(name=GATHER_ACTIVITY)
    async def gather_public_geo(self, request: GatherInput) -> GatherResult:
        gatherer = self._gatherer(lambda step: activity.heartbeat(step))
        return GatherResult(status=await gatherer.run(uuid.UUID(request.gather_id)))

    @activity.defn(name=FAIL_ACTIVITY)
    async def fail_public_geo(self, request: FailInput) -> None:
        gather_id = uuid.UUID(request.gather_id)
        row = await self._store.gather(gather_id)
        if row is None:
            return
        now = datetime.now(UTC)
        await self._store.fail(
            gather_id,
            code=request.code,
            reason=FAILED_REASON,
            incident=Incident(
                code=request.code,
                source_id="gather",
                at=now,
                study_id=row.study_id,
                gather_id=gather_id,
                detail=request.detail,
            ),
            now=now,
        )
