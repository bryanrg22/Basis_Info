"""One georeferenced top-down image from a public imagery source (EVAL_REPORT 101).

Every request asks for a square of ``extent_m`` on the ground around the pin, north up, in Web
Mercator (``bboxSR=3857&imageSR=3857``, ``CRS=EPSG:3857``, or the 3857 tiles themselves), at
the source's native pixel size (no finer: upsampling adds no detail) and never more than
``max_px`` pixels a side. Each protocol:

* ``arcgis-imageserver``: ``exportImage`` with ``format=jpgpng`` (JPEG where opaque);
* ``arcgis-mapserver``: ``export`` with ``format=jpg`` (plus the entry's ``layers``);
* ``wms``: ``GetMap`` 1.1.1 (``SRS``) or 1.3.0 (``CRS``; EPSG:3857's axis order is east, north
  in both) with ``FORMAT=image/jpeg``;
* ``tiles``: the XYZ tiles at the zoom whose pixel is no coarser than the native one (capped at
  the entry's ``max_zoom`` and at ``MAX_TILES``), stitched and cut to the exact box on a thread.

The server's own bytes are stored as they came (no re-encoding) except for stitched tiles. An
answer that is blank (fully transparent, or one flat colour: the standard deviation of its grey
levels under ``BLANK_STD``) means the point is outside the source's coverage, not an error.
"""

from __future__ import annotations

import asyncio
import io
import math
from dataclasses import dataclass
from typing import Final

from basis.errors.codes import E
from basis.geo.geometry import EARTH_RADIUS_M, ViewBox, view_box
from basis.geo.http import PoliteClient, SourceError
from basis.geo.registry import Source

BLANK_STD: Final = 2.0
MAX_TILES: Final = 64
TILE_PX: Final = 256
WORLD_M: Final = 2 * math.pi * EARTH_RADIUS_M


@dataclass(frozen=True, slots=True)
class Image:
    data: bytes
    content_type: str
    box: ViewBox
    blank: bool
    request_url: str
    seconds: float
    attempts: int


@dataclass(frozen=True, slots=True)
class Decoded:
    width: int
    height: int
    blank: bool
    std: float


def inspect(data: bytes) -> Decoded:
    """Size and blankness of an image (CPU: call on a thread)."""
    from PIL import Image as PILImage
    from PIL import ImageStat

    try:
        with PILImage.open(io.BytesIO(data)) as img:
            img.load()
            width, height = img.size
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                alpha = rgba.getchannel("A")
                if alpha.getextrema()[1] == 0:
                    return Decoded(width, height, True, 0.0)
            grey = img.convert("L")
            std = float(ImageStat.Stat(grey).stddev[0])
    except (OSError, ValueError, SyntaxError) as exc:
        raise SourceError(E.GEO_SOURCE_BAD_RESPONSE, f"the image does not decode: {exc}") from exc
    return Decoded(width, height, std < BLANK_STD, std)


def target_pixel(source: Source, extent_m: float, max_px: int) -> float:
    native = source.native_pixel_m or 0.6
    return max(native, extent_m / max_px)


def _params(source: Source, box: ViewBox) -> dict[str, str]:
    bbox = ",".join(f"{v:.3f}" for v in box.bbox)
    extra = {k: v for k, v in source.params.items() if k != "max_zoom"}
    if source.protocol == "arcgis-imageserver":
        return {
            "bbox": bbox,
            "bboxSR": "3857",
            "imageSR": "3857",
            "size": f"{box.width},{box.height}",
            "format": "jpgpng",
            "f": "image",
            **extra,
        }
    if source.protocol == "arcgis-mapserver":
        return {
            "bbox": bbox,
            "bboxSR": "3857",
            "imageSR": "3857",
            "size": f"{box.width},{box.height}",
            "format": "jpg",
            "transparent": "false",
            "f": "image",
            **extra,
        }
    if source.protocol == "wms":
        version = extra.pop("VERSION", "1.3.0")
        srs_key = "SRS" if version == "1.1.1" else "CRS"
        return {
            "SERVICE": "WMS",
            "VERSION": version,
            "REQUEST": "GetMap",
            "STYLES": "",
            srs_key: "EPSG:3857",
            "BBOX": bbox,
            "WIDTH": str(box.width),
            "HEIGHT": str(box.height),
            "FORMAT": "image/jpeg",
            **extra,
        }
    raise ValueError(source.protocol)


def _endpoint(source: Source) -> str:
    base = source.url.rstrip("/")
    if source.protocol == "arcgis-imageserver":
        return f"{base}/exportImage"
    if source.protocol == "arcgis-mapserver":
        return f"{base}/export"
    return base


def tile_zoom(pixel_merc_m: float, max_zoom: int) -> int:
    """The coarsest zoom whose tile pixel is no larger than ``pixel_merc_m``."""
    z = math.ceil(math.log2(WORLD_M / TILE_PX / pixel_merc_m))
    return max(0, min(max_zoom, z))


def tile_range(box: ViewBox, z: int) -> tuple[int, int, int, int]:
    n = 2**z
    size = WORLD_M / n
    x0 = int((box.xmin + WORLD_M / 2) // size)
    x1 = int((box.xmax + WORLD_M / 2) // size)
    y0 = int((WORLD_M / 2 - box.ymax) // size)
    y1 = int((WORLD_M / 2 - box.ymin) // size)
    return x0, y0, x1, y1


def stitch(tiles: dict[tuple[int, int], bytes], z: int, box: ViewBox) -> tuple[bytes, bool]:
    """Paste the tiles, cut the exact box and resize it to the box's pixels (CPU)."""
    from PIL import Image as PILImage

    x0, y0, x1, y1 = tile_range(box, z)
    canvas = PILImage.new("RGB", ((x1 - x0 + 1) * TILE_PX, (y1 - y0 + 1) * TILE_PX))
    for (tx, ty), data in tiles.items():
        with PILImage.open(io.BytesIO(data)) as tile:
            canvas.paste(tile.convert("RGB"), ((tx - x0) * TILE_PX, (ty - y0) * TILE_PX))
    size = WORLD_M / 2**z
    px = size / TILE_PX
    left = (box.xmin + WORLD_M / 2 - x0 * size) / px
    top = (WORLD_M / 2 - box.ymax - y0 * size) / px
    right = left + (box.xmax - box.xmin) / px
    bottom = top + (box.ymax - box.ymin) / px
    cut = canvas.crop((round(left), round(top), round(right), round(bottom))).resize(
        (box.width, box.height), PILImage.Resampling.LANCZOS
    )
    out = io.BytesIO()
    cut.save(out, format="JPEG", quality=90)
    return out.getvalue(), False


async def fetch(
    client: PoliteClient,
    source: Source,
    lat: float,
    lon: float,
    *,
    extent_m: float,
    max_px: int,
) -> Image:
    """The source's view of the square around the point (see the module docstring)."""
    if source.protocol not in ("arcgis-imageserver", "arcgis-mapserver", "wms", "tiles"):
        raise SourceError(E.GEO_SOURCE_BAD_RESPONSE, f"{source.protocol} is not an image protocol")
    pixel = target_pixel(source, extent_m, max_px)
    box = view_box(lat, lon, extent_m=extent_m, pixel_m=pixel, max_px=max_px)
    if source.protocol == "tiles":
        return await _fetch_tiles(client, source, box)
    fetched = await client.get(_endpoint(source), _params(source, box))
    kind = fetched.content_type.split(";")[0].strip().lower()
    if not kind.startswith("image/") and not fetched.content.startswith((b"\xff\xd8", b"\x89PNG")):
        raise SourceError(
            E.GEO_SOURCE_BAD_RESPONSE,
            f"answered {kind or 'no content type'} instead of an image: "
            f"{fetched.content[:160].decode('utf-8', 'replace')}",
            url=fetched.url,
        )
    decoded = await asyncio.to_thread(inspect, fetched.content)
    if not decoded.blank and (decoded.width, decoded.height) != (box.width, box.height):
        # The server sent a different size for the same box: the box is the georeference, so
        # the pixel size follows the pixels it sent.
        box = ViewBox(
            box.xmin,
            box.ymin,
            box.xmax,
            box.ymax,
            decoded.width,
            decoded.height,
            box.lat,
            box.lon,
            extent_m / decoded.width,
        )
    content_type = "image/png" if fetched.content.startswith(b"\x89PNG") else "image/jpeg"
    return Image(
        data=fetched.content,
        content_type=content_type,
        box=box,
        blank=decoded.blank,
        request_url=fetched.url,
        seconds=fetched.seconds,
        attempts=fetched.attempts,
    )


async def _fetch_tiles(client: PoliteClient, source: Source, box: ViewBox) -> Image:
    max_zoom = int(source.params.get("max_zoom", "20"))
    merc_px = (box.xmax - box.xmin) / box.width
    z = tile_zoom(merc_px, max_zoom)
    while True:
        x0, y0, x1, y1 = tile_range(box, z)
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= MAX_TILES or z == 0:
            break
        z -= 1
    # The stitched image keeps no more pixels than the tiles hold.
    tile_px_m = WORLD_M / 2**z / TILE_PX
    if tile_px_m > merc_px * 1.0001:
        side = max(16, math.ceil((box.xmax - box.xmin) / tile_px_m))
        k = (box.xmax - box.xmin) / side
        ground = k * math.cos(math.radians(box.lat))
        box = ViewBox(box.xmin, box.ymin, box.xmax, box.ymax, side, side, box.lat, box.lon, ground)
    tiles: dict[tuple[int, int], bytes] = {}
    seconds = 0.0
    attempts = 0
    blank_tiles = 0
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            url = source.url.replace("{z}", str(z)).replace("{x}", str(tx)).replace("{y}", str(ty))
            try:
                fetched = await client.get(url)
            except SourceError as exc:
                if exc.status == 404:  # a tile the cache does not have: outside coverage
                    blank_tiles += 1
                    continue
                raise
            seconds += fetched.seconds
            attempts += fetched.attempts
            tiles[(tx, ty)] = fetched.content
    if not tiles:
        return Image(b"", "image/jpeg", box, True, source.url, seconds, attempts)
    data, _ = await asyncio.to_thread(stitch, tiles, z, box)
    decoded = await asyncio.to_thread(inspect, data)
    return Image(
        data=data,
        content_type="image/jpeg",
        box=box,
        blank=decoded.blank or blank_tiles > len(tiles),
        request_url=source.url,
        seconds=seconds,
        attempts=attempts,
    )


def _year_in(value: object) -> int | None:
    import re

    if isinstance(value, (int, float)) and 1900 <= int(value) <= 2100:
        return int(value)
    found = [int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d\d)(?!\d)", str(value or ""))]
    return max(found) if found else None


async def year_at(client: PoliteClient, source: Source, lat: float, lon: float) -> int | None:
    """The year of the imagery at the point, from the entry's ``year_query`` (the newest year
    among the features there), or the entry's own ``year``."""
    query = source.year_query
    if query is None:
        return source.year
    answer = (
        await client.get(
            query.url,
            {
                "geometry": f"{lon},{lat}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "where": query.where,
                "outFields": query.field,
                "returnGeometry": "false",
                "f": "json",
            },
        )
    ).json()
    if not isinstance(answer, dict) or "error" in answer:
        raise SourceError(
            E.GEO_SOURCE_BAD_RESPONSE, f"the year query failed: {str(answer)[:160]}", url=query.url
        )
    years = [
        y
        for f in answer.get("features") or []
        if (y := _year_in((f.get("attributes") or {}).get(query.field))) is not None
    ]
    return max(years) if years else source.year
