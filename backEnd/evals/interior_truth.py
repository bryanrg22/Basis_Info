"""Interior ground truth from ARKitScenes meshes (EVAL_REPORT 107, AI_MEASUREMENT_PLAN tier C).

ARKitScenes (Apple, https://github.com/apple/ARKitScenes) scanned 1,661 real rooms with an iPad
(RGB video, its lidar mesh, poses) and a Faro laser scanner. Its LICENSE speaks of "Apple
Software" and grants a commercial licence to licensees under 700M monthly users; Basis uses the
dataset as an **internal evaluation set only**: nothing of it (frames, meshes, numbers per room)
enters the repository or the product, and no model is trained on it. The files live under
``$BASIS_EVAL_DATA_DIR/measure_truth/interiors/`` (:mod:`evals.interior_data` fetches them).

Truth per room is measured here, by code, on the room's 3D mesh (the ARKit reconstruction
shipped with the 3dod subset, z up, metres), with the method written into every truth file:

* **Floor and ceiling**: near the iPad's path, the 4 cm window of height holding the most area
  of faces facing up (the floor) and, 1.8 m or more above it, facing down (the ceiling), each
  refined to the area-weighted median height of its faces within :data:`LEVEL_BAND_M`. The
  ceiling height is their difference.
* **The walls' directions** (Manhattan): the area-weighted histogram of four times the vertical
  faces' normal angle; the room is rotated so the walls lie on the axes.
* **The room's outline**: the vertical faces in a band just under the ceiling
  (:data:`WALL_BAND_M`: above door heads, so doorways read as wall; above furniture, so a sofa
  is not a wall) rasterized on a :data:`CELL_M` grid, gaps closed with a disk of radius
  :data:`CLOSE_RADII_M` (the smallest that seals the room), and the free space containing the
  iPad's path flood-filled. A room that stays open to the grid's edge at the largest radius is
  refused (``open``): an open-plan space has no wall-to-wall truth. The closing rounds the
  room's corners off (a disk cannot reach into them), so the filled region is then grown back
  by the closing radius into free cells (not a wall cell, nor its neighbour) inside its own
  bounding box: the corners are restored, and a gap in an outer wall cannot bulge outwards
  (a gap in an inner wall of an L could; the check picture shows it).
* **Floor area**: the filled cells plus half a cell along the outline (the wall surface lies on
  average at the wall cell's centre). **Length and width**: the filled region's extent along
  the two wall axes. **Perimeter** (the baseboard run, door openings not deducted, as today's
  formula): the region's exposed cell edges after a :data:`PERIMETER_SMOOTH_CELLS` majority
  filter. **Wall area** (gross, openings not deducted): perimeter x ceiling height, given only
  where the ceiling is flat (:data:`FLAT_CEILING_SHARE` of the overhead area on one level).
* **Countertop and cabinet run** (kitchens): from the annotators' oriented boxes (ARKitScenes
  boxed every cabinet, dishwasher, oven and washer on the mesh by hand): the base units
  (:data:`BASE_UNITS` standing on the floor with the top at counter height,
  :data:`BASE_TOP_M`); the countertop is the union of their footprints, the cabinet run the sum
  of their long sides. A first version measured the counter on the mesh (faces facing up at
  counter height); on the second scans it moved 12 to 60 % (clutter on the counters, the
  fixtures it keyed on), against 0 to 8 % for the boxes' run (EVAL_REPORT 107.1), so the boxes
  are the truth.

Every room also gets a top-down check picture (the outline, the wall band, the counters, the
iPad's path), looked at before the room is used. The truth's own error is measured twice in
EVAL_REPORT 107.1: the same room's second and third iPad scans (ARKitScenes scanned each room
about three times) measured the same way, and the Faro laser scan on the rooms it was fetched
for.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt
from scipy import ndimage

SCHEMA: Final = "interior_truth/v1"
M_TO_FT: Final = 1 / 0.3048
SQM_TO_SF: Final = M_TO_FT * M_TO_FT
CELL_M: Final = 0.025
LEVEL_BIN_M: Final = 0.02
LEVEL_BAND_M: Final = 0.04
UP_COS: Final = 0.9  # a face is horizontal when its normal is within 26 degrees of vertical
WALL_COS: Final = 0.15  # a face is vertical when its normal is within 9 degrees of level
WALL_BAND_M: Final = (0.40, 0.06)  # under the ceiling: from 40 cm to 6 cm below it
CLOSE_RADII_M: Final = (0.3, 0.5, 0.8)
MIN_CEILING_SM: Final = 1.0  # less ceiling than this scanned: no ceiling truth, refused
FLAT_CEILING_SHARE: Final = 0.6  # below it the ceiling is sloped or stepped: no height truth
# The base units a countertop sits on: boxed by the annotators, standing on the floor (bottom
# within 25 cm of it) with the top at counter height.
BASE_UNITS: Final = frozenset({"cabinet", "dishwasher", "oven", "washer"})
BASE_BOTTOM_M: Final = 0.25
BASE_TOP_M: Final = (0.75, 1.05)
PERIMETER_SMOOTH_CELLS: Final = 7

Array = npt.NDArray[np.float64]


# ------------------------------------------------------------------ reading the files


def read_ply(path: Path) -> tuple[Array, npt.NDArray[np.int64]]:
    """A binary little-endian PLY mesh: vertices (x, y, z float, then any uchar properties)
    and triangle faces (``list uchar int``)."""
    data = path.read_bytes()
    end = data.index(b"end_header\n") + len(b"end_header\n")
    header = data[:end].decode("ascii").splitlines()
    if "format binary_little_endian 1.0" not in header:
        raise ValueError(f"{path}: not a binary little-endian PLY")
    n_vertex = n_face = 0
    props: list[tuple[str, str]] = []
    element = ""
    for line in header:
        parts = line.split()
        if parts[:1] == ["element"]:
            element = parts[1]
            if element == "vertex":
                n_vertex = int(parts[2])
            elif element == "face":
                n_face = int(parts[2])
        elif parts[:1] == ["property"] and element == "vertex":
            props.append((parts[2], parts[1]))
    types = {"float": "<f4", "double": "<f8", "uchar": "u1", "int": "<i4", "uint": "<u4"}
    vdtype = np.dtype([(name, types[t]) for name, t in props])
    vert = np.frombuffer(data, dtype=vdtype, count=n_vertex, offset=end)
    fdtype = np.dtype([("n", "u1"), ("i", "<i4", (3,))])
    faces = np.frombuffer(data, dtype=fdtype, count=n_face, offset=end + n_vertex * vdtype.itemsize)
    if n_face and not np.all(faces["n"] == 3):
        raise ValueError(f"{path}: not a triangle mesh")
    points = np.stack([vert["x"], vert["y"], vert["z"]], axis=1).astype(np.float64)
    return points, faces["i"].astype(np.int64)


def axis_angle_matrix(rvec: Sequence[float]) -> Array:
    """Rodrigues: the rotation of ``rvec`` (axis times angle, radians)."""
    r = np.asarray(rvec, dtype=np.float64)
    theta = float(np.linalg.norm(r))
    if theta < 1e-12:
        return np.eye(3)
    k = r / theta
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.asarray(np.eye(3) + math.sin(theta) * kx + (1 - math.cos(theta)) * kx @ kx)


def read_traj(path: Path) -> tuple[Array, Array, Array]:
    """``lowres_wide.traj``: timestamp, axis-angle, translation per line, world to camera.
    Returns the timestamps, the camera-to-world rotations (N x 3 x 3) and the camera centres."""
    rows = [list(map(float, line.split())) for line in path.read_text().splitlines() if line]
    ts = np.array([r[0] for r in rows])
    rot = np.empty((len(rows), 3, 3))
    centre = np.empty((len(rows), 3))
    for k, r in enumerate(rows):
        r_wc = axis_angle_matrix(r[1:4])
        t = np.array(r[4:7])
        rot[k] = r_wc.T
        centre[k] = -r_wc.T @ t
    return ts, rot, centre


@dataclass(frozen=True, slots=True)
class Box:
    label: str
    centre: tuple[float, float, float]
    lengths: tuple[float, float, float]
    axes: tuple[tuple[float, float, float], ...]  # three unit axes, one per length

    def corners(self) -> Array:
        c = np.asarray(self.centre)
        a = np.asarray(self.axes)
        half = np.asarray(self.lengths) / 2
        signs = np.array([[i, j, k] for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)])
        return np.asarray(c + (signs * half) @ a)


def read_boxes(path: Path, *, transpose: bool = False) -> list[Box]:
    """The 3dod annotation's oriented boxes (``obbAligned``: metres, z up). ``normalizedAxes``
    is read as rows (or columns, ``transpose``); :func:`box_axes_layout` picks the one the mesh
    agrees with."""
    data = json.loads(path.read_text())
    out = []
    for obj in data.get("data", []):
        seg = obj["segments"]["obbAligned"]
        m = np.asarray(seg["normalizedAxes"], dtype=np.float64).reshape(3, 3)
        if transpose:
            m = m.T
        out.append(
            Box(
                obj["label"],
                tuple(seg["centroid"]),
                tuple(seg["axesLengths"]),
                tuple(tuple(row) for row in m),
            )
        )
    return out


def points_in_box(points: Array, box: Box) -> int:
    local = (points - np.asarray(box.centre)) @ np.asarray(box.axes).T
    return int(np.all(np.abs(local) <= np.asarray(box.lengths) / 2, axis=1).sum())


def box_axes_layout(points: Array, path: Path) -> bool:
    """Whether ``normalizedAxes`` is column-major: the layout whose boxes hold more of the
    mesh's vertices (a cabinet's box is full of its surfaces; the wrong rotation misses them)."""
    rows = read_boxes(path)
    cols = read_boxes(path, transpose=True)
    sample = points[:: max(1, len(points) // 200_000)]
    a = sum(points_in_box(sample, b) for b in rows)
    b = sum(points_in_box(sample, b) for b in cols)
    return b > a


# ------------------------------------------------------------------ the geometry


@dataclass(frozen=True, slots=True)
class Faces:
    centroid: Array
    normal: Array
    area: Array


def face_geometry(points: Array, faces: npt.NDArray[np.int64]) -> Faces:
    a, b, c = points[faces[:, 0]], points[faces[:, 1]], points[faces[:, 2]]
    cross = np.cross(b - a, c - a)
    norm = np.linalg.norm(cross, axis=1)
    keep = norm > 1e-12
    normal = cross[keep] / norm[keep, None]
    return Faces((a + b + c)[keep] / 3, normal, norm[keep] / 2)


def level(heights: Array, areas: Array) -> float | None:
    """The height of the 4 cm window holding the most face area (the floor is the largest
    surface facing up, the ceiling the largest facing down), refined to the area-weighted
    median within :data:`LEVEL_BAND_M`."""
    if len(heights) == 0:
        return None
    lo, hi = float(heights.min()), float(heights.max())
    bins = max(1, int(math.ceil((hi - lo) / LEVEL_BIN_M)) + 1)
    hist, edges = np.histogram(
        heights, bins=bins, range=(lo, lo + bins * LEVEL_BIN_M), weights=areas
    )
    pair = hist + np.concatenate([hist[1:], [0.0]])
    k = int(np.argmax(pair))
    centre = edges[k + 1]
    near = np.abs(heights - centre) <= LEVEL_BAND_M
    return weighted_median(heights[near], areas[near])


def weighted_median(values: Array, weights: Array) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w)
    return float(v[np.searchsorted(cum, cum[-1] / 2)])


def manhattan_yaw(normal: Array, area: Array) -> float:
    """The walls' direction (radians, in [0, pi/2)): the peak of the area-weighted histogram of
    4 x the vertical faces' normal angle (1 degree bins, smoothed over 5)."""
    angle = np.arctan2(normal[:, 1], normal[:, 0])
    quad = np.mod(4 * angle, 2 * math.pi)
    hist, edges = np.histogram(quad, bins=360, range=(0, 2 * math.pi), weights=area)
    smooth = ndimage.uniform_filter1d(hist, 5, mode="wrap")
    k = int(np.argmax(smooth))
    return float(((edges[k] + edges[k + 1]) / 2) / 4)


def rotate_xy(xy: Array, yaw: float) -> Array:
    c, s = math.cos(-yaw), math.sin(-yaw)
    return np.asarray(xy @ np.array([[c, s], [-s, c]]))


def disk(radius_cells: int) -> npt.NDArray[np.bool_]:
    r = radius_cells
    y, x = np.ogrid[-r : r + 1, -r : r + 1]
    return np.asarray(x * x + y * y <= r * r)


@dataclass(frozen=True, slots=True)
class Grid:
    origin: tuple[float, float]  # the (x, y) of cell (0, 0)'s corner, rotated frame
    shape: tuple[int, int]  # rows (y), cols (x)
    cell: float = CELL_M

    def index(self, xy: Array) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
        col = np.floor((xy[:, 0] - self.origin[0]) / self.cell).astype(np.int64)
        row = np.floor((xy[:, 1] - self.origin[1]) / self.cell).astype(np.int64)
        return row, col

    def raster(self, xy: Array) -> npt.NDArray[np.bool_]:
        out = np.zeros(self.shape, dtype=bool)
        row, col = self.index(xy)
        ok = (row >= 0) & (row < self.shape[0]) & (col >= 0) & (col < self.shape[1])
        out[row[ok], col[ok]] = True
        return out


def exposed_edges(region: npt.NDArray[np.bool_]) -> int:
    padded = np.pad(region, 1)
    return int(np.sum(padded[1:, :] != padded[:-1, :]) + np.sum(padded[:, 1:] != padded[:, :-1]))


def regrow(
    region: npt.NDArray[np.bool_], walls: npt.NDArray[np.bool_], cells: int
) -> npt.NDArray[np.bool_]:
    """Grow ``region`` back by ``cells`` into cells that are not wall (nor next to one), inside
    the region's bounding box: the corners a closing rounded off come back."""
    rows, cols = np.nonzero(region)
    box = np.zeros_like(region)
    box[rows.min() : rows.max() + 1, cols.min() : cols.max() + 1] = True
    allowed = box & ~ndimage.binary_dilation(walls)
    out = region.copy()
    for _ in range(cells):
        grown = ndimage.binary_dilation(out) & allowed
        if (grown | out).sum() == out.sum():
            break
        out |= grown
    return np.asarray(out)


def smooth(region: npt.NDArray[np.bool_], size: int = 3) -> npt.NDArray[np.bool_]:
    """A ``size`` x ``size`` majority filter: bumps of the outline smaller than half of it go."""
    votes = ndimage.uniform_filter(region.astype(np.float64), size=size, mode="constant")
    return np.asarray(votes > 0.5)


# ------------------------------------------------------------------ one room


@dataclass(slots=True)
class RoomTruth:
    id: str
    status: str  # ok | refused
    reason: str | None = None
    room_type: str | None = None
    labels: dict[str, int] = field(default_factory=dict)
    floor_z_m: float | None = None
    ceiling_z_m: float | None = None
    ceiling_height_ft: float | None = None
    floor_area_sf: float | None = None
    length_ft: float | None = None
    width_ft: float | None = None
    perimeter_ft: float | None = None
    wall_area_sf: float | None = None
    countertop_sf: float | None = None
    cabinet_lf: float | None = None
    counters: list[dict[str, Any]] = field(default_factory=list)
    close_radius_m: float | None = None
    yaw_rad: float | None = None
    cameras_inside: float | None = None
    camera_height_m: float | None = None  # the iPad's median height above the floor
    rectangularity: float | None = None  # floor area / (length x width)
    method: dict[str, Any] = field(default_factory=dict)

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


def room_type(labels: dict[str, int]) -> str:
    """The room's type from what the annotators boxed in it."""
    if labels.get("sink") and (
        labels.get("stove") or labels.get("oven") or labels.get("dishwasher")
    ):
        return "kitchen"
    if labels.get("toilet") or labels.get("bathtub"):
        return "bathroom"
    if labels.get("bed"):
        return "bedroom"
    if labels.get("sofa"):
        return "living"
    return "other"


def measure_room(
    room_id: str,
    points: Array,
    faces: npt.NDArray[np.int64],
    cameras: Array,
    boxes: Sequence[Box],
) -> tuple[RoomTruth, dict[str, Any]]:
    """The room's truth, and the rasters for the check picture."""
    labels: dict[str, int] = {}
    for b in boxes:
        labels[b.label] = labels.get(b.label, 0) + 1
    truth = RoomTruth(room_id, "ok", room_type=room_type(labels), labels=labels)
    truth.method = {
        "schema": SCHEMA,
        "mesh": "ARKitScenes 3dod mesh (iPad lidar reconstruction), z up, metres",
        "cell_m": CELL_M,
        "wall_band_below_ceiling_m": list(WALL_BAND_M),
        "close_radii_m": list(CLOSE_RADII_M),
        "base_units": sorted(BASE_UNITS),
        "base_top_above_floor_m": list(BASE_TOP_M),
    }
    g = face_geometry(points, faces)
    nz = g.normal[:, 2]
    z = g.centroid[:, 2]
    up, down = nz > UP_COS, nz < -UP_COS
    # The levels are read near the iPad's path (the mesh runs on into other rooms).
    lo_xy, hi_xy = cameras[:, :2].min(0) - 1.5, cameras[:, :2].max(0) + 1.5
    near = np.all((g.centroid[:, :2] >= lo_xy) & (g.centroid[:, :2] <= hi_xy), axis=1)
    floor = level(z[up & near], g.area[up & near])
    above = z > (floor if floor is not None else -np.inf) + 1.8
    ceiling = level(z[down & near & above], g.area[down & near & above])
    if floor is None:
        truth.status, truth.reason = "refused", "no floor"
        return truth, {}
    truth.floor_z_m = floor
    if ceiling is None or ceiling - floor < 1.8:
        truth.status, truth.reason = "refused", "no ceiling"
        return truth, {}
    ceil_area = float(g.area[down & (np.abs(z - ceiling) <= LEVEL_BAND_M)].sum())
    if ceil_area < MIN_CEILING_SM:
        truth.status, truth.reason = "refused", f"ceiling scanned {ceil_area:.2f} m2"
        return truth, {}
    truth.ceiling_z_m = ceiling
    height = ceiling - floor
    # A sloped or stepped ceiling has no one height: the share of the room's downward-facing
    # area (any face tipped more than 60 degrees from vertical, near the path) on the level.
    overhead = (nz < -0.5) & near & above
    flat_share = float(
        g.area[overhead & (np.abs(z - ceiling) <= LEVEL_BAND_M)].sum() / g.area[overhead].sum()
    )
    truth.method["ceiling_flat_share"] = round(flat_share, 3)
    if flat_share >= FLAT_CEILING_SHARE:
        truth.ceiling_height_ft = height * M_TO_FT
    vertical = (np.abs(nz) < WALL_COS) & (z > floor + 0.3)
    yaw = manhattan_yaw(g.normal[vertical], g.area[vertical])
    truth.yaw_rad = yaw
    band = vertical & (z >= ceiling - WALL_BAND_M[0]) & (z <= ceiling - WALL_BAND_M[1])
    wall_xy = rotate_xy(g.centroid[band, :2], yaw)
    cam_xy = rotate_xy(cameras[:, :2], yaw)
    truth.camera_height_m = float(np.median(cameras[:, 2]) - floor)
    if len(wall_xy) < 100:
        truth.status, truth.reason = "refused", "no wall band under the ceiling"
        return truth, {}
    pad = 1.0
    lo = np.minimum(wall_xy.min(0), cam_xy.min(0)) - pad
    hi = np.maximum(wall_xy.max(0), cam_xy.max(0)) + pad
    shape = (int(math.ceil((hi[1] - lo[1]) / CELL_M)), int(math.ceil((hi[0] - lo[0]) / CELL_M)))
    grid = Grid((float(lo[0]), float(lo[1])), shape)
    walls = grid.raster(wall_xy)
    seed_xy = np.median(cam_xy, axis=0, keepdims=True)
    sr, sc = grid.index(seed_xy)
    region = None
    for radius in CLOSE_RADII_M:
        r = int(round(radius / CELL_M))
        closed = ndimage.binary_closing(np.pad(walls, r), structure=disk(r))[r:-r, r:-r]
        free, _ = ndimage.label(~closed)
        k = free[sr[0], sc[0]]
        if k == 0:
            continue
        cand = free == k
        touches = cand[0, :].any() or cand[-1, :].any() or cand[:, 0].any() or cand[:, -1].any()
        if not touches:
            region, truth.close_radius_m = cand, radius
            break
    if region is None:
        truth.status, truth.reason = "refused", "open: no closed outline at 0.8 m"
        return truth, {"grid": grid, "walls": walls, "cam_xy": cam_xy}
    radius_cells = int(round((truth.close_radius_m or 0.0) / CELL_M))
    region = regrow(region, walls, radius_cells)
    # Wall cells inside the room (a pillar, a wardrobe up to the ceiling) are floor, not outline.
    region = smooth(np.asarray(ndimage.binary_fill_holes(region)))
    cr, cc = grid.index(cam_xy)
    ok = (cr >= 0) & (cr < shape[0]) & (cc >= 0) & (cc < shape[1])
    inside = np.zeros(len(cam_xy), dtype=bool)
    inside[ok] = region[cr[ok], cc[ok]]
    truth.cameras_inside = float(inside.mean())
    # The outline's cell edges after a wider majority filter: a wall's 2 to 3 cm roughness
    # would otherwise add a staircase (a straight wall's count does not change, nor a corner's).
    perimeter_m = exposed_edges(smooth(region, PERIMETER_SMOOTH_CELLS)) * CELL_M
    area_m2 = float(region.sum()) * CELL_M * CELL_M + 0.5 * CELL_M * perimeter_m
    rows, cols = np.nonzero(region)
    extent_x = (cols.max() - cols.min() + 2) * CELL_M  # + half a cell at each wall
    extent_y = (rows.max() - rows.min() + 2) * CELL_M
    truth.floor_area_sf = area_m2 * SQM_TO_SF
    truth.length_ft = max(extent_x, extent_y) * M_TO_FT
    truth.width_ft = min(extent_x, extent_y) * M_TO_FT
    truth.perimeter_ft = perimeter_m * M_TO_FT
    if truth.ceiling_height_ft is not None:
        truth.wall_area_sf = perimeter_m * height * SQM_TO_SF
    truth.rectangularity = area_m2 / (extent_x * extent_y)
    counter = None
    if truth.room_type == "kitchen":
        counter = measure_counters(truth, floor, yaw, grid, boxes)
    return truth, {
        "grid": grid,
        "walls": walls,
        "region": region,
        "cam_xy": cam_xy,
        "counter": counter,
    }


def box_footprint(box: Box, yaw: float, grid: Grid) -> npt.NDArray[np.bool_]:
    """The box's footprint on the floor grid (its rotated rectangle, sampled densely)."""
    centre = rotate_xy(np.asarray(box.centre)[None, :2], yaw)[0]
    horiz = [
        (np.asarray(a[:2]), length)
        for a, length in zip(box.axes, box.lengths, strict=True)
        if abs(a[2]) < 0.5
    ]
    if len(horiz) != 2:
        return np.zeros(grid.shape, dtype=bool)
    (a0, l0), (a1, l1) = horiz
    a0 = rotate_xy(a0[None, :], yaw)[0]
    a1 = rotate_xy(a1[None, :], yaw)[0]
    s = np.arange(-0.5, 0.5 + 1e-9, CELL_M / 2 / max(l0, l1, CELL_M))
    uu, vv = np.meshgrid(s * l0, s * l1)
    pts = centre + uu.reshape(-1, 1) * a0 + vv.reshape(-1, 1) * a1
    return grid.raster(pts)


def box_z(box: Box) -> tuple[float, float]:
    z = box.corners()[:, 2]
    return float(z.min()), float(z.max())


def measure_counters(
    truth: RoomTruth, floor: float, yaw: float, grid: Grid, boxes: Sequence[Box]
) -> npt.NDArray[np.bool_]:
    """The countertop and the cabinet run from the annotators' boxes (see the module notes)."""
    keep = np.zeros(grid.shape, dtype=bool)
    runs: list[dict[str, Any]] = []
    for b in boxes:
        if b.label not in BASE_UNITS:
            continue
        zlo, zhi = box_z(b)
        if zlo - floor > BASE_BOTTOM_M or not BASE_TOP_M[0] <= zhi - floor <= BASE_TOP_M[1]:
            continue
        horiz = sorted(
            length for a, length in zip(b.axes, b.lengths, strict=True) if abs(a[2]) < 0.5
        )
        if len(horiz) != 2:
            continue
        runs.append({"label": b.label, "run_m": horiz[1], "depth_m": horiz[0]})
        keep |= box_footprint(b, yaw, grid)
    truth.counters = runs
    if runs:
        truth.countertop_sf = float(keep.sum()) * CELL_M * CELL_M * SQM_TO_SF
        truth.cabinet_lf = sum(float(r["run_m"]) for r in runs) * M_TO_FT
    return keep


def check_picture(truth: RoomTruth, rasters: dict[str, Any], path: Path) -> None:
    """The top-down check: free space grey, the closed room white, wall band black, counters
    orange, the iPad's path blue; north is the rotated frame's +y (up)."""
    from PIL import Image, ImageDraw

    grid: Grid | None = rasters.get("grid")
    if grid is None:
        return
    h, w = grid.shape
    img = np.full((h, w, 3), 200, dtype=np.uint8)
    region = rasters.get("region")
    if region is not None:
        img[region] = (255, 255, 255)
    counter = rasters.get("counter")
    if counter is not None:
        img[counter] = (240, 150, 40)
    img[rasters["walls"]] = (0, 0, 0)
    pic = Image.fromarray(img[::-1])  # rows grow with y; up is +y
    draw = ImageDraw.Draw(pic)
    cr, cc = grid.index(rasters["cam_xy"])
    for r, c in zip(cr[::5], cc[::5], strict=True):
        draw.point((int(c), h - 1 - int(r)), fill=(30, 90, 230))
    q = truth.quantities()
    text = f"{truth.id} {truth.room_type} {truth.status} {truth.reason or ''}\n" + ", ".join(
        f"{k} {v:.1f}" for k, v in q.items()
    )
    draw.text((4, 4), text, fill=(200, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    pic.save(path)


def save(truth: RoomTruth, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(truth), indent=1) + "\n", encoding="utf-8")


def load(path: Path) -> RoomTruth:
    data = json.loads(path.read_text(encoding="utf-8"))
    return RoomTruth(**data)


__all__ = [
    "Box",
    "RoomTruth",
    "SCHEMA",
    "axis_angle_matrix",
    "box_axes_layout",
    "check_picture",
    "exposed_edges",
    "face_geometry",
    "level",
    "load",
    "manhattan_yaw",
    "measure_room",
    "read_boxes",
    "read_ply",
    "read_traj",
    "room_type",
    "save",
]
