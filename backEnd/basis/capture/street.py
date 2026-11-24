"""Ground-level pictures of the front of the property, where the street-level imagery covers
it (EVAL_REPORT 99). Off by default (``CAPTURE_STREET_VIEW``): it does not work headless yet.

The path, built and tried:

1. one page load of the documented panorama address
   (``/maps/@?api=1&map_action=pano&viewpoint=<lat>,<lon>``), which opens the panorama
   nearest the point;
2. the page's address then names the panorama: ``@<lat>,<lon>,3a,...`` (where the camera
   stands) and ``!1s<panorama id>`` (:func:`parse_pano`);
3. the camera turns to the house (:func:`heading_deg`, from the panorama to the pin) with a
   move of the page's address, and the picture is taken.

What EVAL_REPORT 99 found, headless, in both of Chromium's headless modes, with WebGL working
(the top-down viewer renders on the same browser): the panorama lookup
(``/maps/photometa/si/v1``) answers HTTP 500, even at the panorama's own position, and the
imagery tiles (``streetviewpixels-pa.clients6.google.com/v1/tile``) answer HTTP 403, so the
panorama is black. The top-down viewer's own street-level mode makes the same lookup and gets
the same 500. The same panorama renders in a regular, headed browser. So the failure is the
service refusing this browser, not its graphics; making it work would mean presenting the
browser as something it is not, which is the maintainer's decision, not taken here.

Each outcome has its own code and no retry: no panorama near the pin
(``E.CAPTURE_STREET_NO_COVERAGE``), or the panorama refused or black
(``E.CAPTURE_STREET_REFUSED``). Neither
fails the capture or opens the breaker: they are not signs of our pace (the 403 comes on the
first request of a fresh browser).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote, urlsplit

from basis.capture.signals import PageRecord

MAPS_ORIGIN: Final = "https://www.google.com"
STREET_FOV_DEG: Final = 75.0
STREET_PITCH_DEG: Final = 90.0  # the address's ``t``: 90 looks at the horizon
TILE_HOSTS: Final = ("streetviewpixels-pa.clients6.google.com",)
LOOKUP_PATH: Final = "/maps/photometa/"
_POSITION: Final = re.compile(
    r"/@(?P<lat>-?\d+(?:\.\d+)?),(?P<lon>-?\d+(?:\.\d+)?),3a,(?:[\d.]+y,)?(?:(?P<h>[\d.]+)h,)?"
)
_PANO: Final = re.compile(r"!1s(?P<id>[A-Za-z0-9_-]{10,})!2e")


def pano_lookup_address(lat: float, lon: float, origin: str = MAPS_ORIGIN) -> str:
    """The documented address that opens the panorama nearest a point."""
    return f"{origin}/maps/@?api=1&map_action=pano&viewpoint={lat:.7f},{lon:.7f}"


def pano_address(
    pano_id: str, lat: float, lon: float, heading: float, origin: str = MAPS_ORIGIN
) -> str:
    """The address of panorama ``pano_id`` (standing at ``lat``, ``lon``) looking along
    ``heading``."""
    return (
        f"{origin}/maps/@{lat:.7f},{lon:.7f},3a,{STREET_FOV_DEG:g}y,{heading:.1f}h,"
        f"{STREET_PITCH_DEG:g}t/data=!3m4!1e1!3m2!1s{pano_id}!2e0"
    )


@dataclass(frozen=True, slots=True)
class Pano:
    pano_id: str
    lat: float
    lon: float


def parse_pano(address: str) -> Pano | None:
    """The panorama an address names, None when it names none (an empty id: no panorama was
    found near the point)."""
    text = unquote(address)
    position = _POSITION.search(text)
    pano = _PANO.search(text)
    if position is None or pano is None:
        return None
    return Pano(pano["id"], float(position["lat"]), float(position["lon"]))


def heading_deg(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> float:
    """The compass heading from one point to another (initial great-circle bearing)."""
    phi1, phi2 = math.radians(from_lat), math.radians(to_lat)
    dlon = math.radians(to_lon - from_lon)
    y = math.sin(dlon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres."""
    radius = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def refused(page: PageRecord) -> str | None:
    """Why the service refused the panorama, from the failed responses; None when it did
    not (a 403 on the imagery tiles, or a 4xx/5xx on the panorama lookup)."""
    for response in page.failed:
        host = (urlsplit(response.url).hostname or "").lower()
        if host in TILE_HOSTS and response.status in (401, 403):
            return f"the panorama's imagery was refused (HTTP {response.status})"
    for response in page.failed:
        if LOOKUP_PATH in urlsplit(response.url).path and response.status >= 400:
            return f"the panorama lookup failed (HTTP {response.status})"
    return None
