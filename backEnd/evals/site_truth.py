"""Site-item ground truth: county planimetric outlines of driveways, patios, pools, decks and
walks on residential parcels (EVAL_REPORT 106, AI_MEASUREMENT_PLAN.md section 4 tier B).

The truth is Fairfax County, Virginia's public planimetric data (Department of Information
Technology GIS Division, "Publicly available"):

* ``2023_Countywide_Impervious`` (one layer: every impervious polygon with its type and
  subtype; the county's 2017 planimetric capture updated from the 2022 and 2023 EagleView
  orthophotos, a snapshot of 2025-04-01), the outlines scored;
* ``Driveways_and_Parking_Lots`` and ``Building_Additions`` (the same polygons with a per-feature
  ``SOURCE``: which imagery each outline was last drawn from), read to date every item;
* ``Parcels`` (lot lines; PIN and type only, no owner).

Every layer is served in Web Mercator, whose stored ``Shape__Area`` is inflated by
``1 / cos^2(lat)`` (1.64 at 38.7 degrees north). The features are asked for in WGS84
(``outSR=4326``) and every area here is measured on the ellipsoid
(:func:`basis.geo.geometry.ellipsoid_area_m2`, Karney's algorithm), never taken from
``Shape__Area``.

**Selection** (:func:`select`, deterministic): ordinary (not condominium) parcels of 300 to
5,000 m2 with exactly one paved driveway whose area lies at least 70 % inside the parcel
(townhouse rows share one), and every patio, pool, deck and walk whose centre lies inside it and
at least half of whose area does (a public sidewalk running past several lots is not one lot's).
Up to ``per_area`` parcels an area, taken in a fixed pseudo-random order (seeded by the PIN),
those with a patio, pool or deck first so the families other than driveways are represented.

**Schema** (``site_truth/v1``, one JSON per parcel in ``evals/fixtures/site_truth/``): ``id``,
``area`` (the neighbourhood's name), ``centre`` (the parcel's centre, lon/lat), ``parcel`` (the
lot line ring, lon/lat), ``items`` (``id``, ``family``, ``subtype``, ``ring`` lon/lat, ``area_sf``
on the ellipsoid, ``perimeter_ft``, ``source``: the imagery the county drew it from, where the
per-feature layers say), ``layers`` (the URLs and the layers' own revision text), ``fetched_on``
and ``checks`` (what the visual check of the imagery found: ``kept`` or ``dropped`` with the
reason; written by hand after looking at the overlays).

Usage (from ``backEnd/``)::

    uv run python -m evals.site_truth --out evals/fixtures/site_truth \\
        --area chantilly:38.8700,-77.4300 --area vienna:38.9000,-77.2700 --per-area 8
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from basis.domain.traced import lonlat_path_length_ft
from basis.geo.geometry import SQFT_PER_M2, ellipsoid_area_m2
from basis.geo.http import PoliteClient

SCHEMA: Final = "site_truth/v1"
FIXTURES: Final = Path(__file__).parent / "fixtures" / "site_truth"
FAIRFAX: Final = "https://services1.arcgis.com/ioennV6PpG5Xodq0/ArcGIS/rest/services"
LAYERS: Final[dict[str, str]] = {
    "impervious": f"{FAIRFAX}/2023_Countywide_Impervious/FeatureServer/0",
    "driveways": f"{FAIRFAX}/Driveways_and_Parking_Lots/FeatureServer/0",
    "additions": f"{FAIRFAX}/Building_Additions/FeatureServer/0",
    "parcels": f"{FAIRFAX}/Parcels/FeatureServer/0",
}
# The layers' own revision notes, as their descriptions state them (read 2026-11-25).
REVISIONS: Final[dict[str, str]] = {
    "impervious": "2017 planimetric capture, updated from the 2022/2023 EagleView orthophotos; "
    "snapshot of 2025-04-01",
    "driveways": "captured 1997, updated 2014 and 2022, last from 2022 and 2023 orthoimagery; "
    "last revision 2024-03-01",
    "additions": "captured 2013 (2009 stereo), updated 2022 (2017 stereo) and 2024 (2022/2023 "
    "EagleView orthophotos); last revision 2024-03-01",
    "parcels": "Fairfax County parcels (PIN and type only)",
}
Family = Literal["driveway", "patio", "pool", "deck", "walk"]
# (IMPERVIOUS_TYPE, IMPERVIOUS_SUBTYPE) -> family; anything else is not a site item here.
FAMILIES: Final[dict[tuple[str, str | None], Family]] = {
    ("Driveway or Parking Lot", "Paved Driveway"): "driveway",
    ("Building Addition", "Patio"): "patio",
    ("Building Addition", "Pool"): "pool",
    ("Building Addition", "Deck"): "deck",
    ("Sidewalk", None): "walk",
    ("Recreation Feature", "Pathway"): "walk",
}
MIN_PARCEL_M2: Final = 300.0
MAX_PARCEL_M2: Final = 5000.0
MIN_DRIVEWAY_INSIDE: Final = 0.7
MIN_ITEM_INSIDE: Final = 0.5
AREA_HALF_M: Final = 250.0
BUILDING_NEAR_M: Final = 40.0

LonLat = tuple[float, float]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Item(_Model):
    id: str
    family: Family
    subtype: str | None
    ring: tuple[tuple[float, float], ...] = Field(min_length=3)
    area_sf: float = Field(gt=0)
    perimeter_ft: float = Field(gt=0)
    source: str | None = None


class Check(_Model):
    status: Literal["unchecked", "kept", "dropped"] = "unchecked"
    note: str = ""


class Parcel(_Model):
    schema_: str = Field(alias="schema")
    id: str
    area: str
    pin: str
    centre: tuple[float, float]
    parcel: tuple[tuple[float, float], ...] = Field(min_length=3)
    parcel_area_m2: float
    items: tuple[Item, ...] = Field(min_length=1)
    layers: dict[str, str]
    revisions: dict[str, str]
    fetched_on: str
    # Building outlines near the parcel (roof edges as the county drew them): not scored, kept
    # so the image registration can leave raised surfaces out (EVAL_REPORT 106.2).
    buildings: tuple[tuple[tuple[float, float], ...], ...] = ()
    checks: dict[str, Check] = Field(default_factory=dict)

    def by_family(self, family: str) -> list[Item]:
        return [i for i in self.items if i.family == family]


def load(path: Path) -> Parcel:
    return Parcel.model_validate_json(path.read_text(encoding="utf-8"))


def load_all(directory: Path = FIXTURES) -> list[Parcel]:
    return [load(p) for p in sorted(directory.glob("*.json"))]


# ------------------------------------------------------------------ geometry


def ring_area_sf(ring: Sequence[LonLat]) -> float:
    return ellipsoid_area_m2([list(ring)]) * SQFT_PER_M2


def ring_perimeter_ft(ring: Sequence[LonLat]) -> float:
    pts = list(ring)
    return lonlat_path_length_ft([*pts, pts[0]])


def centroid(ring: Sequence[LonLat]) -> LonLat:
    """The area centroid of a small ring (planar in degrees, scaled by cos(lat): exact enough
    for a point-in-parcel test)."""
    pts = list(ring)
    lat0 = sum(p[1] for p in pts) / len(pts)
    k = math.cos(math.radians(lat0))
    # Centred on the first corner: the shoelace sums on raw degrees (77 x 38) cancel away the
    # few square metres of a patio (found in testing: centres 200 m off).
    ox, oy = pts[0]
    local = [((x - ox) * k, y - oy) for x, y in pts]
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(local, local[1:] + local[:1], strict=True):
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a) < 1e-24:
        return (sum(p[0] for p in pts) / len(pts), lat0)
    return (ox + cx / (3 * a) / k, oy + cy / (3 * a))


def inside(point: LonLat, ring: Sequence[LonLat]) -> bool:
    """Ray casting (even-odd)."""
    x, y = point
    pts = list(ring)
    hit = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def share_inside(ring: Sequence[LonLat], container: Sequence[LonLat], n: int = 30) -> float:
    """The share of ``ring``'s area inside ``container``, by an ``n`` x ``n`` grid of points over
    the ring's box (each counted when inside the ring)."""
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    total = hits = 0
    for i in range(n):
        for j in range(n):
            pt = (
                min(xs) + (i + 0.5) * (max(xs) - min(xs)) / n,
                min(ys) + (j + 0.5) * (max(ys) - min(ys)) / n,
            )
            if inside(pt, ring):
                total += 1
                hits += inside(pt, container)
    return hits / total if total else 0.0


def _outer(geometry: Mapping[str, Any]) -> list[LonLat] | None:
    """The outer ring of a GeoJSON polygon (the first polygon of a multipolygon), the closing
    point dropped, rounded to 1e-8 degree (about a millimetre)."""
    kind, coords = geometry.get("type"), geometry.get("coordinates")
    if kind == "MultiPolygon" and coords:
        coords = coords[0]
    elif kind != "Polygon":
        return None
    if not coords or len(coords[0]) < 4:
        return None
    ring = [(round(float(x), 8), round(float(y), 8)) for x, y, *_ in coords[0]]
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    return ring if len(ring) >= 3 else None


# ------------------------------------------------------------------ fetching


async def query(
    client: PoliteClient, layer: str, lat: float, lon: float, half_m: float, fields: str
) -> list[dict[str, Any]]:
    """Every feature of ``layer`` meeting the square of ``half_m`` around the point, as GeoJSON
    in WGS84, paging past the layer's record cap."""
    dlat = half_m / 111_320.0
    dlon = half_m / (111_320.0 * math.cos(math.radians(lat)))
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        got = await client.get(
            f"{layer}/query",
            {
                "geometry": f"{lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "where": "1=1",
                "outFields": fields,
                "outSR": "4326",
                "resultOffset": str(offset),
                "resultRecordCount": "1000",
                "f": "geojson",
            },
        )
        data = got.json()
        features = data.get("features") or []
        out += features
        if not data.get("properties", {}).get("exceededTransferLimit") and not data.get(
            "exceededTransferLimit"
        ):
            break
        offset += len(features)
        if not features:
            break
    return out


def _source_of(ring: Sequence[LonLat], sourced: Sequence[tuple[list[LonLat], str]]) -> str | None:
    """The per-feature SOURCE of the same polygon in a per-feature layer: the one whose centre is
    within 1 m and area within 5 %."""
    c = centroid(ring)
    area = ring_area_sf(ring)
    for other, source in sourced:
        oc = centroid(other)
        d = math.hypot(
            (oc[0] - c[0]) * 111_320.0 * math.cos(math.radians(c[1])), (oc[1] - c[1]) * 111_320.0
        )
        if d <= 1.0 and abs(ring_area_sf(other) - area) <= 0.05 * area:
            return source
    return None


def _near(ring: Sequence[LonLat], parcel: Sequence[LonLat], metres: float) -> bool:
    """Whether the ring's centre is within ``metres`` of the parcel's box."""
    c = centroid(ring)
    dlat = metres / 111_320.0
    dlon = metres / (111_320.0 * math.cos(math.radians(c[1])))
    xs, ys = [p[0] for p in parcel], [p[1] for p in parcel]
    return min(xs) - dlon <= c[0] <= max(xs) + dlon and min(ys) - dlat <= c[1] <= max(ys) + dlat


def _order(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


def select(
    area: str,
    impervious: Sequence[Mapping[str, Any]],
    parcels: Sequence[Mapping[str, Any]],
    sourced: Sequence[tuple[list[LonLat], str]],
    per_area: int,
    fetched_on: str,
) -> list[dict[str, Any]]:
    items: list[tuple[Family, str | None, list[LonLat]]] = []
    buildings: list[list[LonLat]] = []
    for f in impervious:
        if (f.get("properties") or {}).get("IMPERVIOUS_TYPE") == "Building":
            ring = _outer(f.get("geometry") or {})
            if ring is not None:
                buildings.append(ring)
        props = f.get("properties") or {}
        key = (props.get("IMPERVIOUS_TYPE"), props.get("IMPERVIOUS_SUBTYPE"))
        family = FAMILIES.get(key)  # type: ignore[arg-type]
        ring = _outer(f.get("geometry") or {})
        if family is not None and ring is not None:
            items.append((family, key[1], ring))
    chosen: list[dict[str, Any]] = []
    for p in parcels:
        props = p.get("properties") or {}
        if props.get("PARCEL_TYPE") != "ORDINARY":
            continue
        ring = _outer(p.get("geometry") or {})
        pin = str(props.get("PIN") or "")
        if ring is None or not pin:
            continue
        parcel_m2 = ellipsoid_area_m2([ring])
        if not MIN_PARCEL_M2 <= parcel_m2 <= MAX_PARCEL_M2:
            continue
        drives = [
            r
            for fam, _, r in items
            if fam == "driveway" and share_inside(r, ring) >= MIN_DRIVEWAY_INSIDE
        ]
        if len(drives) != 1:
            continue
        # A public sidewalk can run along a whole street with its centre on one lot: an item
        # counts only when most of it lies on the parcel.
        mine: list[tuple[Family, str | None, list[LonLat]]] = [
            (fam, sub, r)
            for fam, sub, r in items
            if fam != "driveway"
            and inside(centroid(r), ring)
            and share_inside(r, ring) >= MIN_ITEM_INSIDE
        ]
        mine = [("driveway", "Paved Driveway", drives[0]), *mine]
        chosen.append(
            {
                "schema": SCHEMA,
                "id": "",
                "area": area,
                "pin": pin,
                "centre": centroid(ring),
                "parcel": ring,
                "parcel_area_m2": round(parcel_m2, 2),
                "buildings": [b for b in buildings if _near(b, ring, BUILDING_NEAR_M)],
                "items": [
                    {
                        "id": f"{fam}-{k}",
                        "family": fam,
                        "subtype": sub,
                        "ring": r,
                        "area_sf": round(ring_area_sf(r), 2),
                        "perimeter_ft": round(ring_perimeter_ft(r), 2),
                        "source": _source_of(r, sourced),
                    }
                    for k, (fam, sub, r) in enumerate(mine, start=1)
                ],
                "layers": dict(LAYERS),
                "revisions": dict(REVISIONS),
                "fetched_on": fetched_on,
            }
        )
    chosen.sort(key=lambda d: (len(d["items"]) == 1, _order(d["pin"])))
    return chosen[:per_area]


async def build(args: argparse.Namespace) -> int:
    client = PoliteClient(host_interval_s=args.interval_s)
    args.out.mkdir(parents=True, exist_ok=True)
    fetched_on = datetime.now(UTC).date().isoformat()
    n = len(list(args.out.glob("*.json")))
    keep: dict[str, tuple[str, dict[str, Any]]] = {}
    if args.keep is not None:
        for old in load_all(args.keep):
            keep[old.pin] = (old.id, {k: v.model_dump() for k, v in old.checks.items()})
    try:
        for spec in args.area:
            name, _, where = spec.partition(":")
            lat, _, lon = where.partition(",")
            la, lo = float(lat), float(lon)
            imp = await query(
                client,
                LAYERS["impervious"],
                la,
                lo,
                args.half_m,
                "IMPERVIOUS_TYPE,IMPERVIOUS_SUBTYPE",
            )
            par = await query(client, LAYERS["parcels"], la, lo, args.half_m, "PIN,PARCEL_TYPE")
            sourced: list[tuple[list[LonLat], str]] = []
            for layer in ("driveways", "additions"):
                for f in await query(client, LAYERS[layer], la, lo, args.half_m, "TYPE,SOURCE"):
                    ring = _outer(f.get("geometry") or {})
                    src = (f.get("properties") or {}).get("SOURCE")
                    if ring is not None and src:
                        sourced.append((ring, str(src)))
            if keep:
                # The parcels of an earlier build, their items recomputed: every lot of the
                # area, then only the kept PINs, under their ids and with their checks.
                picked = [
                    d
                    for d in select(name, imp, par, sourced, 10_000, fetched_on)
                    if d["pin"] in keep
                ]
            else:
                picked = select(name, imp, par, sourced, args.per_area, fetched_on)
            for doc in picked:
                if keep:
                    doc["id"], doc["checks"] = keep[doc["pin"]]
                else:
                    n += 1
                    doc["id"] = f"ffx{n:02d}"
                Parcel.model_validate(doc)
                (args.out / f"{doc['id']}.json").write_text(
                    json.dumps(doc, indent=1) + "\n", encoding="utf-8"
                )
            print(
                json.dumps(
                    {
                        "area": name,
                        "impervious": len(imp),
                        "parcels": len(par),
                        "sourced": len(sourced),
                        "picked": [(d["id"], [i["family"] for i in d["items"]]) for d in picked],
                    }
                ),
                flush=True,
            )
    finally:
        await client.aclose()
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.site_truth", description=__doc__)
    parser.add_argument("--out", type=Path, default=FIXTURES)
    parser.add_argument("--area", action="append", required=True, help="name:lat,lon")
    parser.add_argument("--per-area", type=int, default=8)
    parser.add_argument("--half-m", type=float, default=AREA_HALF_M)
    parser.add_argument("--interval-s", type=float, default=2.0)
    parser.add_argument("--keep", type=Path, help="rebuild the parcels of these truth files")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(build(parse_args(argv)))


__all__ = [
    "FAMILIES",
    "FIXTURES",
    "SCHEMA",
    "Check",
    "Item",
    "Parcel",
    "centroid",
    "inside",
    "load",
    "load_all",
    "ring_area_sf",
    "ring_perimeter_ft",
    "select",
    "share_inside",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
