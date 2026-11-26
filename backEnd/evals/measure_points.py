"""``python -m evals.measure_points``: cache, privately, the 3DEP points (with their return
numbers) and the USA Structures footprints around each truth house, for M1 (EVAL_REPORT 105).

For every house whose public gather (:mod:`evals.measure_gather`) read a lidar project, the
points in the gather's own lidar box are read again through the production code
(:func:`basis.geo.shell.read_points`: the same index, project ranking, octree walk and
:class:`basis.geo.http.PoliteClient`, one request at a time per host) and written as
``<geo>/<id>/points.npz`` (Mercator x, y, z, class, return number, number of returns: the
box's points only, never the tiles) with ``points.json`` (project, nodes, bytes, seconds,
requests). ``--structures`` also asks the USA Structures layer (ORNL and FEMA; the federal
building footprints, one query a house within 25 m of the house's point) and writes
``<geo>/<id>/structures.json``.

Public data only, no study, no client data: the truth houses are HABS houses whose locations
are public. The client property's points are read from the file the gather of EVAL_REPORT 101
left in the private data directory, never fetched here.

Usage (from ``backEnd/``)::

    uv run python -m evals.measure_points --geo "$BASIS_EVAL_DATA_DIR/measure_truth/geo" \\
        [--only il0491,mo1222] [--structures]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np

from basis.geo import shell
from basis.geo.http import PoliteClient
from evals import measure_truth as mt

STRUCTURES_URL: Final = (
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/ArcGIS/rest/services/"
    "USA_Structures_View/FeatureServer/0/query"
)
STRUCTURES_FIELDS: Final = "SQFEET,HEIGHT,SOURCE,IMAGE_DATE,IMAGE_NAME,VAL_METHOD,PROD_DATE"
STRUCTURES_RADIUS_M: Final = 25


def save_points(folder: Path, read: shell.PointsRead) -> None:
    p = read.points
    np.savez_compressed(
        folder / "points.npz",
        x=p.x,
        y=p.y,
        z=p.z,
        cls=p.cls.astype(np.int16),
        return_number=p.return_number.astype(np.int16),
        number_of_returns=p.number_of_returns.astype(np.int16),
    )


def load_points(folder: Path) -> shell.ShellPoints:
    with np.load(folder / "points.npz") as d:
        meta = json.loads((folder / "points.json").read_text(encoding="utf-8"))
        return shell.ShellPoints(
            d["x"].astype(np.float64),
            d["y"].astype(np.float64),
            d["z"].astype(np.float64),
            d["cls"].astype(np.int64),
            d["return_number"].astype(np.int64),
            d["number_of_returns"].astype(np.int64),
            str(meta.get("project", "")),
        )


async def structures_at(client: PoliteClient, lon: float, lat: float) -> dict[str, Any]:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": str(STRUCTURES_RADIUS_M),
        "units": "esriSRUnit_Meter",
        "outFields": STRUCTURES_FIELDS,
        "outSR": "4326",
        "f": "geojson",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    fetched = await client.get(f"{STRUCTURES_URL}?{query}")
    data = fetched.json()
    if not isinstance(data, dict):
        raise ValueError("the structures layer did not answer with an object")
    return data


async def run(args: argparse.Namespace) -> int:
    houses = mt.load_all(args.truth)
    wanted = set(args.only.split(",")) if args.only else None
    client = PoliteClient(host_interval_s=args.interval_s)
    try:
        for house in houses:
            if wanted is not None and house.id not in wanted:
                continue
            folder = args.geo / house.id
            gather = json.loads((folder / "gather.json").read_text(encoding="utf-8"))
            lid = gather.get("lidar") or {}
            out: dict[str, Any] = {"house": house.id}
            if args.structures:
                started = time.monotonic()
                got = await structures_at(client, house.location.lon, house.location.lat)
                got["_query"] = {
                    "lon": house.location.lon,
                    "lat": house.location.lat,
                    "radius_m": STRUCTURES_RADIUS_M,
                    "seconds": round(time.monotonic() - started, 2),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                (folder / "structures.json").write_text(json.dumps(got, indent=1) + "\n")
                out["structures"] = len(got.get("features") or [])
            if not args.no_points and lid.get("box_lonlat") and lid.get("project"):
                if (folder / "points.npz").is_file() and not args.refresh:
                    out["points"] = "cached"
                else:
                    started = time.monotonic()
                    before = client.requests
                    box = tuple(float(v) for v in lid["box_lonlat"])
                    read = await shell.read_points(
                        client,
                        house.location.lon,
                        house.location.lat,
                        (box[0], box[1], box[2], box[3]),
                        max_nodes=64,
                    )
                    if read is None:
                        out["points"] = "none"
                    else:
                        save_points(folder, read)
                        meta = {
                            "project": read.project,
                            "ept_url": read.ept_url,
                            "nodes": read.nodes,
                            "bytes": read.bytes,
                            "skipped": list(read.skipped),
                            "points": int(read.points.x.size),
                            "box_lonlat": list(box),
                            "seconds": round(time.monotonic() - started, 2),
                            "requests": client.requests - before,
                        }
                        (folder / "points.json").write_text(json.dumps(meta, indent=1) + "\n")
                        out["points"] = meta
            print(json.dumps(out))
    finally:
        await client.aclose()
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.measure_points", description=__doc__)
    parser.add_argument("--geo", type=Path, required=True, help="the per-house gathers")
    parser.add_argument("--truth", type=Path, default=mt.FIXTURES)
    parser.add_argument("--only", help="comma-separated house ids")
    parser.add_argument("--structures", action="store_true", help="ask USA Structures too")
    parser.add_argument("--no-points", action="store_true", help="do not read the lidar")
    parser.add_argument("--refresh", action="store_true", help="read cached points again")
    parser.add_argument("--interval-s", type=float, default=1.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
