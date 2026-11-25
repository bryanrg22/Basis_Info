"""Coordinates for the public views, parcels and lidar (EVAL_REPORT 101).

Every public view is requested in Web Mercator (EPSG:3857), north up. Mercator is conformal, so
a pixel is square on the ground and its ground size at the centre is the Mercator pixel times
``cos(lat)``; over a view of a few hundred metres the scale changes by about one part in 10^5
(the cosine of the latitude across the view), well below a traced corner's error. A view
therefore converts exactly like a property-capture view (docs/api/PROPERTY_CAPTURE.md section
3): ``pixel_size_m`` at the centre and heading 0. :func:`pixel_to_lonlat` is the exact inverse
through the stored Mercator box, for code that wants it.

Parcel areas are measured on the WGS84 ellipsoid with Karney's algorithm (geographiclib, the
method of PROJ's ``geod``), never on a Mercator area, which is inflated by ``1 / cos^2(lat)``
(twice at 45 degrees).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

EARTH_RADIUS_M: Final = 6_378_137.0  # the sphere of EPSG:3857 (WGS84's semi-major axis)
MAX_MERCATOR_LAT: Final = 85.051_128_779_806_59
SQFT_PER_M2: Final = 10.763_910_416_709_722
M2_PER_ACRE: Final = 4_046.856_422_4

LonLat = tuple[float, float]


def to_mercator(lon: float, lat: float) -> tuple[float, float]:
    """Spherical Web Mercator metres (EPSG:3857) of a WGS84 point."""
    if not -MAX_MERCATOR_LAT <= lat <= MAX_MERCATOR_LAT:
        raise ValueError(f"latitude {lat} is outside Web Mercator")
    x = EARTH_RADIUS_M * math.radians(lon)
    y = EARTH_RADIUS_M * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def from_mercator(x: float, y: float) -> LonLat:
    """The inverse of :func:`to_mercator`: ``(lon, lat)``."""
    lon = math.degrees(x / EARTH_RADIUS_M)
    lat = math.degrees(2 * math.atan(math.exp(y / EARTH_RADIUS_M)) - math.pi / 2)
    return lon, lat


@dataclass(frozen=True, slots=True)
class ViewBox:
    """The georeference of one north-up view: its Mercator box, its size in pixels and the
    ground pixel size at its centre."""

    xmin: float
    ymin: float
    xmax: float
    ymax: float
    width: int
    height: int
    lat: float
    lon: float
    pixel_size_m: float

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.xmin, self.ymin, self.xmax, self.ymax)

    def bbox_lonlat(self) -> tuple[float, float, float, float]:
        west, south = from_mercator(self.xmin, self.ymin)
        east, north = from_mercator(self.xmax, self.ymax)
        return (west, south, east, north)


def view_box(lat: float, lon: float, *, extent_m: float, pixel_m: float, max_px: int) -> ViewBox:
    """A square view of ``extent_m`` on the ground centred on the point, at ``pixel_m`` metres
    a pixel (coarsened when the side would pass ``max_px``)."""
    if extent_m <= 0 or pixel_m <= 0 or max_px < 16:
        raise ValueError("extent, pixel size and pixel cap must be positive")
    side = max(16, math.ceil(extent_m / pixel_m))
    if side > max_px:
        side = max_px
    ground_px = extent_m / side
    scale = 1.0 / math.cos(math.radians(lat))  # Mercator metres per ground metre
    cx, cy = to_mercator(lon, lat)
    half = extent_m * scale / 2
    return ViewBox(
        xmin=cx - half,
        ymin=cy - half,
        xmax=cx + half,
        ymax=cy + half,
        width=side,
        height=side,
        lat=lat,
        lon=lon,
        pixel_size_m=ground_px,
    )


def pixel_to_lonlat(bbox: Sequence[float], width: int, height: int, x: float, y: float) -> LonLat:
    """The lon/lat of pixel coordinates ``(x, y)`` (origin top-left, pixel edges at integers)
    on a north-up Mercator view whose box is ``bbox`` (xmin, ymin, xmax, ymax)."""
    xmin, ymin, xmax, ymax = bbox
    mx = xmin + x * (xmax - xmin) / width
    my = ymax - y * (ymax - ymin) / height
    return from_mercator(mx, my)


def lonlat_to_pixel(
    bbox: Sequence[float], width: int, height: int, lon: float, lat: float
) -> tuple[float, float]:
    """The inverse of :func:`pixel_to_lonlat`."""
    xmin, ymin, xmax, ymax = bbox
    mx, my = to_mercator(lon, lat)
    return ((mx - xmin) * width / (xmax - xmin), (ymax - my) * height / (ymax - ymin))


def ellipsoid_area_m2(rings: Sequence[Sequence[LonLat]]) -> float:
    """The area in square metres of a polygon on the WGS84 ellipsoid: the outer ring less its
    holes (GeoJSON rings, ``(lon, lat)``; orientation does not matter)."""
    from geographiclib.geodesic import Geodesic

    total = 0.0
    for index, ring in enumerate(rings):
        poly = Geodesic.WGS84.Polygon()
        points = list(ring)
        if len(points) > 1 and points[0] == points[-1]:
            points = points[:-1]
        if len(points) < 3:
            continue
        for lon, lat in points:
            poly.AddPoint(lat, lon)
        _, _, area = poly.Compute(False, True)
        total += abs(area) if index == 0 else -abs(area)
    return max(total, 0.0)


def geojson_area_m2(geometry: dict[str, object]) -> float:
    """:func:`ellipsoid_area_m2` of a GeoJSON Polygon or MultiPolygon."""
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if kind == "Polygon" and isinstance(coords, list):
        return ellipsoid_area_m2([[(p[0], p[1]) for p in ring] for ring in coords])
    if kind == "MultiPolygon" and isinstance(coords, list):
        return sum(
            ellipsoid_area_m2([[(p[0], p[1]) for p in ring] for ring in polygon])
            for polygon in coords
        )
    raise ValueError(f"not a polygon: {kind!r}")


def point_in_ring(lon: float, lat: float, ring: Sequence[Sequence[float]]) -> bool:
    """Even-odd test of a point against one ring (planar in lon/lat, which is exact enough for
    parcel and project outlines a few kilometres across)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < cross:
                inside = not inside
        j = i
    return inside


def point_in_geojson(lon: float, lat: float, geometry: dict[str, object]) -> bool:
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    polygons: list[list[list[list[float]]]]
    if kind == "Polygon" and isinstance(coords, list):
        polygons = [coords]
    elif kind == "MultiPolygon" and isinstance(coords, list):
        polygons = coords
    else:
        return False
    for polygon in polygons:
        if not polygon:
            continue
        if point_in_ring(lon, lat, polygon[0]) and not any(
            point_in_ring(lon, lat, hole) for hole in polygon[1:]
        ):
            return True
    return False


def geojson_bounds(geometry: dict[str, object]) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a GeoJSON polygon's vertices."""
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: object) -> None:
        if isinstance(node, (list, tuple)) and node and isinstance(node[0], (int, float)):
            xs.append(float(node[0]))
            ys.append(float(node[1]))
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(geometry.get("coordinates"))
    if not xs:
        raise ValueError("empty geometry")
    return (min(xs), min(ys), max(xs), max(ys))
