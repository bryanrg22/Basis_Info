"""Public geographic sources of a study's property (docs/api/PUBLIC_GEO.md)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from basis.api.schemas.common import RequestModel, ResponseModel
from basis.errors.codes import E


class PublicViewOut(ResponseModel):
    id: uuid.UUID
    kind: Literal["current", "history"] = Field(
        description="`current`: the best public image at the point (pixel size, then leaf-off, "
        "then recency); `history`: one per year."
    )
    label: str
    source_id: str = Field(description="The registry entry (basis/geo/sources.json).")
    year: int | None = Field(description="The imagery's year (read at the point for mosaics).")
    year_label: str | None = Field(
        description="When the source mixes years, e.g. `2018-2024 by county`."
    )
    leaf_off: bool | None = Field(description="As the source states it; null when not stated.")
    lat: float = Field(description="The view's centre (the pin it was taken around).")
    lon: float
    pixel_size_m: float = Field(
        description="Ground metres per pixel at the centre, north up (Web Mercator is conformal: "
        "square on the ground). Convert a traced pixel like a property view: east = (x - width / "
        "2) * s, north = (height / 2 - y) * s."
    )
    native_pixel_m: float | None = Field(description="The source's own pixel size.")
    crs: str = Field(description="`EPSG:3857`.")
    bbox: list[float] = Field(
        description="`[xmin, ymin, xmax, ymax]` in EPSG:3857 metres: the exact georeference."
    )
    bbox_lonlat: list[float] = Field(description="`[west, south, east, north]`.")
    width: int
    height: int
    content_type: str
    sha256: str
    credit: str = Field(description="The credit line to show and print with the image.")
    terms_class: Literal["public-domain", "open-with-credit", "no-terms-stated", "restricted"]
    terms_url: str = Field(description="Where the terms were read.")
    captured_at: datetime
    stale: bool = Field(
        description="Taken around another point than the pin's current one (more than 1 m away); "
        "confirming the pin or editing its address does not make a view stale."
    )


class ParcelOut(ResponseModel):
    parcel_id: str | None
    source_id: str
    under_pin: bool = Field(description="False: one of the same owner's adjacent parcels.")
    owner: str | None
    address: str | None
    area_sqft: float = Field(description="The polygon's area on the WGS84 ellipsoid.")
    area_acres: float
    layer_acres: float | None = Field(description="The layer's own acres (assessor or deed).")
    land_value: float | None
    improvement_value: float | None
    total_value: float | None
    year_built: int | None
    building_area_sqft: float | None
    stories: float | None
    geometry: dict[str, Any] = Field(description="GeoJSON polygon, lon/lat.")
    credit: str
    terms_class: str


class SiteCheckOut(ResponseModel):
    status: Literal["agree", "adjacent_match", "disagree", "no_appraisal", "no_parcel"]
    flag: bool = Field(description="True only for `disagree`: show it as a review flag.")
    code: str | None = Field(
        description=f"`{E.GEO_SITE_AREA_MISMATCH}` (disagree) or "
        f"`{E.GEO_SITE_AREA_ADJACENT}` (adjacent)."
    )
    message: str
    appraisal_sqft: float | None = Field(description="The appraisal's `site.area_sqft`.")
    parcel_sqft: float | None = Field(description="The pin's parcel figure compared.")
    layer_sqft: float | None
    polygon_sqft: float | None
    difference_pct: float | None = Field(description="(parcel - appraisal) / appraisal.")
    parcels: list[str | None] = Field(description="The parcel ids in the set that was compared.")
    combined_sqft: float | None
    tolerance_pct: float


class LidarBuildingOut(ResponseModel):
    footprint_m2: float = Field(description="The roof's outline as flown (overhangs included).")
    footprint_sqft: float
    peak_m: float = Field(description="Roof peak above the ground around it (99th percentile).")
    max_m: float = Field(description="The highest building point (a chimney reads here).")
    eave_m: float = Field(description="Median height of the roof's edge points.")
    ground_elevation_m: float
    points: int
    distance_m: float = Field(description="0 when the pin is on the building.")


class LidarOut(ResponseModel):
    project: str
    year: int | None
    points: int
    density_per_m2: float
    building: LidarBuildingOut | None
    code: str | None = Field(
        description=f"`{E.GEO_NO_BUILDING}` when no building points are near the pin."
    )
    credit: str
    terms_class: str
    terms_url: str


class GatherOut(ResponseModel):
    id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed"]
    code: str | None
    message: str
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    pin_version: int
    stale: bool = Field(
        description="Gathered at another point than the pin's current one (more than 1 m away)."
    )
    sources: list[dict[str, Any]] = Field(
        description="Each source asked: `source`, `kind`, `status` (`ok`, `blank`: outside its "
        "coverage, `none`: nothing there, `failed`), `code`, `seconds`, `year`."
    )


class PublicPropertyOut(ResponseModel):
    enabled: bool = Field(
        description="Whether this deployment gathers public sources (`PUBLIC_GEO`)."
    )
    gather: GatherOut | None = Field(description="The newest gather.")
    current: PublicViewOut | None
    history: list[PublicViewOut] = Field(description="Newest year first.")
    parcels: list[ParcelOut]
    site_check: SiteCheckOut | None
    lidar: LidarOut | None


class GatherIn(RequestModel):
    force: bool = Field(
        default=False,
        description="Gather again even when the same pin was gathered within `GEO_RECENT_HOURS`.",
    )


class GeoIncidentOut(ResponseModel):
    code: str
    source_id: str
    what: str = Field(description="The catalogue's meaning of the code.")
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    last_study_id: uuid.UUID | None
    last_gather_id: uuid.UUID | None
    last_url: str | None
    last_http_status: int | None
    last_detail: str | None


class GeoIncidentListOut(ResponseModel):
    items: list[GeoIncidentOut]
