"""The engineer's traced measurement (EVAL_REPORT 97, docs/api/OVERRIDES.md section 4).

An ``asset`` correction may carry ``measurement``: the shape the engineer traced on an image,
instead of a typed ``quantity``. The server computes the quantity from the shape and keeps the
shape, the image it was traced on (what it is, its date, its pixel size), the scale, an
expected error and the method, so the takeoff is "carefully documented" (IRS Publication
5653, Rev. 2-2025, ch. 4 C.8) and a reviewer sees what to check
(docs/review_2026-10-15/REMOTE_INSPECTION_AND_MEASUREMENT.md sections 2, 5 and 7.2).

Pure: no I/O, so the decision route (a 422 before anything is recorded) and the activities
(``check_asset``, ``process_assets``) check and compute the same way. :func:`measure` returns
the :class:`Traced` result or raises :class:`MeasurementError` with every problem in plain
words, each naming where it is (``shapes[2].ring``); :func:`problems` is the list alone.

**The body** (every key below; any other key, anywhere, is refused)::

    {"unit": "SF" | "SY" | "LF",
     "space": "lonlat" | "image",
     "shapes": [{"kind": "area", "ring": [[x, y], ...], "cutouts": [[[x, y], ...], ...]},
                {"kind": "line", "path": [[x, y], ...]}],
     "scale": {"points": [[x1, y1], [x2, y2]], "length": 34.0, "unit": "ft" | "m"},
     "image": {"source": "...", "document_id": "<uuid>", "captured_on": "YYYY-MM-DD",
               "pixel_size_m": 0.1524},
     "height_ft": 4.0,
     "drawn_by": "engineer" | "model:<name>@<version>",
     "verified": true | false,
     "view": {"kind": "capture" | "public" | "document", "id": "...", "label": "...",
              "width": 1440, "height": 900},
     "original": {<a measurement body, without an original of its own>}}

``lonlat`` points are WGS84 ``[longitude, latitude]`` in degrees; ``image`` points are
``[x, y]`` pixels of an uploaded image or plan, and need the ``scale`` (two pixels the engineer
clicked and the real length between them). SF and SY take ``area`` shapes (ring areas less
their cutouts) or ``line`` shapes with ``height_ft`` (a wall face: length times height); LF
takes ``line`` shapes only.

**Provenance** (EVAL_REPORT 104; optional, and none of it changes the quantity, the price or
a flag). ``drawn_by`` says who drew the shape: ``engineer`` (the default when absent), or
``model:<name>@<version>`` for a draft a model made for the engineer to check. ``verified``
is the engineer's own mark that the measurement was checked (Roofr's "Mark as done",
SiteRecon's approval: docs/review_2026-10-15/AI_MEASUREMENT_PLAN.md section 3). ``view`` is
the picture it was drawn on, so it can be drawn again: a property capture's view, a public
view or a study document, by ``id`` or ``label`` (at least one), with the pixel frame
(``width`` x ``height``) the points are in when they are pixels. ``original`` is the earlier
measurement this one replaced (an engineer's edit of a model's draft or of an earlier trace):
a whole body, checked and computed by the same rules, kept beside the edit unchanged
(SiteRecon's locked base view, Hover's "original" PDF). An original holds no original of its
own: an edit of an edit keeps the first original only, the shape the record started from.

**Geometry.** Image space is planar: the shoelace area and the segment lengths in pixels,
times the scale (feet per pixel = the scale's length over the distance between its two
points). Lon/lat space uses a local tangent plane on the WGS84 ellipsoid: east-west distance
``N(phi0) cos(phi0) dlambda`` and north-south ``M(phi0) dphi``, where ``M`` and ``N`` are the
meridional and prime-vertical radii of curvature (``M = a(1-e^2)/(1-e^2 sin^2 phi)^(3/2)``,
``N = a/(1-e^2 sin^2 phi)^(1/2)``, the standard expressions of J. P. Snyder, *Map
Projections: A Working Manual*, USGS Professional Paper 1395, 1987, ch. 4), ``phi0`` the middle of
the shape's latitude range for an area and the middle of each segment for a line. EVAL_REPORT
97 compared it, on boxes bounded by meridians and parallels whose ellipsoid area has a closed
form (25 to 49 degrees north, 100 SF to 10 acres) and on lines with exact lengths, with screen
pixels of a Web Mercator map (no latitude correction: 22 to 132 % too large) and with a
mean-radius sphere (the Chamberlain-Duquette ring area and the haversine length Turf.js and
OpenLayers use: 0.01 to 0.38 % off, the sphere's radius against the ellipsoid's curvature);
the tangent plane was within 2e-7 % on every area and 5e-7 % on every line up to a mile
(2e-5 % at 10 km, 0.002 % at 100 km). The three cost the same ($0, about a microsecond a
shape), so the most accurate one ships (AGENTS.md: the cheapest within the noise of the best;
here nothing is cheaper). Longitudes are unwrapped against the first point so a shape that
crosses the antimeridian measures as the small shape it is.

Floats are used inside the computation (the points arrive as JSON doubles; the shoelace runs
on coordinates centred on the shape's first point, so the rounding is about 1e-12 of the
area, ten orders below the 0.01 the result is quantized to); every result leaves as a
``Decimal`` quantized half up to 0.01, as the rest of the takeoff (``basis.domain.takeoffs``).

**Expected error** (a percentage bound, :func:`expected_error_pct`), from section 5 of the
research note: a traced area's error behaves like its perimeter times the uncertainty of an
edge. One pixel of uncertainty on every edge: an area is off by at most ``perimeter x
pixel_size / area`` (cutouts' perimeters included), a line by ``2 x pixel_size / length`` per
line (its two ends). In image space the pixel is one pixel at the scale, plus the scale's own
uncertainty: its two clicked points are each one pixel out, so the scale is off by ``2 px /
the scale's distance in px``, which counts twice for an area (it is squared). In lon/lat space
the pixel size is the imagery's ``pixel_size_m``; without it the expected error is unknown
(None), which the reviewer is told. A wall face's height is typed, not traced: its
uncertainty is not counted.

**Review flags** (:func:`review_flags`; never a refusal, never a change of quantity):
``measure:IMAGE_UNDATED`` (no ``captured_on``), ``measure:IMAGE_AFTER_SERVICE`` (the image
was captured more than ``MEASURE_IMAGE_AFTER_SERVICE_DAYS`` after the placed-in-service date:
it may show what was built after the purchase), ``measure:ERROR_UNKNOWN`` (lon/lat without a
pixel size), ``measure:COARSE`` (the expected error is above ``MEASURE_ERROR_FLAG_PCT``,
default 10: the worst case the independent studies of section 5 report for today's hand
method on a driveway-sized area).

**Limits** (a bad value is a 422 naming it): 1 to 50 shapes; a ring 3 to 500 points (the
closing point is not needed; a repeated first point at the end is dropped), a path 2 to 500,
up to 20 cutouts per ring, and at most :data:`MAX_POINTS` points in all (so checking a
measurement stays a few tens of milliseconds); every number finite (a JSON boolean is not a
number); longitude in [-180, 180], latitude in [-85, 85]; pixels in [0, 100000]; ``length``
and ``height_ft`` above 0 and at most 100000; ``pixel_size_m`` above 0 and at most 100; a ring
(and a cutout) simple, with no repeated corner, no edge crossing or touching another, and an
area above zero; every cutout corner inside its ring or on its edge, and each area shape's
net area above zero; the scale's two points at least 2 pixels apart; the quantity above 0
and at most :data:`MAX_QUANTITY` (the cap of a typed quantity).
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

MEASUREMENT: Final = "measurement"  # the correction's field
UNITS: Final = ("SF", "SY", "LF")
FACADE: Final = "facade"  # a wall and its openings on one photo (EVAL_REPORT 111)
SPACES: Final = ("lonlat", "image", FACADE)
KINDS: Final = ("area", "line")
SCALE_UNITS: Final = ("ft", "m")
MAX_SHAPES: Final = 50
MIN_RING: Final = 3
MAX_RING: Final = 500
MIN_PATH: Final = 2
MAX_PATH: Final = 500
MAX_CUTOUTS: Final = 20
# All rings, cutouts and paths together: the simplicity check compares every pair of a ring's
# edges, so this bounds the work of one measurement (EVAL_REPORT 97: about 22 ms at the cap).
MAX_POINTS: Final = 5000
MAX_PIXEL: Final = 100_000.0
MAX_LENGTH: Final = 100_000.0
MAX_PIXEL_SIZE_M: Final = 100.0
MAX_LAT: Final = 85.0
MIN_SCALE_PX: Final = 2.0
MAX_SOURCE_CHARS: Final = 300
# The same cap as a typed quantity (``basis.activities.corrections.MAX_QUANTITY``; a test
# pins them equal).
MAX_QUANTITY: Final = Decimal(10_000_000)
QUANTUM: Final = Decimal("0.01")
FT_PER_M: Final = 1 / 0.3048  # the international foot is exactly 0.3048 m
SF_PER_SY: Final = 9

# WGS84 (NGA.STND.0036_1.0.0_WGS84, 2014): semi-major axis and flattening.
WGS84_A: Final = 6_378_137.0
WGS84_F: Final = 1 / 298.257223563
WGS84_E2: Final = WGS84_F * (2 - WGS84_F)

METHOD_LONLAT: Final = "local_tangent_plane_wgs84"
METHOD_IMAGE: Final = "image_scale"
METHOD_FACADE: Final = "single_view_metrology"
# A facade (space ``facade``): the wall's four corners and each opening's, on one photo.
FACADE_KEYS: Final = frozenset({"wall", "openings", "wall_length_ft", "wall_length_from", "lens"})
_OPENING_KEYS: Final = frozenset({"kind", "corners"})
_LENS_KEYS: Final = frozenset({"focal_length_35mm", "from"})
OPENING_KINDS: Final = ("window", "door", "garage", "other")
LENS_FROM: Final = ("photo", "engineer")
MAX_OPENINGS: Final = 100
MAX_WALL_FT: Final = 1000.0
MAX_FOCAL_35MM: Final = 5000.0

UNDATED: Final = "measure:IMAGE_UNDATED"
AFTER_SERVICE: Final = "measure:IMAGE_AFTER_SERVICE"
ERROR_UNKNOWN: Final = "measure:ERROR_UNKNOWN"
COARSE: Final = "measure:COARSE"
FLAGS: Final = (UNDATED, AFTER_SERVICE, ERROR_UNKNOWN, COARSE)

_TOP_KEYS: Final = frozenset(
    {"unit", "space", "shapes", "scale", "image", "height_ft"}
    | {"drawn_by", "verified", "view", "original"}  # provenance (EVAL_REPORT 104)
    | {FACADE}  # space facade (EVAL_REPORT 111)
)
# The keys of a body (what a record holds besides what :func:`measure` computes).
BODY_KEYS: Final = _TOP_KEYS
_AREA_KEYS: Final = frozenset({"kind", "ring", "cutouts"})
_LINE_KEYS: Final = frozenset({"kind", "path"})
_SCALE_KEYS: Final = frozenset({"points", "length", "unit"})
_IMAGE_KEYS: Final = frozenset({"source", "document_id", "captured_on", "pixel_size_m"})
_VIEW_KEYS: Final = frozenset({"kind", "id", "label", "width", "height"})
_ISO_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
ENGINEER: Final = "engineer"
# ``model:<name>@<version>``: the model and the version of the drafting method (a prompt or a
# pipeline version), so a reviewer can tell which drafts one change affected.
_MODEL: Final = re.compile(
    r"model:[A-Za-z0-9][A-Za-z0-9._/-]{0,99}@[A-Za-z0-9][A-Za-z0-9._+-]{0,49}"
)
VIEW_KINDS: Final = ("capture", "public", "document")
MAX_VIEW_TEXT: Final = 200

Point = tuple[float, float]


class MeasurementError(ValueError):
    """The measurement cannot be applied; ``problems`` says every reason, in plain words."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True, slots=True)
class Traced:
    """A measurement that :func:`measure` accepted, with what it computed."""

    unit: str
    space: str
    quantity: Decimal
    area_sf: Decimal | None  # area shapes
    length_ft: Decimal | None  # line shapes (a wall face: the length before the height)
    method: str
    expected_error_pct: Decimal | None
    shape_count: int
    corner_count: int  # every point of every ring, cutout and path
    cutout_count: int
    source: str
    captured_on: date | None
    pixel_size_m: float | None
    height_ft: float | None
    scale_ft_per_px: float | None  # image space
    stored: dict[str, Any]  # the validated body as kept (closing points dropped, text trimmed)
    drawn_by: str | None = None  # None: not stated (an engineer's trace, as before)
    verified: bool | None = None
    view: dict[str, Any] | None = None
    original: Traced | None = None  # the measurement this one replaced, computed alike
    facade: dict[str, Any] | None = None  # space facade: what the metrology measured

    @property
    def kind(self) -> str:
        if self.space == FACADE:
            return FACADE
        return "area" if self.area_sf is not None else "line"

    def record(self) -> dict[str, Any]:
        """The stored body plus the computed fields: ``takeoffs.inputs["measurement"]``."""
        computed: dict[str, Any] = {
            "quantity": format(self.quantity, "f"),
            "method": self.method,
            "expected_error_pct": None
            if self.expected_error_pct is None
            else format(self.expected_error_pct, "f"),
            "shape_count": self.shape_count,
            "corner_count": self.corner_count,
            "cutout_count": self.cutout_count,
        }
        if self.area_sf is not None:
            computed["area_sf"] = format(self.area_sf, "f")
        if self.length_ft is not None:
            computed["length_ft"] = format(self.length_ft, "f")
        if self.scale_ft_per_px is not None:
            computed["scale_ft_per_px"] = round(self.scale_ft_per_px, 9)
        if self.facade is not None:
            computed["facade_measured"] = self.facade
        if self.original is not None:
            computed["original"] = self.original.record()
        return {**self.stored, **computed}

    @property
    def by_model(self) -> bool:
        return self.drawn_by is not None and self.drawn_by != ENGINEER


# --------------------------------------------------------------------------- geometry


def radii(lat_deg: float) -> tuple[float, float]:
    """``(M, N)`` in metres at a geodetic latitude: the WGS84 meridional and prime-vertical
    radii of curvature."""
    s = math.sin(math.radians(lat_deg))
    w = 1 - WGS84_E2 * s * s
    return WGS84_A * (1 - WGS84_E2) / w**1.5, WGS84_A / math.sqrt(w)


def unwrap(points: Sequence[Point], reference: float) -> list[Point]:
    """Longitudes moved by whole turns to within 180 degrees of ``reference``."""
    return [(reference + ((lon - reference + 180.0) % 360.0) - 180.0, lat) for lon, lat in points]


def plane(points: Sequence[Point], origin: Point, lat0: float) -> list[Point]:
    """Lon/lat points in feet on the tangent plane at ``lat0`` (east, north), from ``origin``."""
    m, n = radii(lat0)
    kx = math.radians(1) * n * math.cos(math.radians(lat0)) * FT_PER_M
    ky = math.radians(1) * m * FT_PER_M
    return [((lon - origin[0]) * kx, (lat - origin[1]) * ky) for lon, lat in points]


def shoelace(points: Sequence[Point]) -> float:
    """The signed area of a closed ring (counter-clockwise positive), centred on its first
    point for precision."""
    x0, y0 = points[0]
    total = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    return total / 2


def perimeter(points: Sequence[Point]) -> float:
    n = len(points)
    return sum(math.dist(points[i], points[(i + 1) % n]) for i in range(n))


def path_length(points: Sequence[Point]) -> float:
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def lonlat_path_length_ft(points: Sequence[Point]) -> float:
    """A lon/lat path's length in feet, each segment on the tangent plane at its middle."""
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(points, points[1:], strict=False):
        mid = (lat1 + lat2) / 2
        m, n = radii(mid)
        dx = math.radians(lon2 - lon1) * n * math.cos(math.radians(mid))
        dy = math.radians(lat2 - lat1) * m
        total += math.hypot(dx, dy) * FT_PER_M
    return total


def ring_problem(points: Sequence[Point]) -> str | None:
    """Why a closed ring (in a plane) is not simple, or None: a repeated corner, an edge that
    folds back on the one before it, or two edges that cross or touch. Every pair of edges is
    compared (vectorized: a 500-corner ring is 125,000 pairs)."""
    import numpy as np  # local: the API process imports this module for its checks

    p = np.asarray(points, dtype=np.float64)
    n = len(p)
    p = p - p[0]
    q = np.roll(p, -1, axis=0)
    seg = q - p
    lengths = np.hypot(seg[:, 0], seg[:, 1])
    scale = float(lengths.max()) if n else 0.0
    if scale == 0.0 or bool((lengths <= scale * 1e-12).any()):
        return "has a repeated corner (two consecutive corners at the same place)"
    eps = scale * scale * 1e-12
    # An edge folding back along the one before it (a spike).
    prev = np.roll(seg, 1, axis=0)
    cross_prev = prev[:, 0] * seg[:, 1] - prev[:, 1] * seg[:, 0]
    dot_prev = prev[:, 0] * seg[:, 0] + prev[:, 1] * seg[:, 1]
    if bool(((np.abs(cross_prev) <= eps) & (dot_prev < 0)).any()):
        return "folds back on itself (an edge runs back along the one before it)"
    if n == 3:
        return None
    # Only the pairs whose bounding boxes meet can cross: found first, then tested.
    tol = 1e-12 * scale
    lo, hi = np.minimum(p, q), np.maximum(p, q)
    i, j = np.triu_indices(n, k=2)
    keep = ~((i == 0) & (j == n - 1))  # the closing edge and the first are neighbours
    i, j = i[keep], j[keep]
    meet = (
        (lo[i, 0] <= hi[j, 0] + tol)
        & (lo[j, 0] <= hi[i, 0] + tol)
        & (lo[i, 1] <= hi[j, 1] + tol)
        & (lo[j, 1] <= hi[i, 1] + tol)
    )
    i, j = i[meet], j[meet]
    if not len(i):
        return None
    a, b, c, d = p[i], q[i], p[j], q[j]

    def orient(u: Any, v: Any, w: Any) -> Any:
        return (v[:, 0] - u[:, 0]) * (w[:, 1] - u[:, 1]) - (v[:, 1] - u[:, 1]) * (w[:, 0] - u[:, 0])

    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    proper = (o1 * o2 < -eps * eps) & (o3 * o4 < -eps * eps)

    def on_segment(o: Any, u: Any, v: Any, w: Any) -> Any:
        low, high = np.minimum(u, v), np.maximum(u, v)
        inside = (w >= low - tol) & (w <= high + tol)
        return (np.abs(o) <= eps) & inside[:, 0] & inside[:, 1]

    touching = (
        on_segment(o1, a, b, c)
        | on_segment(o2, a, b, d)
        | on_segment(o3, c, d, a)
        | on_segment(o4, c, d, b)
    )
    if bool((proper | touching).any()):
        return "crosses or touches itself (two of its edges meet)"
    return None


def outside_points(points: Sequence[Point], ring: Sequence[Point]) -> list[int]:
    """The indexes of ``points`` that are neither inside a simple ring nor on its edge
    (even-odd ray casting, vectorized over every point and edge)."""
    import numpy as np

    origin = ring[0]
    r = np.asarray(ring, dtype=np.float64) - origin
    s = np.roll(r, -1, axis=0)
    pts = np.asarray(points, dtype=np.float64) - origin
    scale = float(np.hypot(*(s - r).T).max())
    eps, tol = scale * scale * 1e-12, scale * 1e-12
    x, y = pts[:, 0:1], pts[:, 1:2]  # one row per point, one column per edge
    ax, ay, bx, by = r[:, 0], r[:, 1], s[:, 0], s[:, 1]
    cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
    on_edge = (
        (np.abs(cross) <= eps)
        & (x >= np.minimum(ax, bx) - tol)
        & (x <= np.maximum(ax, bx) + tol)
        & (y >= np.minimum(ay, by) - tol)
        & (y <= np.maximum(ay, by) + tol)
    ).any(axis=1)
    straddles = (ay > y) != (by > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        cross_x = ax + (y - ay) * (bx - ax) / (by - ay)
    crossings = (straddles & (x < cross_x)).sum(axis=1)
    inside = (crossings % 2 == 1) | on_edge
    return [int(k) for k in np.nonzero(~inside)[0]]


# --------------------------------------------------------------------------- validation


class _Check:
    """Collects problems; each helper returns the parsed value or None (problem recorded)."""

    def __init__(self, prefix: str = MEASUREMENT) -> None:
        self.problems: list[str] = []
        self.prefix = prefix

    def add(self, where: str, what: str) -> None:
        self.problems.append(f"{self.prefix}{where} {what}")

    def keys(self, where: str, value: Mapping[str, Any], allowed: frozenset[str]) -> None:
        unknown = sorted(str(k) for k in value if k not in allowed)
        if unknown:
            self.add(where, f"has unknown keys: {', '.join(unknown)}")

    def number(self, where: str, value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, int | float):
            self.add(where, f"must be a number, got {_short(value)}")
            return None
        try:
            number = float(value)
        except OverflowError:  # a JSON integer beyond a double
            number = math.inf
        if not math.isfinite(number):
            self.add(where, f"must be a finite number, got {_short(value)}")
            return None
        return number

    def positive(self, where: str, value: Any, limit: float) -> float | None:
        number = self.number(where, value)
        if number is not None and not 0 < number <= limit:
            self.add(where, f"must be above 0 and at most {limit:g}, got {value!r}")
            return None
        return number

    def point(self, where: str, value: Any, space: str | None) -> Point | None:
        if not isinstance(value, list) or len(value) != 2:
            self.add(where, f"must be a pair of numbers [x, y], got {_short(value)}")
            return None
        x, y = self.number(f"{where}[0]", value[0]), self.number(f"{where}[1]", value[1])
        if x is None or y is None:
            return None
        if space == "lonlat":
            if not -180 <= x <= 180:
                self.add(where, f"has longitude {x:g}; a longitude is from -180 to 180")
                return None
            if not -MAX_LAT <= y <= MAX_LAT:
                self.add(where, f"has latitude {y:g}; a latitude is from -85 to 85")
                return None
        elif space == "image" and not (0 <= x <= MAX_PIXEL and 0 <= y <= MAX_PIXEL):
            self.add(where, f"is at pixel [{x:g}, {y:g}]; pixels are from 0 to 100000")
            return None
        return (x, y)

    def points(
        self, where: str, value: Any, space: str | None, low: int, high: int, what: str
    ) -> list[Point] | None:
        if not isinstance(value, list):
            self.add(where, f"must be a list of points, got {_short(value)}")
            return None
        if len(value) > high + (1 if what == "ring" else 0):
            self.add(where, f"has {len(value)} points; a {what} has {low} to {high}")
            return None
        found: list[Point] = []
        for i, item in enumerate(value):
            point = self.point(f"{where}[{i}]", item, space)
            if point is None:
                return None  # the first bad point is named; the list stops there
            found.append(point)
        if what == "ring" and len(found) > 1 and found[0] == found[-1]:
            found = found[:-1]  # the closing point repeats the first: dropped
        if not low <= len(found) <= high:
            self.add(where, f"has {len(found)} points; a {what} has {low} to {high}")
            return None
        return found


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _q(value: float) -> Decimal:
    return Decimal(repr(value)).quantize(QUANTUM, rounding=ROUND_HALF_UP)


def _image(check: _Check, value: Any, space: str | None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        check.add(".image", "must be an object with at least source")
        return None
    check.keys(".image", value, _IMAGE_KEYS)
    stored: dict[str, Any] = {}
    source = value.get("source")
    if (
        not isinstance(source, str)
        or not source.strip()
        or len(source.strip()) > MAX_SOURCE_CHARS
        or "\x00" in source
    ):
        check.add(
            ".image.source",
            f"must say what the image is and where it came from (1 to {MAX_SOURCE_CHARS} "
            "characters)",
        )
    else:
        stored["source"] = source.strip()
    if value.get("document_id") is not None:
        raw = value["document_id"]
        try:
            stored["document_id"] = str(uuid.UUID(str(raw))) if isinstance(raw, str) else None
        except ValueError:
            stored["document_id"] = None
        if stored["document_id"] is None:
            check.add(".image.document_id", f"must be a document id (a UUID), got {_short(raw)}")
    if value.get("captured_on") is not None:
        raw = value["captured_on"]
        captured: date | None = None
        if isinstance(raw, str) and _ISO_DATE.fullmatch(raw):
            try:
                captured = date.fromisoformat(raw)
            except ValueError:
                captured = None
        if captured is None:
            check.add(".image.captured_on", f"must be a date YYYY-MM-DD, got {_short(raw)}")
        else:
            stored["captured_on"] = captured.isoformat()
    if value.get("pixel_size_m") is not None:
        if space == FACADE:
            check.add(
                ".image.pixel_size_m",
                "is for lon/lat imagery only; a facade takes its size from the wall's length",
            )
        elif space == "image":
            check.add(
                ".image.pixel_size_m",
                "is for lon/lat imagery only; an image in pixels takes its size from the scale",
            )
        else:
            size = check.positive(".image.pixel_size_m", value["pixel_size_m"], MAX_PIXEL_SIZE_M)
            if size is not None:
                stored["pixel_size_m"] = size
    return stored


def _scale(check: _Check, value: Any) -> tuple[dict[str, Any], float, float] | None:
    """``(stored, feet per pixel, the scale's distance in pixels)``."""
    if not isinstance(value, Mapping):
        check.add(".scale", "must be an object with points, length and unit")
        return None
    check.keys(".scale", value, _SCALE_KEYS)
    points = value.get("points")
    pair = (
        [check.point(f".scale.points[{i}]", p, "image") for i, p in enumerate(points)]
        if isinstance(points, list) and len(points) == 2
        else None
    )
    if pair is None:
        check.add(".scale.points", "must be the two pixels the known length runs between")
    length = check.positive(".scale.length", value.get("length"), MAX_LENGTH)
    unit = value.get("unit")
    if unit not in SCALE_UNITS:
        check.add(".scale.unit", f"must be ft or m, got {_short(unit)}")
    if pair is None or any(p is None for p in pair) or length is None or unit not in SCALE_UNITS:
        return None
    a, b = pair
    assert a is not None and b is not None
    distance = math.dist(a, b)
    if distance < MIN_SCALE_PX:
        check.add(
            ".scale.points",
            f"are {distance:.2f} pixels apart; the known length must span at least "
            f"{MIN_SCALE_PX:g} pixels",
        )
        return None
    feet = length * FT_PER_M if unit == "m" else length
    stored = {"points": [list(a), list(b)], "length": length, "unit": unit}
    return stored, feet / distance, distance


@dataclass(frozen=True, slots=True)
class _Shape:
    kind: str
    outer: list[Point]  # ring or path, in the given space
    cutouts: list[list[Point]]


def _shapes(check: _Check, value: Any, space: str | None) -> list[_Shape] | None:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SHAPES:
        count = len(value) if isinstance(value, list) else None
        check.add(
            ".shapes",
            f"must be a list of 1 to {MAX_SHAPES} shapes"
            + ("" if count is None else f", got {count}"),
        )
        return None
    shapes: list[_Shape] = []
    for i, item in enumerate(value):
        where = f".shapes[{i}]"
        if not isinstance(item, Mapping) or item.get("kind") not in KINDS:
            check.add(where, "must be an object with kind area or line")
            continue
        kind = item["kind"]
        if kind == "area":
            check.keys(where, item, _AREA_KEYS)
            ring = check.points(
                f"{where}.ring", item.get("ring"), space, MIN_RING, MAX_RING, "ring"
            )
            cutouts: list[list[Point]] = []
            raw = item.get("cutouts", [])
            if raw is None:
                raw = []
            if not isinstance(raw, list) or len(raw) > MAX_CUTOUTS:
                check.add(f"{where}.cutouts", f"must be a list of at most {MAX_CUTOUTS} rings")
                continue
            for k, cut in enumerate(raw):
                parsed = check.points(
                    f"{where}.cutouts[{k}]", cut, space, MIN_RING, MAX_RING, "ring"
                )
                if parsed is not None:
                    cutouts.append(parsed)
            if ring is not None and len(cutouts) == len(raw):
                shapes.append(_Shape("area", ring, cutouts))
        else:
            check.keys(where, item, _LINE_KEYS)
            path = check.points(
                f"{where}.path", item.get("path"), space, MIN_PATH, MAX_PATH, "path"
            )
            if path is not None:
                shapes.append(_Shape("line", path, []))
    return shapes if len(shapes) == len(value) else None


def measure(value: Any) -> Traced:
    """Check and compute a measurement (module doc), or raise :class:`MeasurementError`."""
    return _measure(value, MEASUREMENT, nested=False)


def _measure(value: Any, prefix: str, *, nested: bool) -> Traced:
    """:func:`measure`, with every problem named under ``prefix`` (``measurement.original``
    for the measurement an edit replaced, which may not hold an original of its own)."""
    check = _Check(prefix)
    if not isinstance(value, Mapping):
        raise MeasurementError([f"{prefix} must be an object (unit, space, shapes, image)"])
    check.keys("", value, _TOP_KEYS)
    unit = value.get("unit")
    if unit not in UNITS:
        check.add(".unit", f"must be SF, SY or LF (EA is counted, not traced), got {_short(unit)}")
    space = value.get("space")
    if space not in SPACES:
        check.add(".space", f"must be lonlat, image or facade, got {_short(space)}")
        space = None
    image = _image(check, value.get("image"), space)
    scale: tuple[dict[str, Any], float, float] | None = None
    if space == "image":
        if value.get("scale") is None:
            check.add(".scale", "is required for an image: two pixels and the length between them")
        else:
            scale = _scale(check, value["scale"])
    elif space == "lonlat" and value.get("scale") is not None:
        check.add(".scale", "is for an image in pixels; lon/lat points need no scale")
    height: float | None = None
    if value.get("height_ft") is not None:
        height = check.positive(".height_ft", value["height_ft"], MAX_LENGTH)
    drawn_by = _drawn_by(check, value.get("drawn_by"))
    verified = value.get("verified")
    if verified is not None and not isinstance(verified, bool):
        check.add(".verified", f"must be true or false, got {_short(verified)}")
        verified = None
    view = _view(check, value.get("view"))
    original: Traced | None = None
    if value.get("original") is not None:
        if nested:
            check.add(
                ".original",
                "cannot hold an original of its own: an edit keeps the first original only",
            )
        else:
            try:
                original = _measure(value["original"], f"{prefix}.original", nested=True)
            except MeasurementError as exc:
                check.problems.extend(exc.problems)
    if space == FACADE:
        return _facade(
            check,
            value,
            prefix=prefix,
            unit=unit,
            image=image,
            provenance=(drawn_by, verified, view, original),
        )
    if value.get(FACADE) is not None:
        check.add(".facade", "goes with space facade (a wall on a photo)")
    raw = _raw_points(value.get("shapes"))
    if raw is not None and raw[0] - raw[1] > MAX_POINTS:
        # Refused before any point is parsed (the body may hold far more than the cap; each
        # ring may lose a closing point, so the cap is certainly exceeded).
        check.add(".shapes", f"have {raw[0]} points in all; a measurement has at most {MAX_POINTS}")
        raise MeasurementError(check.problems)
    shapes = _shapes(check, value.get("shapes"), space)
    if shapes is not None:
        total = sum(len(s.outer) + sum(len(c) for c in s.cutouts) for s in shapes)
        if total > MAX_POINTS:
            check.add(
                ".shapes", f"have {total} points in all; a measurement has at most {MAX_POINTS}"
            )
            shapes = None
    kinds = {s.kind for s in shapes} if shapes else set()
    if len(kinds) > 1:
        check.add(".shapes", "mix area and line shapes; one measurement is all areas or all lines")
    elif unit in UNITS and kinds:
        kind = next(iter(kinds))
        if unit == "LF" and kind == "area":
            check.add(".shapes", "are areas; LF is measured with line shapes")
        elif unit == "LF" and value.get("height_ft") is not None:
            check.add(".height_ft", "goes with SF or SY (a wall face); LF is a length alone")
        elif unit in ("SF", "SY") and kind == "line" and value.get("height_ft") is None:
            check.add(
                ".height_ft",
                f"is required to measure {unit} with lines (a wall face: length x height)",
            )
        elif unit in ("SF", "SY") and kind == "area" and value.get("height_ft") is not None:
            check.add(".height_ft", "goes with line shapes (a wall face), not with areas")
    if check.problems or shapes is None or space is None or image is None:
        raise MeasurementError(check.problems or [f"{prefix} is incomplete"])
    feet_per_px = scale[1] if scale else None
    area_sf, length_ft, edges = _compute(check, shapes, space, feet_per_px)
    if check.problems:
        raise MeasurementError(check.problems)
    if area_sf is not None:
        quantity_raw = area_sf / SF_PER_SY if unit == "SY" else area_sf
    else:
        assert length_ft is not None
        quantity_raw = length_ft * (height or 1.0) / (SF_PER_SY if unit == "SY" else 1)
    quantity = _q(quantity_raw)
    if quantity <= 0 or quantity > MAX_QUANTITY:
        raise MeasurementError(
            [
                f"{prefix} comes to {quantity_raw:,.4f} {unit}; the quantity must be above 0 "
                f"and at most {MAX_QUANTITY:,} (after rounding to 0.01)"
            ]
        )
    pixel_size_m = image.get("pixel_size_m")
    error = expected_error_pct(
        area_sf=area_sf,
        length_ft=length_ft,
        edges_ft=edges,
        lines=len(shapes),
        pixel_ft=(feet_per_px if space == "image" else None)
        or (None if pixel_size_m is None else pixel_size_m * FT_PER_M),
        scale_px=None if scale is None else scale[2],
    )
    stored: dict[str, Any] = {
        "unit": unit,
        "space": space,
        "shapes": [_stored_shape(s) for s in shapes],
        "image": image,
    }
    if scale is not None:
        stored["scale"] = scale[0]
    if height is not None:
        stored["height_ft"] = height
    # Provenance is kept only as sent: a body without it stores exactly what it stored before.
    if drawn_by is not None:
        stored["drawn_by"] = drawn_by
    if verified is not None:
        stored["verified"] = verified
    if view is not None:
        stored["view"] = view
    if original is not None:
        stored["original"] = original.stored
    return Traced(
        unit=str(unit),
        space=space,
        quantity=quantity,
        area_sf=None if area_sf is None else _q(area_sf),
        length_ft=None if length_ft is None else _q(length_ft),
        method=METHOD_IMAGE if space == "image" else METHOD_LONLAT,
        expected_error_pct=error,
        shape_count=len(shapes),
        corner_count=sum(len(s.outer) + sum(len(c) for c in s.cutouts) for s in shapes),
        cutout_count=sum(len(s.cutouts) for s in shapes),
        source=str(image["source"]),
        captured_on=None
        if image.get("captured_on") is None
        else date.fromisoformat(image["captured_on"]),
        pixel_size_m=pixel_size_m,
        height_ft=height,
        scale_ft_per_px=feet_per_px,
        stored=stored,
        drawn_by=drawn_by,
        verified=verified,
        view=view,
        original=original,
    )


def _drawn_by(check: _Check, value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and (value == ENGINEER or _MODEL.fullmatch(value)):
        return value
    check.add(
        ".drawn_by",
        f"must be engineer or model:<name>@<version> (letters, digits and . _ - / +), "
        f"got {_short(value)}",
    )
    return None


def _quad(check: _Check, where: str, value: Any, frame: tuple[float, float] | None) -> Any:
    """Four ``[x, y]`` pixels, bottom-left, bottom-right, top-right, top-left as the photo
    shows them, inside the photo's frame; a :class:`basis.geo.facade.Quad` or None."""
    from basis.geo.facade import Quad

    if not isinstance(value, list) or len(value) != 4:
        check.add(
            where,
            "must be the 4 corners [[x, y], ...]: bottom-left, bottom-right, top-right, "
            "top-left as the photo shows them",
        )
        return None
    points: list[Point] = []
    for i, item in enumerate(value):
        point = check.point(f"{where}[{i}]", item, "image")
        if point is None:
            return None
        if frame is not None and not (point[0] <= frame[0] and point[1] <= frame[1]):
            check.add(
                f"{where}[{i}]",
                f"is at pixel [{point[0]:g}, {point[1]:g}], outside the {frame[0]:g} x "
                f"{frame[1]:g} photo",
            )
            return None
        points.append(point)
    bl, br, tr, tl = points
    if not (bl[0] < br[0] and tl[0] < tr[0] and (bl[1] + br[1]) / 2 > (tl[1] + tr[1]) / 2):
        check.add(
            where,
            "must run bottom-left, bottom-right, top-right, top-left as the photo shows them "
            "(the bottom edge lower than the top, left before right)",
        )
        return None
    return Quad(bl, br, tr, tl)


def _facade(
    check: _Check,
    value: Mapping[str, Any],
    *,
    prefix: str,
    unit: Any,
    image: dict[str, Any] | None,
    provenance: tuple[str | None, Any, dict[str, Any] | None, Traced | None],
) -> Traced:
    """Space ``facade`` (EVAL_REPORT 111): a wall's four corners and its openings' on one
    photo, scaled by the wall's known length, measured by :func:`basis.geo.facade.measure`
    (single-view metrology, EVAL_REPORT 109). The quantity is the face net of the openings
    (the siding) in SF or SY; the record adds what the metrology measured
    (``facade_measured``)."""
    from basis.geo import facade as fc
    from basis.vision.camera import hfov_deg

    drawn_by, verified, view, original = provenance
    for key in ("shapes", "scale", "height_ft"):
        if value.get(key) is not None:
            check.add(
                f".{key}",
                "does not go with space facade (the wall's corners and its length size it)",
            )
    if unit == "LF":
        check.add(".unit", "must be SF or SY for a facade (its net face)")
    frame: tuple[float, float] | None = None
    if view is None or "width" not in view or "height" not in view:
        check.add(
            ".view",
            "is required for a facade, with width and height (the photo's frame, across "
            "which the field of view is measured)",
        )
    elif view.get("kind") != "document":
        check.add(".view.kind", "must be document for a facade (a photo of the study)")
    else:
        frame = (float(view["width"]), float(view["height"]))
    if image is not None and image.get("document_id") is None:
        check.add(".image.document_id", "is required for a facade (the photo it is measured on)")
    body = value.get(FACADE)
    if not isinstance(body, Mapping):
        check.add(
            ".facade",
            "must be an object with wall, openings, wall_length_ft and wall_length_from",
        )
        raise MeasurementError(check.problems)
    check.keys(".facade", body, FACADE_KEYS)
    wall = _quad(check, ".facade.wall", body.get("wall"), frame)
    raw_openings = body.get("openings", [])
    if raw_openings is None:
        raw_openings = []
    openings: list[Any] = []
    stored_openings: list[dict[str, Any]] = []
    if not isinstance(raw_openings, list) or len(raw_openings) > MAX_OPENINGS:
        check.add(".facade.openings", f"must be a list of at most {MAX_OPENINGS} openings")
        raw_openings = []
    for i, item in enumerate(raw_openings):
        where = f".facade.openings[{i}]"
        if not isinstance(item, Mapping):
            check.add(where, "must be an object with kind and corners")
            continue
        check.keys(where, item, _OPENING_KEYS)
        kind = item.get("kind")
        if kind not in OPENING_KINDS:
            check.add(f"{where}.kind", f"must be window, door, garage or other, got {_short(kind)}")
            continue
        quad = _quad(check, f"{where}.corners", item.get("corners"), frame)
        if quad is not None:
            openings.append(fc.Opening(kind, quad))
            stored_openings.append({"kind": kind, "corners": [list(p) for p in quad.points()]})
    length = check.positive(".facade.wall_length_ft", body.get("wall_length_ft"), MAX_WALL_FT)
    length_from = body.get("wall_length_from")
    length_from_text: str | None = None
    if length_from is None:
        check.add(
            ".facade.wall_length_from",
            "must say where the wall's length comes from (typed by the engineer, the lidar "
            "shell's side, a plan)",
        )
    else:
        length_from_text = _text(check, ".facade.wall_length_from", length_from)
    lens = body.get("lens")
    focal35: float | None = None
    stored_lens: dict[str, Any] | None = None
    if lens is not None:
        if not isinstance(lens, Mapping):
            check.add(".facade.lens", "must be an object with focal_length_35mm and from")
        else:
            check.keys(".facade.lens", lens, _LENS_KEYS)
            focal35 = check.positive(
                ".facade.lens.focal_length_35mm", lens.get("focal_length_35mm"), MAX_FOCAL_35MM
            )
            if lens.get("from") not in LENS_FROM:
                check.add(
                    ".facade.lens.from",
                    f"must be photo or engineer, got {_short(lens.get('from'))}",
                )
            elif focal35 is not None:
                stored_lens = {"focal_length_35mm": focal35, "from": lens["from"]}
    if check.problems or wall is None or length is None or frame is None or image is None:
        raise MeasurementError(check.problems or [f"{prefix} is incomplete"])
    camera = fc.Camera(
        int(round(frame[0])),
        int(round(frame[1])),
        None if focal35 is None else hfov_deg(focal35, frame[0], frame[1]),
    )
    got = fc.measure(camera, wall, length / FT_PER_M, openings)
    if got.code is not None:
        for part, code, why in got.refusals:
            where = ".facade.wall" if part == "wall" else ".facade.openings"
            check.add(where, f"cannot be measured: {why} [{code}]")
        raise MeasurementError(check.problems)
    for text in got.left_out:
        check.add(".facade.openings", f"has {text}: move its corners onto the wall")
    if check.problems:
        raise MeasurementError(check.problems)
    assert got.height_m is not None and got.gross_m2 is not None and got.net_m2 is not None
    sf = FT_PER_M * FT_PER_M
    net_sf = got.net_m2 * sf
    quantity_raw = net_sf / SF_PER_SY if unit == "SY" else net_sf
    quantity = _q(quantity_raw)
    if quantity <= 0 or quantity > MAX_QUANTITY:
        raise MeasurementError(
            [
                f"{prefix} comes to {quantity_raw:,.4f} {unit}; the quantity must be above 0 "
                f"and at most {MAX_QUANTITY:,} (after rounding to 0.01)"
            ]
        )
    errors = got.expected_error_pct
    error_key = "net" if "net" in errors else "height"
    error = _q(errors[error_key]) if error_key in errors else None
    measured: dict[str, Any] = {
        "height_ft": format(_q(got.height_m * FT_PER_M), "f"),
        "gross_sf": format(_q(got.gross_m2 * sf), "f"),
        "openings_sf": format(_q((got.openings_m2 or 0.0) * sf), "f"),
        "net_sf": format(_q(net_sf), "f"),
        "trim_lf": format(_q((got.trim_m or 0.0) * FT_PER_M), "f"),
        "windows": got.windows or 0,
        "doors": got.doors or 0,
        "openings": [
            {
                "kind": o.kind,
                "width_ft": format(_q(o.width_m * FT_PER_M), "f"),
                "height_ft": format(_q(o.height_m * FT_PER_M), "f"),
                "area_sf": format(_q(o.area_m2 * sf), "f"),
            }
            for o in got.openings
        ],
        "focal_from": got.focal_from,
        "hfov_deg": None if camera.hfov_deg is None else round(camera.hfov_deg, 4),
        "view_deg": None if got.view_deg is None else round(got.view_deg, 2),
        "skew_deg": None if got.skew_deg is None else round(got.skew_deg, 2),
        "expected_error_pct": {k: format(_q(v), "f") for k, v in sorted(errors.items())},
    }
    stored_facade: dict[str, Any] = {
        "wall": [list(p) for p in wall.points()],
        "openings": stored_openings,
        "wall_length_ft": length,
        "wall_length_from": length_from_text,
    }
    if stored_lens is not None:
        stored_facade["lens"] = stored_lens
    stored: dict[str, Any] = {"unit": unit, "space": FACADE, FACADE: stored_facade, "image": image}
    if drawn_by is not None:
        stored["drawn_by"] = drawn_by
    if verified is not None:
        stored["verified"] = verified
    if view is not None:
        stored["view"] = view
    if original is not None:
        stored["original"] = original.stored
    return Traced(
        unit=str(unit),
        space=FACADE,
        quantity=quantity,
        area_sf=_q(net_sf),
        length_ft=None,
        method=METHOD_FACADE,
        expected_error_pct=error,
        shape_count=1 + len(openings),
        corner_count=4 * (1 + len(openings)),
        cutout_count=len(openings),
        source=str(image["source"]),
        captured_on=None
        if image.get("captured_on") is None
        else date.fromisoformat(image["captured_on"]),
        pixel_size_m=None,
        height_ft=float(_q(got.height_m * FT_PER_M)),
        scale_ft_per_px=None,
        stored=stored,
        drawn_by=drawn_by,
        verified=verified,
        view=view,
        original=original,
        facade=measured,
    )


def _text(check: _Check, where: str, value: Any) -> str | None:
    if (
        isinstance(value, str)
        and value.strip()
        and len(value.strip()) <= MAX_VIEW_TEXT
        and "\x00" not in value
    ):
        return value.strip()
    check.add(where, f"must be text of 1 to {MAX_VIEW_TEXT} characters, got {_short(value)}")
    return None


def _view(check: _Check, value: Any) -> dict[str, Any] | None:
    """The picture the shape was drawn on (module doc, "Provenance"), or None when absent."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        check.add(".view", "must be an object with kind and an id or a label")
        return None
    check.keys(".view", value, _VIEW_KEYS)
    stored: dict[str, Any] = {}
    kind = value.get("kind")
    if kind not in VIEW_KINDS:
        check.add(".view.kind", f"must be capture, public or document, got {_short(kind)}")
    else:
        stored["kind"] = kind
    for key in ("id", "label"):
        if value.get(key) is not None:
            text = _text(check, f".view.{key}", value[key])
            if text is not None:
                stored[key] = text
    if value.get("id") is None and value.get("label") is None:
        check.add(".view", "must name the picture by its id or its label")
    has_w, has_h = value.get("width") is not None, value.get("height") is not None
    if has_w != has_h:
        check.add(".view", "takes width and height together (the pixel frame of the points)")
    elif has_w:
        for key in ("width", "height"):
            size = check.positive(f".view.{key}", value[key], MAX_PIXEL)
            if size is not None:
                stored[key] = size
    return stored


def _raw_points(shapes: Any) -> tuple[int, int] | None:
    """``(points, rings)`` a body's shapes hold before parsing, or None when they are not
    lists of the expected form (refused later, with their own messages)."""
    if not isinstance(shapes, list) or len(shapes) > MAX_SHAPES:
        return None
    points = rings = 0
    for shape in shapes:
        if not isinstance(shape, Mapping):
            return None
        lists: list[Any] = [shape.get("path")] if "path" in shape else []
        if "ring" in shape:
            cutouts = shape.get("cutouts") or []
            if not isinstance(cutouts, list):
                return None
            lists += [shape.get("ring"), *cutouts]
            rings += 1 + len(cutouts)
        for item in lists:
            if not isinstance(item, list):
                return None
            points += len(item)
    return points, rings


def _stored_shape(shape: _Shape) -> dict[str, Any]:
    if shape.kind == "line":
        return {"kind": "line", "path": [list(p) for p in shape.outer]}
    stored: dict[str, Any] = {"kind": "area", "ring": [list(p) for p in shape.outer]}
    if shape.cutouts:
        stored["cutouts"] = [[list(p) for p in c] for c in shape.cutouts]
    return stored


def _compute(
    check: _Check, shapes: Sequence[_Shape], space: str, feet_per_px: float | None
) -> tuple[float | None, float | None, float]:
    """``(area in SF or None, length in ft or None, the edges' total length in ft)``; a
    shape that is not simple, a cutout outside its ring or a shape with no net area is a
    problem recorded on ``check``."""
    reference = shapes[0].outer[0][0]
    if shapes[0].kind == "line":
        total = 0.0
        for shape in shapes:
            if space == "lonlat":
                total += lonlat_path_length_ft(unwrap(shape.outer, reference))
            else:
                assert feet_per_px is not None
                total += path_length(shape.outer) * feet_per_px
        return None, total, total
    area = 0.0
    edges = 0.0
    for i, shape in enumerate(shapes):
        where = f".shapes[{i}]"
        rings = [shape.outer, *shape.cutouts]
        if space == "lonlat":
            rings = [unwrap(r, reference) for r in rings]
            lats = [lat for _, lat in rings[0]]
            lat0 = (min(lats) + max(lats)) / 2
            origin = rings[0][0]
            flat = [plane(r, origin, lat0) for r in rings]
        else:
            assert feet_per_px is not None
            flat = [[(x * feet_per_px, y * feet_per_px) for x, y in r] for r in rings]
        names = ["ring", *(f"cutouts[{k}]" for k in range(len(shape.cutouts)))]
        bad = False
        for name, ring in zip(names, flat, strict=True):
            problem = ring_problem(ring)
            if problem is None and abs(shoelace(ring)) <= 1e-9 * perimeter(ring) ** 2:
                problem = "has no area (its corners lie on one line)"
            if problem is not None:
                check.add(f"{where}.{name}", problem)
                bad = True
        if bad:
            continue
        outer = flat[0]
        for k, cut in enumerate(flat[1:]):
            outside = outside_points(cut, outer)
            if outside:
                check.add(
                    f"{where}.cutouts[{k}]",
                    f"is not inside its ring (corner {outside[0]} is outside)",
                )
                bad = True
        if bad:
            continue
        net = abs(shoelace(outer)) - sum(abs(shoelace(c)) for c in flat[1:])
        if net <= 0:
            check.add(where, "has no area left once its cutouts are taken out")
            continue
        area += net
        edges += sum(perimeter(r) for r in flat)
    return area, None, edges


def expected_error_pct(
    *,
    area_sf: float | None,
    length_ft: float | None,
    edges_ft: float,
    lines: int,
    pixel_ft: float | None,
    scale_px: float | None,
) -> Decimal | None:
    """The expected error bound in percent (module doc), or None when the pixel size is not
    known. ``edges_ft``: the perimeter of every ring and cutout for an area; ``lines``: the
    number of line shapes (two traced ends each); ``scale_px``: the image scale's distance
    in pixels (None in lon/lat space)."""
    if pixel_ft is None:
        return None
    if area_sf is not None:
        relative = edges_ft * pixel_ft / area_sf
        if scale_px is not None:
            relative += 2 * (2 / scale_px)
    else:
        assert length_ft is not None
        relative = 2 * pixel_ft * lines / length_ft
        if scale_px is not None:
            relative += 2 / scale_px
    return _q(relative * 100)


def problems(value: Any) -> list[str]:
    """Why ``value`` cannot be applied as a measurement (empty when it can)."""
    try:
        measure(value)
    except MeasurementError as exc:
        return exc.problems
    return []


# --------------------------------------------------------------------------- takeoff


def _counts(traced: Traced) -> str:
    n = traced.shape_count
    noun = traced.kind + ("s" if n != 1 else "")
    corners = traced.corner_count
    if traced.kind == "area":
        text = f"{n} {noun} of {corners} corners"
        if traced.cutout_count:
            text += f" ({traced.cutout_count} cut out)"
        return text
    text = f"{n} {noun} of {corners} points"
    if traced.height_ft is not None:
        text += f" x {traced.height_ft:g} ft high"
    return text


def _who(traced: Traced) -> str:
    # A model's name is the operator's to see (the record keeps ``drawn_by``); the sentence,
    # which the schedule and the report may print, says Basis (AGENTS.md: no provider names).
    return "drafted by Basis" if traced.by_model else "traced by the engineer"


def formula(traced: Traced) -> str:
    """The takeoff's formula, e.g. ``5,844.12 SF traced by the engineer on <source>
    (captured 2020-04-18), 1 area of 15 corners; expected error up to 3.40 %``. A model's
    draft reads "drafted by Basis"; ``verified`` adds "verified by the engineer" or "not
    verified"; an ``original`` adds what it replaced ("replaces 812.40 SF drafted by
    Basis"). A body without provenance reads exactly as before."""
    when = (
        f"captured {traced.captured_on.isoformat()}"
        if traced.captured_on is not None
        else "undated"
    )
    error = (
        "expected error unknown (no pixel size)"
        if traced.expected_error_pct is None
        else f"expected error up to {traced.expected_error_pct} %"
    )
    scale = ""
    if traced.scale_ft_per_px is not None:
        scale = f", scale {traced.scale_ft_per_px:.4g} ft per pixel"
    tail = ""
    if traced.verified is not None:
        tail += "; verified by the engineer" if traced.verified else "; not verified"
    if traced.original is not None:
        was = traced.original
        tail += f"; replaces {was.quantity:,} {was.unit} {_who(was)}"
    if traced.space == FACADE and traced.facade is not None:
        return _facade_formula(traced, when, tail)
    return (
        f"{traced.quantity:,} {traced.unit} {_who(traced)} on {traced.source} ({when}), "
        f"{_counts(traced)}{scale}; {error}{tail}"
    )


def _facade_formula(traced: Traced, when: str, tail: str) -> str:
    """A facade's sentence, e.g. ``478.10 SF net siding measured by the engineer on <source>
    (undated): a wall 36.33 ft long (typed by the engineer) x 19.82 ft high, 6 openings (5
    windows, 1 door) 121.10 SF out, trim 98.40 LF; expected error up to 2.60 % (the wall's
    length not counted)``."""
    m = traced.facade or {}
    body = traced.stored.get(FACADE, {})
    who = "drafted by Basis" if traced.by_model else "measured by the engineer"
    n = len(m.get("openings", []))
    windows, doors = m.get("windows", 0), m.get("doors", 0)
    kinds = (
        f"{windows} window{'s' if windows != 1 else ''}, {doors} door{'s' if doors != 1 else ''}"
    )
    error = (
        "expected error unknown"
        if traced.expected_error_pct is None
        else f"expected error up to {traced.expected_error_pct} %"
    )
    lens = "" if m.get("focal_from") == "field of view" else f", lens from {m.get('focal_from')}"
    return (
        f"{traced.quantity:,} {traced.unit} net siding {who} on {traced.source} ({when}): a wall "
        f"{body.get('wall_length_ft', 0):g} ft long ({body.get('wall_length_from')}) x "
        f"{m.get('height_ft')} ft high, {n} opening{'s' if n != 1 else ''} ({kinds}) "
        f"{m.get('openings_sf')} SF out, trim {m.get('trim_lf')} LF{lens}; {error} (the wall's "
        f"length not counted){tail}"
    )


def review_flags(
    traced: Traced,
    *,
    placed_in_service: date | None,
    after_service_days: int,
    error_flag_pct: Decimal,
) -> list[str]:
    """The review reasons the measurement raises (module doc); never a refusal. The
    after-service check needs the study's placed-in-service date (the intake's)."""
    flags: list[str] = []
    if traced.captured_on is None:
        flags.append(UNDATED)
    elif (
        placed_in_service is not None
        and (traced.captured_on - placed_in_service).days > after_service_days
    ):
        flags.append(AFTER_SERVICE)
    if traced.expected_error_pct is None:
        flags.append(ERROR_UNKNOWN)
    elif traced.expected_error_pct > error_flag_pct:
        flags.append(COARSE)
    return flags


__all__ = [
    "AFTER_SERVICE",
    "BODY_KEYS",
    "FACADE",
    "COARSE",
    "ENGINEER",
    "ERROR_UNKNOWN",
    "FLAGS",
    "MAX_POINTS",
    "MAX_QUANTITY",
    "MEASUREMENT",
    "METHOD_IMAGE",
    "METHOD_LONLAT",
    "UNDATED",
    "VIEW_KINDS",
    "MeasurementError",
    "Traced",
    "expected_error_pct",
    "formula",
    "lonlat_path_length_ft",
    "measure",
    "plane",
    "problems",
    "radii",
    "review_flags",
    "ring_problem",
    "shoelace",
    "unwrap",
]
