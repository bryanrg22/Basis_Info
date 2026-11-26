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
    "(`OBJECT_EXCLUSION`), `unit_cost_override` (`UNIT_COST_OVERRIDE`), "
    "`traced_measurement` (`TRACED_MEASUREMENT`), `property_capture` (`PROPERTY_CAPTURE`), "
    "`public_geo` (`PUBLIC_GEO`), `site_drafts` (`SITE_DRAFTS` with `TRACED_MEASUREMENT`) and "
    "`facade_measure` (`GEO_FACADE` with `TRACED_MEASUREMENT`) follow their settings; "
    "`room_create` is always false. "
    "A client that gets anything but a 200 should offer none of them.",
)
async def get_capabilities(request: Request) -> CapabilitiesOut:
    settings: Settings = request.app.state.settings
    return CapabilitiesOut(
        object_exclusion=settings.object_exclusion,
        unit_cost_override=settings.unit_cost_override,
        traced_measurement=settings.traced_measurement,
        property_capture=settings.property_capture,
        public_geo=settings.public_geo,
        site_drafts=settings.site_drafts == "on" and settings.traced_measurement,
        facade_measure=settings.geo_facade and settings.traced_measurement,
        room_create=False,
    )
