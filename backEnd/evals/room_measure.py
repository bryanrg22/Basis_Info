"""A room's measurements from ordinary photos: a depth model's shape, scaled by objects of known
size (AI_MEASUREMENT_PLAN M4, EVAL_REPORT 107).

A monocular depth model (MoGe-2, Depth Anything 3) turns a photo into a point per pixel. Its
*shape* is good; its *scale* is not (EVAL_REPORT 107.4 measures both): the standard fix, single-
view metrology since Criminisi, Reid and Zisserman (IJCV 2000), is a reference of known size in
the picture. This module is the pure geometry: given per photo the points (any unit), which of
them the segmenter calls floor, wall, ceiling, door or counter (ADE20K class ids, the classes
the production segmenter already gives), it

1. fits the **floor plane** (RANSAC) and takes its normal as up; the camera's height above it;
2. reads the **ceiling** as the median height of the ceiling's level points;
3. finds the **walls' directions** (Manhattan: the peak of four times the wall normals' angle)
   and, along each wall axis, the **wall planes** on either side of the camera (the strongest
   cluster of wall points facing along that axis); a side with no wall seen is ``open`` and the
   room's extent ends at the camera there (an owner stands at a wall or in a doorway);
4. collects the **references**: a door's top above the floor (an interior door is 80 in tall,
   :data:`DOOR_HEIGHT_M`; used only when its segment reaches the floor), in a kitchen a
   counter's top above the floor (36 in, :data:`COUNTER_HEIGHT_M`); and the camera's own
   height above the floor against an assumed :data:`CAMERA_HEIGHT_M` (the panorama-layout
   literature's fixed camera height: HorizonNet and LED2-Net use 1.6 m), kept apart as its
   own scale source;
5. turns it into the room: length and width (the larger of each over the photos, since an
   unregistered photo sees only part of the room), floor area = length x width, perimeter =
   2 (length + width) (the baseboard run, door openings not deducted), wall area = perimeter x
   ceiling height (gross), the countertop (the counter's top-facing points rasterized on the
   floor plane, :data:`COUNTER_CELL_M`) and the cabinet run = countertop / the production's
   25 in depth (``basis.domain.takeoffs.COUNTERTOP_DEPTH_IN``).

The **scale** is one number per room: ``references`` (the median over every door and counter
reference in the room's photos; none: the room is refused, ``no reference``), ``camera_height``,
or ``model`` (the depth model's own metres, no correction). Photos that share one frame (a
multi-view model: :func:`measure_room` with ``registered=True``) are measured as one view, so
walls seen from different photos close the room and counters seen twice count once.

Pure numpy, deterministic (a seeded RANSAC); CPU work, so an async caller would run it on a
thread (``asyncio.to_thread``, AGENTS.md). It lives in ``evals``, not ``basis``: no setup built
on it clearly beat today's formulas on the ARKitScenes rooms (EVAL_REPORT 107.6), so nothing in
production calls it; the multi-view setup is the one to take further.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

import numpy as np
import numpy.typing as npt

M_TO_FT: Final = 1 / 0.3048
SQM_TO_SF: Final = M_TO_FT * M_TO_FT
DOOR_HEIGHT_M: Final = 80 * 0.0254  # a standard interior door, 6'-8"
COUNTER_HEIGHT_M: Final = 36 * 0.0254  # a kitchen counter's top above the floor
CAMERA_HEIGHT_M: Final = 1.5  # a phone held for a room photo (the layout literature uses 1.6)
COUNTER_DEPTH_M: Final = 25 * 0.0254  # the production cabinet run's depth (takeoffs.py)
COUNTER_CELL_M: Final = 0.02
COUNTER_CLOSE_M: Final = 0.04
SPLAT_STEPS: Final = 5  # sub-points across each pixel's footprint

# ADE20K ids (the Mask2Former checkpoint's id2label).
WALL: Final = frozenset({0})
FLOOR: Final = frozenset({3, 28})  # floor, rug
CEILING: Final = frozenset({5})
DOOR: Final = frozenset({14, 58})  # door, screen door
# A counter's top: the segmenter rarely says countertop; it paints the top with the base
# cabinets (EVAL_REPORT 107.2). Only in a kitchen, where a cabinet under the camera is a base
# cabinet (in a bedroom it is a dresser, in a bathroom a vanity, neither 36 in).
COUNTER: Final = frozenset({10, 45, 70, 73})  # cabinet, counter, countertop, kitchen island

MIN_FLOOR_POINTS: Final = 200
MIN_CLASS_POINTS: Final = 60
RANSAC_ITERATIONS: Final = 200
UP_COS: Final = 0.85  # a surface faces up (or down) within 32 degrees
WALL_SIN: Final = 0.25  # a wall's normal is within 14 degrees of level
AXIS_COS: Final = 0.9  # a wall faces along an axis within 26 degrees
DOOR_FLOOR_SHARE: Final = 0.08  # a door's segment reaches the floor: its lowest points within
# 8 % of its height of the floor
COUNTER_HEIGHT_RANGE: Final = (0.6, 1.4)  # counter height over door height, for a sanity check

ScaleSource = Literal["references", "doors", "doors_checked", "camera_height", "model"]
# doors_checked: a metric model's metres, corrected by the doors that agree with them within
# DOOR_AGREEMENT (a door the segmenter cut short, or a tall closet door, disagrees by 30 to 60 %
# and is dropped); no agreeing door leaves the model's metres.
DOOR_AGREEMENT: Final = 0.25
Array = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class View:
    """One photo's points (H x W x 3, any unit), validity and ADE20K class per point, and the
    camera centre in the points' frame (the origin for a single-view model's camera frame)."""

    points: npt.NDArray[np.floating]
    valid: npt.NDArray[np.bool_]
    labels: npt.NDArray[np.integer]
    camera: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class Reference:
    kind: Literal["door", "counter", "camera_height"]
    measured: float  # in the model's units
    known_m: float
    view: int

    @property
    def scale(self) -> float:
        return self.known_m / self.measured


@dataclass(slots=True)
class ViewGeometry:
    """What one view (or one registered set of views) shows, in the model's units."""

    refused: str | None = None
    camera_height: float | None = None
    ceiling_height: float | None = None
    extent: list[float] = field(default_factory=list)  # along the two wall axes
    closed: list[bool] = field(default_factory=list)  # walls seen on both sides of the axis
    counter_xy: Array | None = None  # top-facing counter points on the floor plane
    counter_height: float | None = None
    door_heights: list[float] = field(default_factory=list)


@dataclass(slots=True)
class RoomMeasurement:
    refused: str | None
    scale: float | None
    scale_source: ScaleSource
    references: list[Reference] = field(default_factory=list)
    length_ft: float | None = None
    width_ft: float | None = None
    floor_area_sf: float | None = None
    perimeter_ft: float | None = None
    ceiling_height_ft: float | None = None
    wall_area_sf: float | None = None
    countertop_sf: float | None = None
    cabinet_lf: float | None = None
    views_used: int = 0
    notes: list[str] = field(default_factory=list)

    def quantities(self) -> dict[str, float]:
        keys = (
            "floor_area_sf",
            "length_ft",
            "width_ft",
            "ceiling_height_ft",
            "perimeter_ft",
            "wall_area_sf",
            "countertop_sf",
            "cabinet_lf",
        )
        return {k: float(v) for k in keys if (v := getattr(self, k)) is not None}


# ------------------------------------------------------------------ geometry helpers


def grid_normals(points: Array) -> Array:
    """Per-point normals from the point grid's neighbours (central differences); zero at the
    border and where a neighbour is missing."""
    p = points
    n = np.zeros_like(p)
    dx = p[1:-1, 2:] - p[1:-1, :-2]
    dy = p[2:, 1:-1] - p[:-2, 1:-1]
    c = np.cross(dx, dy)
    norm = np.linalg.norm(c, axis=-1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        n[1:-1, 1:-1] = np.where(norm > 0, c / norm, 0.0)
    return np.nan_to_num(n)


def pixel_spans(points: Array) -> tuple[Array, Array]:
    """Each pixel's footprint on its surface: the vectors to the next pixel across and down
    (half the central difference; zero at the border)."""
    across = np.zeros_like(points)
    down = np.zeros_like(points)
    across[:, 1:-1] = (points[:, 2:] - points[:, :-2]) / 2
    down[1:-1, :] = (points[2:, :] - points[:-2, :]) / 2
    return np.nan_to_num(across), np.nan_to_num(down)


def splat(xy: Array, across: Array, down: Array) -> Array:
    """Points spread over their pixels' footprints (in the plane: ``across`` and ``down`` are
    2D), so a surface seen at a grazing angle, sampled in rows centimetres apart, rasterizes
    whole. A footprint longer than four times the median is cut to it (a pixel on a counter's
    edge has a neighbour on the wall behind)."""
    if len(xy) == 0:
        return xy
    out = []
    for span in (across, down):
        length = np.linalg.norm(span, axis=1)
        cap = 4 * float(np.median(length)) if len(length) else 0.0
        factor = np.where(length > cap, cap / np.maximum(length, 1e-12), 1.0)
        out.append(span * factor[:, None])
    a, d = out
    steps = np.linspace(-0.5, 0.5, SPLAT_STEPS)
    parts = [xy + s * a + t * d for s in steps for t in steps]
    return np.concatenate(parts)


def fit_plane(
    points: Array, threshold: float, rng: np.random.Generator
) -> tuple[Array, float, int]:
    """RANSAC plane ``n . x = d`` (unit n), refined by least squares on the inliers."""
    best_inliers = np.zeros(len(points), dtype=bool)
    for _ in range(RANSAC_ITERATIONS):
        a, b, c = points[rng.choice(len(points), 3, replace=False)]
        n = np.cross(b - a, c - a)
        norm = float(np.linalg.norm(n))
        if norm < 1e-12:
            continue
        n = n / norm
        inliers = np.abs(points @ n - n @ a) < threshold
        if inliers.sum() > best_inliers.sum():
            best_inliers = inliers
    if best_inliers.sum() < 3:
        raise ValueError("no plane")
    q = points[best_inliers]
    centre = q.mean(0)
    _, _, vt = np.linalg.svd(q - centre, full_matrices=False)
    n = vt[2]
    return n, float(n @ centre), int(best_inliers.sum())


def manhattan_angle(directions: Array) -> float:
    """The walls' direction from horizontal normals (2D): the peak of 4 x angle, 1 degree bins
    smoothed over 5."""
    theta = np.arctan2(directions[:, 1], directions[:, 0])
    hist, edges = np.histogram(np.mod(4 * theta, 2 * math.pi), bins=360, range=(0, 2 * math.pi))
    kernel = np.ones(5) / 5
    padded = np.concatenate([hist[-2:], hist, hist[:2]]).astype(np.float64)
    smooth = np.convolve(padded, kernel, mode="valid")
    k = int(np.argmax(smooth))
    return float((edges[k] + edges[k + 1]) / 2 / 4)


def wall_position(coords: Array, unit: float) -> float | None:
    """The strongest cluster of coordinates (a wall's position along an axis, a counter top's
    height; bins of 2 % of ``unit``), or None when too few points."""
    if len(coords) < MIN_CLASS_POINTS:
        return None
    width = 0.02 * unit
    lo, hi = float(coords.min()), float(coords.max())
    bins = max(1, int(math.ceil((hi - lo) / width)) + 1)
    hist, edges = np.histogram(coords, bins=bins, range=(lo, lo + bins * width))
    smooth = np.convolve(hist.astype(np.float64), np.ones(3) / 3, mode="same")
    k = int(np.argmax(smooth))
    centre = (edges[k] + edges[k + 1]) / 2
    near = np.abs(coords - centre) <= 1.5 * width
    return float(np.median(coords[near]))


# ------------------------------------------------------------------ one view


def view_geometry(
    views: Sequence[View], *, kitchen: bool = False, seed: int = 107
) -> tuple[ViewGeometry, list[Reference]]:
    """The geometry of one photo, or of photos that share one frame (registered); counters
    (and their references) only in a kitchen."""
    rng = np.random.default_rng(seed)
    pts, lab, nrm, idx, acr, dwn = [], [], [], [], [], []
    for k, v in enumerate(views):
        p = np.asarray(v.points, dtype=np.float64)
        ok = np.asarray(v.valid, dtype=bool) & np.all(np.isfinite(p), axis=-1)
        n = grid_normals(p)
        across, down = pixel_spans(p)
        ok &= np.linalg.norm(n, axis=-1) > 0
        pts.append(p[ok])
        lab.append(np.asarray(v.labels)[ok])
        nrm.append(n[ok])
        acr.append(across[ok])
        dwn.append(down[ok])
        idx.append(np.full(int(ok.sum()), k))
    P = np.concatenate(pts)
    L = np.concatenate(lab)
    N = np.concatenate(nrm)
    A = np.concatenate(acr)
    D = np.concatenate(dwn)
    which = np.concatenate(idx)
    cams = np.array([v.camera for v in views], dtype=np.float64)
    geo = ViewGeometry()
    refs: list[Reference] = []
    if len(P) == 0:
        geo.refused = "no points"
        return geo, refs
    unit = float(np.median(np.linalg.norm(P - cams[which], axis=1)))
    floor_mask = np.isin(L, list(FLOOR))
    if floor_mask.sum() < MIN_FLOOR_POINTS:
        geo.refused = "no floor"
        return geo, refs
    try:
        n_up, d, _ = fit_plane(P[floor_mask], 0.02 * unit, rng)
    except ValueError:
        geo.refused = "no floor plane"
        return geo, refs
    if float(n_up @ cams.mean(0)) - d < 0:  # the cameras stand above the floor
        n_up, d = -n_up, -d
    height = P @ n_up - d  # every point's height above the floor
    cam_h = cams @ n_up - d
    if float(np.min(cam_h)) <= 0:
        geo.refused = "camera below the floor plane"
        return geo, refs
    geo.camera_height = float(np.median(cam_h))
    for k in range(len(views)):
        refs.append(Reference("camera_height", float(cam_h[k]), CAMERA_HEIGHT_M, k))
    # Normals facing the camera (the grid's cross product has either sign).
    facing = np.sign(np.einsum("ij,ij->i", N, cams[which] - P))
    N = N * facing[:, None]
    up_cos = N @ n_up
    ceiling = np.isin(L, list(CEILING)) & (up_cos < -UP_COS)
    if ceiling.sum() >= MIN_CLASS_POINTS:
        geo.ceiling_height = float(np.median(height[ceiling]))
    # Horizontal frame: e1, e2 perpendicular to up.
    e1 = np.cross(n_up, [0.0, 0.0, 1.0])
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(n_up, [1.0, 0.0, 0.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n_up, e1)
    walls = np.isin(L, list(WALL)) & (np.abs(up_cos) < WALL_SIN)
    if walls.sum() < MIN_CLASS_POINTS:
        geo.refused = "no walls"
        return geo, refs
    wn = np.stack([N[walls] @ e1, N[walls] @ e2], axis=1)
    psi = manhattan_angle(wn)
    axes = [
        math.cos(psi) * e1 + math.sin(psi) * e2,
        -math.sin(psi) * e1 + math.cos(psi) * e2,
    ]
    floor_pts = floor_mask & (np.abs(height) < 0.05 * unit)
    for a in axes:
        coord = P @ a
        along = walls & (np.abs(N @ a) > AXIS_COS)
        c_cam = cams @ a
        lo_side = along & (coord < c_cam.min())
        hi_side = along & (coord > c_cam.max())
        lo = wall_position(coord[lo_side], unit)
        hi = wall_position(coord[hi_side], unit)
        floor_lo = float(coord[floor_pts].min()) if floor_pts.any() else float(c_cam.min())
        floor_hi = float(coord[floor_pts].max()) if floor_pts.any() else float(c_cam.max())
        # An open side ends at the camera, or at the farthest floor seen beyond it.
        a_lo = lo if lo is not None else min(float(c_cam.min()), floor_lo)
        a_hi = hi if hi is not None else max(float(c_cam.max()), floor_hi)
        geo.extent.append(a_hi - a_lo)
        geo.closed.append(lo is not None and hi is not None)
    # Counters: the counter classes' points on their top's level (the strongest height between
    # a fifth of the camera's height and the camera; normals are not used: on a counter seen
    # at a grazing angle most pixels border another surface). Each photo's own level is its
    # reference; the room's level is the median.
    in_band = (height > 0.2 * geo.camera_height) & (height < geo.camera_height)
    labelled = np.isin(L, list(COUNTER)) & in_band & kitchen
    counter = np.zeros(len(P), dtype=bool)
    for k in range(len(views)):
        mine = labelled & (which == k)
        if mine.sum() < MIN_CLASS_POINTS:
            continue
        top = wall_position(height[mine], unit)
        if top is None:
            continue
        on_top = mine & (np.abs(height - top) <= 0.02 * unit)
        if on_top.sum() < MIN_CLASS_POINTS:
            continue
        counter |= on_top
        refs.append(Reference("counter", float(np.median(height[on_top])), COUNTER_HEIGHT_M, k))
    if counter.sum() >= MIN_CLASS_POINTS:
        geo.counter_height = float(np.median(height[counter]))
        plane = np.stack(axes, axis=1)  # 3 x 2: onto the floor plane's wall axes
        geo.counter_xy = splat(P[counter] @ plane, A[counter] @ plane, D[counter] @ plane)
    doors = np.isin(L, list(DOOR))
    for k in range(len(views)):
        mine = doors & (which == k)
        if mine.sum() < MIN_CLASS_POINTS:
            continue
        top = float(np.percentile(height[mine], 99))
        bottom = float(np.percentile(height[mine], 1))
        if top <= 0 or bottom > DOOR_FLOOR_SHARE * top:
            continue  # the door's foot is hidden: its top's height above the floor is not its own
        geo.door_heights.append(top)
        refs.append(Reference("door", top, DOOR_HEIGHT_M, k))
    return geo, refs


def counter_area(xy: Array, scale: float) -> float:
    """The area (m2) of the counter's top-facing points rasterized at :data:`COUNTER_CELL_M`,
    gaps up to twice :data:`COUNTER_CLOSE_M` closed (a counter seen at a grazing angle is
    sampled in rows several centimetres apart; a closing fills between them without growing
    the outline)."""
    from scipy import ndimage

    r = int(round(COUNTER_CLOSE_M / COUNTER_CELL_M))
    cells = np.floor(xy * scale / COUNTER_CELL_M).astype(np.int64)
    cells -= cells.min(0)
    pad = r + 2
    grid = np.zeros(
        (int(cells[:, 0].max()) + 2 * pad + 1, int(cells[:, 1].max()) + 2 * pad + 1), dtype=bool
    )
    grid[cells[:, 0] + pad, cells[:, 1] + pad] = True
    y, x = np.ogrid[-r : r + 1, -r : r + 1]
    closed = ndimage.binary_closing(grid, structure=x * x + y * y <= r * r)
    return float((closed | grid).sum()) * COUNTER_CELL_M * COUNTER_CELL_M


# ------------------------------------------------------------------ the room


def room_scale(
    refs: Sequence[Reference], source: ScaleSource
) -> tuple[float | None, list[Reference], str | None]:
    """The room's scale and the references it rests on, or a refusal."""
    if source == "model":
        return 1.0, [], None
    if source == "doors":
        mine = [r for r in refs if r.kind == "door" and r.measured > 0]
        if not mine:
            return None, [], "no door"
        return float(np.median([r.scale for r in mine])), mine, None
    if source == "doors_checked":
        mine = [r for r in refs if r.kind == "door" and r.measured > 0]
        agree = [r for r in mine if abs(r.scale - 1.0) <= DOOR_AGREEMENT]
        if not agree:
            return 1.0, [], None
        return float(np.median([r.scale for r in agree])), agree, None
    if source == "camera_height":
        mine = [r for r in refs if r.kind == "camera_height" and r.measured > 0]
        if not mine:
            return None, [], "no camera height"
        return float(np.median([r.scale for r in mine])), mine, None
    mine = [r for r in refs if r.kind in ("door", "counter") and r.measured > 0]
    if not mine:
        return None, [], "no reference"
    return float(np.median([r.scale for r in mine])), mine, None


def measure_room(
    views: Sequence[View],
    *,
    source: ScaleSource = "references",
    registered: bool = False,
    kitchen: bool = False,
) -> RoomMeasurement:
    """The room's measurements from its photos (see the module notes)."""
    if not views:
        return RoomMeasurement("no photos", None, source)
    groups = [list(views)] if registered else [[v] for v in views]
    geos: list[ViewGeometry] = []
    refs: list[Reference] = []
    notes: list[str] = []
    for g_index, group in enumerate(groups):
        geo, mine = view_geometry(group, kitchen=kitchen)
        if geo.refused:
            notes.append(f"view {g_index + 1}: {geo.refused}")
            continue
        offset = g_index if not registered else 0
        refs.extend(Reference(r.kind, r.measured, r.known_m, r.view + offset) for r in mine)
        geos.append(geo)
    if not geos:
        return RoomMeasurement("no usable photo", None, source, notes=notes)
    scale, used, why = room_scale(refs, source)
    out = RoomMeasurement(why, scale, source, references=used, views_used=len(geos), notes=notes)
    if scale is None:
        return out
    dims = [sorted((e * scale for e in g.extent), reverse=True) for g in geos if len(g.extent) == 2]
    if dims:
        length = max(d[0] for d in dims)
        width = max(d[1] for d in dims)
        out.length_ft = length * M_TO_FT
        out.width_ft = width * M_TO_FT
        out.floor_area_sf = length * width * SQM_TO_SF
        out.perimeter_ft = 2 * (length + width) * M_TO_FT
    heights = [g.ceiling_height for g in geos if g.ceiling_height is not None]
    if heights:
        ceiling = float(np.median(heights)) * scale
        out.ceiling_height_ft = ceiling * M_TO_FT
        if out.perimeter_ft is not None:
            out.wall_area_sf = out.perimeter_ft * out.ceiling_height_ft
    if kitchen:
        areas = [counter_area(g.counter_xy, scale) for g in geos if g.counter_xy is not None]
        if areas:
            top = max(areas)  # unregistered photos may show the same counter twice
            out.countertop_sf = top * SQM_TO_SF
            out.cabinet_lf = top / COUNTER_DEPTH_M * M_TO_FT
    return out


__all__ = [
    "CAMERA_HEIGHT_M",
    "COUNTER_HEIGHT_M",
    "DOOR_HEIGHT_M",
    "Reference",
    "RoomMeasurement",
    "View",
    "ViewGeometry",
    "counter_area",
    "fit_plane",
    "grid_normals",
    "manhattan_angle",
    "measure_room",
    "room_scale",
    "view_geometry",
    "wall_position",
]
