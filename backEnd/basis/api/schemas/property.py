"""Property pictures: the pin, the captures and their views (docs/api/PROPERTY_CAPTURE.md)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from basis.api.schemas.common import RequestModel, ResponseModel


class PinIn(RequestModel):
    """The whole pin: the address, the point (both or neither) and the engineer's
    confirmation that the point is on the right house."""

    address: str | None = Field(default=None, min_length=1, max_length=300)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    confirmed: bool = Field(
        default=False,
        description="The point is on the right house. Needs `lat` and `lon`. Nothing should "
        "be measured on the views until it is true.",
    )

    @model_validator(mode="after")
    def _whole(self) -> PinIn:
        if (self.lat is None) != (self.lon is None):
            raise ValueError("send both lat and lon, or neither")
        if self.address is None and self.lat is None:
            raise ValueError("send an address, a point, or both")
        if self.confirmed and self.lat is None:
            raise ValueError("only a point can be confirmed")
        return self


class PinOut(ResponseModel):
    address: str | None
    lat: float | None
    lon: float | None
    source: Literal["engineer", "search"] = Field(
        description="`search`: the capture's address search placed the point; `engineer`: "
        "placed or moved by hand."
    )
    place_label: str | None = Field(description="What the search matched, as shown.")
    confirmed: bool
    confirmed_at: datetime | None
    version: int = Field(description="Also the `ETag`.")
    updated_at: datetime


class ViewOut(ResponseModel):
    id: uuid.UUID
    kind: Literal["aerial", "oblique", "history", "street"]
    label: str = Field(
        description="`aerial-close`, `aerial-wide`, `side-south` ..., `history-YYYY-MM-DD` "
        "(a dated top-down view with `aerial-close`'s camera), `street-front`."
    )
    lat: float = Field(description="The point the camera looks at (the view's centre).")
    lon: float
    ground_alt_m: float
    range_m: float = Field(description="Camera to the point, metres.")
    fov_deg: float = Field(description="Vertical field of view.")
    heading_deg: float
    tilt_deg: float = Field(description="0 looks straight down.")
    pixel_size_m: float | None = Field(
        description="Metres per pixel at the centre, top-down views only (`2 * range * "
        "tan(fov / 2) / height`; EVAL_REPORT 98: within 2.0 to 2.6 % of the imagery's own "
        "scale bar, not checked against a measured length). Null for tilted views, which "
        "must not be measured on."
    )
    imagery_date: str | None = Field(
        description="As the imagery showed it (`M/D/YYYY`, read off the viewer's status bar; "
        "EVAL_REPORT 99); null when it could not be read (`imagery_date_note` says why)."
    )
    imagery_on: date | None = Field(
        default=None,
        description="`imagery_date` as a date (what a measurement's `captured_on` takes).",
    )
    imagery_date_note: str | None = Field(
        default=None,
        description="How the date was read (`status-bar`, `..., matches the address`), or why "
        "it was not. Null on views taken before EVAL_REPORT 99.",
    )
    leaf: Literal["leaf-off", "leaf-on", "unknown"] | None = Field(
        default=None,
        description="Trees bare or in leaf on the imagery date, by the month and the latitude "
        "(EVAL_REPORT 99); `unknown` between seasons, for a date known only to the year, or "
        "undated. Null on views taken before EVAL_REPORT 99.",
    )
    green_share: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Share of green vegetation pixels in a top-down view: supporting evidence "
        "for `leaf`, not the label itself (lawns and evergreens are green in every season).",
    )
    camera_lat: float | None = Field(
        default=None,
        description="Street-level views: where the camera stood (the panorama); `lat`, `lon` "
        "are the house it looks at, `heading_deg` from the one to the other.",
    )
    camera_lon: float | None = Field(default=None)
    width: int
    height: int
    sha256: str
    source: str = Field(description="The provenance line to show with the view.")
    captured_at: datetime
    pin_version: int
    stale: bool = Field(
        description="Taken at another point than the pin's current one (more than 1 m away); "
        "confirming the pin or editing its address does not make a view stale."
    )


class CaptureNoteOut(ResponseModel):
    part: Literal["history", "street", "date"]
    code: str = Field(description="The `BASIS-CAPTURE-<nnn>` (docs/ERROR_CODES.md).")
    message: str = Field(description="What to tell the engineer; never names the provider.")


class CaptureOut(ResponseModel):
    id: uuid.UUID
    status: Literal["queued", "waiting", "running", "succeeded", "failed"]
    code: str | None = Field(description="The `BASIS-CAPTURE-<nnn>` of the last pause or failure.")
    message: str = Field(
        description="What to tell the engineer, in plain words; never names the imagery "
        "provider. A pause's end is `resume_at` (show it in the engineer's time)."
    )
    resume_at: datetime | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    pin_version: int
    views: list[ViewOut] = Field(default_factory=list)
    notes: list[CaptureNoteOut] = Field(
        default_factory=list,
        description="Parts that did not work while the capture went on (EVAL_REPORT 99): "
        "history, street level, an unread date. The capture still succeeded.",
    )
    measure_on: uuid.UUID | None = Field(
        default=None,
        description="The view suggested for measuring paved areas: the newest leaf-off "
        "top-down view, else the newest dated top-down view. The engineer may pick another "
        "(a date before a renovation, for example).",
    )


class PropertyOut(ResponseModel):
    pin: PinOut | None
    capture: CaptureOut | None = Field(description="The newest capture, with its views.")
    enabled: bool = Field(description="Whether this deployment takes captures.")


class CaptureListOut(ResponseModel):
    items: list[CaptureOut]


class CaptureIn(RequestModel):
    force: bool = Field(
        default=False,
        description="Capture again even when a capture of the same pin succeeded within "
        "`CAPTURE_RECENT_HOURS`.",
    )


class IncidentOut(ResponseModel):
    code: str
    what: str = Field(description="The catalogue's meaning of the code.")
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    last_study_id: uuid.UUID | None
    last_capture_id: uuid.UUID | None
    last_url: str | None
    last_http_status: int | None
    last_detail: str | None
    last_evidence: str | None = Field(
        description="Storage prefix holding the screenshot, HTML and page record."
    )


class IncidentListOut(ResponseModel):
    items: list[IncidentOut]


class CaptureHealthOut(ResponseModel):
    """`GET /v1/ops/health`'s `property_capture` member."""

    enabled: bool
    worker: dict[str, str | None] = Field(
        description="`status`: `up`, `down` (no sign for 90 s) or `never`; `seen_at`."
    )
    breaker: dict[str, str | int | None] = Field(
        description="`state` (`closed`, `open`, `half_open`: lapsed, the next capture is the "
        "trial), `open_until`, `opened_at`, `code`, `reason`, `trips` (consecutive)."
    )
    used: dict[str, int] = Field(
        description="Navigations in their windows and the caps: `loads_hour`, "
        "`loads_hour_cap`, `loads_day`, `loads_day_cap`, `moves_hour`, `moves_hour_cap`."
    )
    last_success_at: datetime | None
    last_navigation_at: datetime | None
    capturing: bool = Field(description="A worker holds the capture lease now.")
