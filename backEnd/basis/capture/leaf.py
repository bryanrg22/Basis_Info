"""Leaf-off, leaf-on or unknown for a dated top-down view, and which view to measure on
(EVAL_REPORT 99).

Trees in leaf hide drives, walks and patios; a leaf-off picture shows them. Three ways were
compared on the probe property's dated views against the hand check (one property: a small
key, EVAL_REPORT 99 says so):

* :func:`by_month`, **shipped**: the imagery month against the property's latitude. The
  windows, at 44° N (central Wisconsin):

  - leaf-off from 10 November to 25 April. The spring end is the first-leaf date: the USA
    National Phenology Network's 30-year (1991-2020) First Leaf Index puts it in late April
    or early May in Wisconsin; aerial programs fly "leaf-off" in that spring gap (the
    Wisconsin Regional Orthoimagery Consortium flies every cycle in spring, leaf-off).
  - leaf-on from 1 June to 15 October: leaves are out about a month after first leaf, and
    still on through the region's peak autumn colour in early to mid October.
  - unknown between them (leaves coming or going), and for a 31 December date: the viewer
    dates imagery known only to the year as 31 December (the probe's 2005-12-31 picture is
    in full leaf, its 1985-12-31 one is a blur), so its month says nothing.

  North or south of 44° the spring dates move later or earlier by 2.65 days a degree and the
  autumn dates earlier or later by 2.0 days a degree: the deciduous green-up and green-down
  slopes Richardson et al. (2019, "Testing Hopkins' Bioclimatic Law with PhenoCam data",
  Applied Plant Sciences) measured across North American sites (Hopkins' own rule was 4 days
  a degree). The rule is for the northern United States, 37 to 49.5° N; elsewhere it answers
  unknown. The autumn edges are the region's general timing, not a published table, and
  they were written knowing the hand-checked key: its agreement there is not an
  independent test.
* :func:`by_picture`: the share of green vegetation pixels, ``ExG - ExR > 0`` on
  chromatic coordinates (Meyer and Neto 2008), over pixels that are neither too dark nor
  grey; leaf-on from 0.50, leaf-off up to 0.30. Lawns and evergreens are green in every
  season, so it confuses a leaf-off April with a leaf-on October (EVAL_REPORT 99).
* :func:`combined`: a label only where both agree, else unknown.

The share is stored with every dated view (``green_share``) as supporting evidence.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

Leaf = Literal["leaf-off", "leaf-on", "unknown"]
REFERENCE_LAT: Final = 44.0
SPRING_DAYS_PER_DEGREE: Final = 2.65
AUTUMN_DAYS_PER_DEGREE: Final = -2.0
LAT_RANGE: Final = (37.0, 49.5)
# (month, day) at 44° N in a non-leap year.
LEAF_OFF_ENDS: Final = (4, 25)
LEAF_ON_STARTS: Final = (6, 1)
LEAF_ON_ENDS: Final = (10, 15)
LEAF_OFF_STARTS: Final = (11, 10)
GREEN_ON: Final = 0.50
GREEN_OFF: Final = 0.30
DARK_SUM: Final = 60
GREY_SPREAD: Final = 6
GREY_FRAME: Final = 0.5


def _day(month_day: tuple[int, int], shift_days: float, year: int) -> date:
    return date(year, *month_day) + timedelta(days=round(shift_days))


def by_month(on: date | None, lat: float) -> tuple[Leaf, str]:
    """The label and why, from the imagery date and the latitude."""
    if on is None:
        return "unknown", "no imagery date"
    if not LAT_RANGE[0] <= lat <= LAT_RANGE[1]:
        return "unknown", f"the month rule covers {LAT_RANGE[0]:g} to {LAT_RANGE[1]:g}° N"
    if (on.month, on.day) == (12, 31):
        return "unknown", "31 December: the imagery is dated to the year only"
    spring = SPRING_DAYS_PER_DEGREE * (lat - REFERENCE_LAT)
    autumn = AUTUMN_DAYS_PER_DEGREE * (lat - REFERENCE_LAT)
    off_ends = _day(LEAF_OFF_ENDS, spring, on.year)
    on_starts = _day(LEAF_ON_STARTS, spring, on.year)
    on_ends = _day(LEAF_ON_ENDS, autumn, on.year)
    off_starts = _day(LEAF_OFF_STARTS, autumn, on.year)
    if on <= off_ends or on >= off_starts:
        return "leaf-off", f"before {off_ends:%d %b} or from {off_starts:%d %b} at {lat:.1f}° N"
    if on_starts <= on <= on_ends:
        return "leaf-on", f"{on_starts:%d %b} to {on_ends:%d %b} at {lat:.1f}° N"
    return "unknown", "leaves coming or going"


@dataclass(frozen=True, slots=True)
class Greenness:
    share: float | None
    """Green vegetation pixels over usable pixels; None for a grey (black-and-white) frame."""
    grey: float


def greenness(png: bytes) -> Greenness:
    """The green vegetation share of a view. CPU work: call it on a thread."""
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float64)
    total = rgb.sum(axis=2)
    usable = total > DARK_SUM
    r, g, b = (rgb[..., k] / np.maximum(total, 1.0) for k in range(3))
    spread = rgb.max(axis=2) - rgb.min(axis=2)
    grey = float((spread < GREY_SPREAD).mean())
    if grey >= GREY_FRAME or not usable.any():
        return Greenness(None, grey)
    exg = 2 * g - r - b
    exr = 1.4 * r - g
    vegetation = ((exg - exr) > 0) & usable
    return Greenness(float(vegetation.sum() / usable.sum()), grey)


def by_picture(green: Greenness) -> tuple[Leaf, str]:
    if green.share is None:
        return "unknown", "a black-and-white picture"
    if green.share >= GREEN_ON:
        return "leaf-on", f"{green.share:.0%} green"
    if green.share <= GREEN_OFF:
        return "leaf-off", f"{green.share:.0%} green"
    return "unknown", f"{green.share:.0%} green"


def combined(month: Leaf, picture: Leaf) -> Leaf:
    return month if month == picture else "unknown"


@dataclass(frozen=True, slots=True)
class Dated:
    """A stored top-down view as the choice of a view to measure on sees it."""

    key: str
    on: date | None
    leaf: Leaf
    pixel_size_m: float | None


def measure_on(views: Sequence[Dated]) -> str | None:
    """The view to measure paved areas on: the newest leaf-off top-down view, else the
    newest dated top-down view, else any top-down view; None when there is none. The
    engineer may pick another (a date before a renovation, for example)."""
    top = [v for v in views if v.pixel_size_m is not None]
    for pool in (
        [v for v in top if v.leaf == "leaf-off" and v.on is not None],
        [v for v in top if v.on is not None],
    ):
        if pool:
            return max(pool, key=lambda v: (v.on, v.key)).key
    return top[0].key if top else None
