"""The parcel under the pin, and the same owner's adjacent parcels, from the state's open parcel
layer (ArcGIS feature query; EVAL_REPORT 101).

* Under the pin: ``query`` with the point (``inSR=4326``, intersects), every field, the polygon
  in lon/lat (``outSR=4326``, ``f=geojson``).
* Adjacent: when the layer has an owner field and the parcel under the pin has an owner, the
  parcels meeting the parcel's box grown by ``ADJACENT_M``, kept when the owner is the same
  (case, spaces and punctuation ignored) and the polygons come within ``TOUCH_M`` of each other
  (shared lines in parcel maps are digitised separately, so "touching" is a small gap).
* Area: each polygon on the WGS84 ellipsoid (:func:`basis.geo.geometry.geojson_area_m2`), and the
  layer's own acres where it has a field for them (the assessor's or the deed's figure, which is
  what an appraisal usually quotes).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Final

from basis.errors.codes import E
from basis.geo.geometry import (
    M2_PER_ACRE,
    geojson_area_m2,
    geojson_bounds,
    point_in_geojson,
    to_mercator,
)
from basis.geo.http import PoliteClient, SourceError
from basis.geo.registry import ParcelFields, Source

ADJACENT_M: Final = 30.0
TOUCH_M: Final = 3.0
MAX_ADJACENT: Final = 12
SQFT_PER_ACRE: Final = 43_560.0


@dataclass(frozen=True, slots=True)
class Parcel:
    source_id: str
    parcel_id: str | None
    under_pin: bool
    owner: str | None
    address: str | None
    area_m2: float
    layer_acres: float | None
    land_value: float | None
    improvement_value: float | None
    total_value: float | None
    year_built: int | None
    building_area_sqft: float | None
    stories: float | None
    geometry: dict[str, Any]
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def acres(self) -> float:
        """The layer's acres when it has them, else the polygon's."""
        return self.layer_acres if self.layer_acres is not None else self.area_m2 / M2_PER_ACRE


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip().replace(",", "")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def owner_key(owner: str | None) -> str | None:
    if not owner:
        return None
    key = re.sub(r"[^A-Z0-9]+", "", owner.upper())
    return key or None


def layer_acres(attrs: dict[str, Any], fields: ParcelFields) -> float | None:
    if fields.acres_unit is not None:
        unit = str(attrs.get(fields.acres_unit) or "").strip().upper()
        if not unit.startswith("A"):
            return None
    for name in fields.acres:
        value = _num(attrs.get(name))
        if value is not None and value > 0:
            return value
    if fields.area_sqft is not None:
        sqft = _num(attrs.get(fields.area_sqft))
        if sqft is not None and sqft > 0:
            return sqft / SQFT_PER_ACRE
    return None


def to_parcel(source: Source, feature: dict[str, Any], *, under_pin: bool) -> Parcel:
    fields = source.fields
    assert fields is not None
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise SourceError(E.GEO_SOURCE_BAD_RESPONSE, "a parcel without a polygon")
    attrs = feature.get("properties") or {}

    def get(name: str | None) -> object:
        return None if name is None else attrs.get(name)

    year = _num(get(fields.year_built))
    stories = _num(get(fields.stories))
    building_area = _num(get(fields.building_area))
    return Parcel(
        source_id=source.id,
        parcel_id=_text(get(fields.id)),
        under_pin=under_pin,
        owner=_text(get(fields.owner)),
        address=_text(get(fields.address)),
        area_m2=geojson_area_m2(geometry),
        layer_acres=layer_acres(attrs, fields),
        land_value=_num(get(fields.land)),
        improvement_value=_num(get(fields.improvements)),
        total_value=_num(get(fields.total)),
        year_built=int(year) if year is not None and 1600 <= year <= 2100 else None,
        building_area_sqft=building_area if building_area and building_area > 0 else None,
        stories=stories if stories and stories > 0 else None,
        geometry=geometry,
        attributes={k: attrs.get(k) for k in _kept(fields) if k in attrs},
    )


def _kept(fields: ParcelFields) -> list[str]:
    names = [
        fields.id,
        fields.owner,
        *fields.acres,
        fields.acres_unit,
        fields.area_sqft,
        fields.land,
        fields.improvements,
        fields.total,
        fields.year_built,
        fields.building_area,
        fields.stories,
        fields.address,
    ]
    return [n for n in names if n]


def _features(answer: object, url: str) -> list[dict[str, Any]]:
    if not isinstance(answer, dict) or "error" in answer:
        raise SourceError(
            E.GEO_SOURCE_BAD_RESPONSE, f"the parcel query failed: {str(answer)[:200]}", url=url
        )
    features = answer.get("features")
    if not isinstance(features, list):
        raise SourceError(E.GEO_SOURCE_BAD_RESPONSE, "the answer has no features", url=url)
    return [f for f in features if isinstance(f, dict)]


def _local(lon: float, lat: float, lat0: float) -> tuple[float, float]:
    x, y = to_mercator(lon, lat)
    k = math.cos(math.radians(lat0))
    return x * k, y * k


def _rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        return list(coords)
    return [ring for polygon in coords for ring in polygon]


def _segment_distance(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def gap_m(a: dict[str, Any], b: dict[str, Any]) -> float:
    """The smallest distance in ground metres between two polygons' outlines (vertex to
    segment, both ways); 0 when one's vertex lies inside the other."""
    lat0 = geojson_bounds(a)[1]
    ra = [[_local(p[0], p[1], lat0) for p in ring] for ring in _rings(a)]
    rb = [[_local(p[0], p[1], lat0) for p in ring] for ring in _rings(b)]
    for x, y in (p for ring in _rings(a) for p in ring[:1]):
        if point_in_geojson(x, y, b):
            return 0.0
    best = math.inf
    for first, second in ((ra, rb), (rb, ra)):
        for ring in first:
            for p in ring:
                for other in second:
                    for i in range(len(other) - 1):
                        best = min(best, _segment_distance(p, other[i], other[i + 1]))
    return best


async def _query(
    client: PoliteClient, source: Source, params: dict[str, str]
) -> list[dict[str, Any]]:
    url = f"{source.url.rstrip('/')}/query"
    answer = (
        await client.get(
            url,
            {
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
                **params,
            },
        )
    ).json()
    return _features(answer, url)


async def parcels_at(client: PoliteClient, source: Source, lat: float, lon: float) -> list[Parcel]:
    """The parcel(s) under the point, then the same owner's adjacent ones (module docstring).
    Empty when the layer has nothing there."""
    found = await _query(
        client, source, {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint"}
    )
    under = [to_parcel(source, f, under_pin=True) for f in found]
    if not under:
        return []
    owners = {owner_key(p.owner) for p in under} - {None}
    if source.fields is None or source.fields.owner is None or not owners:
        return under
    west, south, east, north = geojson_bounds(under[0].geometry)
    grow_lat = ADJACENT_M / 111_320.0
    grow_lon = grow_lat / max(0.01, math.cos(math.radians(lat)))
    envelope = f"{west - grow_lon},{south - grow_lat},{east + grow_lon},{north + grow_lat}"
    around = await _query(
        client, source, {"geometry": envelope, "geometryType": "esriGeometryEnvelope"}
    )
    seen = {p.parcel_id for p in under}
    adjacent: list[Parcel] = []
    for feature in around:
        try:
            parcel = to_parcel(source, feature, under_pin=False)
        except SourceError:
            continue
        if parcel.parcel_id in seen or owner_key(parcel.owner) not in owners:
            continue
        if min(gap_m(parcel.geometry, p.geometry) for p in under) > TOUCH_M:
            continue
        seen.add(parcel.parcel_id)
        adjacent.append(parcel)
        if len(adjacent) >= MAX_ADJACENT:
            break
    return under + adjacent
