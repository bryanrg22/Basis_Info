"""``python -m evals.measure_gather``: the public geo gather for each truth house, through the
production code (EVAL_REPORT 103, step 4).

For every house in ``evals/fixtures/measure_truth/`` (or the points given with ``--point``) this
runs :class:`basis.geo.gather.Gatherer` exactly as the geo worker does (parcels, the current
public ortho, its history, the 3DEP lidar summary, the site check), with the in-memory store
(:class:`basis.geo.store.MemoryGeoStore`) instead of Postgres and local files instead of the
bucket. No study is made. Per house it writes ``<out>/<id>/gather.json`` (the sources asked and
their outcomes, the lidar summary, every view's georeference, the parcels with their polygons)
and the images under ``<out>/<id>/objects/``.

The public servers are asked as the worker asks them (:class:`basis.geo.http.PoliteClient`:
one request at a time per host, ``GEO_HOST_INTERVAL_S`` apart, the same retries). Parcel layers
carry owner names: the output stays in the private data directory and is never quoted.

Usage (from ``backEnd/``)::

    uv run python -m evals.measure_gather --out "$BASIS_EVAL_DATA_DIR/measure_truth/geo"
    uv run python -m evals.measure_gather --out ... --point il0491:39.79702,-89.64517 \\
        --no-history --no-lidar                  # screening a candidate before its truth file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from basis.geo.gather import GatherConfig, Gatherer
from basis.geo.http import PoliteClient
from basis.geo.store import GatherRow, MemoryGeoStore
from basis.storage.local import LocalStorage
from evals import measure_truth as mt

BUCKET = "measure-truth"


def points(args: argparse.Namespace) -> list[tuple[str, float, float]]:
    if args.point:
        out = []
        for spec in args.point:
            hid, _, where = spec.partition(":")
            lat, _, lon = where.partition(",")
            out.append((hid, float(lat), float(lon)))
        return out
    houses = mt.load_all(args.truth)
    wanted = set(args.only.split(",")) if args.only else None
    return [
        (h.id, h.location.lat, h.location.lon) for h in houses if wanted is None or h.id in wanted
    ]


async def gather_one(
    hid: str, lat: float, lon: float, out: Path, config: GatherConfig, client: PoliteClient
) -> dict[str, Any]:
    folder = out / hid
    folder.mkdir(parents=True, exist_ok=True)
    store = MemoryGeoStore()
    gid, study = uuid.uuid4(), uuid.uuid4()
    store.gathers[gid] = GatherRow(gid, study, "queued", 1, lat, lon)
    storage = LocalStorage(folder / "objects")
    gatherer = Gatherer(store, storage, BUCKET, client, config=config)
    started = time.monotonic()
    before = client.requests
    status = await gatherer.run(gid)
    finished = store.finished.get(gid, {})
    views = []
    for v in store.views:
        d = asdict(v)
        d["gather_id"] = str(v.gather_id)
        d["study_id"] = str(v.study_id)
        d["captured_at"] = v.captured_at.isoformat()
        d["file"] = v.uri.replace(f"gs://{BUCKET}/", "objects/" + BUCKET + "/")
        views.append(d)
    parcels = []
    for p in store.parcels.get(gid, []):
        d = asdict(p)
        d.pop("attributes", None)
        parcels.append(d)
    result = {
        "house": hid,
        "lat": lat,
        "lon": lon,
        "status": status,
        "at": datetime.now(UTC).isoformat(),
        "seconds": round(time.monotonic() - started, 1),
        "requests": client.requests - before,
        "config": asdict(config),
        "sources": finished.get("sources"),
        "lidar": finished.get("lidar"),
        "views": views,
        "parcels": parcels,
        "incidents": [
            {**asdict(i), "at": i.at.isoformat(), "study_id": None, "gather_id": None}
            for i in store.incidents
        ],
        "events": [e[0] for e in store.events],
    }
    (folder / "gather.json").write_text(json.dumps(result, indent=1, default=str) + "\n")
    return result


async def run(args: argparse.Namespace) -> int:
    config = replace(
        GatherConfig(),
        history=not args.no_history,
        lidar=not args.no_lidar,
        extent_m=args.extent_m,
    )
    client = PoliteClient(host_interval_s=args.interval_s)
    try:
        for hid, lat, lon in points(args):
            result = await gather_one(hid, lat, lon, args.out, config, client)
            lidar = result["lidar"] or {}
            building = lidar.get("building") or {}
            print(
                json.dumps(
                    {
                        "house": hid,
                        "status": result["status"],
                        "seconds": result["seconds"],
                        "requests": result["requests"],
                        "views": [(v["source_id"], v["year"]) for v in result["views"]],
                        "parcels": len(result["parcels"]),
                        "lidar": {
                            "project": lidar.get("project"),
                            "footprint_m2": building.get("footprint_m2"),
                            "eave_m": building.get("eave_m"),
                            "peak_m": building.get("peak_m"),
                            "code": lidar.get("code"),
                        },
                        "failed": [
                            (s.get("source"), s.get("code"))
                            for s in result["sources"] or []
                            if s.get("status") == "failed"
                        ],
                    }
                )
            )
    finally:
        await client.aclose()
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.measure_gather", description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--truth", type=Path, default=mt.FIXTURES)
    parser.add_argument("--only", help="comma-separated house ids")
    parser.add_argument("--point", action="append", help="id:lat,lon instead of the truth files")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--no-lidar", action="store_true")
    parser.add_argument("--extent-m", type=float, default=GatherConfig().extent_m)
    parser.add_argument("--interval-s", type=float, default=1.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
