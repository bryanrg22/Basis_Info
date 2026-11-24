"""The viewer's camera, as its page address carries it, and the scale of a top-down view.

The address of a view is ``https://earth.google.com/web/@<lat>,<lon>,<alt>a,<range>d,<fov>y,
<heading>h,<tilt>t,<roll>r``: the point looked at (latitude, longitude, its altitude in metres
above sea level), the distance from the camera to that point in metres, the vertical field of
view in degrees, the compass heading, the tilt from straight down, the roll. EVAL_REPORT 98
checked two of those readings on the probe's screenshots, without a page load:

* at ``250.1a,214d,35y,0t`` the status bar read ``Camera: 464 m``, which is 250 + 214: ``d`` is
  the camera's distance from the point (straight up when the tilt is 0);
* the page's own 20 m and 80 m scale bars measured 130 and 190 pixels at ``214d`` and ``589d``,
  0.1538 and 0.4211 m per pixel, against 0.1499 and 0.4128 from :func:`pixel_size_m` with
  ``y`` as the *vertical* field of view over the 900-pixel height (the horizontal reading
  would give 0.094 and 0.258). The formula reads 2.0 to 2.6 % finer than the page's own bar.
  The bar is itself an estimate; neither has been checked against a known length on the
  ground, which is why a view's pixel size is a measurement aid and every traced quantity
  stays the engineer's (EVAL_REPORT 78).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote_plus

ORIGIN: Final = "https://earth.google.com"
_CAMERA: Final = re.compile(
    r"/@(?P<lat>-?\d+(?:\.\d+)?),(?P<lon>-?\d+(?:\.\d+)?),(?P<alt>-?\d+(?:\.\d+)?)a,"
    r"(?P<range>\d+(?:\.\d+)?)d,(?P<fov>\d+(?:\.\d+)?)y,(?P<heading>-?\d+(?:\.\d+)?)h,"
    r"(?P<tilt>-?\d+(?:\.\d+)?)t,(?P<roll>-?\d+(?:\.\d+)?)r"
)


@dataclass(frozen=True, slots=True)
class Camera:
    lat: float
    lon: float
    alt_m: float
    range_m: float
    fov_deg: float = 35.0
    heading_deg: float = 0.0
    tilt_deg: float = 0.0
    roll_deg: float = 0.0

    def __post_init__(self) -> None:
        if not -90 <= self.lat <= 90 or not -180 <= self.lon <= 180:
            raise ValueError(f"not a point on the globe: {self.lat}, {self.lon}")
        if self.range_m <= 0:
            raise ValueError("range must be positive")
        if not 0 < self.fov_deg < 180:
            raise ValueError("field of view must be between 0 and 180 degrees")
        if not 0 <= self.tilt_deg < 90:
            raise ValueError("tilt must be from 0 (straight down) to below 90 degrees")

    def address(self) -> str:
        """The page address that puts the camera here."""
        return (
            f"{ORIGIN}/web/@{self.lat:.7f},{self.lon:.7f},{self.alt_m:.2f}a,"
            f"{self.range_m:.2f}d,{self.fov_deg:g}y,{self.heading_deg:g}h,"
            f"{self.tilt_deg:g}t,{self.roll_deg:g}r"
        )

    def looking(self, *, range_m: float, heading_deg: float = 0.0, tilt_deg: float = 0.0) -> Camera:
        """The same point seen from another distance, heading or tilt."""
        return Camera(
            self.lat, self.lon, self.alt_m, range_m, self.fov_deg, heading_deg % 360, tilt_deg
        )


# A search that finds nothing can still leave a camera in the page's address, at latitude 0
# and a longitude near 0 (the live run of 2026-11-24: 0.0000, -0.2049, altitude 0): the
# failed-geocode spot mapping tools call Null Island. This box around it is open ocean (the
# nearest land, Sao Tome, is 6.5 degrees east), so no property is ever in it.
NO_PLACE_BOX: Final = (0.5, 1.0)  # |lat| and |lon| in degrees


def found_nothing(camera: Camera) -> bool:
    """Whether a searched camera is the no-place spot (:data:`NO_PLACE_BOX`), not a place."""
    lat_limit, lon_limit = NO_PLACE_BOX
    return abs(camera.lat) <= lat_limit and abs(camera.lon) <= lon_limit


def parse(address: str) -> Camera | None:
    """The camera an address names, None when it names none (a search that found no place
    leaves the address without ``/@...``, or with the no-place camera: :func:`found_nothing`)."""
    found = _CAMERA.search(address)
    if found is None:
        return None
    try:
        return Camera(
            lat=float(found["lat"]),
            lon=float(found["lon"]),
            alt_m=float(found["alt"]),
            range_m=float(found["range"]),
            fov_deg=float(found["fov"]),
            heading_deg=float(found["heading"]),
            tilt_deg=float(found["tilt"]),
            roll_deg=float(found["roll"]),
        )
    except ValueError:
        return None


def search_address(query: str) -> str:
    """The page address of a search for ``query`` (an address or a place name)."""
    cleaned = " ".join(query.split())
    if not cleaned:
        raise ValueError("empty search")
    return f"{ORIGIN}/web/search/{quote_plus(cleaned, safe='')}"


def pixel_size_m(range_m: float, fov_deg: float, height_px: int) -> float:
    """Metres per pixel at the centre of a top-down view: the vertical field of view spans
    ``2 * range * tan(fov / 2)`` metres over ``height_px`` pixels (EVAL_REPORT 98)."""
    if range_m <= 0 or height_px <= 0 or not 0 < fov_deg < 180:
        raise ValueError("range, field of view and height must be positive")
    return 2 * range_m * math.tan(math.radians(fov_deg) / 2) / height_px
