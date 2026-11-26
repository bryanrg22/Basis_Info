"""``python -m evals.site_views``: the top-down views of each site-truth parcel (EVAL_REPORT 106).

Per parcel of :mod:`evals.site_truth`, two public views through the gather's own fetch
(:func:`basis.geo.imagery.fetch`: Web Mercator, north up, the source's native pixel, the
server's bytes kept as they came) and :class:`basis.geo.http.PoliteClient` (one request at a time
per host, 2 s apart):

* ``county``: Fairfax County's 2023 orthophoto (3 inch, ``AerialPhotography/
  2023AerialPhotography3inch``), the imagery the county's 2022/2023 planimetric update was drawn
  from. It is not in the production registry (:data:`FAIRFAX_2023`, defined here only): the
  county states no terms on the service.
* ``naip``: the registry's ``us-naip-plus`` (0.6 m), what the production gather fetches in
  Virginia, where no state orthophoto is registered.

The square is the parcel and its items' box plus :data:`MARGIN_M` on each side (at least
:data:`MIN_EXTENT_M`), capped at :data:`MAX_PX` pixels (SAM 3 resizes its input to 1008 px, so
more would be thrown away). Each view's georeference is written beside it (``<view>.json``: the
Mercator box, the size, the pixel size). ``--overlay`` draws every truth outline on each view
(downscaled montages, for the visual check that the outlines and the picture agree).

Usage (from ``backEnd/``)::

    uv run python -m evals.site_views --out "$BASIS_EVAL_DATA_DIR/site_truth/views" [--overlay]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from basis.geo import imagery, registry
from basis.geo.geometry import lonlat_to_pixel
from basis.geo.http import PoliteClient
from basis.geo.registry import Source
from evals import site_truth as st

MARGIN_M: Final = 8.0
MIN_EXTENT_M: Final = 30.0
MAX_PX: Final = 1008
FAIRFAX_2023: Final = Source(
    id="va-fairfax-2023",
    kind="imagery",
    name="Fairfax County 2023 orthophotography (3 inch)",
    states=("VA",),
    bbox=(-77.54, 38.59, -77.04, 39.06),
    protocol="arcgis-imageserver",
    url="https://www.fairfaxcounty.gov/gisimagery/rest/services/AerialPhotography/"
    "2023AerialPhotography3inch/ImageServer",
    native_pixel_m=0.0762,
    year=2023,
    terms_class="no-terms-stated",
    terms_quote="(the service states no terms or copyright)",
    terms_url="https://www.fairfaxcounty.gov/gisimagery/rest/services/AerialPhotography",
    credit="Fairfax County, Virginia",
)


def extent_m(parcel: st.Parcel) -> float:
    """The side of the square holding the parcel and its items with the margin."""
    pts = [*parcel.parcel, *(p for i in parcel.items for p in i.ring)]
    lat0 = parcel.centre[1]
    k = 111_320.0
    dx = [abs(p[0] - parcel.centre[0]) * k * math.cos(math.radians(lat0)) for p in pts]
    dy = [abs(p[1] - parcel.centre[1]) * k for p in pts]
    return max(MIN_EXTENT_M, 2 * (max(dx + dy) + MARGIN_M))


def sources() -> dict[str, Source]:
    naip = next(s for s in registry.load() if s.id == "us-naip-plus")
    return {"county": FAIRFAX_2023, "naip": naip}


async def fetch_parcel(
    client: PoliteClient, parcel: st.Parcel, out: Path, chosen: dict[str, Source]
) -> dict[str, Any]:
    folder = out / parcel.id
    folder.mkdir(parents=True, exist_ok=True)
    lon, lat = parcel.centre
    side = extent_m(parcel)
    got: dict[str, Any] = {}
    for name, source in chosen.items():
        if (folder / f"{name}.json").is_file():
            got[name] = json.loads((folder / f"{name}.json").read_text())
            continue
        image = await imagery.fetch(client, source, lat, lon, extent_m=side, max_px=MAX_PX)
        ext = "png" if image.content_type == "image/png" else "jpg"
        (folder / f"{name}.{ext}").write_bytes(image.data)
        meta = {
            "view": name,
            "source_id": source.id,
            "year": source.year,
            "file": f"{name}.{ext}",
            "bbox": list(image.box.bbox),
            "width": image.box.width,
            "height": image.box.height,
            "pixel_size_m": image.box.pixel_size_m,
            "lat": lat,
            "lon": lon,
            "extent_m": side,
            "blank": image.blank,
            "request_url": image.request_url,
        }
        (folder / f"{name}.json").write_text(json.dumps(meta, indent=1) + "\n")
        got[name] = meta
    return got


def overlay(parcel: st.Parcel, folder: Path, view: str) -> Any:
    """The view with the parcel line (yellow) and each item's outline (by family) drawn."""
    from PIL import Image, ImageDraw

    meta = json.loads((folder / f"{view}.json").read_text())
    with Image.open(folder / meta["file"]) as im:
        img = im.convert("RGB")
    draw = ImageDraw.Draw(img)
    colours = {
        "driveway": (255, 0, 0),
        "patio": (0, 200, 255),
        "deck": (255, 0, 255),
        "walk": (0, 255, 0),
        "pool": (0, 0, 255),
    }

    def px(ring: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
        return [
            lonlat_to_pixel(meta["bbox"], meta["width"], meta["height"], lo, la) for lo, la in ring
        ]

    ring = px(parcel.parcel)
    draw.line([*ring, ring[0]], fill=(255, 255, 0), width=1)
    for item in parcel.items:
        r = px(item.ring)
        draw.line([*r, r[0]], fill=colours[item.family], width=2)
    draw.text((4, 4), f"{parcel.id} {view}", fill=(255, 255, 255))
    return img


def montage(images: Sequence[Any], path: Path, cell: int = 420, cols: int = 3) -> None:
    from PIL import Image

    rows = math.ceil(len(images) / cols)
    sheet = Image.new("RGB", (cols * cell, rows * cell), (0, 0, 0))
    for k, im in enumerate(images):
        thumb = im.copy()
        thumb.thumbnail((cell, cell))
        sheet.paste(thumb, ((k % cols) * cell, (k // cols) * cell))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=80)
    path.write_bytes(buf.getvalue())


async def run(args: argparse.Namespace) -> int:
    parcels = st.load_all(args.truth)
    if args.only:
        wanted = set(args.only.split(","))
        parcels = [p for p in parcels if p.id in wanted]
    chosen = {k: v for k, v in sources().items() if k in args.views.split(",")}
    client = PoliteClient(host_interval_s=args.interval_s)
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        for parcel in parcels:
            got = await fetch_parcel(client, parcel, args.out, chosen)
            print(
                json.dumps(
                    {
                        "parcel": parcel.id,
                        "views": {
                            k: (v["width"], round(v["pixel_size_m"], 4), v["blank"])
                            for k, v in got.items()
                        },
                    }
                ),
                flush=True,
            )
    finally:
        await client.aclose()
    if args.overlay:
        for view in chosen:
            images = [overlay(p, args.out / p.id, view) for p in parcels]
            for k in range(0, len(images), 6):
                montage(images[k : k + 6], args.out / f"overlay_{view}_{k // 6 + 1}.jpg")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.site_views", description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--truth", type=Path, default=st.FIXTURES)
    parser.add_argument("--only")
    parser.add_argument("--views", default="county,naip")
    parser.add_argument("--overlay", action="store_true")
    parser.add_argument("--interval-s", type=float, default=2.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
