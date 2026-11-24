"""The appraisal sketch's outline, read from the PDF's own line work.

:mod:`basis.extraction.sketch` reads the sketch's area table from text and stops there: the
table does not carry the outline, so the takeoff's perimeter stayed a 1.5:1 rectangle
(EVAL_REPORT 96.4). A sketch a tool printed into a born-digital PDF is a vector drawing: its
walls are straight stroked segments. This module turns those strokes into closed loops and
believes a loop only when the page's own table agrees with it (SKETCH_READER.md section 6;
measured in EVAL_REPORT 100). No model, no OCR, no call.

The steps:

1. **Strokes** (:func:`page_drawings`, the only I/O: ``pdfplumber`` on the sketch pages). On a
   page whose text layer carries the "Area Calculations Summary" heading, every straight,
   solid, stroked segment drawn above the heading. A dash pattern is not a wall (the tools
   draw porches and other uncounted areas dashed); a path with a curve operator is not read.
   A page with no heading in its text layer, or no stroke above it, has no line work to read
   (a scanned or image sketch).
2. **Loops** (:func:`faces`). The strokes of one style (width and colour) form a planar
   graph: endpoints are snapped, segments are split where they meet or cross, duplicates are
   merged, and the bounded faces are walked edge by edge (at each corner the next edge by
   angle). Strokes that close nothing (a stray line, a leader) are dropped by the walk. The
   result does not depend on drawing order.
3. **Belief** (:func:`match_page`). One scale (points per foot) per page. A loop is believed
   for one of the page's *verified* table rows when its area at that scale is the row's
   printed area and it shows the row's printed dimensions (:func:`shows_dimensions`: every
   rectangle the row's calculation prints spans the loop's corners). The area fixes the
   scale; the dimensions are the independent check, because on a page with many loops some
   pair of them always reproduces two areas at some scale, and a small loop's walls land on
   any printed precision (EVAL_REPORT 100.3). The scale at which the most rows are
   reproduced, one loop each, wins; a tie between two scales is ambiguous and nothing on the
   page is believed.
4. **Fields** (:func:`read_outline`). ``sketch.ground_perimeter_ft``: the first-floor loops
   and the attached-garage loops of one page as one outline, walls they share counted once.
   ``sketch.upper_perimeter_ft``: the loops of the floors above, per floor; loops on one page
   are joined where they share a wall, and loops on separate pages are summed as separate
   masses, because nothing on two pages places one against the other (a wall they share
   would be counted twice; ``separate_pages`` in the trace says when that happened).

Whenever a step fails, no field is written and the reason is recorded in
``raw["sketch"]["outline"]``; the takeoff's perimeter then stays derived. No substitute value
is involved (EVAL_REPORT 78): a perimeter is written only when it was read.
"""

from __future__ import annotations

import io
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

from basis.extraction.field_mappings import TABLE_CONFIDENCE
from basis.extraction.results import ExtractedField
from basis.extraction.sketch import (
    GARAGE_AREA,
    GROUND_FLOOR_AREA,
    SketchArea,
    SketchReading,
)

GROUND_PERIMETER: Final = "sketch.ground_perimeter_ft"
UPPER_PERIMETER: Final = "sketch.upper_perimeter_ft"
FIELD_KEYS: Final = (GROUND_PERIMETER, UPPER_PERIMETER)
# The source of a field read from the PDF's drawing operators (``results.Source``).
SOURCE: Final = "drawing"

Point = tuple[float, float]
Segment = tuple[Point, Point]

# Endpoints closer than this (PDF points; 1/72 inch) are one corner. The client PDF's
# coincident endpoints differ by up to 0.0001 pt; the thinnest wall seen is 1.4 pt wide.
SNAP_PT: Final = 0.05
# A style with more strokes than this is not a building outline (hatching, a form's rules);
# it bounds the pairwise split, which is quadratic.
MAX_STROKES: Final = 1500
# How far a loop's area at the page's scale may be from the printed area, besides the
# rounding of the printed figure: coordinate noise, as a share of the area.
AREA_NOISE: Final = 0.0005
# Coordinate noise allowed on a length that is compared with a printed dimension, in points
# (so 0.02 ft at 5 points per foot): the client PDF's walls are within 0.06 pt of their
# printed lengths (EVAL_REPORT 100). The scale's own uncertainty from the rounding of the
# printed areas is added to it.
WALL_NOISE_PT: Final = 0.1
# ...and never more than this in feet, half of the finest printed precision: a loop drawn too
# small to resolve it cannot show a printed dimension, whatever its coordinates say (without
# the cap, a loop a few points across "matches" any dimension at the scale its area implies).
WALL_NOISE_CAP_FT: Final = 0.05
# Printed precisions a loop's walls can be stated at, coarsest first: when every wall lands
# on the page's precision the perimeter is the sum of the printed lengths, otherwise of the
# drawn ones. It is not what makes a loop believed (EVAL_REPORT 100.3).
GRIDS_FT: Final = (Decimal(1), Decimal("0.5"), Decimal("0.1"))
# Two scales this close (relative) are the same scale.
SAME_SCALE: Final = 0.002

_HEADING: Final = r"area\s+calculations?\s+summary"
_CURVE_OPS: Final = frozenset({"c", "v", "y"})


# --- strokes ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stroke:
    """One straight stroked segment of a page, in points from the page's top-left."""

    a: Point
    b: Point
    width: float
    colour: str
    dashed: bool

    @property
    def style(self) -> tuple[float, str]:
        return (round(self.width, 2), self.colour)


@dataclass(frozen=True, slots=True)
class PageDrawing:
    """What one sketch page's drawing operators give the reader."""

    page: int
    # Top of the area table's heading in the text layer; None: the text layer has none.
    heading_top: float | None
    strokes: tuple[Stroke, ...] = ()  # straight stroked segments above the heading
    curved_paths: int = 0  # stroked paths with a curve operator above the heading (not read)
    images: int = 0  # images on the page


def _is_dashed(dash: Any) -> bool:
    """pdfminer's dash is ``(pattern, phase)``; a solid line has no pattern."""
    if not dash:
        return False
    pattern = dash[0] if isinstance(dash, (tuple, list)) else dash
    try:
        return any(float(v) > 0 for v in pattern)
    except TypeError:
        return bool(pattern)


def _path_segments(obj: Mapping[str, Any]) -> tuple[list[Segment], bool]:
    """``(the straight segments of a pdfplumber path object, whether it has a curve)``."""
    path = obj.get("path")
    if not path:
        pts = obj.get("pts") or []
        return list(zip(pts, pts[1:], strict=False)), False
    out: list[Segment] = []
    start: Point | None = None
    last: Point | None = None
    for step in path:
        op = step[0]
        if op in _CURVE_OPS:
            return [], True
        if op == "m":
            start = last = step[1]
        elif op == "l" and last is not None:
            out.append((last, step[1]))
            last = step[1]
        elif op == "h" and last is not None and start is not None:
            out.append((last, start))
            last = start
    return out, False


def _page_drawing(page: Any, number: int) -> PageDrawing:
    found = page.search(_HEADING, regex=True, case=False, return_chars=False, return_groups=False)
    images = len(page.images)
    if not found:
        return PageDrawing(number, None, images=images)
    top = min(float(match["top"]) for match in found)
    strokes: list[Stroke] = []
    curved = 0

    def add(segments: Iterable[Segment], obj: Mapping[str, Any]) -> None:
        width = float(obj.get("linewidth") or 0)
        colour = repr(obj.get("stroking_color"))
        dashed = _is_dashed(obj.get("dash"))
        for (x0, y0), (x1, y1) in segments:
            if max(y0, y1) > top or math.hypot(x1 - x0, y1 - y0) <= SNAP_PT:
                continue
            strokes.append(
                Stroke((float(x0), float(y0)), (float(x1), float(y1)), width, colour, dashed)
            )

    for obj in (*page.lines, *page.curves):
        if not obj.get("stroke"):
            continue
        segments, has_curve = _path_segments(obj)
        if has_curve:
            if float(obj["bottom"]) <= top:
                curved += 1
            continue
        add(segments, obj)
    for rect in page.rects:
        if not rect.get("stroke"):
            continue
        x0, y0, x1, y1 = (float(rect[k]) for k in ("x0", "top", "x1", "bottom"))
        add(
            [
                ((x0, y0), (x1, y0)),
                ((x1, y0), (x1, y1)),
                ((x1, y1), (x0, y1)),
                ((x0, y1), (x0, y0)),
            ],
            rect,
        )
    return PageDrawing(number, top, tuple(strokes), curved, images)


def page_drawings(pdf_bytes: bytes, pages: Sequence[int]) -> dict[int, PageDrawing]:
    """The line work of ``pages`` (1-based) of a PDF (synchronous and CPU-bound: run on a
    thread). A page the document does not have is left out; a PDF that cannot be opened
    gives no page (the extraction has already read the same bytes)."""
    import pdfplumber
    from pdfminer.pdfparser import PDFSyntaxError
    from pdfplumber.utils.exceptions import PdfminerException

    out: dict[int, PageDrawing] = {}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for number in sorted(set(pages)):
                if not 1 <= number <= len(pdf.pages):
                    continue
                page = pdf.pages[number - 1]
                out[number] = _page_drawing(page, number)
                page.flush_cache()
    except (PdfminerException, PDFSyntaxError):
        return {}
    return out


# --- loops: the bounded faces of a set of segments -----------------------------------------


@dataclass(frozen=True, slots=True)
class Loop:
    """A closed outline: its corners in order (collinear points merged), in points."""

    corners: tuple[Point, ...]

    @property
    def area(self) -> float:
        return abs(_signed_area(self.corners))

    @property
    def walls(self) -> tuple[Segment, ...]:
        return tuple(zip(self.corners, self.corners[1:] + self.corners[:1], strict=True))

    @property
    def perimeter(self) -> float:
        return sum(math.dist(a, b) for a, b in self.walls)


def _signed_area(points: Sequence[Point]) -> float:
    """The shoelace formula."""
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, (*points[1:], points[0]), strict=True):
        total += x0 * y1 - x1 * y0
    return total / 2


def _snap(points: Iterable[Point]) -> dict[Point, int]:
    """Each point's corner id: points within :data:`SNAP_PT` of each other are one corner."""
    ids: dict[Point, int] = {}
    count = 0
    cells: dict[tuple[int, int], list[tuple[Point, int]]] = defaultdict(list)
    for point in points:
        if point in ids:
            continue
        cx, cy = int(point[0] // SNAP_PT), int(point[1] // SNAP_PT)
        found: int | None = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other, other_id in cells.get((cx + dx, cy + dy), ()):
                    if math.dist(point, other) <= SNAP_PT:
                        found = other_id
                        break
                if found is not None:
                    break
            if found is not None:
                break
        if found is None:
            found, count = count, count + 1
        ids[point] = found
        cells[(cx, cy)].append((point, ids[point]))
    return ids


def _along(a: Point, b: Point, p: Point) -> tuple[float, float]:
    """``(t, distance)``: where ``p`` projects on segment ``a``-``b`` (0 at a, 1 at b) and
    how far from the segment's line it lies."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2
    return t, abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / math.sqrt(length2)


def _crossing(s: Segment, t: Segment) -> Point | None:
    """Where two segments properly cross (not at an end of either, not when parallel)."""
    (x1, y1), (x2, y2) = s
    (x3, y3), (x4, y4) = t
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-12:
        return None
    u = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    v = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / den
    if not (0 < u < 1 and 0 < v < 1):
        return None
    return (x1 + u * (x2 - x1), y1 + u * (y2 - y1))


def _node(segments: Sequence[Segment]) -> list[Segment]:
    """``segments`` split wherever another one ends on them or crosses them."""
    boxes = [
        (
            min(a[0], b[0]) - SNAP_PT,
            min(a[1], b[1]) - SNAP_PT,
            max(a[0], b[0]) + SNAP_PT,
            max(a[1], b[1]) + SNAP_PT,
        )
        for a, b in segments
    ]
    cuts: list[list[tuple[float, Point]]] = [[] for _ in segments]
    for i, s in enumerate(segments):
        for j in range(i + 1, len(segments)):
            bi, bj = boxes[i], boxes[j]
            if bi[2] < bj[0] or bj[2] < bi[0] or bi[3] < bj[1] or bj[3] < bi[1]:
                continue
            t = segments[j]
            for mine, cut, other in ((s, cuts[i], t), (t, cuts[j], s)):
                length = math.dist(*mine)
                for end in other:
                    at, off = _along(mine[0], mine[1], end)
                    if off <= SNAP_PT and SNAP_PT < at * length < length - SNAP_PT:
                        cut.append((at, end))
            cross = _crossing(s, t)
            if cross is not None:
                for mine, cut in ((s, cuts[i]), (t, cuts[j])):
                    at, _ = _along(mine[0], mine[1], cross)
                    length = math.dist(*mine)
                    if SNAP_PT < at * length < length - SNAP_PT:
                        cut.append((at, cross))
    out: list[Segment] = []
    for (a, b), cut in zip(segments, cuts, strict=True):
        points = [a, *(p for _, p in sorted(cut)), b]
        out.extend(zip(points, points[1:], strict=False))
    return out


def _cycles(edges: set[tuple[int, int]], at: Mapping[int, Point]) -> list[list[int]]:
    """Every face of the planar graph as a cycle of corner ids: from each directed edge,
    the next edge at its head is the next one by angle after the way back."""
    around: dict[int, list[int]] = defaultdict(list)
    for u, v in edges:
        around[u].append(v)
        around[v].append(u)
    for u, near in around.items():
        ux, uy = at[u]
        near.sort(key=lambda v: math.atan2(at[v][1] - uy, at[v][0] - ux))
    position = {(u, v): i for u, near in around.items() for i, v in enumerate(near)}
    seen: set[tuple[int, int]] = set()
    cycles: list[list[int]] = []
    for u, near in around.items():
        for v in near:
            if (u, v) in seen:
                continue
            cycle: list[int] = []
            a, b = u, v
            while (a, b) not in seen:
                seen.add((a, b))
                cycle.append(a)
                ring = around[b]
                a, b = b, ring[(position[(b, a)] + 1) % len(ring)]
            cycles.append(cycle)
    return cycles


def _corners(points: Sequence[Point]) -> tuple[Point, ...]:
    """``points`` without the ones that lie on the straight line between their neighbours."""
    kept = list(points)
    changed = True
    while changed and len(kept) > 3:
        changed = False
        for i, point in enumerate(kept):
            before, after = kept[i - 1], kept[(i + 1) % len(kept)]
            _, off = _along(before, after, point)
            if off <= SNAP_PT:
                del kept[i]
                changed = True
                break
    return tuple(kept)


def faces(segments: Sequence[Segment]) -> list[Loop]:
    """The bounded faces of the planar graph ``segments`` draw (module doc, step 2), largest
    first. Segments that bound no face (dangling ends, a line joining two loops) are dropped:
    a face's walk passes such an edge in both directions, so it is removed and the walk is
    repeated until every edge separates two faces."""
    if not segments:
        return []
    split = _node(list(segments))
    ids = _snap(point for segment in split for point in segment)
    members: dict[int, list[Point]] = defaultdict(list)
    for point, corner in ids.items():
        members[corner].append(point)
    at = {
        corner: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        for corner, pts in members.items()
    }
    edges = {(min(ids[a], ids[b]), max(ids[a], ids[b])) for a, b in split if ids[a] != ids[b]}
    while edges:
        cycles = _cycles(edges, at)
        both_ways: set[tuple[int, int]] = set()
        for cycle in cycles:
            walked = set(zip(cycle, (*cycle[1:], cycle[0]), strict=True))
            both_ways |= {(min(a, b), max(a, b)) for a, b in walked if (b, a) in walked}
        if not both_ways:
            break
        edges -= both_ways
    if not edges:
        return []
    loops: list[Loop] = []
    for cycle in cycles:
        points = [at[corner] for corner in cycle]
        # Bounded faces are walked one way round and each component's outside the other.
        if len(points) >= 3 and _signed_area(points) < 0:
            loop = Loop(_corners(points))
            if len(loop.corners) >= 3 and loop.area > SNAP_PT:
                loops.append(loop)
    return sorted(loops, key=lambda loop: -loop.area)


def page_loops(drawing: PageDrawing) -> tuple[list[Loop], dict[str, Any]]:
    """The loops of a page, one planar graph per stroke style, and what was set aside."""
    by_style: dict[tuple[float, str], list[Segment]] = defaultdict(list)
    dashed = 0
    for stroke in drawing.strokes:
        if stroke.dashed:
            dashed += 1
            continue
        by_style[stroke.style].append((stroke.a, stroke.b))
    loops: list[Loop] = []
    styles: list[dict[str, Any]] = []
    for (width, colour), segments in sorted(by_style.items(), key=lambda kv: -kv[0][0]):
        entry: dict[str, Any] = {"width_pt": width, "colour": colour, "strokes": len(segments)}
        if len(segments) > MAX_STROKES:
            entry["skipped"] = f"more than {MAX_STROKES} strokes"
        else:
            found = faces(segments)
            entry["loops"] = len(found)
            loops.extend(found)
        styles.append(entry)
    return loops, {"dashed_strokes": dashed, "styles": styles}


# --- belief: loops against the page's table ------------------------------------------------


@dataclass(frozen=True, slots=True)
class PageMatch:
    """A page's accepted scale and which loop reproduces which table row."""

    page: int
    scale: float  # points per foot
    pairs: tuple[tuple[SketchArea, Loop], ...]
    grid_ft: Decimal | None  # the precision the page's calculation prints; None: too fine
    # The scale's relative uncertainty: the printed areas it was fit to are rounded.
    slack: float = 0.0

    def loop_of(self, row: SketchArea) -> Loop | None:
        for candidate, loop in self.pairs:
            if candidate is row:
                return loop
        return None


def _noise_ft(scale: float) -> float:
    """The coordinate noise of a length in feet at ``scale`` (points per foot)."""
    return min(WALL_NOISE_PT / scale, WALL_NOISE_CAP_FT)


def _rounding(area: Decimal) -> float:
    """Half a unit of the last digit an area is printed to."""
    exponent = area.as_tuple().exponent
    return 0.5 * 10 ** min(int(exponent), 0) if isinstance(exponent, int) else 0.5


def printed_grid(rows: Sequence[SketchArea]) -> Decimal | None:
    """The coarsest of :data:`GRIDS_FT` every dimension in the rows' calculations lands on
    (a triangle's 0.5 is a factor too, which only makes the grid finer); None when one is
    printed finer than a tenth of a foot."""
    factors = [f for row in rows for rect in row.rectangles for f in rect.factors]
    if not factors:
        return None
    for grid in GRIDS_FT:
        if all(f % grid == 0 for f in factors):
            return grid
    return None


def on_printed_precision(loop: Loop, scale: float, grid: Decimal | None, slack: float) -> bool:
    """Whether every wall of ``loop`` lands on ``grid`` at ``scale``; ``slack`` is the
    scale's relative uncertainty (from the rounding of the printed areas it was fit to)."""
    if grid is None:
        return False
    step = float(grid)
    for a, b in loop.walls:
        length = math.dist(a, b) / scale
        nearest = round(length / step) * step
        if nearest <= 0 or abs(length - nearest) > _noise_ft(scale) + length * slack:
            return False
    return True


def _spans(values: Iterable[float], scale: float) -> list[float]:
    """Every distance (feet) between two of the corners' coordinates along one axis."""
    distinct: list[float] = []
    for value in sorted(values):
        if not distinct or value - distinct[-1] > SNAP_PT:
            distinct.append(value)
    return [(b - a) / scale for i, a in enumerate(distinct) for b in distinct[i + 1 :]]


def shows_dimensions(loop: Loop, row: SketchArea, scale: float, slack: float = 0.0) -> bool:
    """Whether ``loop`` is the shape ``row``'s calculation describes: every rectangle the
    row prints (``16 x 10``) spans the loop's corners, one side across and one side up, at
    ``scale``. The table sums an area from rectangles cut through the outline's corners, so
    each printed side is a distance between two corners along an axis. This is what makes a
    loop more than a coincidence of area: the area fixes the scale, and the printed
    dimensions then have to be in the drawing. A rectangle the text lost a factor of, or a
    drawing turned off the page's axes, fails."""
    across = _spans((x for x, _ in loop.corners), scale)
    up = _spans((y for _, y in loop.corners), scale)

    def has(spans: Sequence[float], side: float) -> bool:
        return any(abs(span - side) <= _noise_ft(scale) + side * slack for span in spans)

    if not row.rectangles:
        return False
    for rect in row.rectangles:
        if not rect.product_ok:
            return False
        sides = [float(f) for f in rect.factors]
        if len(sides) == 3 and 0.5 in sides:
            sides.remove(0.5)  # a triangle: half of base times height
        if len(sides) != 2:
            return False
        a, b = sides
        if not ((has(across, a) and has(up, b)) or (has(across, b) and has(up, a))):
            return False
    return True


@dataclass(frozen=True, slots=True)
class _Candidate:
    scale: float
    slack: float
    pairs: tuple[tuple[int, int], ...]  # (row index, loop index)
    contested: tuple[int, ...]  # rows two or more loops reproduce at this scale
    by_area: int  # rows some loop reproduces by area alone at the seed's scale


def _candidate(
    seed_row: int, seed_loop: int, rows: Sequence[SketchArea], loops: Sequence[Loop]
) -> _Candidate | None:
    """The rows the loops reproduce, area and printed dimensions, at the scale the seed
    pair implies."""
    seed_area = float(rows[seed_row].area_sf)
    scale2 = loops[seed_loop].area / seed_area
    seed_slack = _rounding(rows[seed_row].area_sf) / seed_area
    scale = math.sqrt(scale2)
    matched: dict[int, list[int]] = {}
    by_area = 0
    for i, row in enumerate(rows):
        want = float(row.area_sf)
        allow = _rounding(row.area_sf) + want * (AREA_NOISE + seed_slack)
        hits = [j for j, loop in enumerate(loops) if abs(loop.area / scale2 - want) <= allow]
        by_area += bool(hits)
        hits = [j for j in hits if shows_dimensions(loops[j], row, scale, seed_slack / 2)]
        if hits:
            matched[i] = hits
    pairs = sorted((i, hits[0]) for i, hits in matched.items() if len(hits) == 1)
    contested = tuple(sorted(i for i, hits in matched.items() if len(hits) > 1))
    if not pairs or len({j for _, j in pairs}) != len(pairs):
        # Nothing reproduced, or one loop for two rows of the same shape: nothing says which.
        return _Candidate(scale, 0.0, (), contested, by_area)
    # Refit on every pair, then hold each pair to its own rounding.
    printed = sum(float(rows[i].area_sf) for i, _ in pairs)
    scale2 = sum(loops[j].area for _, j in pairs) / printed
    scale = math.sqrt(scale2)
    slack = sum(_rounding(rows[i].area_sf) for i, _ in pairs) / printed / 2
    for i, j in pairs:
        want = float(rows[i].area_sf)
        if abs(loops[j].area / scale2 - want) > _rounding(rows[i].area_sf) + want * AREA_NOISE:
            return _Candidate(scale, 0.0, (), contested, by_area)
        if not shows_dimensions(loops[j], rows[i], scale, slack):
            return _Candidate(scale, 0.0, (), contested, by_area)
    return _Candidate(scale, slack, tuple(pairs), contested, by_area)


def match_page(
    page: int, rows: Sequence[SketchArea], loops: Sequence[Loop]
) -> tuple[PageMatch | None, dict[str, Any]]:
    """Module doc, step 3: ``(the page's accepted match or None, the trace)``. ``rows`` are
    the page's verified table rows."""
    trace: dict[str, Any] = {"rows": len(rows), "loops": len(loops)}
    if not rows:
        return None, {**trace, "status": "no_verified_row"}
    if not loops:
        return None, {**trace, "status": "no_loop"}
    grid = printed_grid(rows)
    trace["printed_precision_ft"] = None if grid is None else str(grid)
    stands: dict[tuple[tuple[int, int], ...], _Candidate] = {}
    area_only: set[int] = set()  # scales (to 0.2 %) where area alone reproduces 2+ rows
    for i in range(len(rows)):
        for j in range(len(loops)):
            candidate = _candidate(i, j, rows, loops)
            if candidate is None:
                continue
            if candidate.by_area >= 2:
                area_only.add(round(math.log(candidate.scale) / SAME_SCALE))
            if candidate.pairs and candidate.pairs not in stands:
                stands[candidate.pairs] = candidate
    # What the weaker check (two rows reproduced by area at one scale) would have accepted.
    trace["scales_reproducing_two_rows_by_area"] = len(area_only)
    if not stands:
        return None, {**trace, "status": "no_match"}
    ranked = sorted(stands.values(), key=lambda candidate: -len(candidate.pairs))
    best = ranked[0]
    rivals = [
        other
        for other in ranked[1:]
        if len(other.pairs) == len(best.pairs) and abs(other.scale / best.scale - 1) > SAME_SCALE
    ]
    if rivals:
        scales = sorted(round(c.scale, 4) for c in (best, *rivals))
        return None, {**trace, "status": "ambiguous_scale", "scales_pt_per_ft": scales}
    match = PageMatch(
        page=page,
        scale=best.scale,
        pairs=tuple((rows[i], loops[j]) for i, j in best.pairs),
        grid_ft=grid,
        slack=best.slack,
    )
    trace.update(
        status="read",
        scale_pt_per_ft=round(best.scale, 4),
        matched=[_loop_json(row, loop, match) for row, loop in match.pairs],
    )
    if best.contested:
        trace["contested_rows"] = [rows[i].label for i in best.contested]
    return match, trace


def _wall_lengths(loop: Loop, match: PageMatch) -> tuple[list[float], bool]:
    """A loop's wall lengths in feet, and whether every one lands on the printed precision
    (each is then that printed length; otherwise the lengths are as drawn)."""
    drawn = [math.dist(a, b) / match.scale for a, b in loop.walls]
    if not on_printed_precision(loop, match.scale, match.grid_ft, match.slack):
        return drawn, False
    step = float(match.grid_ft or 1)
    return [round(v / step) * step for v in drawn], True


def _loop_json(row: SketchArea, loop: Loop, match: PageMatch) -> dict[str, Any]:
    lengths, on_grid = _wall_lengths(loop, match)
    (x0, y0) = (min(p[0] for p in loop.corners), max(p[1] for p in loop.corners))
    return {
        "label": row.label,
        "kind": row.kind,
        "level": row.level,
        "printed_area_sf": str(row.area_sf),
        "area_sf": round(loop.area / match.scale**2, 2),
        "walls": len(lengths),
        "wall_lengths_ft": [round(v, 2) for v in lengths],
        "walls_on_printed_precision": on_grid,
        "perimeter_ft": round(sum(lengths), 2),
        # The outline in feet from its lower-left corner (x to the right, y up).
        "outline_ft": [
            [round((x - x0) / match.scale, 2), round((y0 - y) / match.scale, 2)]
            for x, y in loop.corners
        ],
    }


# --- fields: loops joined into outlines ------------------------------------------------------


def shared_length(a: Loop, b: Loop) -> float:
    """The length (points) along which two loops' walls coincide."""
    total = 0.0
    for p, q in a.walls:
        length = math.dist(p, q)
        for r, s in b.walls:
            (t0, off0), (t1, off1) = _along(p, q, r), _along(p, q, s)
            if off0 > SNAP_PT or off1 > SNAP_PT:
                continue
            low, high = max(min(t0, t1), 0.0), min(max(t0, t1), 1.0)
            if high > low:
                total += (high - low) * length
    return total


def _inside(point: Point, loop: Loop) -> bool:
    """Whether ``point`` is strictly inside ``loop`` (not on a wall)."""
    x, y = point
    odd = False
    for (x0, y0), (x1, y1) in loop.walls:
        t, off = _along((x0, y0), (x1, y1), point)
        if off <= SNAP_PT and -1e-9 <= t <= 1 + 1e-9:
            return False
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            odd = not odd
    return odd


def _probes(loop: Loop) -> list[Point]:
    """Points that tell whether another loop covers part of this one: each wall's midpoint,
    and a point just inside the loop beside each wall."""
    out: list[Point] = []
    for (x0, y0), (x1, y1) in loop.walls:
        mid = ((x0 + x1) / 2, (y0 + y1) / 2)
        out.append(mid)
        length = math.hypot(x1 - x0, y1 - y0)
        nx, ny = -(y1 - y0) / length, (x1 - x0) / length
        for sign in (1, -1):
            beside = (mid[0] + sign * nx * 4 * SNAP_PT, mid[1] + sign * ny * 4 * SNAP_PT)
            if _inside(beside, loop):
                out.append(beside)
                break
    return out


def overlap(a: Loop, b: Loop) -> bool:
    """Whether two loops cover common ground (as opposed to touching along walls)."""
    for s in a.walls:
        for t in b.walls:
            cross = _crossing(s, t)
            # A wall that ends on another (a T) is touching, not crossing.
            if cross is not None and all(math.dist(cross, end) > 4 * SNAP_PT for end in (*s, *t)):
                return True
    return any(_inside(p, b) for p in _probes(a)) or any(_inside(p, a) for p in _probes(b))


@dataclass(frozen=True, slots=True)
class _Outline:
    perimeter_ft: float
    shared_ft: float
    groups: int  # separate masses (loops joined by shared walls are one)
    exact: bool  # every wall on the printed precision


def _join(items: Sequence[tuple[Loop, PageMatch]]) -> _Outline | str:
    """Loops of one page as outlines: the perimeter of their union when they only touch
    along walls; a sentence when two of them cover common ground."""
    match = items[0][1]
    perimeter, exact = 0.0, True
    for loop, _ in items:
        lengths, on_grid = _wall_lengths(loop, match)
        perimeter += sum(lengths)
        exact = exact and on_grid
    shared = 0.0
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (a, _) in enumerate(items):
        for j in range(i + 1, len(items)):
            b = items[j][0]
            if overlap(a, b):
                return "two of the outlines cover the same ground"
            run = shared_length(a, b) / match.scale
            if run > _noise_ft(match.scale):
                if exact and match.grid_ft is not None:
                    step = float(match.grid_ft)
                    run = round(run / step) * step
                shared += run
                parent[find(i)] = find(j)
    groups = len({find(i) for i in range(len(items))})
    return _Outline(perimeter - 2 * shared, shared, groups, exact)


@dataclass
class OutlineReading:
    """What the outline reader found: the fields it supports and the trace for ``raw``."""

    fields: dict[str, ExtractedField] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)


def _field(key: str, value: float, page: int, text: str) -> ExtractedField:
    number = Decimal(str(round(value, 2)))
    shown = format(number.normalize(), "f") if number == number.to_integral_value() else str(number)
    return ExtractedField(
        key=key,
        raw_text=text,
        value_text=shown,
        value_num=number,
        confidence=TABLE_CONFIDENCE,
        source=SOURCE,
        page=page,
    )


def _names(rows: Sequence[SketchArea]) -> str:
    return ", ".join(f"{row.label!r}" for row in rows)


def read_outline(
    reading: SketchReading,
    table_fields: Mapping[str, ExtractedField],
    drawings: Mapping[int, PageDrawing],
) -> OutlineReading:
    """Module doc, steps 2 to 4, over the line work of the reading's pages.

    ``table_fields`` are the fields :func:`basis.extraction.sketch.sketch_fields` wrote from
    the same reading: a perimeter is read only for a footprint the table supports (the
    takeoff uses the one with the other), and the garage's walls belong to the ground outline
    exactly when the table's garage is the takeoff's garage."""
    result = OutlineReading()
    pages: list[dict[str, Any]] = []
    matches: dict[int, PageMatch] = {}
    for number in reading.pages:
        drawing = drawings.get(number)
        entry: dict[str, Any] = {"page": number}
        pages.append(entry)
        if drawing is None:
            entry["status"] = "page_not_read"
            continue
        entry["images"] = drawing.images
        if drawing.heading_top is None:
            # The table was read from OCR lines; the page itself is a picture of the sketch.
            entry["status"] = "raster" if drawing.images else "no_heading_in_text_layer"
            continue
        entry["strokes"] = len(drawing.strokes)
        entry["curved_paths"] = drawing.curved_paths
        if not any(not stroke.dashed for stroke in drawing.strokes):
            entry["status"] = "raster" if drawing.images else "no_strokes"
            continue
        loops, detail = page_loops(drawing)
        entry.update(detail)
        rows = [row for row in reading.areas if row.page == number and row.verified]
        match, trace = match_page(number, rows, loops)
        entry.update(trace)
        if match is not None:
            matches[number] = match
    result.trace["pages"] = pages
    notes: list[str] = []
    result.trace["notes"] = notes

    def loops_of(rows: Sequence[SketchArea], what: str) -> list[tuple[Loop, PageMatch]] | None:
        found: list[tuple[Loop, PageMatch]] = []
        for row in rows:
            match = matches.get(row.page)
            loop = None if match is None else match.loop_of(row)
            if match is None or loop is None:
                status = next((p.get("status") for p in pages if p["page"] == row.page), None)
                notes.append(
                    f"{what}: no outline on page {row.page} reproduces {row.label!r} "
                    f"({row.area_sf} Sq ft); the page's line work: {status}"
                )
                return None
            found.append((loop, match))
        return found

    # --- the ground story ---
    living = reading.of("living")
    ground_rows = [row for row in living if row.level == 1]
    garage_rows = list(reading.of("garage"))
    if GROUND_FLOOR_AREA not in table_fields:
        notes.append("ground perimeter: the table gives no ground floor area, so none is read")
        result.trace["status"] = "not_read"
        return result
    if garage_rows and GARAGE_AREA not in table_fields:
        notes.append(
            "ground perimeter: the table's garage could not be verified, so the ground "
            "story's outline is not read"
        )
        result.trace["status"] = "not_read"
        return result
    story = [*ground_rows, *garage_rows]
    if len({row.page for row in story}) > 1:
        notes.append(
            "ground perimeter: the ground story is drawn on more than one page "
            f"({_names(story)}), so its outline as one is not shown"
        )
        result.trace["status"] = "not_read"
        return result
    ground_items = loops_of(story, "ground perimeter")
    if ground_items is None:
        result.trace["status"] = "not_read"
        return result
    joined = _join(ground_items)
    if isinstance(joined, str):
        notes.append(f"ground perimeter: {joined} ({_names(story)})")
        result.trace["status"] = "not_read"
        return result
    if joined.groups > 1:
        notes.append(
            "ground perimeter: the outlines do not share a wall "
            f"({_names(story)}), so whether they are attached is not shown"
        )
        result.trace["status"] = "not_read"
        return result
    page = story[0].page
    match = matches[page]
    result.fields[GROUND_PERIMETER] = _field(
        GROUND_PERIMETER,
        joined.perimeter_ft,
        page,
        f"outline of {_names(story)} on page {page}, "
        f"{round(joined.shared_ft, 2)} ft of shared wall counted once "
        f"({round(match.scale, 4)} points per foot)",
    )
    result.trace["ground"] = {
        "page": page,
        "rows": [row.label for row in story],
        "perimeter_ft": round(joined.perimeter_ft, 2),
        "shared_wall_ft": round(joined.shared_ft, 2),
        "walls_on_printed_precision": joined.exact,
    }
    result.trace["status"] = "read"

    # --- the floors above ---
    upper_rows = [row for row in living if row.level is not None and row.level > 1]
    if not upper_rows:
        return result
    upper_items = loops_of(upper_rows, "upper perimeter")
    if upper_items is None:
        return result
    total, separate, blocks = 0.0, False, []
    for level in sorted({row.level for row in upper_rows if row.level is not None}):
        on_level = [
            (row, item)
            for row, item in zip(upper_rows, upper_items, strict=True)
            if row.level == level
        ]
        by_page: dict[int, list[tuple[Loop, PageMatch]]] = defaultdict(list)
        for row, item in on_level:
            by_page[row.page].append(item)
        separate = separate or len(by_page) > 1
        for number, items in sorted(by_page.items()):
            outline = _join(items)
            if isinstance(outline, str):
                notes.append(f"upper perimeter: {outline} (level {level}, page {number})")
                return result
            total += outline.perimeter_ft
            blocks.append(
                {
                    "level": level,
                    "page": number,
                    "perimeter_ft": round(outline.perimeter_ft, 2),
                    "shared_wall_ft": round(outline.shared_ft, 2),
                    "masses": outline.groups,
                }
            )
    first = min(row.page for row in upper_rows)
    text = f"outline of {_names(upper_rows)} on page" + (
        f"s {', '.join(str(b['page']) for b in blocks)}, summed as separate masses "
        "(a wall they share would be counted twice)"
        if separate
        else f" {first}"
    )
    result.fields[UPPER_PERIMETER] = _field(UPPER_PERIMETER, total, first, text)
    result.trace["upper"] = {
        "perimeter_ft": round(total, 2),
        "separate_pages": separate,
        "blocks": blocks,
    }
    if separate:
        notes.append(
            "upper perimeter: one floor is drawn on more than one page; the outlines are "
            "summed as separate masses, and a wall they share would be counted twice"
        )
    return result


__all__ = [
    "FIELD_KEYS",
    "GROUND_PERIMETER",
    "SOURCE",
    "UPPER_PERIMETER",
    "Loop",
    "OutlineReading",
    "PageDrawing",
    "PageMatch",
    "Stroke",
    "faces",
    "match_page",
    "overlap",
    "page_drawings",
    "page_loops",
    "printed_grid",
    "read_outline",
    "shared_length",
    "shows_dimensions",
]
