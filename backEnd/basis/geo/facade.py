"""One facade measured from one ground photo, scaled by the wall's known length (EVAL_REPORT
109, method M3 of ``docs/review_2026-10-15/AI_MEASUREMENT_PLAN.md``).

:func:`measure` takes the photo's size and field of view, the wall drawn on it as four image
corners (grade and eave at the wall's two ends: :class:`Quad`), the openings drawn the same way,
and the wall's length from another source (the lidar shell, :mod:`basis.geo.shell`, or a plan),
and returns the facade in feet-free metres: the wall height (grade to eave), the gross face, each
opening's width, height and area, the face net of the openings (the siding), and the openings'
trim length. It is pure (no I/O, no image pixels: the corners are the input) and
deterministic. Who draws the corners (an engineer in the measure tool, a vision model, a line
detector) is the caller's business; EVAL_REPORT 109 measures which.

**The standard it follows.**

* *Single-view metrology* (A. Criminisi, I. Reid and A. Zisserman, "Single View Metrology",
  IJCV 40(2), 2000): a plane seen in one perspective photo is measured, up to one scale, from
  the plane's image; one known length on the plane sets the scale. Here the plane is the wall,
  its image the four corners, the known length its width.
* *Metric rectification* (R. Hartley and A. Zisserman, *Multiple View Geometry*, 2nd ed., 2003,
  sections 2.7 and 8.8): the homography ``G`` from the unit square to the wall's four corners is
  ``K [w r1, h r2, t]`` up to scale, ``K`` the camera matrix (square pixels, principal point at
  the image centre, focal length ``f``). With ``K`` known, ``M = K^-1 G`` has columns ``a``,
  ``b`` proportional to ``w r1`` and ``h r2``, and ``r1``, ``r2`` are unit vectors, so the
  wall's height over its width is ``|b| / |a|``. ``f`` comes from the photo's horizontal field
  of view; without it, from the two vanishing points of the wall's edges, which must be
  orthogonal directions (``a . b = 0``, the calibration of B. Caprile and V. Torre, "Using
  vanishing points for camera calibration", IJCV 4(2), 1990): ``f^2 = -(g11 g12 + g21 g22) /
  (g31 g32)``. A wall photographed square-on (both vanishing points at infinity) needs no
  ``f``; one photographed with parallel verticals and converging horizontals needs the field
  of view.
* *Its error* (Criminisi's first-order propagation): each corner placed within
  ``CORNER_SIGMA_PX`` (a standard deviation, in pixels) moves each quantity by its derivative;
  the derivatives are taken numerically and the expected error is their root sum of squares.
* *What an opening is*: the area the siding does not cover, the window's or door's outline at
  the outer edge of its exterior casing (Hover's reports list openings by width, height, area
  and the tops, sills and sides of their trim; the trim length here is that perimeter). The
  wall face is the rectangle from grade to the eave over the wall's length (EVAL_REPORT 103.1's
  wall face per side; gable triangles are not in it).

**Refusals** (each a ``code`` and a reason in :attr:`Facade.refusals`; nothing is guessed):
corners that are not a convex quadrilateral, or a wall narrower than ``MIN_WALL_PX`` in the
photo (``GEO_FACADE_UNREADABLE``); no field of view and vanishing points that cannot give one
(``GEO_FACADE_NO_FOCAL``); a wall seen more obliquely than ``MAX_VIEW_DEG`` from its normal, or
whose corners are not a rectangle under this camera by more than ``MAX_SKEW_DEG``
(``GEO_FACADE_OBLIQUE``); a wall more than ``MAX_OCCLUDED_SHARE`` hidden (trees, a porch, a
car) has its height measured but not its openings (``GEO_FACADE_OCCLUDED``). An opening outside
the wall's rectangle is left out and named.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray

from basis.errors.codes import E

Point = tuple[float, float]
OpeningKind = Literal["window", "door", "garage", "other"]

# A corner placed by hand or by a model on a 1024 px photo is good to a few pixels.
CORNER_SIGMA_PX: Final = 3.0
MIN_WALL_PX: Final = 120.0
MAX_VIEW_DEG: Final = 70.0
MAX_SKEW_DEG: Final = 12.0
MAX_OCCLUDED_SHARE: Final = 0.5
# Plausible horizontal fields of view for a focal length solved from vanishing points.
MIN_FOV_DEG: Final = 15.0
MAX_FOV_DEG: Final = 130.0
# An opening may stick out of the wall's rectangle by this share of the wall's size (a sill or
# a step drawn a little past the grade line) before it is left out.
INSIDE_TOLERANCE: Final = 0.04
# Opposite edges this close to parallel are taken as parallel (a square-on view).
SQUARE_ON_DEG: Final = 0.05


@dataclass(frozen=True, slots=True)
class Quad:
    """Four image points (pixels, x right, y down): bottom-left, bottom-right, top-right,
    top-left as the photo shows them. For a wall: grade at the left end, grade at the right
    end, the eave at the right end, the eave at the left end."""

    bl: Point
    br: Point
    tr: Point
    tl: Point

    def points(self) -> list[Point]:
        return [self.bl, self.br, self.tr, self.tl]


@dataclass(frozen=True, slots=True)
class Camera:
    width_px: int
    height_px: int
    hfov_deg: float | None = None  # None: solve the focal length from the wall's corners


@dataclass(frozen=True, slots=True)
class Opening:
    kind: OpeningKind
    quad: Quad


@dataclass(frozen=True, slots=True)
class MeasuredOpening:
    kind: OpeningKind
    width_m: float
    height_m: float
    area_m2: float
    perimeter_m: float
    left_m: float  # from the wall's left end
    sill_m: float  # above grade


@dataclass(frozen=True, slots=True)
class Facade:
    wall_length_m: float
    height_m: float | None = None
    gross_m2: float | None = None
    openings: tuple[MeasuredOpening, ...] = ()
    openings_m2: float | None = None
    net_m2: float | None = None
    trim_m: float | None = None
    windows: int | None = None
    doors: int | None = None
    focal_px: float | None = None
    focal_from: Literal["field of view", "vanishing points", "square-on"] | None = None
    view_deg: float | None = None
    skew_deg: float | None = None
    wall_px: float | None = None
    expected_error_pct: dict[str, float] = field(default_factory=dict)
    left_out: tuple[str, ...] = ()
    code: str | None = None  # the whole facade refused
    refusals: tuple[tuple[str, str, str], ...] = ()


# ------------------------------------------------------------------ geometry


def square_to_quad(quad: Quad) -> NDArray[np.float64]:
    """The homography taking the unit square's (0,0), (1,0), (1,1), (0,1) to the quad's
    bottom-left, bottom-right, top-right, top-left (a 3 x 3, last entry 1)."""
    src = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    rows = []
    rhs = []
    for (u, v), (x, y) in zip(src, quad.points(), strict=True):
        rows.append([u, v, 1, 0, 0, 0, -u * x, -v * x])
        rhs.append(x)
        rows.append([0, 0, 0, u, v, 1, -u * y, -v * y])
        rhs.append(y)
    h = np.linalg.solve(np.asarray(rows, dtype=float), np.asarray(rhs, dtype=float))
    return np.append(h, 1.0).reshape(3, 3)


def is_convex(points: Sequence[Point]) -> bool:
    """True for a strictly convex quadrilateral (either orientation)."""
    signs = []
    n = len(points)
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        x2, y2 = points[(i + 2) % n]
        cross = (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)
        if abs(cross) < 1e-9:
            return False
        signs.append(cross > 0)
    return all(signs) or not any(signs)


def _centred(g: NDArray[np.float64], camera: Camera) -> NDArray[np.float64]:
    """``g`` with the image origin moved to the principal point (the image centre)."""
    t = np.array(
        [[1.0, 0.0, -camera.width_px / 2], [0.0, 1.0, -camera.height_px / 2], [0.0, 0.0, 1.0]]
    )
    return t @ g


def focal_from_fov(camera: Camera) -> float | None:
    if camera.hfov_deg is None:
        return None
    return camera.width_px / 2 / math.tan(math.radians(camera.hfov_deg) / 2)


def focal_from_vanishing(gc: NDArray[np.float64]) -> float | None:
    """The focal length that makes the wall's horizontal and vertical directions orthogonal
    (Caprile and Torre), or ``None`` where a vanishing point is at infinity or the answer is not
    a real focal length."""
    g31, g32 = gc[2, 0], gc[2, 1]
    scale = max(abs(gc[0, 0]), abs(gc[1, 0]), abs(gc[0, 1]), abs(gc[1, 1]), 1e-12)
    if abs(g31 * g32) * scale * scale < 1e-12:
        return None
    f2 = -(gc[0, 0] * gc[0, 1] + gc[1, 0] * gc[1, 1]) / (g31 * g32)
    if not math.isfinite(f2) or f2 <= 0:
        return None
    return math.sqrt(f2)


def _parallel(p0: Point, p1: Point, q0: Point, q1: Point) -> bool:
    ax, ay = p1[0] - p0[0], p1[1] - p0[1]
    bx, by = q1[0] - q0[0], q1[1] - q0[1]
    sine = abs(ax * by - ay * bx) / (math.hypot(ax, ay) * math.hypot(bx, by))
    return sine < math.sin(math.radians(SQUARE_ON_DEG))


def _square_on(quad: Quad) -> bool:
    """Both pairs of opposite edges parallel in the photo: both vanishing points at infinity,
    the wall parallel to the image (an affine view, whose aspect needs no focal length)."""
    return _parallel(quad.bl, quad.br, quad.tl, quad.tr) and _parallel(
        quad.bl, quad.tl, quad.br, quad.tr
    )


def _aspect(gc: NDArray[np.float64], focal: float) -> tuple[float, float, float]:
    """The wall's height over its width, the skew (degrees from orthogonal) and the view
    angle (degrees between the wall's normal and the ray to its centre)."""
    k_inv = np.diag([1.0 / focal, 1.0 / focal, 1.0])
    m = k_inv @ gc
    a, b = m[:, 0], m[:, 1]
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    cos_ab = float(np.dot(a, b)) / (na * nb)
    skew = abs(90.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos_ab)))))
    normal = np.cross(a, b)
    centre = m @ np.array([0.5, 0.5, 1.0])
    cos_view = abs(float(np.dot(normal, centre))) / (
        float(np.linalg.norm(normal)) * float(np.linalg.norm(centre))
    )
    view = math.degrees(math.acos(max(-1.0, min(1.0, cos_view))))
    return nb / na, skew, view


def to_wall(g: NDArray[np.float64], point: Point) -> Point:
    """An image point in the unit square of the wall (0..1 across, 0..1 up)."""
    inv = np.linalg.inv(g)
    p = inv @ np.array([point[0], point[1], 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


def _shoelace(points: Sequence[Point]) -> float:
    twice = 0.0
    for i, (x0, y0) in enumerate(points):
        x1, y1 = points[(i + 1) % len(points)]
        twice += x0 * y1 - x1 * y0
    return abs(twice) / 2


def _dist(p: Point, q: Point) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


# ------------------------------------------------------------------ measuring


@dataclass(frozen=True, slots=True)
class _Core:
    height_m: float
    focal_px: float
    focal_from: Literal["field of view", "vanishing points", "square-on"]
    skew_deg: float
    view_deg: float
    g: NDArray[np.float64]


def _core(camera: Camera, wall: Quad, wall_length_m: float) -> _Core | tuple[str, str]:
    g = square_to_quad(wall)
    gc = _centred(g, camera)
    focal = focal_from_fov(camera)
    focal_from: Literal["field of view", "vanishing points", "square-on"] = "field of view"
    if focal is None:
        if _square_on(wall):
            # Any focal length gives the same aspect for an affine view.
            focal, focal_from = float(max(camera.width_px, camera.height_px)), "square-on"
        else:
            focal = focal_from_vanishing(gc)
            focal_from = "vanishing points"
            if focal is None:
                return (
                    E.GEO_FACADE_NO_FOCAL,
                    "no field of view, and the wall's vanishing points cannot give one (a "
                    "vanishing point at infinity, or edges that are not a rectangle's)",
                )
            fov = math.degrees(2 * math.atan(camera.width_px / 2 / focal))
            if not MIN_FOV_DEG <= fov <= MAX_FOV_DEG:
                return (
                    E.GEO_FACADE_NO_FOCAL,
                    f"the vanishing points give a {fov:.0f} degree field of view, outside "
                    f"{MIN_FOV_DEG:.0f} to {MAX_FOV_DEG:.0f}",
                )
    aspect, skew, view = _aspect(gc, focal)
    return _Core(wall_length_m * aspect, focal, focal_from, skew, view, g)


def _opening(
    g: NDArray[np.float64], o: Opening, width_m: float, height_m: float
) -> MeasuredOpening | None:
    pts = [to_wall(g, p) for p in o.quad.points()]
    tol = INSIDE_TOLERANCE
    if any(not (-tol <= u <= 1 + tol and -tol <= v <= 1 + tol) for u, v in pts):
        return None
    metres = [(u * width_m, v * height_m) for u, v in pts]
    bl, br, tr, tl = metres
    w = (_dist(bl, br) + _dist(tl, tr)) / 2
    h = (_dist(bl, tl) + _dist(br, tr)) / 2
    return MeasuredOpening(
        kind=o.kind,
        width_m=w,
        height_m=h,
        area_m2=_shoelace(metres),
        perimeter_m=sum(_dist(metres[i], metres[(i + 1) % 4]) for i in range(4)),
        left_m=min(x for x, _ in metres),
        sill_m=min(y for _, y in metres),
    )


def measure(
    camera: Camera,
    wall: Quad,
    wall_length_m: float,
    openings: Sequence[Opening] = (),
    *,
    occluded_share: float | None = None,
    corner_sigma_px: float = CORNER_SIGMA_PX,
) -> Facade:
    """The facade on ``wall``'s plane, scaled so that the wall is ``wall_length_m`` long.

    ``occluded_share`` is the caller's estimate of how much of the wall is hidden (``None``:
    not known, taken as visible)."""
    if not wall_length_m > 0 or not math.isfinite(wall_length_m):
        raise ValueError("the wall's length must be a positive number of metres")
    base = Facade(wall_length_m=wall_length_m)
    points = wall.points()
    if not all(math.isfinite(c) for p in points for c in p) or not is_convex(points):
        return replace(
            base,
            code=E.GEO_FACADE_UNREADABLE,
            refusals=(("wall", E.GEO_FACADE_UNREADABLE, "the corners are not a quadrilateral"),),
        )
    wall_px = min(_dist(wall.bl, wall.br), _dist(wall.tl, wall.tr))
    if wall_px < MIN_WALL_PX:
        why = f"the wall is {wall_px:.0f} px wide in the photo, under {MIN_WALL_PX:.0f}"
        return replace(
            base,
            wall_px=wall_px,
            code=E.GEO_FACADE_UNREADABLE,
            refusals=(("wall", E.GEO_FACADE_UNREADABLE, why),),
        )
    core = _core(camera, wall, wall_length_m)
    if isinstance(core, tuple):
        code, why = core
        return replace(base, wall_px=wall_px, code=code, refusals=(("wall", code, why),))
    seen = replace(
        base,
        wall_px=wall_px,
        focal_px=core.focal_px,
        focal_from=core.focal_from,
        view_deg=core.view_deg,
        skew_deg=core.skew_deg,
    )
    if core.view_deg > MAX_VIEW_DEG:
        why = f"the wall is seen {core.view_deg:.0f} degrees off square, over {MAX_VIEW_DEG:.0f}"
        return replace(
            seen,
            code=E.GEO_FACADE_OBLIQUE,
            refusals=(("wall", E.GEO_FACADE_OBLIQUE, why),),
        )
    if core.focal_from == "field of view" and core.skew_deg > MAX_SKEW_DEG:
        why = (
            f"the corners are {core.skew_deg:.0f} degrees from a rectangle under this camera "
            f"(over {MAX_SKEW_DEG:.0f}): a corner misplaced, a wrong field of view, or not one "
            "plane"
        )
        return replace(
            seen,
            code=E.GEO_FACADE_OBLIQUE,
            refusals=(("wall", E.GEO_FACADE_OBLIQUE, why),),
        )
    height = core.height_m
    gross = wall_length_m * height
    out = replace(seen, height_m=height, gross_m2=gross)
    if occluded_share is not None and occluded_share > MAX_OCCLUDED_SHARE:
        why = f"{occluded_share:.0%} of the wall is hidden: its openings cannot be counted"
        out = replace(out, refusals=(("openings", E.GEO_FACADE_OCCLUDED, why),))
        return replace(
            out, expected_error_pct=_expected(camera, wall, wall_length_m, (), corner_sigma_px)
        )
    measured: list[MeasuredOpening] = []
    left_out: list[str] = []
    for i, o in enumerate(openings):
        if not is_convex(o.quad.points()):
            left_out.append(f"opening {i + 1} ({o.kind}): not a quadrilateral")
            continue
        m = _opening(core.g, o, wall_length_m, height)
        if m is None:
            left_out.append(f"opening {i + 1} ({o.kind}): outside the wall")
            continue
        measured.append(m)
    openings_m2 = sum(m.area_m2 for m in measured)
    out = replace(
        out,
        openings=tuple(measured),
        openings_m2=openings_m2,
        net_m2=max(gross - openings_m2, 0.0),
        trim_m=sum(m.perimeter_m for m in measured),
        windows=sum(1 for m in measured if m.kind == "window"),
        doors=sum(1 for m in measured if m.kind in ("door", "garage")),
        left_out=tuple(left_out),
    )
    kept = [o for o in openings if is_convex(o.quad.points()) and _opening(core.g, o, 1.0, 1.0)]
    return replace(
        out, expected_error_pct=_expected(camera, wall, wall_length_m, kept, corner_sigma_px)
    )


def _quantities(
    camera: Camera, wall: Quad, length: float, openings: Sequence[Opening]
) -> dict[str, float] | None:
    core = _core(camera, wall, length)
    if isinstance(core, tuple):
        return None
    q = {"height": core.height_m}
    area = 0.0
    for o in openings:
        m = _opening(core.g, o, length, core.height_m)
        if m is not None:
            area += m.area_m2
    if openings:
        q["openings"] = area
        q["net"] = length * core.height_m - area
    return q


def _expected(
    camera: Camera,
    wall: Quad,
    length: float,
    openings: Sequence[Opening],
    sigma: float,
) -> dict[str, float]:
    """First-order error (Criminisi): each of the wall's and the openings' corners moved by
    ``sigma`` pixels in x and in y, the quantities' changes added in quadrature, as a percent
    of each quantity."""
    base = _quantities(camera, wall, length, openings)
    if base is None:
        return {}
    step = 0.5
    sums = dict.fromkeys(base, 0.0)

    def moved(q: Quad, k: int, axis: int, d: float) -> Quad:
        pts = q.points()
        x, y = pts[k]
        pts[k] = (x + d, y) if axis == 0 else (x, y + d)
        return Quad(*pts)

    targets: list[tuple[int, int]] = [(-1, k) for k in range(4)] + [
        (i, k) for i in range(len(openings)) for k in range(4)
    ]
    for which, k in targets:
        for axis in (0, 1):
            if which < 0:
                got = _quantities(camera, moved(wall, k, axis, step), length, openings)
            else:
                shifted = list(openings)
                o = shifted[which]
                shifted[which] = Opening(o.kind, moved(o.quad, k, axis, step))
                got = _quantities(camera, wall, length, shifted)
            if got is None:
                continue
            for key, value in got.items():
                sums[key] += ((value - base[key]) / step * sigma) ** 2
    return {key: 100.0 * math.sqrt(sums[key]) / abs(base[key]) for key in base if base[key] != 0}


# ------------------------------------------------------------------ the wall's length from M1

# The four sides a facade photo can face, by the azimuth of the wall's outward normal.
SIDES: Final = (("north", 0.0), ("east", 90.0), ("south", 180.0), ("west", 270.0))
SIDE_SPREAD_DEG: Final = 60.0


def side_width(
    walls: Sequence[Point], azimuth_deg: float, *, spread_deg: float = SIDE_SPREAD_DEG
) -> tuple[float, int] | None:
    """The width of the side of a wall outline (east, north metres) that faces
    ``azimuth_deg``, as an elevation of that side shows it: the edges whose outward normal is
    within ``spread_deg`` of the azimuth, projected on that side's horizontal axis, first end
    to last. The simplified lidar outline cuts corners into short diagonal edges, so a single
    edge is not a wall (EVAL_REPORT 109.4, the rule scored there: -4.1 and +3.8 % where the
    wall is the whole side; a side holding wings, blocks or porches reads long). Returns the
    width in metres and how many edges made it, or None."""
    if len(walls) < 3:
        return None
    ring = list(walls)
    twice = sum(
        x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True)
    )
    if twice < 0:
        ring.reverse()
    az = math.radians(azimuth_deg)
    want = (math.sin(az), math.cos(az))  # east, north
    axis = (want[1], -want[0])
    ends: list[float] = []
    used = 0
    for i, (x0, y0) in enumerate(ring):
        x1, y1 = ring[(i + 1) % len(ring)]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 0.3:
            continue
        normal = (dy / length, -dx / length)  # outward for a counter-clockwise ring
        if normal[0] * want[0] + normal[1] * want[1] < math.cos(math.radians(spread_deg)):
            continue
        used += 1
        ends += [x0 * axis[0] + y0 * axis[1], x1 * axis[0] + y1 * axis[1]]
    if not ends:
        return None
    return max(ends) - min(ends), used


def main_axis_deg(walls: Sequence[Point]) -> float:
    """The outline's main wall direction, modulo 90 degrees, in [0, 90): the length-weighted
    mean of its edges' azimuths on the circle of period 90 (each angle times four), so a
    house turned 20 degrees off north reads 20 whatever the order of its corners."""
    sx = sy = 0.0
    for (x0, y0), (x1, y1) in zip(walls, [*walls[1:], walls[0]], strict=True):
        length = math.hypot(x1 - x0, y1 - y0)
        if length == 0:
            continue
        azimuth = math.atan2(x1 - x0, y1 - y0)  # from north, clockwise (east, north input)
        sx += length * math.cos(4 * azimuth)
        sy += length * math.sin(4 * azimuth)
    return math.degrees(math.atan2(sy, sx) / 4) % 90.0


def sides(walls: Sequence[Point]) -> list[tuple[str, float, float, int]]:
    """:func:`side_width` for each of the outline's four sides, square to its main axis
    (:func:`main_axis_deg`), so a turned house's walls are measured along themselves:
    ``(compass name of the side's outward normal, its azimuth, width m, edges)``."""
    if len(walls) < 3:
        return []
    axis = main_axis_deg(walls)
    out = []
    for k in range(4):
        azimuth = (axis + 90.0 * k) % 360.0
        got = side_width(walls, azimuth)
        if got is not None:
            name = min(SIDES, key=lambda s: abs((azimuth - s[1] + 180.0) % 360.0 - 180.0))[0]
            out.append((name, azimuth, got[0], got[1]))
    return sorted(out, key=lambda s: [n for n, _ in SIDES].index(s[0]))


__all__ = [
    "CORNER_SIGMA_PX",
    "SIDES",
    "MAX_OCCLUDED_SHARE",
    "MAX_SKEW_DEG",
    "MAX_VIEW_DEG",
    "MIN_WALL_PX",
    "Camera",
    "Facade",
    "MeasuredOpening",
    "Opening",
    "Quad",
    "focal_from_vanishing",
    "is_convex",
    "main_axis_deg",
    "measure",
    "side_width",
    "sides",
    "square_to_quad",
    "to_wall",
]
