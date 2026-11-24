"""One gather start to end with an in-memory store and mocked servers (basis.geo.gather,
basis.geo.activities; EVAL_REPORT 101): the current view and its fallback, the history (one per
year, the current year not twice), parcels, the site check, lidar, every failure's outcome,
incident and event, a retry that skips what is stored, and the settings that turn parts off."""

from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from geotest_support import Router, jpeg, png, source

from basis.config import Settings
from basis.errors.codes import E
from basis.geo import lidar
from basis.geo.activities import GeoActivities
from basis.geo.contracts import FailInput, GatherInput
from basis.geo.gather import GatherConfig, Gatherer, gather_prefix, view_label
from basis.geo.store import GatherRow, MemoryGeoStore
from basis.storage import LocalStorage

LAT, LON = 41.60, -93.60
NO_LIDAR = GatherConfig(extent_m=9.6, max_px=64, lidar=False)


def rect(lon: float, lat: float, w: float, h: float) -> dict[str, Any]:
    dlon = w / (111_412.84 * math.cos(math.radians(lat)))
    dlat = h / 111_132.954
    return {
        "type": "Polygon",
        "coordinates": [
            [[lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat], [lon, lat + dlat], [lon, lat]]
        ],
    }


def sources() -> list[Any]:
    return [
        source(
            id="state",
            url="https://state.test/Latest/ImageServer",
            native_pixel_m=0.15,
            year=None,
            year_label="by county",
            leaf_off=True,
            year_query={"url": "https://state.test/index/query", "field": "YEAR"},
        ),
        source(
            id="state-2010",
            url="https://state.test/Y2010/ImageServer",
            native_pixel_m=0.45,
            year=2010,
            leaf_off=True,
        ),
        source(
            id="naip-2020", url="https://naip.test/N2020/ImageServer", native_pixel_m=0.6, year=2020
        ),
        source(
            id="naip-2018", url="https://naip.test/N2018/ImageServer", native_pixel_m=0.6, year=2018
        ),
        source(
            id="naip-2016", url="https://naip.test/N2016/ImageServer", native_pixel_m=0.6, year=2016
        ),
        source(
            id="elsewhere", url="https://far.test/ImageServer", bbox=[-80, 30, -70, 40], year=2024
        ),
        source(
            id="parcels",
            kind="parcels",
            protocol="arcgis-feature",
            url="https://parcels.test/FeatureServer/0",
            fields={"id": "PID", "owner": "OWNER", "acres": ["ACRES"]},
        ),
    ]


def servers() -> Router:
    router = Router()
    router.on("state.test/index/query", {"features": [{"attributes": {"YEAR": 2020}}]})
    router.on("state.test/Latest", jpeg(64, 64))
    router.on("state.test/Y2010", jpeg(64, 64))
    router.on("naip.test/N2020", jpeg(16, 16))
    router.on("naip.test/N2018", jpeg(16, 16))
    router.on("naip.test/N2016", png(16, 16, transparent=True))  # not flown here that year
    k = 111_412.84 * math.cos(math.radians(LAT))
    pin = {
        "type": "Feature",
        "geometry": rect(LON - 0.0001, LAT - 0.0001, 50, 32.4),
        "properties": {"PID": "pin", "OWNER": "A", "ACRES": 0.40},
    }
    nxt = {
        "type": "Feature",
        "geometry": rect(LON - 0.0001 + 50 / k, LAT - 0.0001, 70, 43.4),
        "properties": {"PID": "next", "OWNER": "A", "ACRES": 0.75},
    }

    def parcels(request: httpx.Request) -> httpx.Response:
        point = "esriGeometryPoint" in str(request.url)
        return httpx.Response(200, json={"features": [pin] if point else [pin, nxt]})

    router.on("parcels.test", parcels)
    return router


def setup(
    tmp_path: Path, router: Router, config: GatherConfig = NO_LIDAR, srcs: list[Any] | None = None
) -> tuple[MemoryGeoStore, Gatherer, uuid.UUID, LocalStorage]:
    store = MemoryGeoStore()
    gid, sid = uuid.uuid4(), uuid.uuid4()
    store.gathers[gid] = GatherRow(gid, sid, "queued", 3, LAT, LON)
    store.appraisal[sid] = 1.15 * 43_560
    storage = LocalStorage(tmp_path)
    gatherer = Gatherer(
        store,
        storage,
        "bucket",
        router.client(retries=0),
        config=config,
        sources=srcs if srcs is not None else sources(),
    )
    return store, gatherer, gid, storage


async def test_a_gather_keeps_the_best_view_one_per_year_and_the_parcels(tmp_path: Path) -> None:
    router = servers()
    store, gatherer, gid, storage = setup(tmp_path, router)
    assert await gatherer.run(gid) == "succeeded"
    views = {v.label: v for v in store.views}
    assert set(views) == {"current-state-2020", "history-naip-2018-2018", "history-state-2010-2010"}
    current = views["current-state-2020"]
    assert current.kind == "current" and current.year == 2020 and current.leaf_off is True
    assert current.year_label == "by county" and current.credit == "Credit"
    assert current.pixel_size_m == pytest.approx(0.15) and current.width == 64
    assert current.uri.startswith(gather_prefix("bucket", store.gathers[gid].study_id, gid))
    assert current.uri.endswith(".jpg") and await storage.read(current.uri)
    # 2020 is the current view's year, so NAIP 2020 is not fetched; NAIP 2016 was blank.
    asked = [str(r.url) for r in router.requests]
    assert not any("N2020" in u for u in asked) and any("far.test" in u for u in asked) is False
    outcomes = store.finished[gid]["sources"]
    assert {o["source"]: o["status"] for o in outcomes if o["kind"] == "history"} == {
        "naip-2018": "ok",
        "naip-2016": "blank",
        "state-2010": "ok",
    }
    assert [p.parcel_id for p in store.parcels[gid]] == ["pin", "next"]
    (checked,) = [d for e, d in store.events if e == "geo.site_area_checked"]
    assert checked["status"] == "adjacent_match" and checked["code"] == E.GEO_SITE_AREA_ADJACENT
    assert store.gathers[gid].status == "succeeded" and store.gathers[gid].code == E.GEO_COMPLETED
    assert store.finished[gid]["lidar"] is None and not store.incidents


async def test_the_next_source_is_tried_when_the_best_is_blank_or_fails(tmp_path: Path) -> None:
    router = Router()
    router.on("state.test/index/query", httpx.Response(500))  # the year falls back to the entry's
    router.on("state.test/Latest", httpx.Response(503))
    router.on("state.test/Y2010", png(64, 64, transparent=True))
    router.on("naip.test/N2020", jpeg(16, 16))
    router.on("parcels.test", {"features": []})
    store, gatherer, gid, _ = setup(
        tmp_path, router, GatherConfig(extent_m=9.6, max_px=64, history=False, lidar=False)
    )
    assert await gatherer.run(gid) == "succeeded"
    assert [v.label for v in store.views] == ["current-naip-2020-2020"]
    current = [o for o in store.finished[gid]["sources"] if o["kind"] == "current"]
    # The 2010 leaf-off image is ten years older than NAIP 2020 in the same pixel class, so the
    # newer one is next after the state's mosaic.
    assert [(o["source"], o["status"]) for o in current] == [
        ("state", "failed"),
        ("naip-2020", "ok"),
    ]
    assert current[0]["code"] == E.GEO_SOURCE_HTTP
    (incident,) = store.incidents
    assert (incident.code, incident.source_id, incident.http_status) == (
        E.GEO_SOURCE_HTTP,
        "state",
        503,
    )
    assert ("geo.source_failed", current[0]) in store.events
    parcels = [o for o in store.finished[gid]["sources"] if o["kind"] == "parcels"]
    assert parcels[0]["status"] == "none" and parcels[0]["code"] == E.GEO_NO_PARCEL
    assert not [
        e for e, _ in store.events if e == "geo.site_area_checked"
    ]  # no parcel: nothing to check


async def test_no_source_covering_the_point(tmp_path: Path) -> None:
    store, gatherer, gid, _ = setup(tmp_path, Router(), srcs=[source(bbox=[-80, 30, -70, 40])])
    assert await gatherer.run(gid) == "succeeded"
    codes = {o["kind"]: o.get("code") for o in store.finished[gid]["sources"]}
    assert codes == {"parcels": E.GEO_NO_PARCEL, "current": E.GEO_NO_IMAGERY}
    assert store.views == []


async def test_a_retry_skips_what_is_stored_and_an_ended_gather_is_left_alone(
    tmp_path: Path,
) -> None:
    router = servers()
    store, gatherer, gid, _ = setup(tmp_path, router)
    await gatherer.run(gid)
    labels = [v.label for v in store.views]
    store.gathers[gid] = GatherRow(gid, store.gathers[gid].study_id, "running", 3, LAT, LON)
    before = len(router.requests)
    await gatherer.run(gid)
    assert [v.label for v in store.views] == labels  # nothing stored twice
    image_requests = [r for r in router.requests[before:] if "ImageServer" in str(r.url)]
    assert len(image_requests) == 1  # only the blank NAIP 2016, asked again
    assert len(store.parcels[gid]) == 2
    assert await gatherer.run(gid) == "succeeded"  # ended: nothing runs
    assert await gatherer.run(uuid.uuid4()) == "missing"


async def test_lidar_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = GatherConfig(extent_m=9.6, max_px=64, history=False, lidar=True)
    boxes: list[Any] = []

    async def none(_c: Any, _lon: float, _lat: float, box: Any, *, max_nodes: int) -> None:
        boxes.append(box)
        return None

    monkeypatch.setattr(lidar, "summarize", none)
    store, gatherer, gid, _ = setup(tmp_path, servers(), config)
    await gatherer.run(gid)
    (out,) = [o for o in store.finished[gid]["sources"] if o["kind"] == "lidar"]
    assert out["status"] == "none" and out["code"] == E.GEO_NO_LIDAR
    # The box is the parcel's outline plus about 10 m each way.
    w, s, e, n = boxes[0]
    assert (e - w) * 111_412.84 * math.cos(math.radians(LAT)) == pytest.approx(70, abs=1.5)

    async def broken(*_a: Any, **_k: Any) -> None:
        from basis.geo.http import SourceError

        raise SourceError(E.GEO_LIDAR_UNREADABLE, "bad tile", url="https://lidar.test/x.laz")

    monkeypatch.setattr(lidar, "summarize", broken)
    store, gatherer, gid, _ = setup(tmp_path, servers(), config)
    await gatherer.run(gid)
    assert store.incidents[0].source_id == "us-3dep-lidar"
    assert store.incidents[0].code == E.GEO_LIDAR_UNREADABLE

    async def found(*_a: Any, **_k: Any) -> lidar.LidarSummary:
        return lidar.LidarSummary("P_2018", 2018, "u", 10, 100.0, 0.1, 0, 10, None, 1, 5)

    monkeypatch.setattr(lidar, "summarize", found)
    store, gatherer, gid, _ = setup(tmp_path, servers(), config)
    await gatherer.run(gid)
    summary = store.finished[gid]["lidar"]
    assert summary["project"] == "P_2018" and summary["code"] == E.GEO_NO_BUILDING
    assert summary["terms_class"] == "public-domain" and "3DEP" in summary["credit"]


async def test_the_fail_activity_marks_a_running_gather_failed_once(tmp_path: Path) -> None:
    store, gatherer, gid, _ = setup(tmp_path, servers())
    activities = GeoActivities(lambda _beat: gatherer, store)
    store.gathers[gid] = GatherRow(gid, store.gathers[gid].study_id, "running", 3, LAT, LON)
    await activities.fail_public_geo(
        FailInput(gather_id=str(gid), code=E.GEO_GATHER_FAILED, detail="boom")
    )
    row = store.gathers[gid]
    assert (row.status, row.code) == ("failed", E.GEO_GATHER_FAILED)
    assert store.incidents[-1].source_id == "gather"
    await activities.fail_public_geo(
        FailInput(gather_id=str(gid), code=E.GEO_GATHER_FAILED, detail="again")
    )
    assert len(store.incidents) == 1
    await activities.fail_public_geo(
        FailInput(gather_id=str(uuid.uuid4()), code=E.GEO_GATHER_FAILED, detail="x")
    )


def test_labels_and_settings() -> None:
    assert view_label("current", "wi", 2020) == "current-wi-2020"
    assert view_label("current", "naip", None) == "current-naip"
    settings = Settings(env="test")
    assert settings.public_geo is False
    config = GatherConfig.of(settings)
    assert (config.extent_m, config.max_px, config.tolerance_pct) == (160.0, 2048, 5.0)
    assert config.history and config.lidar
    assert settings.geo_task_queue == "basis-geo"
    assert Settings(env="test", geo_task_queue="  ").geo_task_queue == "basis-geo"
    with pytest.raises(ValueError):
        Settings(env="test", geo_site_tolerance_pct=0)
    _ = GatherInput(gather_id=str(uuid.uuid4()))
