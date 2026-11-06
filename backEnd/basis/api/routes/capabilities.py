"""``GET /v1/capabilities``: the engineer overrides this deployment accepts (any signed-in
user; docs/api/OVERRIDES.md). Each flag is a setting read at request time, so the answer is
what the routes themselves enforce: an override the answer calls off is refused by its
route (409 or 422), never silently dropped."""

from __future__ import annotations

from fastapi import Request

from basis.api.routes.common import read_router
from basis.api.schemas.capabilities import CapabilitiesOut
from basis.config import Settings

reads = read_router(tags=["me"])


@reads.get(
    "/capabilities",
    summary="The engineer overrides this deployment accepts",
    description="Any signed-in user, with or without a firm. `object_exclusion` "
    "(`OBJECT_EXCLUSION`) and `unit_cost_override` (`UNIT_COST_OVERRIDE`) follow their "
    "settings; `room_create` is always false. A client that gets anything but a 200 should "
    "offer none of them.",
)
async def get_capabilities(request: Request) -> CapabilitiesOut:
    settings: Settings = request.app.state.settings
    return CapabilitiesOut(
        object_exclusion=settings.object_exclusion,
        unit_cost_override=settings.unit_cost_override,
        room_create=False,
    )
