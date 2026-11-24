"""Parcels under the pin and the owner's adjacent ones, and lidar heights from a synthetic
point set (basis.geo.parcels, basis.geo.lidar; EVAL_REPORT 101). HTTP is mocked."""

from __future__ import annotations

import io
import math
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import numpy as np
import pytest
from geotest_support import Router, source

from basis.errors.codes import E
from basis.geo import lidar, parcels
from basis.geo.geometry import M2_PER_ACRE, from_mercator, to_mercator
from basis.geo.http import SourceError

LAT, LON = 41.60, -93.60
FIELDS = {
    "id": "PID",
    "owner": "OWNER",
    "acres": ["ACRES"],
    "land": "LAND",
    "improvements": "IMP",
    "total": "TOTAL",
    "year_built": "YB",
    "building_area": "AREA",
    "stories": "ST",
    "address": "ADDR",
}


def rect(lon: float, lat: float, w_m: float, h_m: float) -> dict[str, Any]:
    dlon = w_m / (111_412.84 * math.cos(math.radians(lat)))
    dlat = h_m / 111_132.954
    ring = [[lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat], [lon, lat + dlat], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


def feature(pid: str, owner: str | None, geom: dict[str, Any], **props: Any) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": geom,
        "properties": {"PID": pid, "OWNER": owner, **props},
    }


def layer(**kw: Any) -> Any:
    return source(
        id="p",
        kind="parcels",
        protocol="arcgis-feature",
        url="https://p.example.test/FeatureServer/0",
        fields={**FIELDS, **kw},
    )


def test_a_feature_becomes_a_parcel_with_its_fields() -> None:
    geom = rect(LON, LAT, 50, 32.4)
    p = parcels.to_parcel(
        layer(),
        feature(
            "1",
            "SMITH JOHN",
            geom,
            ACRES="0.40",
            LAND=80000,
            IMP="312,000",
            TOTAL=None,
            YB=1995,
            AREA=0,
            ST="1.5",
            ADDR=" 1 Main St ",
            EXTRA="x",
        ),
        under_pin=True,
    )
    assert p.parcel_id == "1" and p.owner == "SMITH JOHN" and p.address == "1 Main St"
    assert p.layer_acres == 0.40 and p.land_value == 80000 and p.improvement_value == 312000
    assert p.total_value is None and p.year_built == 1995 and p.stories == 1.5
    assert p.building_area_sqft is None  # zero means unknown
    assert p.area_m2 == pytest.approx(50 * 32.4, rel=3e-3)
    assert "EXTRA" not in p.attributes and p.attributes["PID"] == "1"
    assert p.acres == 0.40
    # A lot-size field whose unit is not acres is not read as acres; square feet are.
    ma = layer(acres=["LOT"], acres_unit="UNIT")
    assert (
        parcels.to_parcel(
            ma, feature("2", None, geom, LOT=8000, UNIT="S"), under_pin=True
        ).layer_acres
        is None
    )
    assert (
        parcels.to_parcel(
            ma, feature("2", None, geom, LOT=1.2, UNIT="Acres"), under_pin=True
        ).layer_acres
        == 1.2
    )
    fl = layer(acres=[], area_sqft="SQFT")
    got = parcels.to_parcel(fl, feature("3", None, geom, SQFT=43560), under_pin=True)
    assert got.layer_acres == pytest.approx(1.0)
    no_acres = parcels.to_parcel(layer(acres=[]), feature("4", None, geom, YB=0), under_pin=True)
    assert no_acres.acres == pytest.approx(no_acres.area_m2 / M2_PER_ACRE)
    assert no_acres.year_built is None
    with pytest.raises(SourceError):
        parcels.to_parcel(
            layer(), {"geometry": {"type": "Point"}, "properties": {}}, under_pin=True
        )


def test_owner_keys_ignore_case_spaces_and_punctuation() -> None:
    assert parcels.owner_key("Smith, John  & Mary") == parcels.owner_key("SMITH JOHN & MARY.")
    assert parcels.owner_key("  ") is None and parcels.owner_key(None) is None


def test_gap_between_polygons() -> None:
    a = rect(LON, LAT, 50, 50)
    touching = rect(LON + 50 / (111_412.84 * math.cos(math.radians(LAT))), LAT, 30, 50)
    apart = rect(LON + 60 / (111_412.84 * math.cos(math.radians(LAT))), LAT, 30, 50)
    assert parcels.gap_m(a, touching) == pytest.approx(0, abs=0.3)
    assert parcels.gap_m(a, apart) == pytest.approx(10, rel=0.02)
    inside = rect(LON + 0.0001, LAT + 0.0001, 5, 5)
    assert parcels.gap_m(inside, a) == 0.0


async def test_the_parcel_under_the_pin_and_the_owners_adjacent_ones() -> None:
    k = 111_412.84 * math.cos(math.radians(LAT))
    pin = feature("pin", "Smith John", rect(LON - 0.0001, LAT - 0.0001, 50, 32.4), ACRES=0.40)
    neighbour = feature(
        "next", "SMITH, JOHN", rect(LON - 0.0001 + 50 / k, LAT - 0.0001, 70, 43.4), ACRES=0.75
    )
    far = feature("far", "Smith John", rect(LON + 200 / k, LAT, 20, 20))
    stranger = feature("other", "Doe Jane", rect(LON - 0.0001 - 30 / k, LAT - 0.0001, 30, 32.4))
    router = Router()

    def answer(request: httpx.Request) -> httpx.Response:
        q = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        assert q["outSR"] == "4326" and q["f"] == "geojson"
        feats = (
            [pin] if q["geometryType"] == "esriGeometryPoint" else [pin, neighbour, far, stranger]
        )
        return httpx.Response(200, json={"type": "FeatureCollection", "features": feats})

    router.on("/query", answer)
    found = await parcels.parcels_at(router.client(), layer(), LAT, LON)
    assert [(p.parcel_id, p.under_pin) for p in found] == [("pin", True), ("next", False)]
    assert sum(p.layer_acres or 0 for p in found) == pytest.approx(1.15)


async def test_no_adjacent_search_without_an_owner_and_nothing_under_the_pin() -> None:
    router = Router()
    router.on(
        "/query",
        {"type": "FeatureCollection", "features": [feature("a", None, rect(LON, LAT, 10, 10))]},
    )
    found = await parcels.parcels_at(router.client(), layer(), LAT, LON)
    assert [p.parcel_id for p in found] == ["a"] and len(router.requests) == 1
    no_owner_field = await parcels.parcels_at(router.client(), layer(owner=None), LAT, LON)
    assert len(no_owner_field) == 1
    empty = Router()
    empty.on("/query", {"type": "FeatureCollection", "features": []})
    assert await parcels.parcels_at(empty.client(), layer(), LAT, LON) == []
    broken = Router()
    broken.on("/query", {"error": {"code": 400, "message": "Requested operation is not supported"}})
    with pytest.raises(SourceError) as err:
        await parcels.parcels_at(broken.client(), layer(), LAT, LON)
    assert err.value.code == E.GEO_SOURCE_BAD_RESPONSE


# ------------------------------------------------------------------ lidar


def synthetic_points(*, pin_on_building: bool = True, density: float = 4.0) -> lidar.Points:
    """Ground at 100 m over 80 x 80 m; a 10 x 20 m gable building whose roof rises from 5 m at
    the eaves to 8 m at the ridge, centred on the pin (or 30 m east of it)."""
    rng = np.random.default_rng(7)
    k = math.cos(math.radians(LAT))
    cx, cy = to_mercator(LON, LAT)
    n = int(80 * 80 * density)
    e = rng.uniform(-40, 40, n)
    north = rng.uniform(-40, 40, n)
    off = 0.0 if pin_on_building else 30.0
    on = (np.abs(e - off) <= 5) & (np.abs(north) <= 10)
    z = np.full(n, 100.0) + rng.normal(0, 0.02, n)
    z[on] = 100 + 8 - 3 * np.abs(e[on] - off) / 5
    cls = np.where(on, lidar.BUILDING, lidar.GROUND)
    return lidar.Points(cx + e / k, cy + north / k, z, cls.astype(np.int64))


def box_m(half: float) -> tuple[float, float, float, float]:
    cx, cy = to_mercator(LON, LAT)
    h = half / math.cos(math.radians(LAT))
    return (cx - h, cy - h, cx + h, cy + h)


def test_heights_and_footprint_of_a_synthetic_building() -> None:
    count, area, b = lidar.measure(synthetic_points(), LON, LAT, box_m(40))
    assert area == pytest.approx(6400, rel=1e-6) and count == pytest.approx(25600, rel=0.01)
    assert b is not None and b.distance_m == 0
    assert b.ground_elevation_m == pytest.approx(100, abs=0.05)
    assert b.peak_m == pytest.approx(8, abs=0.15)
    assert b.max_m == pytest.approx(8, abs=0.05)
    assert b.eave_m == pytest.approx(5, abs=0.5)
    assert b.footprint_m2 == pytest.approx(200, rel=0.12)  # the 0.5 m grid's edge cells


def test_a_building_off_the_pin_is_found_within_reach_only() -> None:
    _, _, near = lidar.measure(synthetic_points(pin_on_building=False), LON, LAT, box_m(40))
    assert near is None  # 25 m from the pin to its west wall, beyond BUILDING_SEARCH_M
    pts = synthetic_points(pin_on_building=False)
    shifted = lidar.Points(pts.x - 15 / math.cos(math.radians(LAT)), pts.y, pts.z, pts.cls)
    _, _, b = lidar.measure(shifted, LON, LAT, box_m(40))
    assert b is not None and 9 < b.distance_m < 11


def test_no_building_points_means_no_building() -> None:
    pts = synthetic_points()
    ground = lidar.Points(pts.x, pts.y, pts.z, np.full_like(pts.cls, lidar.GROUND))
    count, _, b = lidar.measure(ground, LON, LAT, box_m(40))
    assert b is None and count > 0
    empty = lidar.Points.concat([])
    assert lidar.measure(empty, LON, LAT, box_m(40))[2] is None


def test_the_newest_project_holding_the_point_is_chosen() -> None:
    def proj(name: str, west: float, count: int = 1) -> dict[str, Any]:
        return {
            "properties": {"name": name, "count": count, "url": f"https://e.test/{name}/ept.json"},
            "geometry": rect(west, LAT - 0.1, 30_000, 30_000),
        }

    feats = [
        proj("WI_Old_2010", LON - 0.1),
        proj("XX_Example_2018", LON - 0.1),
        proj("MN_Far_2022", LON + 5),
        {"properties": {}, "geometry": None},
    ]
    assert lidar.choose_project(feats, LON, LAT)["properties"]["name"] == "XX_Example_2018"
    assert lidar.choose_project(feats, LON + 20, LAT) is None
    assert lidar.project_year("USGS_LPC_WI_Statewide_2019_2020_A20") == 2020
    assert lidar.project_year("NoYear") is None


def test_undated_projects_rank_after_dated_ones_and_ties_go_to_more_points() -> None:
    def proj(name: str, count: int) -> dict[str, Any]:
        return {
            "properties": {"name": name, "count": count},
            "geometry": rect(LON - 0.1, LAT - 0.1, 30_000, 30_000),
        }

    # EVAL_REPORT 101.5: MN_FullState and the *_B22 projects carry no year in their name.
    feats = [proj("MN_FullState", 900), proj("XX_2015", 1), proj("YY_B22", 50), proj("ZZ_2015", 7)]
    names = [f["properties"]["name"] for f in lidar.rank_projects(feats, LON, LAT)]
    assert names == ["ZZ_2015", "XX_2015", "MN_FullState", "YY_B22"]
    assert lidar.rank_projects(feats, LON + 20, LAT) == []


def test_a_building_cut_by_the_box_is_flagged() -> None:
    # The 10 x 20 m building runs 10 m north and south of the pin: an 8 m half-box cuts it.
    _, _, whole = lidar.measure(synthetic_points(), LON, LAT, box_m(40))
    _, _, cut = lidar.measure(synthetic_points(), LON, LAT, box_m(8))
    assert whole is not None and not whole.reaches_box_edge
    assert cut is not None and cut.reaches_box_edge
    assert cut.footprint_m2 < whole.footprint_m2


def laz(points: lidar.Points) -> bytes:
    import laspy

    header = laspy.LasHeader(point_format=6, version="1.4")
    header.offsets = [points.x.min(), points.y.min(), points.z.min()]
    header.scales = [0.01, 0.01, 0.01]
    las = laspy.LasData(header)
    las.x, las.y, las.z = points.x, points.y, points.z
    las.classification = points.cls.astype(np.uint8)
    out = io.BytesIO()
    las.write(out, laz_backend=laspy.LazBackend.Lazrs)
    return out.getvalue()


async def test_summary_reads_only_the_tiles_over_the_lot() -> None:
    lidar._index_cache.clear()
    pts = synthetic_points()
    cx, cy = to_mercator(LON, LAT)
    root = [cx - 1000, cy - 1000, 0, cx + 1000, cy + 1000, 2000]
    first = lidar.Points(pts.x[::2], pts.y[::2], pts.z[::2], pts.cls[::2])
    second = lidar.Points(pts.x[1::2], pts.y[1::2], pts.z[1::2], pts.cls[1::2])
    west, south = from_mercator(root[0], root[1])
    east, north = from_mercator(root[3], root[4])
    outline = {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }
    router = Router()
    router.on(
        "resources.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "properties": {
                        "name": "WI_Test_2018",
                        "count": 9,
                        "url": "https://lidar.test/WI_Test_2018/ept.json",
                    },
                    "geometry": outline,
                }
            ],
        },
    )
    router.on("/ept.json", {"bounds": root, "srs": {"horizontal": "3857"}, "dataType": "laszip"})
    # The lot is in the root's south-west-low child (1-0-0-0); the other children are elsewhere.
    router.on(
        "/ept-hierarchy/0-0-0-0.json", {"0-0-0-0": 10, "1-0-0-0": 10, "1-1-1-0": 10, "1-1-1-1": -1}
    )
    router.on("/ept-data/0-0-0-0.laz", httpx.Response(200, content=laz(first)))
    router.on("/ept-data/1-0-0-0.laz", httpx.Response(200, content=laz(second)))
    # The pin is the root's centre: a box just west and south of it lies in 1-0-0-0 only.
    box = (*from_mercator(cx - 30 / 0.72, cy - 30 / 0.72), *from_mercator(cx - 0.1, cy - 0.1))
    summary = await lidar.summarize(router.client(), LON, LAT, box, max_nodes=8)
    assert summary is not None and summary.year == 2018 and summary.nodes == 2
    fetched = [str(r.url).rsplit("/", 1)[1] for r in router.requests]
    assert "1-1-1-0.laz" not in fetched and "1-1-1-1.json" not in fetched
    assert summary.building is not None
    with pytest.raises(SourceError) as many:
        await lidar.summarize(router.client(), LON, LAT, box, max_nodes=1)
    assert many.value.code == E.GEO_LIDAR_UNREADABLE


async def test_lidar_failures() -> None:
    lidar._index_cache.clear()
    outline = rect(LON - 0.1, LAT - 0.1, 30_000, 30_000)
    project = {
        "properties": {"name": "P_2018", "url": "https://lidar.test/P/ept.json"},
        "geometry": outline,
    }
    router = Router()
    router.on("resources.geojson", {"type": "FeatureCollection", "features": [project]})
    router.on(
        "/ept.json",
        {"bounds": [0, 0, 0, 1, 1, 1], "srs": {"horizontal": "26915"}, "dataType": "laszip"},
    )
    with pytest.raises(SourceError) as srs:
        await lidar.summarize(
            router.client(), LON, LAT, lidar.box_around(LON, LAT, 40), max_nodes=8
        )
    assert srs.value.code == E.GEO_LIDAR_UNREADABLE
    assert (
        await lidar.summarize(
            router.client(), LON + 30, LAT, lidar.box_around(LON + 30, LAT, 40), max_nodes=8
        )
        is None
    )
    with pytest.raises(SourceError) as garbage:
        lidar.decode_laz(b"not a laz file")
    assert garbage.value.code == E.GEO_LIDAR_UNREADABLE
    lidar._index_cache.clear()
    bad = Router()
    bad.on("resources.geojson", {"nothing": True})
    with pytest.raises(SourceError):
        await lidar.load_index(bad.client())
    lidar._index_cache.clear()


def _ept_project(
    router: Router, name: str, points: lidar.Points, root: list[float]
) -> dict[str, Any]:
    router.on(
        f"/{name}/ept.json", {"bounds": root, "srs": {"horizontal": "3857"}, "dataType": "laszip"}
    )
    router.on(f"/{name}/ept-hierarchy/0-0-0-0.json", {"0-0-0-0": 10})
    router.on(f"/{name}/ept-data/0-0-0-0.laz", httpx.Response(200, content=laz(points)))
    return {
        "properties": {"name": name, "count": 9, "url": f"https://lidar.test/{name}/ept.json"},
        "geometry": rect(LON - 0.1, LAT - 0.1, 30_000, 30_000),
    }


async def test_a_project_with_no_point_at_the_pin_is_passed_over() -> None:
    # EVAL_REPORT 101.5, the Lincoln Memorial: the newest project's index outline holds the pin
    # but its data has no point there; the next project's has.
    lidar._index_cache.clear()
    cx, cy = to_mercator(LON, LAT)
    root = [cx - 2000, cy - 2000, 0, cx + 2000, cy + 2000, 4000]
    pts = synthetic_points()
    away = lidar.Points(pts.x + 1000 / 0.72, pts.y, pts.z, pts.cls)  # 1 km east, out of the box
    router = Router()
    empty_a = _ept_project(router, "AA_Empty_2022", away, root)
    empty_b = _ept_project(router, "BB_Empty_2020", away, root)
    full = _ept_project(router, "CC_Full_2015", pts, root)
    router.on(
        "resources.geojson", {"type": "FeatureCollection", "features": [full, empty_b, empty_a]}
    )
    box = lidar.box_around(LON, LAT, 40)
    summary = await lidar.summarize(router.client(), LON, LAT, box, max_nodes=8)
    assert summary is not None and summary.project == "CC_Full_2015"
    assert summary.skipped == ("AA_Empty_2022", "BB_Empty_2020")
    assert summary.building is not None and summary.points > 0

    # Three empty projects: no lidar, and a fourth is never read.
    lidar._index_cache.clear()
    router = Router()
    feats = [_ept_project(router, f"P{i}_Empty_202{i}", away, root) for i in range(3)]
    feats.append(_ept_project(router, "P9_Full_2010", pts, root))
    router.on("resources.geojson", {"type": "FeatureCollection", "features": feats})
    assert await lidar.summarize(router.client(), LON, LAT, box, max_nodes=8) is None
    assert not any("P9_Full_2010" in str(r.url) for r in router.requests)
