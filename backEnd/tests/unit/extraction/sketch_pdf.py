"""Fabricated building-sketch PDFs for the outline reader's tests (not a test).

Each page is drawn the way a sketching tool prints one into a born-digital report: the
drawing as vector strokes above an "Area Calculations Summary" table set as text. Walls are
given in feet and drawn at the page's scale; every number is invented. The PDFs are built in
memory with reportlab, so no file is stored.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

Feet = tuple[float, float]
PAGE = (612.0, 792.0)
HEADING_Y = 300.0  # the table's heading, in points from the bottom of the page
WALL = 1.5  # the wall stroke's width in points
THIN = 0.5


def rectangle(x: float, y: float, width: float, depth: float) -> list[Feet]:
    return [(x, y), (x + width, y), (x + width, y + depth), (x, y + depth)]


@dataclass
class SketchPage:
    """One sketch page: drawing commands in feet, then the table's lines."""

    scale: float = 6.0  # points per foot
    origin: tuple[float, float] = (120.0, 420.0)  # where (0, 0) ft lands, from bottom-left
    heading: str = "Area Calculations Summary"
    ops: list[tuple[Any, ...]] = field(default_factory=list)
    table: list[tuple[str, str, str]] = field(default_factory=list)

    def at(self, point: Feet) -> tuple[float, float]:
        return (self.origin[0] + point[0] * self.scale, self.origin[1] + point[1] * self.scale)

    def line(
        self, a: Feet, b: Feet, *, width: float = WALL, dash: Sequence[float] = ()
    ) -> SketchPage:
        self.ops.append(("line", a, b, width, tuple(dash)))
        return self

    def walls(
        self,
        points: Sequence[Feet],
        *,
        width: float = WALL,
        dash: Sequence[float] = (),
        order: Sequence[int] | None = None,
        closed: bool = True,
    ) -> SketchPage:
        """A wall outline as separate line segments (``order``: the order they are drawn in)."""
        ring = list(points) + ([points[0]] if closed else [])
        segments = list(zip(ring, ring[1:], strict=False))
        for index in order if order is not None else range(len(segments)):
            self.line(*segments[index], width=width, dash=dash)
        return self

    def path(
        self, points: Sequence[Feet], *, width: float = WALL, fill: bool = False
    ) -> SketchPage:
        """A closed outline drawn as one path (a polyline, not separate lines)."""
        self.ops.append(("path", tuple(points), width, fill))
        return self

    def box(
        self, x: float, y: float, width: float, depth: float, *, stroke: float = WALL
    ) -> SketchPage:
        """A rectangle drawn with the PDF rectangle operator."""
        self.ops.append(("rect", (x, y), width, depth, stroke))
        return self

    def arc_wall(self, a: Feet, control: Feet, b: Feet, *, width: float = WALL) -> SketchPage:
        """A curved wall (a Bezier from ``a`` to ``b``)."""
        self.ops.append(("curve", a, control, b, width))
        return self

    def chopped(self, a: Feet, b: Feet, *, piece: float = 0.8, gap: float = 0.5) -> SketchPage:
        """A dashed line drawn the way one tool prints it: short filled, stroked bars."""
        self.ops.append(("chopped", a, b, piece, gap))
        return self

    def picture(self, image: Any) -> SketchPage:
        """A raster image of a drawing, placed where the drawing goes."""
        self.ops.append(("image", image))
        return self

    def text(self, value: str, at: Feet) -> SketchPage:
        self.ops.append(("text", value, at))
        return self

    def section(self, name: str) -> SketchPage:
        self.table.append((name, "", "Calculation Details" if not self.table else ""))
        return self

    def row(self, label: str, area: str, *calculation: str) -> SketchPage:
        first, *rest = calculation or ("",)
        self.table.append((label, area, first))
        self.table.extend(("", "", line) for line in rest)
        return self


def _draw(canvas: Canvas, page: SketchPage) -> None:
    for op in page.ops:
        kind = op[0]
        canvas.setDash()
        if kind == "line":
            _, a, b, width, dash = op
            canvas.setLineWidth(width)
            if dash:
                canvas.setDash(list(dash))
            canvas.line(*page.at(a), *page.at(b))
        elif kind == "path":
            _, points, width, fill = op
            canvas.setLineWidth(width)
            path = canvas.beginPath()
            path.moveTo(*page.at(points[0]))
            for point in points[1:]:
                path.lineTo(*page.at(point))
            path.close()
            canvas.setFillGray(0.9)
            canvas.drawPath(path, stroke=1, fill=1 if fill else 0)
            canvas.setFillGray(0)
        elif kind == "rect":
            _, corner, width, depth, stroke = op
            canvas.setLineWidth(stroke)
            x, y = page.at(corner)
            canvas.rect(x, y, width * page.scale, depth * page.scale, stroke=1, fill=0)
        elif kind == "curve":
            _, a, control, b, width = op
            canvas.setLineWidth(width)
            path = canvas.beginPath()
            path.moveTo(*page.at(a))
            path.curveTo(*page.at(control), *page.at(control), *page.at(b))
            canvas.drawPath(path, stroke=1, fill=0)
        elif kind == "chopped":
            _, a, b, piece, gap = op
            (x0, y0), (x1, y1) = page.at(a), page.at(b)
            length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            ux, uy = (x1 - x0) / length, (y1 - y0) / length
            step, bar, half = (piece + gap) * page.scale, piece * page.scale, 0.7
            canvas.setLineWidth(THIN)
            travelled = 0.0
            while travelled + bar <= length:
                sx, sy = x0 + ux * travelled, y0 + uy * travelled
                ex, ey = sx + ux * bar, sy + uy * bar
                path = canvas.beginPath()
                path.moveTo(sx - uy * half, sy + ux * half)
                path.lineTo(ex - uy * half, ey + ux * half)
                path.lineTo(ex + uy * half, ey - ux * half)
                path.lineTo(sx + uy * half, sy - ux * half)
                path.close()
                canvas.drawPath(path, stroke=1, fill=1)
                travelled += step
        elif kind == "image":
            canvas.drawImage(ImageReader(op[1]), 90, HEADING_Y + 40, width=420, height=380)
        elif kind == "text":
            canvas.setFont("Helvetica", 7)
            canvas.drawString(*page.at(op[2]), op[1])
    canvas.setDash()
    if not page.table and not page.heading:
        return
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(250, HEADING_Y, page.heading)
    canvas.setFont("Helvetica", 8)
    y = HEADING_Y - 14
    for label, area, calculation in page.table:
        if label:
            canvas.drawString(50, y, label)
        if area:
            canvas.drawString(260, y, area)
        if calculation:
            canvas.drawString(440, y, calculation)
        y -= 11


def build(*pages: SketchPage, before: int = 0) -> bytes:
    """The PDF of ``pages``, after ``before`` pages of plain text (the form itself)."""
    out = io.BytesIO()
    canvas = Canvas(out, pagesize=PAGE, pageCompression=0)
    for number in range(before):
        canvas.setFont("Helvetica", 9)
        canvas.drawString(72, 700, f"Uniform Residential Appraisal Report, page {number + 1}")
        canvas.showPage()
    for page in pages:
        _draw(canvas, page)
        canvas.showPage()
    canvas.save()
    return out.getvalue()


# --- the houses the tests share (all dimensions invented) --------------------------------------

# An L-shaped first floor, 1,008 SF, perimeter 140 ft.
L_HOUSE: list[Feet] = [(0, 0), (40, 0), (40, 18), (24, 18), (24, 30), (0, 30)]
# A garage on its right side, 440 SF, perimeter 84 ft; 18 ft of its left wall is the house's.
GARAGE: list[Feet] = rectangle(40, 0, 20, 22)
L_ROWS = (("First Floor", "1008 Sq ft", "40 x 18 = 720", "24 x 12 = 288"),)
GARAGE_ROW = ("2 Car Attached", "440 Sq ft", "20 x 22 = 440")
GROUND_PERIMETER = 140 + 84 - 2 * 18


def l_house_page(*, garage: bool = True, order: Sequence[int] | None = None) -> SketchPage:
    """The L-shaped house with its attached garage, the table under it."""
    page = SketchPage().walls(L_HOUSE, order=order)
    page.section("Living Area").row(*L_ROWS[0])
    page.row("Total Living Area (Rounded):", "1008 Sq ft")
    if garage:
        page.walls(GARAGE)
        page.section("Non-living Area").row(*GARAGE_ROW)
    return page
