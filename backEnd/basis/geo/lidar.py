"""Heights and the footprint of the building under the pin, from the federal 3DEP lidar
(public domain) as Entwine Point Tiles on AWS (EVAL_REPORT 101).

* **The project.** ``resources.geojson`` (the index the EPT bucket's maintainers publish, about
  2.5 MB compressed) lists every project with its outline; the newest project (the year in its
  name; a name without one ranks after every dated one) whose outline holds the pin is used.
  When that project has no point in the box (its outline in the index is coarser than its
  data), the next is read, up to ``MAX_PROJECTS`` (EVAL_REPORT 101.5). The index is cached in
  memory for ``INDEX_TTL``.
* **Only the tiles over the lot.** EPT is an octree: ``ept.json`` gives the cube, each
  ``ept-hierarchy/<key>.json`` the point counts of the nodes below it (``-1`` = a further
  hierarchy file). Points are spread over every depth, so every node whose box meets the query
  box is read, and no other. The query box is the parcel's outline plus 10 m (or 80 m around the
  pin without a parcel); 14 nodes over a lot of about an acre (EVAL_REPORT 101).
* **Reading.** LAZ through ``laspy`` with the ``lazrs`` backend, on a thread (decoding is CPU).
* **What is read off the points** (all horizontal distances in ground metres: the tiles are in
  EPSG:3857, so Mercator metres are multiplied by ``cos(lat)``):

  - density: points per square metre of the query box;
  - the building under the pin: the points classified building (ASPRS class 6) on a 0.5 m grid,
    closed once with a 3 x 3 square and hole-filled (a 0.5 m cell holds 0.85 points on average
    at 3.4 per m^2, so the closing bridges the empty roof cells); the connected part that holds
    the pin, else the nearest within ``BUILDING_SEARCH_M``. Its footprint is the cells' area:
    the roof's outline as flown, overhangs included, so it reads larger than a sketch's wall
    lines. EVAL_REPORT 101 chose the grid on synthetic rotated buildings of known area: at 3.4
    points per m^2 this reads 2 to 3 % under the true outline, a 1 m grid 4 to 8 % over, and
    the 0.5 m grid without the closing 13 to 18 % under (and fails below 2 per m^2);
  - ground: the median elevation of the ground points (class 2) within 2 to 15 m of that
    footprint;
  - roof peak: the 99th percentile of the building's points above ground (the percentile
    rejects a chimney or a stray bird return; the maximum is reported beside it); eave: the
    5th percentile (the lowest roof points are the eaves; on a gable roof rising 3 m the
    percentile reads about 0.15 m above them). A flat roof reads peak and eave alike.
"""

from __future__ import annotations

import asyncio
import io
import math
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from basis.errors.codes import E
from basis.geo.geometry import from_mercator, point_in_geojson, to_mercator
from basis.geo.http import PoliteClient, SourceError

INDEX_URL: Final = (
    "https://raw.githubusercontent.com/hobuinc/usgs-lidar/master/boundaries/resources.geojson"
)
INDEX_TTL: Final = 24 * 3600.0
GROUND: Final = 2
BUILDING: Final = 6
CELL_M: Final = 0.5
BUILDING_SEARCH_M: Final = 15.0
GROUND_RING_M: Final = (2.0, 15.0)
EDGE_CELLS: Final = 2  # a footprint this close to the box's edge (1 m) is cut by it
PEAK_PERCENTILE: Final = 99.0
EAVE_PERCENTILE: Final = 5.0
CREDIT: Final = "U.S. Geological Survey, 3D Elevation Program (3DEP) lidar"
TERMS_URL: Final = (
    "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits"
)
_YEAR = re.compile(r"(?<=_)(\d{4})(?=_|$)")

_index_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def project_year(name: str) -> int | None:
    years = [int(y) for y in _YEAR.findall(name) if 1990 <= int(y) <= 2100]
    return max(years) if years else None


def rank_projects(
    features: Sequence[dict[str, Any]], lon: float, lat: float
) -> list[dict[str, Any]]:
    """Every project whose outline holds the point, newest first by the year in its name. A
    name without a year (``MN_FullState``, ``..._B22``) ranks after every dated one, since its
    age is unknown; ties go to the project with more points."""
    covering = [
        f
        for f in features
        if isinstance(f.get("geometry"), dict) and point_in_geojson(lon, lat, f["geometry"])
    ]
    return sorted(
        covering,
        key=lambda f: (
            project_year(str(f.get("properties", {}).get("name", ""))) or 0,
            int(f.get("properties", {}).get("count") or 0),
        ),
        reverse=True,
    )


def choose_project(
    features: Sequence[dict[str, Any]], lon: float, lat: float
) -> dict[str, Any] | None:
    """The first of :func:`rank_projects`, or ``None`` when no outline holds the point."""
    ranked = rank_projects(features, lon, lat)
    return ranked[0] if ranked else None


async def load_index(client: PoliteClient, *, now: float | None = None) -> list[dict[str, Any]]:
    clock = time.monotonic() if now is None else now
    cached = _index_cache.get(INDEX_URL)
    if cached is not None and clock - cached[0] < INDEX_TTL:
        return cached[1]
    fetched = await client.get(INDEX_URL)
    data = await asyncio.to_thread(fetched.json)
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise SourceError(
            E.GEO_LIDAR_UNREADABLE, "the project index has no features", url=INDEX_URL
        )
    _index_cache[INDEX_URL] = (clock, features)
    return features


@dataclass(frozen=True, slots=True)
class Node:
    key: str  # "D-X-Y-Z"
    count: int
    bounds: tuple[float, float, float, float, float, float]


def node_bounds(
    root: Sequence[float], depth: int, x: int, y: int, z: int
) -> tuple[float, float, float, float, float, float]:
    xmin, ymin, zmin, xmax, ymax, zmax = root
    n = 2**depth
    sx, sy, sz = (xmax - xmin) / n, (ymax - ymin) / n, (zmax - zmin) / n
    return (
        xmin + x * sx,
        ymin + y * sy,
        zmin + z * sz,
        xmin + (x + 1) * sx,
        ymin + (y + 1) * sy,
        zmin + (z + 1) * sz,
    )


def _meets(b: Sequence[float], box: Sequence[float]) -> bool:
    return b[0] <= box[2] and b[3] >= box[0] and b[1] <= box[3] and b[4] >= box[1]


async def nodes_over(
    client: PoliteClient, base: str, root: Sequence[float], box: Sequence[float], *, limit: int
) -> list[Node]:
    """Every node with points whose box meets ``box`` (Mercator xmin, ymin, xmax, ymax),
    walking only the hierarchy files under such nodes."""
    found: list[Node] = []
    pending = ["0-0-0-0"]
    while pending:
        start = pending.pop()
        document = (await client.get(f"{base}/ept-hierarchy/{start}.json")).json()
        if not isinstance(document, dict):
            raise SourceError(E.GEO_LIDAR_UNREADABLE, "a hierarchy file is not an object")
        frontier = [start]
        while frontier:
            key = frontier.pop()
            count = document.get(key)
            if count is None:
                continue
            d, x, y, z = (int(v) for v in key.split("-"))
            bounds = node_bounds(root, d, x, y, z)
            if not _meets(bounds, box):
                continue
            if count == -1 and key != start:
                pending.append(key)
                continue
            if count > 0:
                found.append(Node(key, int(count), bounds))
                if len(found) > limit:
                    raise SourceError(
                        E.GEO_LIDAR_UNREADABLE, f"more than {limit} lidar tiles over the lot"
                    )
            for dx in (0, 1):
                for dy in (0, 1):
                    for dz in (0, 1):
                        frontier.append(f"{d + 1}-{2 * x + dx}-{2 * y + dy}-{2 * z + dz}")
    return found


@dataclass(frozen=True, slots=True)
class Points:
    x: NDArray[np.float64]
    y: NDArray[np.float64]
    z: NDArray[np.float64]
    cls: NDArray[np.int64]

    @staticmethod
    def concat(parts: Sequence[Points]) -> Points:
        if not parts:
            empty = np.zeros(0)
            return Points(empty, empty, empty, np.zeros(0, dtype=np.int64))
        return Points(
            np.concatenate([p.x for p in parts]),
            np.concatenate([p.y for p in parts]),
            np.concatenate([p.z for p in parts]),
            np.concatenate([p.cls for p in parts]),
        )


def decode_laz(data: bytes) -> Points:
    """One LAZ tile's coordinates and classes (CPU: call through ``asyncio.to_thread``)."""
    import laspy

    try:
        las = laspy.read(io.BytesIO(data))
    except Exception as exc:  # laspy raises several types for a bad file
        raise SourceError(E.GEO_LIDAR_UNREADABLE, f"a tile is not readable LAZ: {exc}") from exc
    return Points(
        np.asarray(las.x, dtype=np.float64),
        np.asarray(las.y, dtype=np.float64),
        np.asarray(las.z, dtype=np.float64),
        np.asarray(las.classification, dtype=np.int64),
    )


@dataclass(frozen=True, slots=True)
class Building:
    footprint_m2: float
    peak_m: float
    max_m: float
    eave_m: float
    ground_elevation_m: float
    points: int
    distance_m: float  # 0 when the pin is on it
    outline_cells: int
    # The footprint comes within 1 m of the edge of the area read: it may be cut there, or be a
    # block of attached roofs that runs to it (EVAL_REPORT 101.5). Cautious: a building that
    # just fits is flagged too, and attached roofs merged well inside the area are not.
    reaches_box_edge: bool = False


@dataclass(frozen=True, slots=True)
class LidarSummary:
    project: str
    year: int | None
    ept_url: str
    points: int
    box_area_m2: float
    density_per_m2: float
    building_points: int
    ground_points: int
    building: Building | None
    nodes: int
    bytes: int
    # Newer projects over the point that had no point in the box, passed over (EVAL_REPORT
    # 101.5: an index outline can be coarser than the project's data).
    skipped: tuple[str, ...] = ()


def measure(
    points: Points, lon: float, lat: float, box: Sequence[float]
) -> tuple[int, float, Building | None]:
    """``(points in box, box ground area, the building)`` (pure; see the module docstring)."""
    from scipy import ndimage

    k = math.cos(math.radians(lat))
    xmin, ymin, xmax, ymax = box
    inside = (points.x >= xmin) & (points.x <= xmax) & (points.y >= ymin) & (points.y <= ymax)
    x, y, z, cls = points.x[inside], points.y[inside], points.z[inside], points.cls[inside]
    area = (xmax - xmin) * k * (ymax - ymin) * k
    cx, cy = to_mercator(lon, lat)
    east, north = (x - cx) * k, (y - cy) * k
    west_m, south_m = (xmin - cx) * k, (ymin - cy) * k
    nx = max(1, math.ceil((xmax - xmin) * k / CELL_M))
    ny = max(1, math.ceil((ymax - ymin) * k / CELL_M))

    def cell(
        e: NDArray[np.float64], n: NDArray[np.float64]
    ) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
        i = np.clip(((e - west_m) / CELL_M).astype(np.int64), 0, nx - 1)
        j = np.clip(((n - south_m) / CELL_M).astype(np.int64), 0, ny - 1)
        return i, j

    is_b = cls == BUILDING
    if not is_b.any():
        return int(inside.sum()), area, None
    bi, bj = cell(east[is_b], north[is_b])
    grid = np.zeros((nx, ny), dtype=bool)
    grid[bi, bj] = True
    grid = ndimage.binary_closing(grid, structure=np.ones((3, 3)), border_value=0)
    grid = ndimage.binary_fill_holes(grid)
    labels, count = ndimage.label(grid)
    if count == 0:
        return int(inside.sum()), area, None
    pi, pj = cell(np.array([0.0]), np.array([0.0]))
    label = int(labels[pi[0], pj[0]])
    distance = 0.0
    if label == 0:
        ii, jj = np.nonzero(labels)
        d = np.hypot((ii + 0.5) * CELL_M + west_m, (jj + 0.5) * CELL_M + south_m)
        nearest = int(np.argmin(d))
        distance = float(d[nearest])
        if distance > BUILDING_SEARCH_M:
            return int(inside.sum()), area, None
        label = int(labels[ii[nearest], jj[nearest]])
    mask = labels == label
    footprint = float(mask.sum()) * CELL_M * CELL_M
    # Within EDGE_CELLS of the box: the closing above treats outside the box as empty, so it
    # trims the outermost cell of a building that runs past it.
    m = EDGE_CELLS
    reaches_edge = bool(
        mask[:m, :].any() or mask[-m:, :].any() or mask[:, :m].any() or mask[:, -m:].any()
    )
    edge = mask & ~ndimage.binary_erosion(mask, structure=np.ones((3, 3)), border_value=0)
    on = mask[bi, bj]
    bz = z[is_b][on]
    near = ndimage.binary_dilation(mask, iterations=int(GROUND_RING_M[1] / CELL_M))
    close = ndimage.binary_dilation(mask, iterations=int(GROUND_RING_M[0] / CELL_M))
    ring = near & ~close
    is_g = cls == GROUND
    gi, gj = cell(east[is_g], north[is_g])
    gz = z[is_g][ring[gi, gj]]
    if gz.size == 0 or bz.size == 0:
        return int(inside.sum()), area, None
    ground = float(np.median(gz))
    peak = float(np.percentile(bz, PEAK_PERCENTILE)) - ground
    eave = float(np.percentile(bz, EAVE_PERCENTILE)) - ground
    return (
        int(inside.sum()),
        area,
        Building(
            footprint_m2=footprint,
            peak_m=peak,
            max_m=float(bz.max()) - ground,
            eave_m=eave,
            ground_elevation_m=ground,
            points=int(bz.size),
            distance_m=distance,
            outline_cells=int(edge.sum()),
            reaches_box_edge=reaches_edge,
        ),
    )


MAX_PROJECTS: Final = 3


async def summarize(
    client: PoliteClient,
    lon: float,
    lat: float,
    box_lonlat: tuple[float, float, float, float],
    *,
    max_nodes: int,
) -> LidarSummary | None:
    """The lidar summary for the point, from the newest project with points in the box.

    ``None`` when no project covers the point, or when none of the first ``MAX_PROJECTS`` that
    do has a point in the box (an index outline can be coarser than the data: EVAL_REPORT
    101.5, the Lincoln Memorial). The projects passed over are listed in ``skipped``."""
    features = await load_index(client)
    skipped: list[str] = []
    for project in rank_projects(features, lon, lat)[:MAX_PROJECTS]:
        summary = await _read_project(client, project, lon, lat, box_lonlat, max_nodes)
        if summary.points > 0:
            return replace(summary, skipped=tuple(skipped))
        skipped.append(summary.project)
    return None


async def _read_project(
    client: PoliteClient,
    project: dict[str, Any],
    lon: float,
    lat: float,
    box_lonlat: tuple[float, float, float, float],
    max_nodes: int,
) -> LidarSummary:
    props = project.get("properties", {})
    name = str(props.get("name", ""))
    ept_url = str(props.get("url", ""))
    if not ept_url.endswith("/ept.json"):
        raise SourceError(E.GEO_LIDAR_UNREADABLE, f"project {name} has no ept.json url")
    base = ept_url[: -len("/ept.json")]
    meta = (await client.get(ept_url)).json()
    srs = (meta.get("srs") or {}) if isinstance(meta, dict) else {}
    if str(srs.get("horizontal")) != "3857" or meta.get("dataType") != "laszip":
        raise SourceError(
            E.GEO_LIDAR_UNREADABLE,
            f"project {name}: srs {srs.get('horizontal')!r}, data {meta.get('dataType')!r}",
            url=ept_url,
        )
    root = meta["bounds"]
    x0, y0 = to_mercator(box_lonlat[0], box_lonlat[1])
    x1, y1 = to_mercator(box_lonlat[2], box_lonlat[3])
    box = (x0, y0, x1, y1)
    nodes = await nodes_over(client, base, root, box, limit=max_nodes)
    parts: list[Points] = []
    size = 0
    for node in nodes:
        fetched = await client.get(f"{base}/ept-data/{node.key}.laz")
        size += len(fetched.content)
        parts.append(await asyncio.to_thread(decode_laz, fetched.content))
    points = Points.concat(parts)
    in_box, area, building = await asyncio.to_thread(measure, points, lon, lat, box)
    inside = (points.x >= x0) & (points.x <= x1) & (points.y >= y0) & (points.y <= y1)
    return LidarSummary(
        project=name,
        year=project_year(name),
        ept_url=ept_url,
        points=in_box,
        box_area_m2=area,
        density_per_m2=in_box / area if area > 0 else 0.0,
        building_points=int((points.cls[inside] == BUILDING).sum()),
        ground_points=int((points.cls[inside] == GROUND).sum()),
        building=building,
        nodes=len(nodes),
        bytes=size,
    )


def box_around(lon: float, lat: float, half_m: float) -> tuple[float, float, float, float]:
    """A lon/lat box ``half_m`` ground metres each way from the point."""
    cx, cy = to_mercator(lon, lat)
    h = half_m / math.cos(math.radians(lat))
    west, south = from_mercator(cx - h, cy - h)
    east, north = from_mercator(cx + h, cy + h)
    return (west, south, east, north)
