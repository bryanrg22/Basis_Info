"""``POST /v1/studies/{id}/property/site-drafts``: a site item's outline drafted from the
engineer's box (docs/api/MEASURE_DRAFTS.md, EVAL_REPORT 106 and 111)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from basis.api.schemas.common import JSON_NUMBER, RequestModel, ResponseModel
from basis.errors.codes import E

Pixel = Annotated[float, Field(ge=0.0, le=100_000.0), JSON_NUMBER]
Family = Literal["driveway", "parking", "walk", "patio", "pool_deck", "pool", "deck", "fence"]


class DraftViewIn(RequestModel):
    kind: Literal["capture", "public"] = Field(
        description="`capture`: a view of `GET .../property` (a top-down one: the close aerial "
        "or a dated one); `public`: a view of `GET .../property/public`."
    )
    id: uuid.UUID


class DraftBoxIn(RequestModel):
    """The box the engineer dragged, in the view's own pixels (x right, y down)."""

    x1: Pixel
    y1: Pixel
    x2: Pixel
    y2: Pixel

    @model_validator(mode="after")
    def _ordered(self) -> DraftBoxIn:
        if not (self.x2 - self.x1 >= 4 and self.y2 - self.y1 >= 4):
            raise ValueError(
                "the box must run left to right and top to bottom, at least 4 pixels a side"
            )
        return self


class SiteDraftIn(RequestModel):
    asset_id: uuid.UUID = Field(
        description="The asset of the study's current revision the draft is for."
    )
    view: DraftViewIn
    box: DraftBoxIn
    family: Family = Field(
        description="What is in the box. A `fence` is answered with the refusal "
        f"`{E.SITE_NOT_DRAFTED}` (no method sees one from above, EVAL_REPORT 106.6), with no call."
    )
    unit: Literal["SF", "SY"] = Field(
        default="SF", description="The unit the draft's measurement is in (the asset's)."
    )


class DraftRefusalOut(ResponseModel):
    code: str = Field(description="A `BASIS-SITE-<nnn>` code (docs/ERROR_CODES.md).")
    message: str = Field(description="What happened and what to do, in plain words.")


class SiteDraftOut(ResponseModel):
    status: Literal["drafted", "refused"]
    family: Family
    measurement: dict[str, Any] | None = Field(
        description="The draft as a measurement record (docs/api/OVERRIDES.md section 4): the "
        "body `traced.measure` accepted (lon/lat, `drawn_by: model:sam3@site-v1`, `verified: "
        "false`, the view and its date) plus what it computes (`quantity`, `area_sf`, "
        "`expected_error_pct`...). Null when refused."
    )
    quantity: str | None = Field(description="The draft's quantity, a decimal string.")
    unit: Literal["SF", "SY"]
    expected_error_pct: str | None = Field(
        description="The larger of the one-pixel bound and the method's measured 90th "
        "percentile for the family (EVAL_REPORT 106.3): a starting outline, not a measurement."
    )
    refusal: DraftRefusalOut | None
