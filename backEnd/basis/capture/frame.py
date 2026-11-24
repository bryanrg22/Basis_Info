"""Views free of the viewer's interface, and where a pixel of a stored view is on the ground
(EVAL_REPORT 99).

The viewer draws its menu bar, history panel, floating buttons and status bar over the
imagery. It offers no way to hide them all: "Hide the menu bar" hides only the menu bar, and
the floating search box, the history date, the corner buttons and the status bar stay
(EVAL_REPORT 99, probe E1). So a clean view is a crop:

* the viewport is taller than the view by ``margin`` pixels above and below
  (:class:`Layout`, ``1440 x (900 + 2 * 170)`` by default), and the stored view is the band
  between the margins. The interface lies inside the margins: the history panel ends 156 px
  from the top, the corner buttons and the status bar start 108 px from the bottom
  (probe E1);
* the crop is symmetric, so the stored view's centre is the viewport's centre, which is the
  point the camera looks at: hovering the viewport's centre read the camera's own point in
  the status bar at both 900 and 1130 px tall (probe E1), so ``lat``, ``lon`` do not move;
* the vertical field of view spans the whole viewport, so the metres per pixel are
  ``2 * range * tan(fov / 2) / viewport height``; the plan lengthens every range by
  ``viewport height / view height`` (:meth:`Layout.range_for`), so a stored view covers the
  same ground at the same metres per pixel as the uncropped 900-pixel views of EVAL_REPORT
  98.

The status bar, which carries the imagery date, is cut off with the margins: the date is
read from the full screenshot first (:mod:`basis.capture.imagery_date`), and the strip is
kept next to the view as its evidence.

:func:`ground_offset_m` and :func:`lonlat_at` turn a pixel of a stored top-down view into
metres from its centre and into a point (heading 0, the local flat approximation the
measurement code uses at this scale). CPU work on images (:func:`crop_view`) runs on a
thread.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Final

EARTH_RADIUS_M: Final = 6_378_137.0
VIEW_WIDTH: Final = 1440
VIEW_HEIGHT: Final = 900
# The interface's reach from each edge of the viewport, measured on probe E1's screenshots:
# the menu bar and the history panel end at 156 px from the top (a hover tooltip under the
# history buttons reaches 165), the corner buttons and the status bar begin 108 px from the
# bottom. 170 clears both with room for a tooltip.
DEFAULT_MARGIN_PX: Final = 170
# The status bar's left part, where the imagery date is, measured from the viewport's bottom.
STATUS_STRIP_HEIGHT: Final = 28
STATUS_STRIP_X: Final = (230, 560)


@dataclass(frozen=True, slots=True)
class Layout:
    """The viewport and the band of it that is stored. ``margin`` 0 is the uncropped
    viewport of EVAL_REPORT 98 (the interface is then in the stored view)."""

    width: int = VIEW_WIDTH
    view_height: int = VIEW_HEIGHT
    margin: int = 0

    def __post_init__(self) -> None:
        if self.width <= 0 or self.view_height <= 0 or self.margin < 0:
            raise ValueError("the view must have a size and the margin must not be negative")

    @property
    def viewport_height(self) -> int:
        return self.view_height + 2 * self.margin

    @property
    def viewport(self) -> dict[str, int]:
        return {"width": self.width, "height": self.viewport_height}

    @property
    def crop_box(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) of the stored view inside the screenshot."""
        return (0, self.margin, self.width, self.margin + self.view_height)

    def range_for(self, range_m: float) -> float:
        """The camera range that gives a stored view the ground pixel size an uncropped
        ``view_height`` viewport has at ``range_m``."""
        return range_m * self.viewport_height / self.view_height

    def pixel_size_m(self, range_m: float, fov_deg: float) -> float:
        """Metres per pixel at the centre: the field of view spans the whole viewport."""
        if range_m <= 0 or not 0 < fov_deg < 180:
            raise ValueError("range and field of view must be positive")
        return 2 * range_m * math.tan(math.radians(fov_deg) / 2) / self.viewport_height


def ground_offset_m(
    x: float, y: float, *, width: int, height: int, pixel_size_m: float
) -> tuple[float, float]:
    """Metres east and north of a top-down view's centre at pixel ``(x, y)`` (heading 0)."""
    return (x - width / 2) * pixel_size_m, (height / 2 - y) * pixel_size_m


def lonlat_at(
    lat: float,
    lon: float,
    x: float,
    y: float,
    *,
    width: int,
    height: int,
    pixel_size_m: float,
) -> tuple[float, float]:
    """The (lat, lon) under pixel ``(x, y)`` of a top-down view centred on ``lat``, ``lon``
    (heading 0; the flat local approximation, under a millimetre over a 100 m view)."""
    east, north = ground_offset_m(x, y, width=width, height=height, pixel_size_m=pixel_size_m)
    dlat = math.degrees(north / EARTH_RADIUS_M)
    dlon = math.degrees(east / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


@dataclass(frozen=True, slots=True)
class Cropped:
    view: bytes
    status_strip: bytes
    width: int
    height: int


def crop_view(png: bytes, layout: Layout) -> Cropped:
    """The stored view (the band between the margins) and the status-bar strip of the full
    screenshot, as PNGs. With ``margin`` 0 the view is the screenshot itself. CPU work: call
    it on a thread."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        image.load()
        full_w, full_h = image.size
        left, right = STATUS_STRIP_X
        strip = image.crop(
            (
                min(left, full_w - 1),
                max(0, full_h - STATUS_STRIP_HEIGHT),
                min(right, full_w),
                full_h,
            )
        )
        strip_buffer = io.BytesIO()
        strip.save(strip_buffer, "PNG")
        if layout.margin == 0:
            return Cropped(png, strip_buffer.getvalue(), full_w, full_h)
        if full_h < layout.viewport_height or full_w < layout.width:
            raise ValueError(
                f"screenshot {full_w}x{full_h} is smaller than the layout's viewport "
                f"{layout.width}x{layout.viewport_height}"
            )
        view = image.crop(layout.crop_box)
        buffer = io.BytesIO()
        view.save(buffer, "PNG")
        return Cropped(buffer.getvalue(), strip_buffer.getvalue(), view.width, view.height)
