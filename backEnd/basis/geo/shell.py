"""The building shell from 3DEP lidar, with no model call (EVAL_REPORT 105, method M1 of
``docs/review_2026-10-15/AI_MEASUREMENT_PLAN.md``).

:func:`derive` takes the points over the lot (:class:`ShellPoints`, read by
:func:`read_points` exactly as :mod:`basis.geo.lidar` reads them, with the return numbers kept)
and drafts the shell quantities a takeoff needs: the wall footprint and perimeter, the eave and
ridge heights, the roof's planes, pitch and surface, the eave (gutter) length and the gross
wall face. It is pure (no I/O) and deterministic (the plane search uses a fixed seed). Nothing
in the priced path calls it yet: the wiring into the takeoff is a later step, behind
``GEO_SHELL``.

**The steps, and the standard each follows.**

1. *The building's points.* Where the project classified buildings (ASPRS class 6), those
   points, gridded, closed and hole-filled exactly as :func:`basis.geo.lidar.measure` grids
   them (so with no neck the roof's cells, eave and peak are the summary's). Where it did not
   (EVAL_REPORT 103: 6 of 8 houses' projects never classified buildings), the points are
   classified here the way LAStools' ``lasclassify`` does (its README: points at least
   ``-ground_offset 2`` m above the ground that form planar regions, ``-planar 0.1`` m, are
   roofs): the height above a ground surface made from the project's own ground class (1 m
   cells, the empty ones filled from the nearest); a pulse that came back once (``RETURNS``:
   77 to 88 % of class-6 points, 1 to 2 % of vegetation, EVAL_REPORT 105.2); then a plane
   fitted to each point's ``PLANAR_K`` nearest neighbours, and a point is roof when they sit
   within ``PLANAR_STD_M`` of it, reach no farther than ``PLANAR_REACH_M`` and the plane is not
   steeper than ``MAX_ROOF_SLOPE_DEG``. Classes the project marked as something else (ground,
   vegetation, noise, water, bridge) are never roof. The roof's edge, where a pulse hits the
   eave and the ground and so comes back twice, is grown back ``GROW_M`` on the roof's own
   planes. The part under the pin is cut from parts joined to it only by a neck narrower than
   about ``2 * NECK_M`` (:func:`split_necks`: a neighbour the closing bridged).
2. *The roof outline* (as flown, eaves included): the building's cells traced and simplified
   by :func:`basis.geo.lidar.trace_outline` and :func:`basis.geo.lidar.simplify_ring`
   (EVAL_REPORT 104.2).
3. *The walls*: the roof outline moved inward by the eave overhang (a mitred offset of every
   edge; a ring the offset would fold is offset on a 5 cm raster instead). The overhang is not
   visible to this lidar (EVAL_REPORT 105.2 found 3 to 29 returns under the eaves of a house),
   so it is a prior: ``overhang_m`` (default ``OVERHANG_PRIOR_M``, 18 in, the middle of the 12
   to 24 in trade guides give for houses and the most the IRC allows unsupported). No walls
   are drafted for a building that reaches the edge of the area read.
4. *Heights* above the ground ring (the ground class's median 2 to 15 m outside the outline,
   as :mod:`basis.geo.lidar`): ``eave_m`` the 5th percentile of the roof's points and the ridge
   the 99th, the summary's own reading; ``eave_main_m`` the eave edges' heights (step 6)
   grouped into levels, the highest level holding at least ``MAIN_LEVEL_SHARE`` of the eave
   length (:func:`main_eave`): a porch roof or a one-storey wing is not the wall height of a
   two-storey house, and the 5th percentile reads them. The wall height is the main eave.
5. *Roof planes* by RANSAC (M. A. Fischler and R. C. Bolles, 1981) with the normal check of
   R. Schnabel, R. Wahl and R. Klein, "Efficient RANSAC for point-cloud shape detection" (2007):
   a plane through three points is scored by the points within ``PLANE_DISTANCE_M`` of it
   whose own normal is within ``PLANE_NORMAL_DEG`` of its normal; the best is refitted to its
   points by least squares and removed, until no plane has ``MIN_PLANE_POINTS``. Each roof
   cell takes the plane most of its points belong to (the nearest planed cell where it has
   none); the roof surface is the sum of the cells' plan area over the cosine of their plane's
   slope, the standard plan area x sqrt(1 + (rise/run)^2). Points along a ridge, whose
   neighbours straddle it, can form a thin flat strip (a few % of the roof's plan).
6. *Eaves and rakes*: an outline edge is an eave where the roof inside it slopes down toward it
   (the planes of the points within ``EDGE_BAND_M`` inside the edge, one vote a point: the
   descent direction within 45 degrees of the edge's outward normal), a rake where it runs
   along the slope; on a flat plane (under ``FLAT_DEG``) every edge counts as an eave. An
   eave's height is where its planes meet the edge. The gutter is the eaves' length on the
   roof outline (the gutter hangs at the roof's edge).
7. *The wall face* (siding before openings): the wall perimeter times the wall height.

A quantity that cannot be read is not guessed: :class:`Shell` carries a ``code`` for each part
it refuses (no building under the pin, no ground class, trees over more than
``MAX_CANOPY_SHARE`` of the roof, the building at the edge of the area read, walls that do not
survive the offset, no roof plane found), and the numbers it could read. The footprint is
drafted but reads high wherever a porch sits under the roof (EVAL_REPORT 105.1): the takeoff
should keep the appraisal's GLA for it.
"""

from __future__ import annotations

import asyncio
import io
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

import numpy as np
from numpy.typing import NDArray

from basis.errors.codes import E
from basis.geo import lidar
from basis.geo.geometry import from_mercator, to_mercator
from basis.geo.http import PoliteClient, SourceError

MAX_PROJECTS: Final = lidar.MAX_PROJECTS
CELL_M: Final = lidar.CELL_M
# Classes that are never roof: ground, the three vegetation classes, low and high noise,
# water, bridge deck (ASPRS LAS 1.4 R15 table 17).
NOT_ROOF: Final = frozenset({2, 3, 4, 5, 7, 9, 17, 18})
GROUND_CELL_M: Final = 1.0
MIN_HEIGHT_M: Final = 2.0  # lasclassify -ground_offset 2
PLANAR_K: Final = 10
PLANAR_STD_M: Final = 0.10  # lasclassify -planar 0.1
PLANAR_REACH_M: Final = 2.0
MAX_ROOF_SLOPE_DEG: Final = 65.0
MIN_FOOTPRINT_M2: Final = 20.0
# 18 in: the middle of the 12 to 24 in trade guides give for a house's eaves (EVAL_REPORT 105).
OVERHANG_PRIOR_M: Final = 0.4572
FINE_CELL_M: Final = 0.05
PLANE_DISTANCE_M: Final = 0.15
PLANE_NORMAL_DEG: Final = 20.0
MIN_PLANE_POINTS: Final = 12
MAX_PLANES: Final = 24
RANSAC_ITERATIONS: Final = 300
RANSAC_SEED: Final = 105
EDGE_BAND_M: Final = 1.5
NECK_M: Final = 1.0  # parts joined by a neck under about 2 m wide are told apart
GROW_M: Final = 1.0  # the own classification's roof edge is grown back this far
LEVEL_GAP_M: Final = 1.5  # eave heights this far apart are different levels
MAIN_LEVEL_SHARE: Final = 0.25  # the main eave's level holds at least this share
# Trees over the roof: refuse when this share of the roof's cells has a first-of-many
# return more than 0.5 m above the roof (EVAL_REPORT 105.2).
MAX_CANOPY_SHARE: Final = 0.25
FLAT_DEG: Final = 2.0
STOREY_EAVE_M: Final = (5.0, 8.0)  # eaves under 5 m: one storey; under 8 m: two; else three

Classification = Literal["auto", "lidar", "own"]
# Which returns may be roof in the own classification: every one, single returns only, or
# single returns and the last return of a pulse that came back several times.
Returns = Literal["any", "single", "single_or_last"]
RETURNS: Final[Returns] = "single"


# ------------------------------------------------------------------ the points


@dataclass(frozen=True, slots=True)
class ShellPoints:
    """The points over the lot as read from the tiles: Mercator ``x``, ``y`` (EPSG:3857), ``z``
    in metres, the ASPRS class, and the pulse's return number and number of returns."""

    x: NDArray[np.float64]
    y: NDArray[np.float64]
    z: NDArray[np.float64]
    cls: NDArray[np.int64]
    return_number: NDArray[np.int64]
    number_of_returns: NDArray[np.int64]
    project: str = ""

    @staticmethod
    def concat(parts: Sequence[ShellPoints], project: str = "") -> ShellPoints:
        if not parts:
            e = np.zeros(0)
            i = np.zeros(0, dtype=np.int64)
            return ShellPoints(e, e, e, i, i, i, project)
        return ShellPoints(
            np.concatenate([p.x for p in parts]),
            np.concatenate([p.y for p in parts]),
            np.concatenate([p.z for p in parts]),
            np.concatenate([p.cls for p in parts]),
            np.concatenate([p.return_number for p in parts]),
            np.concatenate([p.number_of_returns for p in parts]),
            project,
        )

    def within(self, box: Sequence[float]) -> ShellPoints:
        x0, y0, x1, y1 = box
        k = (self.x >= x0) & (self.x <= x1) & (self.y >= y0) & (self.y <= y1)
        return ShellPoints(
            self.x[k],
            self.y[k],
            self.z[k],
            self.cls[k],
            self.return_number[k],
            self.number_of_returns[k],
            self.project,
        )


def decode_laz_returns(data: bytes) -> ShellPoints:
    """One LAZ tile's coordinates, classes and return numbers (CPU: call on a thread)."""
    import laspy

    try:
        las = laspy.read(io.BytesIO(data))
    except Exception as exc:  # laspy raises several types for a bad file
        raise SourceError(E.GEO_LIDAR_UNREADABLE, f"a tile is not readable LAZ: {exc}") from exc
    return ShellPoints(
        np.asarray(las.x, dtype=np.float64),
        np.asarray(las.y, dtype=np.float64),
        np.asarray(las.z, dtype=np.float64),
        np.asarray(las.classification, dtype=np.int64),
        np.asarray(las.return_number, dtype=np.int64),
        np.asarray(las.number_of_returns, dtype=np.int64),
    )


@dataclass(frozen=True, slots=True)
class PointsRead:
    points: ShellPoints
    project: str
    ept_url: str
    nodes: int
    bytes: int
    skipped: tuple[str, ...]


async def read_points(
    client: PoliteClient,
    lon: float,
    lat: float,
    box_lonlat: tuple[float, float, float, float],
    *,
    max_nodes: int,
) -> PointsRead | None:
    """The points in the box from the newest 3DEP project that has any there, read as
    :func:`basis.geo.lidar.summarize` reads them (the same index, ranking, octree walk and
    client), with the return numbers kept. ``None`` when no project has a point in the box."""
    features = await lidar.load_index(client)
    skipped: list[str] = []
    x0, y0 = to_mercator(box_lonlat[0], box_lonlat[1])
    x1, y1 = to_mercator(box_lonlat[2], box_lonlat[3])
    box = (x0, y0, x1, y1)
    for project in lidar.rank_projects(features, lon, lat)[:MAX_PROJECTS]:
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
        nodes = await lidar.nodes_over(client, base, meta["bounds"], box, limit=max_nodes)
        parts: list[ShellPoints] = []
        size = 0
        for node in nodes:
            fetched = await client.get(f"{base}/ept-data/{node.key}.laz")
            size += len(fetched.content)
            tile = await asyncio.to_thread(decode_laz_returns, fetched.content)
            parts.append(tile.within(box))
        points = ShellPoints.concat(parts, name)
        if points.x.size > 0:
            return PointsRead(points, name, ept_url, len(nodes), size, tuple(skipped))
        skipped.append(name)
    return None


# ------------------------------------------------------------------ the result


@dataclass(frozen=True, slots=True)
class Plane:
    normal: tuple[float, float, float]  # unit, pointing up
    slope_deg: float
    points: int
    plan_m2: float  # the roof cells it covers


@dataclass(frozen=True, slots=True)
class Edge:
    start: tuple[float, float]  # ground metres east, north of the pin
    end: tuple[float, float]
    length_m: float
    kind: Literal["eave", "rake", "other", "unknown"]
    facing: str  # N, E, S or W: the outward normal's nearest compass side
    height_m: float | None = None  # the roof's median height within 1 m inside, above ground


@dataclass(frozen=True, slots=True)
class Shell:
    """What :func:`derive` could read. ``code`` is ``None`` when every part was drafted;
    otherwise the first refusal, and ``refusals`` lists each part's (part, code, reason)."""

    code: str | None
    classification: Literal["lidar", "own"] | None
    refusals: tuple[tuple[str, str, str], ...] = ()
    # The roof as flown (eaves included).
    roof_outline_m: tuple[tuple[float, float], ...] = ()
    roof_plan_m2: float | None = None
    roof_perimeter_m: float | None = None
    # The walls: the roof outline less the overhang.
    overhang_m: float | None = None
    wall_outline_m: tuple[tuple[float, float], ...] = ()
    wall_outline_lonlat: tuple[tuple[float, float], ...] = ()
    footprint_m2: float | None = None
    perimeter_m: float | None = None
    long_side_m: float | None = None
    offset_method: Literal["mitre", "raster"] | None = None
    # Heights above the ground ring.
    ground_elevation_m: float | None = None
    eave_m: float | None = None  # the roof points' 5th percentile, as the lidar summary
    eave_main_m: float | None = None  # the eave edges' heights, median by length
    ridge_m: float | None = None
    stories_estimate: int | None = None
    # The roof.
    planes: tuple[Plane, ...] = ()
    roof_area_m2: float | None = None
    pitch_deg: float | None = None  # the planes' slope weighted by their plan area
    edges: tuple[Edge, ...] = ()
    gutter_m: float | None = None
    wall_face_m2: float | None = None
    # Diagnostics.
    building_points: int = 0
    grown_points: int = 0
    canopy_share: float | None = None
    reaches_box_edge: bool = False
    distance_m: float = 0.0


# ------------------------------------------------------------------ geometry helpers


def ring_area(ring: Sequence[tuple[float, float]]) -> float:
    """The shoelace area, positive for a counter-clockwise ring."""
    p = np.asarray(ring, dtype=np.float64)
    if len(p) < 3:
        return 0.0
    x, y = p[:, 0], p[:, 1]
    return float(0.5 * (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def ring_perimeter(ring: Sequence[tuple[float, float]]) -> float:
    p = np.asarray(ring, dtype=np.float64)
    if len(p) < 2:
        return 0.0
    return float(np.hypot(*(np.roll(p, -1, axis=0) - p).T).sum())


def long_side(ring: Sequence[tuple[float, float]]) -> float:
    """The longer side of the ring's minimum-area enclosing rectangle (rotating calipers over
    the convex hull's edges, H. Freeman and R. Shapira, 1975)."""
    from scipy.spatial import ConvexHull

    p = np.asarray(ring, dtype=np.float64)
    hull = p[ConvexHull(p).vertices]
    best_area, best_long = math.inf, 0.0
    for k in range(len(hull)):
        d = hull[(k + 1) % len(hull)] - hull[k]
        length = math.hypot(d[0], d[1])
        if length == 0:
            continue
        u = d / length
        v = np.array([-u[1], u[0]])
        a, b = hull @ u, hull @ v
        w, h = float(a.max() - a.min()), float(b.max() - b.min())
        if w * h < best_area:
            best_area, best_long = w * h, max(w, h)
    return best_long


def offset_inward_mitre(
    ring: Sequence[tuple[float, float]], d: float
) -> list[tuple[float, float]] | None:
    """The counter-clockwise ring with every edge moved ``d`` to its left (inward) and the
    corners mitred; ``None`` when an edge would turn round (it is shorter than the two corners
    take off it) or the result is not simple."""
    from basis.domain.traced import ring_problem

    p = np.asarray(ring, dtype=np.float64)
    n = len(p)
    if n < 3:
        return None
    if d == 0:
        return [(float(x), float(y)) for x, y in p]
    seg = np.roll(p, -1, axis=0) - p
    length = np.hypot(seg[:, 0], seg[:, 1])
    if bool((length == 0).any()):
        return None
    t = seg / length[:, None]
    inward = np.column_stack([-t[:, 1], t[:, 0]])
    out: list[tuple[float, float]] = []
    for k in range(n):
        a_dir, a_pt = t[k - 1], p[k - 1] + d * inward[k - 1]
        b_dir, b_pt = t[k], p[k] + d * inward[k]
        cross = a_dir[0] * b_dir[1] - a_dir[1] * b_dir[0]
        if abs(cross) < 1e-9:
            q = p[k] + d * inward[k]
        else:
            diff = b_pt - a_pt
            s = (diff[0] * b_dir[1] - diff[1] * b_dir[0]) / cross
            q = a_pt + s * a_dir
        out.append((float(q[0]), float(q[1])))
    o = np.asarray(out)
    oseg = np.roll(o, -1, axis=0) - o
    if bool(((oseg * seg).sum(axis=1) <= 0).any()):
        return None
    if ring_problem(out) is not None or ring_area(out) <= 0:
        return None
    return out


def offset_inward_raster(
    ring: Sequence[tuple[float, float]], d: float, *, cell: float = FINE_CELL_M
) -> list[tuple[float, float]] | None:
    """The ring moved ``d`` inward on a ``cell`` raster: the cells whose centre is inside the
    ring and more than ``d`` from outside, traced and simplified at one cell's diagonal (the
    largest part only). For rings the mitred offset folds."""
    from scipy import ndimage

    p = np.asarray(ring, dtype=np.float64)
    lo = p.min(axis=0) - 2 * cell
    hi = p.max(axis=0) + 2 * cell
    nx, ny = (np.ceil((hi - lo) / cell).astype(int)) + 1
    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    centres = np.column_stack(
        [(ii.ravel() + 0.5) * cell + lo[0], (jj.ravel() + 0.5) * cell + lo[1]]
    )
    inside = points_in_ring(centres, p).reshape(nx, ny)
    dist = ndimage.distance_transform_edt(inside) * cell
    keep = dist > d
    labels, count = ndimage.label(keep)
    if count == 0:
        return None
    sizes = ndimage.sum(keep, labels, range(1, count + 1))
    mask = labels == (int(np.argmax(sizes)) + 1)
    mask = ndimage.binary_fill_holes(mask)
    corners = lidar.trace_outline(mask)
    if len(corners) < 3:
        return None
    metres = [(lo[0] + i * cell, lo[1] + j * cell) for i, j in corners]
    simple = lidar.simplify_ring(metres, cell * math.sqrt(2))
    return simple if len(simple) >= 3 else None


def points_in_ring(q: NDArray[np.float64], ring: NDArray[np.float64]) -> NDArray[np.bool_]:
    """Even-odd test of every point of ``q`` against the ring (vectorized over the points)."""
    inside = np.zeros(len(q), dtype=bool)
    x, y = q[:, 0], q[:, 1]
    n = len(ring)
    for k in range(n):
        (x1, y1), (x2, y2) = ring[k], ring[(k + 1) % n]
        if y1 == y2:
            continue
        crosses = (y1 > y) != (y2 > y)
        at = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= crosses & (x < at)
    return inside


def _facing(nx: float, ny: float) -> str:
    angle = math.degrees(math.atan2(nx, ny)) % 360.0  # bearing of the outward normal
    return "NESW"[int(((angle + 45.0) % 360.0) // 90.0)]


# ------------------------------------------------------------------ the points' features


def local_normals(
    e: NDArray[np.float64], n: NDArray[np.float64], z: NDArray[np.float64], k: int = PLANAR_K
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Per point: the unit normal (pointing up) of the plane through its ``k`` nearest
    neighbours (itself included), the neighbours' standard deviation from that plane (the
    square root of the covariance's smallest eigenvalue) and the horizontal reach of the
    neighbourhood (the farthest neighbour)."""
    from scipy.spatial import cKDTree

    pts = np.column_stack([e, n, z])
    count = len(pts)
    if count < 3:
        return np.zeros((count, 3)), np.full(count, np.inf), np.full(count, np.inf)
    kk = min(k, count)
    dist, idx = cKDTree(pts).query(pts, k=kk)
    nb = pts[idx]  # (N, k, 3)
    centred = nb - nb.mean(axis=1, keepdims=True)
    cov = np.einsum("nki,nkj->nij", centred, centred) / kk
    values, vectors = np.linalg.eigh(cov)
    normal = vectors[:, :, 0]
    normal = normal * np.where(normal[:, 2:3] < 0, -1.0, 1.0)
    std = np.sqrt(np.clip(values[:, 0], 0.0, None))
    reach = np.hypot(nb[:, :, 0] - e[:, None], nb[:, :, 1] - n[:, None]).max(axis=1)
    return normal, std, reach


def ground_surface(
    e: NDArray[np.float64],
    n: NDArray[np.float64],
    z: NDArray[np.float64],
    west: float,
    south: float,
    nx: int,
    ny: int,
) -> NDArray[np.float64]:
    """The ground's height on ``GROUND_CELL_M`` cells: each cell's mean ground point, an empty
    cell taking its nearest non-empty cell's value."""
    from scipy import ndimage

    i = np.clip(((e - west) / GROUND_CELL_M).astype(np.int64), 0, nx - 1)
    j = np.clip(((n - south) / GROUND_CELL_M).astype(np.int64), 0, ny - 1)
    flat = i * ny + j
    total = np.bincount(flat, weights=z, minlength=nx * ny)
    count = np.bincount(flat, minlength=nx * ny)
    grid = np.where(count > 0, total / np.maximum(count, 1), np.nan).reshape(nx, ny)
    empty = np.isnan(grid)
    if empty.all():
        return grid
    _, (ii, jj) = ndimage.distance_transform_edt(empty, return_indices=True)
    return np.asarray(grid[ii, jj], dtype=np.float64)


@dataclass(frozen=True, slots=True)
class OwnClasses:
    """The own classification of every point: ``roof`` (planar, the returns rule met),
    ``above`` (a class that may be roof, at least ``MIN_HEIGHT_M`` above the ground: the points
    a roof edge may be grown back from) and ``hag`` (height above the ground surface)."""

    roof: NDArray[np.bool_]
    above: NDArray[np.bool_]
    hag: NDArray[np.float64]


def own_roof_points(
    e: NDArray[np.float64],
    n: NDArray[np.float64],
    z: NDArray[np.float64],
    cls: NDArray[np.int64],
    return_number: NDArray[np.int64] | None = None,
    number_of_returns: NDArray[np.int64] | None = None,
    *,
    west: float,
    south: float,
    width: float,
    height: float,
    planar_std_m: float = PLANAR_STD_M,
    returns: Returns = RETURNS,
) -> OwnClasses | None:
    """The points classified roof here (step 1 of the module docstring), or ``None`` when the
    project has no ground class to measure heights from."""
    is_g = cls == lidar.GROUND
    if not is_g.any():
        return None
    gx = max(1, math.ceil(width / GROUND_CELL_M))
    gy = max(1, math.ceil(height / GROUND_CELL_M))
    ground = ground_surface(e[is_g], n[is_g], z[is_g], west, south, gx, gy)
    gi = np.clip(((e - west) / GROUND_CELL_M).astype(np.int64), 0, gx - 1)
    gj = np.clip(((n - south) / GROUND_CELL_M).astype(np.int64), 0, gy - 1)
    hag = z - ground[gi, gj]
    above = ~np.isin(cls, list(NOT_ROOF)) & (hag >= MIN_HEIGHT_M)
    candidate = above.copy()
    if (
        returns != "any"
        and return_number is not None
        and number_of_returns is not None
        and bool((number_of_returns > 0).any())
    ):
        # A pulse that came back more than once went through something (leaves, a roof's
        # edge): 77 to 88 % of class-6 points are single returns and 1 to 2 % of vegetation
        # points (EVAL_REPORT 105.2). Under a tree the roof is the last return of such a pulse.
        keep = number_of_returns == 1
        if returns == "single_or_last":
            keep |= return_number == number_of_returns
        candidate &= keep
    roof = np.zeros(len(e), dtype=bool)
    idx = np.nonzero(candidate)[0]
    if idx.size >= 3:
        normal, std, reach = local_normals(e[idx], n[idx], z[idx])
        slope = np.degrees(np.arccos(np.clip(normal[:, 2], -1.0, 1.0)))
        planar = (std <= planar_std_m) & (reach <= PLANAR_REACH_M) & (slope <= MAX_ROOF_SLOPE_DEG)
        roof[idx[planar]] = True
    return OwnClasses(roof, above, hag)


# ------------------------------------------------------------------ roof planes


def fit_planes(
    pts: NDArray[np.float64], normals: NDArray[np.float64]
) -> tuple[list[tuple[NDArray[np.float64], float]], NDArray[np.int64]]:
    """Sequential RANSAC with a normal check (step 5): the planes as (unit normal up, offset)
    with ``normal . p = offset``, and each point's plane index (-1: none)."""
    rng = np.random.default_rng(RANSAC_SEED)
    label = np.full(len(pts), -1, dtype=np.int64)
    planes: list[tuple[NDArray[np.float64], float]] = []
    cos_tol = math.cos(math.radians(PLANE_NORMAL_DEG))
    free = np.nonzero(label < 0)[0]
    while free.size >= MIN_PLANE_POINTS and len(planes) < MAX_PLANES:
        sub, sn = pts[free], normals[free]
        best: NDArray[np.bool_] | None = None
        best_count = 0
        for _ in range(RANSAC_ITERATIONS):
            a, b, c = sub[rng.choice(len(sub), 3, replace=False)]
            normal = np.cross(b - a, c - a)
            norm = float(np.linalg.norm(normal))
            if norm < 1e-9:
                continue
            normal = normal / norm
            if normal[2] < 0:
                normal = -normal
            inl = (np.abs((sub - a) @ normal) <= PLANE_DISTANCE_M) & (sn @ normal >= cos_tol)
            count = int(inl.sum())
            if count > best_count:
                best, best_count = inl, count
        if best is None or best_count < MIN_PLANE_POINTS:
            break
        # Refit by least squares (the smallest principal axis of the inliers), take its
        # inliers again with the same tests.
        normal, offset = _pca_plane(sub[best])
        inl = (np.abs(sub @ normal - offset) <= PLANE_DISTANCE_M) & (sn @ normal >= cos_tol)
        if int(inl.sum()) < MIN_PLANE_POINTS:
            break
        normal, offset = _pca_plane(sub[inl])
        label[free[inl]] = len(planes)
        planes.append((normal, offset))
        free = np.nonzero(label < 0)[0]
    return planes, label


def _pca_plane(p: NDArray[np.float64]) -> tuple[NDArray[np.float64], float]:
    centre = p.mean(axis=0)
    _, vectors = np.linalg.eigh(np.cov((p - centre).T))
    normal = vectors[:, 0]
    if normal[2] < 0:
        normal = -normal
    return normal, float(normal @ centre)


# ------------------------------------------------------------------ derive


def _disk(radius: int) -> NDArray[np.bool_]:
    r = np.arange(-radius, radius + 1)
    return np.asarray((r[:, None] ** 2 + r[None, :] ** 2) <= radius * radius)


def split_necks(mask: NDArray[np.bool_], pin: tuple[int, int], radius: int) -> NDArray[np.bool_]:
    """The part of ``mask`` that belongs to the pin when parts joined only by a neck narrower
    than about ``2 * radius`` cells are told apart: the mask is eroded by a disk of ``radius``
    cells, every cell of the mask goes to the nearest eroded part, and the pin's part (the
    eroded part nearest the pin) is kept. A building with no such neck comes back whole: its
    thin parts (a porch roof along a wall) go to the one eroded part beside them. Two roofs a
    closing bridged across a gap (EVAL_REPORT 105.2: a neighbour 1 m away) are told apart."""
    from scipy import ndimage

    if radius <= 0:
        return mask
    eroded = ndimage.binary_erosion(mask, structure=_disk(radius), border_value=0)
    labels, count = ndimage.label(eroded)
    if count <= 1:
        return mask
    _, (ii, jj) = ndimage.distance_transform_edt(labels == 0, return_indices=True)
    nearest = labels[ii, jj]
    return np.asarray(mask & (nearest == nearest[pin]))


def _grid_component(
    e: NDArray[np.float64],
    n: NDArray[np.float64],
    *,
    west: float,
    south: float,
    nx: int,
    ny: int,
    neck_cells: int,
) -> tuple[NDArray[np.bool_], float] | None:
    """The 0.5 m mask of the building under the pin (or the nearest within
    ``lidar.BUILDING_SEARCH_M``), closed once and hole-filled exactly as
    :func:`basis.geo.lidar.measure` does, cut at necks (:func:`split_necks`), and its distance
    from the pin."""
    from scipy import ndimage

    if e.size == 0:
        return None
    i = np.clip(((e - west) / CELL_M).astype(np.int64), 0, nx - 1)
    j = np.clip(((n - south) / CELL_M).astype(np.int64), 0, ny - 1)
    grid = np.zeros((nx, ny), dtype=bool)
    grid[i, j] = True
    grid = ndimage.binary_closing(grid, structure=np.ones((3, 3)), border_value=0)
    grid = ndimage.binary_fill_holes(grid)
    labels, count = ndimage.label(grid)
    if count == 0:
        return None
    pi = min(max(int((0.0 - west) / CELL_M), 0), nx - 1)
    pj = min(max(int((0.0 - south) / CELL_M), 0), ny - 1)
    label = int(labels[pi, pj])
    distance = 0.0
    if label == 0:
        ii, jj = np.nonzero(labels)
        d = np.hypot((ii + 0.5) * CELL_M + west, (jj + 0.5) * CELL_M + south)
        nearest = int(np.argmin(d))
        distance = float(d[nearest])
        if distance > lidar.BUILDING_SEARCH_M:
            return None
        label = int(labels[ii[nearest], jj[nearest]])
        pi, pj = int(ii[nearest]), int(jj[nearest])
    mask = split_necks(labels == label, (pi, pj), neck_cells)
    return mask, distance


def _planes_on(
    pts: NDArray[np.float64],
) -> tuple[list[tuple[NDArray[np.float64], float]], NDArray[np.int64]]:
    if len(pts) < MIN_PLANE_POINTS:
        return [], np.full(len(pts), -1, dtype=np.int64)
    normals, _, _ = local_normals(pts[:, 0], pts[:, 1], pts[:, 2])
    return fit_planes(pts, normals)


def derive(
    points: ShellPoints,
    lon: float,
    lat: float,
    box: Sequence[float],
    *,
    classification: Classification = "auto",
    overhang_m: float = OVERHANG_PRIOR_M,
    planar_std_m: float = PLANAR_STD_M,
    returns: Returns = RETURNS,
    neck_m: float = NECK_M,
    grow_edges: bool = True,
) -> Shell:
    """The shell of the building under the pin (the module docstring has the steps).

    ``box`` is the area read in Mercator (xmin, ymin, xmax, ymax), as
    :func:`basis.geo.lidar.measure` takes it. ``classification``: ``lidar`` uses class 6 only,
    ``own`` classifies the points here even where the project has class 6 (to compare), and
    ``auto`` uses class 6 when it finds the building and classifies here otherwise. The other
    keywords are the settings EVAL_REPORT 105 compared (their defaults are its choices)."""
    from scipy import ndimage

    if overhang_m < 0:
        raise ValueError("the overhang cannot be negative")
    refusals: list[tuple[str, str, str]] = []
    k = math.cos(math.radians(lat))
    xmin, ymin, xmax, ymax = box
    inside = (points.x >= xmin) & (points.x <= xmax) & (points.y >= ymin) & (points.y <= ymax)
    x, y, z, cls = points.x[inside], points.y[inside], points.z[inside], points.cls[inside]
    rn, nr = points.return_number[inside], points.number_of_returns[inside]
    cx, cy = to_mercator(lon, lat)
    east, north = (x - cx) * k, (y - cy) * k
    west_m, south_m = (xmin - cx) * k, (ymin - cy) * k
    width, height = (xmax - xmin) * k, (ymax - ymin) * k
    nx = max(1, math.ceil(width / CELL_M))
    ny = max(1, math.ceil(height / CELL_M))
    neck_cells = int(round(neck_m / CELL_M))

    def component(sel: NDArray[np.bool_]) -> tuple[NDArray[np.bool_], float] | None:
        if not sel.any():
            return None
        return _grid_component(
            east[sel], north[sel], west=west_m, south=south_m, nx=nx, ny=ny, neck_cells=neck_cells
        )

    def refuse(code: str, reason: str) -> Shell:
        return Shell(code, None, (("building", code, reason),))

    if east.size == 0:
        return refuse(E.GEO_SHELL_NO_BUILDING, "no lidar point in the area read")
    ci = np.clip(((east - west_m) / CELL_M).astype(np.int64), 0, nx - 1)
    cj = np.clip(((north - south_m) / CELL_M).astype(np.int64), 0, ny - 1)

    # Step 1: the building's points.
    chosen: Literal["lidar", "own"] | None = None
    roof_pts: NDArray[np.bool_] | None = None
    found: tuple[NDArray[np.bool_], float] | None = None
    grown = 0
    if classification in ("auto", "lidar"):
        is_b = cls == lidar.BUILDING
        found = component(is_b)
        if found is not None:
            chosen, roof_pts = "lidar", is_b
    if found is None and classification in ("auto", "own"):
        own = own_roof_points(
            east,
            north,
            z,
            cls,
            rn,
            nr,
            west=west_m,
            south=south_m,
            width=width,
            height=height,
            planar_std_m=planar_std_m,
            returns=returns,
        )
        if own is None:
            return refuse(
                E.GEO_SHELL_NO_GROUND,
                "the project has no ground-classified points to measure heights from",
            )
        found = component(own.roof)
        if found is not None:
            chosen, roof_pts = "own", own.roof
            if grow_edges:
                # The roof's edge is where a pulse hits the eave and the ground (two returns),
                # so the returns rule drops it: grow it back from the points above the ground
                # within GROW_M of the roof that lie on the plane of the roof cell nearest them
                # (EVAL_REPORT 105.2). A tree beside the roof is not on its planes.
                seed_mask = found[0]
                seed = own.roof & seed_mask[ci, cj]
                pts0 = np.column_stack([east[seed], north[seed], z[seed]])
                planes0, plane_of0 = _planes_on(pts0)
                if planes0:
                    cells0 = _cell_planes(seed_mask, ci[seed], cj[seed], plane_of0, len(planes0))
                    zone = ndimage.binary_dilation(
                        seed_mask, iterations=int(round(GROW_M / CELL_M))
                    )
                    near = own.above & ~own.roof & zone[ci, cj]
                    idx = np.nonzero(near)[0]
                    if idx.size:
                        _, (ii, jj) = ndimage.distance_transform_edt(
                            cells0 < 0, return_indices=True
                        )
                        q = cells0[ii[ci[idx], cj[idx]], jj[ci[idx], cj[idx]]]
                        normal = np.array([planes0[int(v)][0] for v in q])
                        offset = np.array([planes0[int(v)][1] for v in q])
                        p3 = np.column_stack([east[idx], north[idx], z[idx]])
                        on_plane = np.abs((p3 * normal).sum(axis=1) - offset) <= PLANE_DISTANCE_M
                        add = np.zeros(len(east), dtype=bool)
                        add[idx[on_plane]] = True
                        grown = int(add.sum())
                        if grown:
                            regrown = component(own.roof | add)
                            if regrown is not None:
                                found, roof_pts = regrown, own.roof | add
    if found is None or roof_pts is None or chosen is None:
        return refuse(
            E.GEO_SHELL_NO_BUILDING,
            "no building points under the pin or within "
            f"{lidar.BUILDING_SEARCH_M:g} m of it"
            + (" (the project has no building class)" if not (cls == lidar.BUILDING).any() else ""),
        )
    mask, distance = found
    plan_m2 = float(mask.sum()) * CELL_M * CELL_M
    if plan_m2 < MIN_FOOTPRINT_M2:
        return refuse(
            E.GEO_SHELL_NO_BUILDING,
            f"the building found covers {plan_m2:.1f} m2, under {MIN_FOOTPRINT_M2:g} m2",
        )
    m = lidar.EDGE_CELLS
    reaches_edge = bool(
        mask[:m, :].any() or mask[-m:, :].any() or mask[:, :m].any() or mask[:, -m:].any()
    )

    # Step 4: heights above the ground ring.
    on = roof_pts & mask[ci, cj]
    near_ring = ndimage.binary_dilation(mask, iterations=int(lidar.GROUND_RING_M[1] / CELL_M))
    close_ring = ndimage.binary_dilation(mask, iterations=int(lidar.GROUND_RING_M[0] / CELL_M))
    ring = near_ring & ~close_ring
    is_g = (cls == lidar.GROUND) & ring[ci, cj]
    if not is_g.any():
        return refuse(
            E.GEO_SHELL_NO_GROUND,
            "no ground-classified points 2 to 15 m around the building",
        )
    ground = float(np.median(z[is_g]))
    bz = z[on]
    eave = float(np.percentile(bz, lidar.EAVE_PERCENTILE)) - ground
    ridge = float(np.percentile(bz, lidar.PEAK_PERCENTILE)) - ground

    # Step 2: the roof outline, in ground metres east and north of the pin.
    from basis.domain.traced import ring_problem

    corners = lidar.trace_outline(mask)
    metres = [(west_m + i * CELL_M, south_m + j * CELL_M) for i, j in corners]
    roof_ring = lidar.simplify_ring(metres, lidar.OUTLINE_TOLERANCE_M)
    if len(roof_ring) < 3 or ring_problem(roof_ring) is not None:
        roof_ring = metres
    if reaches_edge:
        refusals.append(
            (
                "walls",
                E.GEO_LIDAR_FOOTPRINT_UNCERTAIN,
                "the building comes within 1 m of the edge of the area read: it may be cut "
                "there or be a block of attached roofs",
            )
        )

    # Step 3: the walls (none from a roof the area read may cut or that runs into others).
    walls: list[tuple[float, float]] | None = None
    method: Literal["mitre", "raster"] | None = None
    if not reaches_edge:
        walls = offset_inward_mitre(roof_ring, overhang_m)
        method = "mitre"
        if walls is None:
            walls = offset_inward_raster(roof_ring, overhang_m)
            method = "raster"
    if walls is None and not reaches_edge:
        method = None
        refusals.append(
            (
                "walls",
                E.GEO_SHELL_WALLS_UNREAD,
                f"the roof outline does not survive moving it {overhang_m:.2f} m inward",
            )
        )

    # Step 5: the planes.
    pts = np.column_stack([east[on], north[on], bz])
    planes, plane_of = _planes_on(pts)
    plane_list: list[Plane] = []
    roof_area: float | None = None
    pitch: float | None = None
    if planes:
        cell_plane = _cell_planes(mask, ci[on], cj[on], plane_of, len(planes))
        slopes = [math.degrees(math.acos(min(1.0, abs(float(nrm[2]))))) for nrm, _ in planes]
        cells = [int((cell_plane == q).sum()) for q in range(len(planes))]
        cell_area = CELL_M * CELL_M
        roof_area = sum(
            c * cell_area / math.cos(math.radians(s)) for c, s in zip(cells, slopes, strict=True)
        )
        covered = sum(cells)
        pitch = (
            sum(c * s for c, s in zip(cells, slopes, strict=True)) / covered if covered else None
        )
        for q, ((normal, _), s, c) in enumerate(zip(planes, slopes, cells, strict=True)):
            plane_list.append(
                Plane(
                    (float(normal[0]), float(normal[1]), float(normal[2])),
                    s,
                    int((plane_of == q).sum()),
                    c * cell_area,
                )
            )
    else:
        refusals.append(
            ("roof", E.GEO_SHELL_NO_PLANES, "no roof plane of at least 12 points was found")
        )

    # Step 6: eaves and rakes, and each eave's height.
    edges: list[Edge] = []
    gutter: float | None = None
    eave_main: float | None = None
    if planes:
        edges = _classify_edges(roof_ring, pts, plane_of, planes, ground)
        gutter = sum(ed.length_m for ed in edges if ed.kind == "eave")
        eave_main = main_eave(edges)

    # Trees over the roof: the share of roof cells with a first-of-many return above the roof.
    canopy: float | None = None
    if bool((nr > 0).any()):
        over = (rn == 1) & (nr > 1) & mask[ci, cj] & (z - ground > eave + 0.5)
        if planes:
            cp = _cell_planes(mask, ci[on], cj[on], plane_of, len(planes))
            q = cp[ci, cj]
            has = over & (q >= 0)
            if has.any():
                pn = np.array([planes[int(v)][0] for v in q[has]])
                po = np.array([planes[int(v)][1] for v in q[has]])
                p3 = np.column_stack([east[has], north[has], z[has]])
                above = (p3 * pn).sum(axis=1) - po > 0.5
                keep = np.zeros(len(z), dtype=bool)
                keep[np.nonzero(has)[0][above]] = True
                over = keep
        cov = np.zeros_like(mask)
        cov[ci[over], cj[over]] = True
        canopy = float((cov & mask).sum()) / float(mask.sum())
        if canopy > MAX_CANOPY_SHARE:
            return Shell(
                E.GEO_SHELL_UNDER_TREES,
                chosen,
                (
                    (
                        "building",
                        E.GEO_SHELL_UNDER_TREES,
                        f"trees stand over {canopy:.0%} of the roof: its points cannot be told "
                        "from the trees'",
                    ),
                ),
                roof_outline_m=tuple(roof_ring),
                roof_plan_m2=plan_m2,
                canopy_share=canopy,
                building_points=int(on.sum()),
                distance_m=distance,
            )

    perimeter = ring_perimeter(walls) if walls is not None else None
    lonlat = (
        tuple(from_mercator(cx + ex / k, cy + ny_ / k) for ex, ny_ in walls)
        if walls is not None
        else ()
    )
    wall_h = eave_main if eave_main is not None else eave
    first = refusals[0][1] if refusals else None
    return Shell(
        code=first,
        classification=chosen,
        refusals=tuple(refusals),
        roof_outline_m=tuple(roof_ring),
        roof_plan_m2=plan_m2,
        roof_perimeter_m=ring_perimeter(roof_ring),
        overhang_m=overhang_m,
        wall_outline_m=tuple(walls) if walls is not None else (),
        wall_outline_lonlat=lonlat,
        footprint_m2=ring_area(walls) if walls is not None else None,
        perimeter_m=perimeter,
        long_side_m=long_side(walls) if walls is not None else None,
        offset_method=method,
        ground_elevation_m=ground,
        eave_m=eave,
        eave_main_m=eave_main,
        ridge_m=ridge,
        stories_estimate=_stories(eave_main if eave_main is not None else eave),
        planes=tuple(plane_list),
        roof_area_m2=roof_area,
        pitch_deg=pitch,
        edges=tuple(edges),
        gutter_m=gutter,
        wall_face_m2=perimeter * wall_h if perimeter is not None else None,
        building_points=int(on.sum()),
        grown_points=grown,
        canopy_share=canopy,
        reaches_box_edge=reaches_edge,
        distance_m=distance,
    )


def _stories(eave_m: float) -> int:
    return 1 if eave_m < STOREY_EAVE_M[0] else 2 if eave_m < STOREY_EAVE_M[1] else 3


def _weighted_median(pairs: Sequence[tuple[float, float]]) -> float:
    """The value at which half the total weight lies below (pairs are (value, weight))."""
    ordered = sorted(pairs)
    total = sum(w for _, w in ordered)
    run = 0.0
    for value, w in ordered:
        run += w
        if run >= total / 2:
            return value
    return ordered[-1][0]


def _cell_planes(
    mask: NDArray[np.bool_],
    ci: NDArray[np.int64],
    cj: NDArray[np.int64],
    plane_of: NDArray[np.int64],
    count: int,
) -> NDArray[np.int64]:
    """Each roof cell's plane: the one most of its points belong to; a cell with no planed
    point takes the nearest planed cell's. Cells off the roof are -1."""
    from scipy import ndimage

    nx, ny = mask.shape
    votes = np.zeros((nx * ny, count), dtype=np.int64)
    has = plane_of >= 0
    np.add.at(votes, (ci[has] * ny + cj[has], plane_of[has]), 1)
    best = votes.argmax(axis=1).reshape(nx, ny)
    planed = (votes.sum(axis=1) > 0).reshape(nx, ny) & mask
    out = np.full((nx, ny), -1, dtype=np.int64)
    if not planed.any():
        return out
    _, (ii, jj) = ndimage.distance_transform_edt(~planed, return_indices=True)
    filled = best[ii, jj]
    out[mask] = filled[mask]
    return out


def _classify_edges(
    ring: Sequence[tuple[float, float]],
    pts: NDArray[np.float64],
    plane_of: NDArray[np.int64],
    planes: Sequence[tuple[NDArray[np.float64], float]],
    ground: float = 0.0,
) -> list[Edge]:
    """Step 6: each outline edge as eave, rake or other, by the planes of the roof points within
    ``EDGE_BAND_M`` inside it (one vote a point). An eave's height is where its planes meet the
    edge: each voting point's plane evaluated at the point's foot on the edge, the median, above
    the ground (the points' own heights would read high by the band's depth times the pitch)."""
    p = np.asarray(ring, dtype=np.float64)
    out: list[Edge] = []
    cos45 = math.cos(math.radians(45.0))
    normals = np.array([n for n, _ in planes]) if planes else np.zeros((0, 3))
    offsets = np.array([o for _, o in planes]) if planes else np.zeros(0)
    for k in range(len(p)):
        a, b = p[k], p[(k + 1) % len(p)]
        d = b - a
        length = float(math.hypot(d[0], d[1]))
        if length == 0:
            continue
        t = d / length
        outward = np.array([t[1], -t[0]])  # right of a counter-clockwise ring's edge
        rel = pts[:, :2] - a
        s = rel @ t
        depth = -(rel @ outward)  # metres inside the edge
        band = (s >= 0) & (s <= length) & (depth >= -0.25) & (depth <= EDGE_BAND_M)
        band &= plane_of >= 0
        idx = np.nonzero(band)[0]
        kind: Literal["eave", "rake", "other", "unknown"] = "unknown"
        height: float | None = None
        if idx.size:
            q = plane_of[idx]
            nrm = normals[q]
            slope = np.degrees(np.arccos(np.clip(np.abs(nrm[:, 2]), 0.0, 1.0)))
            horiz = np.hypot(nrm[:, 0], nrm[:, 1])
            c = np.where(horiz > 0, (nrm[:, :2] @ outward) / np.where(horiz > 0, horiz, 1.0), 0.0)
            vote = np.where(
                slope < FLAT_DEG,
                0,
                np.where(c >= cos45, 0, np.where(np.abs(c) < cos45, 1, 2)),
            )
            counts = np.bincount(vote, minlength=3)
            kinds: tuple[Literal["eave", "rake", "other"], ...] = ("eave", "rake", "other")
            kind = kinds[int(np.argmax(counts))]
            if kind == "eave":
                sel = idx[vote == 0]
                foot = a + np.outer(np.clip(s[sel], 0.0, length), t)
                ns = normals[plane_of[sel]]
                at_edge = (offsets[plane_of[sel]] - (ns[:, :2] * foot).sum(axis=1)) / ns[:, 2]
                height = float(np.median(at_edge)) - ground
        out.append(
            Edge(
                (float(a[0]), float(a[1])),
                (float(b[0]), float(b[1])),
                length,
                kind,
                _facing(float(outward[0]), float(outward[1])),
                height,
            )
        )
    return out


def main_eave(edges: Sequence[Edge]) -> float | None:
    """The main eave height: the eave edges' heights grouped into levels (a gap over
    ``LEVEL_GAP_M`` starts a new level), the highest level holding at least
    ``MAIN_LEVEL_SHARE`` of the eave length, its median by length. A one-storey wing or a
    porch roof lower than the main block forms its own level and is passed over; the wall
    face then uses one eave for the whole building, as a takeoff from elevations does."""
    pairs = sorted((e.height_m, e.length_m) for e in edges if e.kind == "eave" and e.height_m)
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    levels: list[list[tuple[float, float]]] = [[pairs[0]]]
    for h, w in pairs[1:]:
        if h - levels[-1][-1][0] > LEVEL_GAP_M:
            levels.append([])
        levels[-1].append((h, w))
    for level in reversed(levels):
        if sum(w for _, w in level) >= MAIN_LEVEL_SHARE * total:
            return _weighted_median(level)
    return _weighted_median(pairs)


def as_record(s: Shell) -> dict[str, Any]:
    """The shell as the gather stores it beside the lidar summary (JSON; metres, square metres,
    degrees; the wall outline in lon/lat). A draft for the engineer, never a priced quantity."""
    return {
        "method": "basis.geo.shell (EVAL_REPORT 105)",
        "code": s.code,
        "classification": s.classification,
        "refusals": [{"part": p, "code": c, "reason": r} for p, c, r in s.refusals],
        "overhang_m": s.overhang_m,
        "roof_plan_m2": s.roof_plan_m2,
        "roof_perimeter_m": s.roof_perimeter_m,
        "footprint_m2": s.footprint_m2,
        "perimeter_m": s.perimeter_m,
        "long_side_m": s.long_side_m,
        "wall_outline": [[lon, lat] for lon, lat in s.wall_outline_lonlat],
        "offset_method": s.offset_method,
        "ground_elevation_m": s.ground_elevation_m,
        "eave_m": s.eave_m,
        "eave_main_m": s.eave_main_m,
        "ridge_m": s.ridge_m,
        "stories_estimate": s.stories_estimate,
        "roof_area_m2": s.roof_area_m2,
        "pitch_deg": s.pitch_deg,
        "planes": [
            {"slope_deg": p.slope_deg, "plan_m2": p.plan_m2, "points": p.points} for p in s.planes
        ],
        "edges": [
            {"kind": e.kind, "facing": e.facing, "length_m": e.length_m, "height_m": e.height_m}
            for e in s.edges
        ],
        "gutter_m": s.gutter_m,
        "wall_face_m2": s.wall_face_m2,
        "building_points": s.building_points,
        "grown_points": s.grown_points,
        "canopy_share": s.canopy_share,
        "reaches_box_edge": s.reaches_box_edge,
        "distance_m": s.distance_m,
    }


__all__ = [
    "OVERHANG_PRIOR_M",
    "as_record",
    "main_eave",
    "split_necks",
    "points_in_ring",
    "Edge",
    "Plane",
    "PointsRead",
    "Shell",
    "ShellPoints",
    "decode_laz_returns",
    "derive",
    "fit_planes",
    "local_normals",
    "long_side",
    "offset_inward_mitre",
    "offset_inward_raster",
    "own_roof_points",
    "read_points",
    "ring_area",
    "ring_perimeter",
]
