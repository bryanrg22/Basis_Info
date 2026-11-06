"""``GET /v1/capabilities``: which engineer overrides this deployment accepts.

The app asks once per session and offers an override only when its flag is true, so a
deployment with a setting off shows exactly what it showed before the override existed
(docs/api/OVERRIDES.md, EVAL_REPORT section 90).
"""

from __future__ import annotations

from pydantic import Field

from basis.api.schemas.common import ResponseModel


class CapabilitiesOut(ResponseModel):
    object_exclusion: bool = Field(
        description="`PATCH .../photos/{document_id}/objects/{object_id}` marks a detected "
        "object Not counted (and counts it again) at the components review "
        "(setting `OBJECT_EXCLUSION`)."
    )
    unit_cost_override: bool = Field(
        description="`asset` and `component` corrections accept `unit_cost` and "
        "`unit_cost_source`, the engineer's unit price (setting `UNIT_COST_OVERRIDE`)."
    )
    room_create: bool = Field(
        description="The rooms review accepts a `room_create` correction. Always false: "
        "the API has no such correction yet (web/FRONTEND_BACKLOG.md section 14)."
    )
