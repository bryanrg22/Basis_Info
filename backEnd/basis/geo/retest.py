"""Re-test every source in the registry live, keyless, with one small request each
(``python -m basis.geo.retest [--write] [--out results.json] [--only id,...]``; EVAL_REPORT 101).

Each source is asked at a public test point of its state (the state capitol; the Wisconsin
capitol for the federal sources). Imagery: the service's own metadata where it has one (an
ArcGIS service's ``pixelSizeX`` or finest cached level, spatial reference and full extent), then
one image of 80 m around the point at no more than 256 pixels, read for blankness. When the
capitol is outside a partial service, the centre of the service's extent is tried once. Parcels:
one point query, the polygon measured on the ellipsoid against the layer's own acres.

``--write`` records in ``sources.json``: ``checked_on``, ``retest`` (what was seen), the
service's extent as ``bbox`` (lon/lat, when the service reports it in Web Mercator or lon/lat),
and ``status: unavailable`` with the reason for a source that failed. Entries already marked
unavailable for a reason the client cannot fix (``protocol: unsupported``) are not asked.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import date
from pathlib import Path
from typing import Any, Final

from basis.errors.codes import E
from basis.geo import imagery, parcels
from basis.geo.geometry import M2_PER_ACRE, from_mercator
from basis.geo.http import PoliteClient, SourceError
from basis.geo.registry import SOURCES_PATH, Source, parse

TEST_POINTS: Final[dict[str, tuple[float, float]]] = {
    "US": (43.07472, -89.38417),
    "WI": (43.07472, -89.38417),
    "MN": (44.95528, -93.10222),
    "TX": (30.27472, -97.74056),
    "FL": (30.43811, -84.2816),
    "CT": (41.76416667, -72.68222222),
    "MA": (42.35816667, -71.06369444),
    "NJ": (40.220437, -74.769902),
    "PA": (40.26444444, -76.88361111),
    "MD": (38.97888889, -76.49111111),
    "OH": (39.96138889, -82.99888889),
    "IN": (39.76861111, -86.16277778),
    "IL": (39.79833333, -89.65472222),
    "KY": (38.1867, -84.8753),
    "MO": (38.57916667, -92.17277778),
    "NC": (35.78036111, -78.63911111),
}
WEB_MERCATOR: Final = (3857, 102100, 102113, 900913)


def _extent_lonlat(extent: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not isinstance(extent, dict):
        return None
    sr = extent.get("spatialReference") or {}
    wkid = sr.get("latestWkid") or sr.get("wkid")
    try:
        xmin, ymin, xmax, ymax = (float(extent[k]) for k in ("xmin", "ymin", "xmax", "ymax"))
    except (KeyError, TypeError, ValueError):
        return None
    if wkid in WEB_MERCATOR:
        west, south = from_mercator(xmin, ymin)
        east, north = from_mercator(xmax, ymax)
    elif wkid == 4326:
        west, south, east, north = xmin, ymin, xmax, ymax
    else:
        return None
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        return None
    return (round(west, 4), round(south, 4), round(east, 4), round(north, 4))


async def _metadata(client: PoliteClient, source: Source) -> dict[str, Any]:
    if source.protocol not in ("arcgis-imageserver", "arcgis-mapserver"):
        return {}
    meta = (await client.get(source.url.rstrip("/"), {"f": "json"})).json()
    if not isinstance(meta, dict) or "error" in meta:
        raise SourceError(E.GEO_SOURCE_BAD_RESPONSE, f"metadata: {str(meta)[:160]}", url=source.url)
    out: dict[str, Any] = {}
    sr = meta.get("spatialReference") or {}
    out["service_wkid"] = sr.get("latestWkid") or sr.get("wkid")
    if "pixelSizeX" in meta:
        out["service_pixel"] = meta.get("pixelSizeX")
    lods = (meta.get("tileInfo") or {}).get("lods") or []
    if lods:
        out["finest_cached_level_m"] = min(float(lod["resolution"]) for lod in lods)
    bbox = _extent_lonlat(meta.get("fullExtent") or meta.get("extent"))
    if bbox is not None:
        out["extent_lonlat"] = list(bbox)
    return out


async def test_imagery(
    client: PoliteClient, source: Source, point: tuple[float, float]
) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {}
    try:
        result.update(await _metadata(client, source))
    except SourceError as exc:
        result["metadata_error"] = f"{exc.code} {exc.detail}"
    points = [point]
    if "extent_lonlat" in result:
        w, s, e, n = result["extent_lonlat"]
        points.append(((s + n) / 2, (w + e) / 2))
    for lat, lon in points:
        try:
            img = await imagery.fetch(client, source, lat, lon, extent_m=80.0, max_px=256)
        except SourceError as exc:
            result.update(ok=False, code=exc.code, detail=exc.detail, http_status=exc.status)
            break
        result.update(
            ok=True,
            point=[lat, lon],
            blank=img.blank,
            bytes=len(img.data),
            content_type=img.content_type,
            size=[img.box.width, img.box.height],
            request_seconds=round(img.seconds, 2),
        )
        if not img.blank:
            try:
                year = await imagery.year_at(client, source, lat, lon)
            except SourceError as exc:
                result["year_error"] = exc.detail
            else:
                if source.year_query is not None:
                    result["year_at_point"] = year
            break
    result["seconds"] = round(time.monotonic() - started, 2)
    return result


async def test_parcels(
    client: PoliteClient, source: Source, point: tuple[float, float]
) -> dict[str, Any]:
    started = time.monotonic()
    lat, lon = point
    try:
        found = await parcels.parcels_at(client, source, lat, lon)
    except SourceError as exc:
        return {
            "ok": False,
            "code": exc.code,
            "detail": exc.detail,
            "http_status": exc.status,
            "seconds": round(time.monotonic() - started, 2),
        }
    under = [p for p in found if p.under_pin]
    out: dict[str, Any] = {
        "ok": True,
        "point": [lat, lon],
        "under_pin": len(under),
        "adjacent_same_owner": len(found) - len(under),
        "seconds": round(time.monotonic() - started, 2),
    }
    if under:
        p = under[0]
        out["polygon_acres"] = round(p.area_m2 / M2_PER_ACRE, 4)
        out["layer_acres"] = p.layer_acres
        if p.layer_acres:
            out["polygon_vs_layer_pct"] = round(
                (p.area_m2 / M2_PER_ACRE - p.layer_acres) / p.layer_acres * 100, 2
            )
        out["fields_present"] = sorted(
            k
            for k in (
                "owner",
                "address",
                "land_value",
                "improvement_value",
                "total_value",
                "year_built",
                "building_area_sqft",
                "stories",
            )
            if getattr(p, k) is not None
        )
    return out


async def run(only: set[str] | None, host_interval_s: float) -> dict[str, dict[str, Any]]:
    document = json.loads(SOURCES_PATH.read_text())
    sources = parse(document)
    client = PoliteClient(host_interval_s=host_interval_s, timeout_s=90.0, retries=1)
    results: dict[str, dict[str, Any]] = {}

    async def one(source: Source) -> None:
        if source.protocol == "unsupported":
            results[source.id] = {"skipped": source.status_reason}
            return
        point = TEST_POINTS[source.states[0]]
        if source.kind == "imagery":
            results[source.id] = await test_imagery(client, source, point)
        else:
            results[source.id] = await test_parcels(client, source, point)
        print(source.id, json.dumps(results[source.id]), flush=True)

    try:
        # Hosts are spaced by the client; sources on different hosts run side by side.
        await asyncio.gather(*(one(s) for s in sources if only is None or s.id in only))
    finally:
        await client.aclose()
    return results


def write(results: dict[str, dict[str, Any]], today: str) -> None:
    document = json.loads(SOURCES_PATH.read_text())
    for entry in document["sources"]:
        r = results.get(entry["id"])
        if r is None or "skipped" in r:
            continue
        entry["checked_on"] = today
        entry["retest"] = r
        if r.get("ok") is False:
            entry["status"] = "unavailable"
            entry["status_reason"] = f"{r.get('code')}: {r.get('detail')}"
        else:
            entry["status"] = "available"
            entry.pop("status_reason", None)
        if "extent_lonlat" in r:
            entry["bbox"] = r["extent_lonlat"]
    SOURCES_PATH.write_text(json.dumps(document, indent=1) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m basis.geo.retest", description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--only")
    parser.add_argument("--host-interval", type=float, default=1.0)
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None
    results = asyncio.run(run(only, args.host_interval))
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    if args.write:
        write(results, date.today().isoformat())
    bad = sorted(k for k, v in results.items() if v.get("ok") is False)
    print(f"{len(results)} sources, {len(bad)} failed: {bad}")


if __name__ == "__main__":
    main()
