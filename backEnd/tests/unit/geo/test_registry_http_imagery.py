"""The registry and its ranking, the polite client, and image fetching
(basis.geo.registry, basis.geo.http, basis.geo.imagery; EVAL_REPORT 101). HTTP is mocked."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from geotest_support import Router, jpeg, png, source

from basis.errors.codes import E
from basis.geo import imagery, registry
from basis.geo.geometry import pixel_to_lonlat
from basis.geo.http import PoliteClient, SourceError

LAT, LON = 41.60, -93.60


def test_the_shipped_registry_loads_and_is_consistent() -> None:
    sources = registry.load()
    assert len(sources) >= 100
    ids = [s.id for s in sources]
    assert len(ids) == len(set(ids))
    for s in sources:
        assert s.terms_quote and s.terms_url.startswith("http") and s.credit
        if s.status == "available" and s.kind == "imagery":
            assert s.native_pixel_m is not None and s.protocol != "unsupported"
            assert s.year is not None or s.year_query is not None or s.year_label
        if s.status == "unavailable":
            assert s.status_reason and s.checked_on
    states = {st for s in sources for st in s.states}
    for wanted in (
        "US",
        "WI",
        "MN",
        "TX",
        "FL",
        "CT",
        "IL",
        "IN",
        "KY",
        "MA",
        "MD",
        "MO",
        "NC",
        "NJ",
        "OH",
        "PA",
    ):
        assert wanted in states
    # At the Wisconsin capitol (a public point) the state's leaf-off mosaic ranks first.
    ranked = registry.rank(registry.covering(sources, 43.07472, -89.38417, "imagery"))
    assert ranked[0].id == "wi-leafoff-latest"


def test_the_registry_refuses_duplicates_and_parcels_without_fields() -> None:
    entry = source().model_dump()
    with pytest.raises(ValueError, match="duplicate"):
        registry.parse({"sources": [entry, entry]})
    with pytest.raises(ValueError, match="fields"):
        registry.parse({"sources": [{**entry, "kind": "parcels", "protocol": "arcgis-feature"}]})
    with pytest.raises(ValueError):
        registry.parse({"sources": [{**entry, "bbox": [10, 0, 5, 1]}]})
    with pytest.raises(ValueError):
        registry.parse({"nothing": []})


def test_covering_needs_the_box_the_kind_and_availability() -> None:
    inside = source(id="in")
    outside = source(id="out", bbox=[-80, 30, -70, 40])
    down = source(id="down", status="unavailable", status_reason="x", checked_on="2026-11-23")
    parcels = source(id="p", kind="parcels", protocol="arcgis-feature", fields={"id": "PID"})
    found = registry.covering([inside, outside, down, parcels], LAT, LON, "imagery")
    assert [s.id for s in found] == ["in"]
    assert [s.id for s in registry.covering([parcels], LAT, LON, "parcels")] == ["p"]


def test_ranking_pixel_first_then_leaf_off_within_four_years_then_recency() -> None:
    naip_new = source(id="naip", native_pixel_m=0.6, year=2024, leaf_off=False)
    sharp_old = source(id="sharp", native_pixel_m=0.15, year=2016, leaf_off=False)
    fair = source(id="fair", native_pixel_m=0.30, year=2025, leaf_off=True)
    assert [s.id for s in registry.rank([naip_new, fair, sharp_old])] == ["sharp", "fair", "naip"]
    leaf_on_new = source(id="on", native_pixel_m=0.0762, year=2024, leaf_off=False)
    leaf_off_3y = source(id="off3", native_pixel_m=0.15, year=2021, leaf_off=True)
    leaf_off_5y = source(id="off5", native_pixel_m=0.15, year=2019, leaf_off=True)
    assert [s.id for s in registry.rank([leaf_on_new, leaf_off_3y])] == ["off3", "on"]
    assert [s.id for s in registry.rank([leaf_on_new, leaf_off_5y])] == ["on", "off5"]
    unknown = source(id="unknown", native_pixel_m=0.15, year=2024, leaf_off=None)
    off = source(id="off", native_pixel_m=0.15, year=2022, leaf_off=True)
    assert [s.id for s in registry.rank([unknown, off])] == ["off", "unknown"]
    # Same class, season and year: the finer pixel, then the id.
    a = source(id="b", native_pixel_m=0.0762, year=2024)
    b = source(id="a", native_pixel_m=0.15, year=2024)
    c = source(id="c", native_pixel_m=0.15, year=2024)
    assert [s.id for s in registry.rank([c, b, a])] == ["b", "a", "c"]
    # A mosaic's year read at the point decides its place.
    mosaic = source(
        id="mosaic",
        native_pixel_m=0.15,
        year=None,
        year_query={"url": "u", "field": "Y"},
        year_label="by county",
        leaf_off=True,
    )
    other = source(id="other", native_pixel_m=0.15, year=2022, leaf_off=True)
    assert registry.rank([other, mosaic], {"mosaic": 2024})[0].id == "mosaic"
    assert registry.rank([other, mosaic], {"mosaic": 2010})[0].id == "other"


def test_history_plan_is_one_list_per_year_newest_first() -> None:
    plan = registry.history_plan(
        registry.rank(
            [
                source(id="naip22", native_pixel_m=0.6, year=2022),
                source(id="state22", native_pixel_m=0.15, year=2022),
                source(id="naip18", native_pixel_m=0.6, year=2018),
                source(id="mosaic", year=None, year_query={"url": "u", "field": "Y"}),
                source(id="undated", year=None, year_label="unknown"),
            ]
        ),
        {"mosaic": 2020},
    )
    assert [(y, [s.id for s in ss]) for y, ss in plan] == [
        (2022, ["state22", "naip22"]),
        (2020, ["mosaic"]),
        (2018, ["naip18"]),
    ]


async def test_the_client_retries_5xx_and_429_with_backoff_and_retry_after() -> None:
    calls = {"n": 0}

    def flaky(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        if calls["n"] == 2:
            return httpx.Response(429, headers={"retry-after": "30"})
        return httpx.Response(200, content=b"ok")

    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)

    client = PoliteClient(
        transport=httpx.MockTransport(flaky), sleep=sleep, host_interval_s=0, jitter=lambda: 0.5
    )
    got = await client.get("https://a.example.test/x")
    assert got.content == b"ok" and got.attempts == 3
    assert sleeps == [2.0, 30.0]  # 2 s, then the server's 30 s over the 4 s backoff
    await client.aclose()


async def test_the_client_gives_up_and_does_not_retry_other_4xx() -> None:
    router = Router()
    router.on("/gone", httpx.Response(404))
    router.on("/down", httpx.Response(502))
    client = router.client(retries=2)
    with pytest.raises(SourceError) as gone:
        await client.get("https://a.example.test/gone")
    assert gone.value.code == E.GEO_SOURCE_HTTP and gone.value.status == 404
    assert len(router.requests) == 1
    with pytest.raises(SourceError) as down:
        await client.get("https://a.example.test/down")
    assert down.value.status == 502 and len(router.requests) == 4


async def test_the_client_maps_timeouts_and_connection_errors() -> None:
    def timeout(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=r)

    def refused(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no", request=r)

    async def no_sleep(_s: float) -> None:
        return None

    for handler, code in ((timeout, E.GEO_SOURCE_TIMEOUT), (refused, E.GEO_SOURCE_HTTP)):
        client = PoliteClient(transport=httpx.MockTransport(handler), sleep=no_sleep, retries=1)
        with pytest.raises(SourceError) as err:
            await client.get("https://a.example.test/")
        assert err.value.code == code


async def test_the_client_spaces_requests_per_host_only() -> None:
    now = {"t": 100.0}
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(round(s, 3))
        now["t"] += s

    client = PoliteClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
        sleep=sleep,
        clock=lambda: now["t"],
        host_interval_s=1.0,
    )
    await client.get("https://a.example.test/1")
    await client.get("https://a.example.test/2")
    await client.get("https://b.example.test/1")
    now["t"] += 5
    await client.get("https://a.example.test/3")
    assert sleeps == [1.0]


def params(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}


async def test_each_protocol_asks_for_the_same_web_mercator_box() -> None:
    router = Router()
    router.on("exportImage", jpeg(64, 64))
    router.on("/export", jpeg(64, 64))
    router.on("wms", jpeg(64, 64))
    client = router.client()
    cases: list[tuple[Any, dict[str, str]]] = [
        (
            source(params={"bandIds": "0,1,2"}),
            {"bboxSR": "3857", "imageSR": "3857", "format": "jpgpng", "bandIds": "0,1,2"},
        ),
        (
            source(
                protocol="arcgis-mapserver",
                url="https://m.example.test/MapServer",
                params={"layers": "show:3"},
            ),
            {"bboxSR": "3857", "format": "jpg", "layers": "show:3", "transparent": "false"},
        ),
        (
            source(
                protocol="wms",
                url="https://w.example.test/wms",
                params={"VERSION": "1.1.1", "LAYERS": "L"},
            ),
            {"SRS": "EPSG:3857", "VERSION": "1.1.1", "LAYERS": "L", "REQUEST": "GetMap"},
        ),
        (
            source(protocol="wms", url="https://w.example.test/wms13", params={"LAYERS": "L"}),
            {"CRS": "EPSG:3857", "VERSION": "1.3.0"},
        ),
    ]
    boxes = []
    for src, wanted in cases:
        image = await imagery.fetch(client, src, LAT, LON, extent_m=9.6, max_px=64)
        sent = params(router.requests[-1])
        assert wanted.items() <= sent.items(), (src.protocol, sent)
        boxes.append(sent.get("bbox") or sent.get("BBOX"))
        assert not image.blank and image.content_type == "image/jpeg"
        assert (image.box.width, image.box.height) == (64, 64)
        assert image.box.pixel_size_m == pytest.approx(0.15)
    assert len(set(boxes)) == 1
    with pytest.raises(SourceError):
        await imagery.fetch(
            client, source(protocol="arcgis-feature"), LAT, LON, extent_m=10, max_px=64
        )


async def test_a_fetched_image_is_georeferenced_on_the_pin() -> None:
    router = Router()
    router.on("exportImage", jpeg(1050, 1050))
    image = await imagery.fetch(
        router.client(), source(native_pixel_m=0.1524), LAT, LON, extent_m=160, max_px=2048
    )
    box = image.box
    assert (box.width, box.pixel_size_m) == (1050, pytest.approx(160 / 1050))
    centre = pixel_to_lonlat(box.bbox, box.width, box.height, box.width / 2, box.height / 2)
    assert centre == pytest.approx((LON, LAT), abs=1e-9)
    sent = [float(v) for v in params(router.requests[-1])["bbox"].split(",")]
    assert sent == pytest.approx(list(box.bbox), abs=1e-3)


async def test_blank_answers_mean_outside_coverage() -> None:
    router = Router()
    router.on("/transparent/", png(64, 64, transparent=True))
    router.on("/white/", jpeg(64, 64, flat=True))
    router.on("/real/", png(64, 64))
    client = router.client()
    for name, blank in (("transparent", True), ("white", True), ("real", False)):
        src = source(url=f"https://i.example.test/{name}/ImageServer")
        image = await imagery.fetch(client, src, LAT, LON, extent_m=9.6, max_px=64)
        assert image.blank is blank, name
    assert image.content_type == "image/png"


async def test_error_documents_and_undecodable_images_are_bad_responses() -> None:
    router = Router()
    router.on("/json/", {"error": {"code": 400, "message": "Invalid or missing input parameters."}})
    router.on(
        "/broken/",
        httpx.Response(200, content=b"\xff\xd8not a jpeg", headers={"content-type": "image/jpeg"}),
    )
    client = router.client()
    for name in ("json", "broken"):
        with pytest.raises(SourceError) as err:
            await imagery.fetch(
                client,
                source(url=f"https://i.example.test/{name}/ImageServer"),
                LAT,
                LON,
                extent_m=9.6,
                max_px=64,
            )
        assert err.value.code == E.GEO_SOURCE_BAD_RESPONSE


async def test_a_server_that_sends_another_size_keeps_the_box() -> None:
    router = Router()
    router.on("exportImage", jpeg(32, 32))
    image = await imagery.fetch(router.client(), source(), LAT, LON, extent_m=9.6, max_px=64)
    assert (image.box.width, image.box.pixel_size_m) == (32, pytest.approx(0.3))


async def test_tiles_are_stitched_and_cut_to_the_box() -> None:
    router = Router()
    tile = png(256, 256)
    router.on("/tile/", tile)
    src = source(
        protocol="tiles",
        url="https://t.example.test/tile/{z}/{y}/{x}",
        params={"max_zoom": "20"},
        native_pixel_m=0.15,
    )
    image = await imagery.fetch(router.client(), src, LAT, LON, extent_m=40, max_px=2048)
    assert not image.blank and image.content_type == "image/jpeg"
    zooms = {str(r.url).split("/tile/")[1].split("/")[0] for r in router.requests}
    assert zooms == {"20"}
    x0, y0, x1, y1 = imagery.tile_range(image.box, 20)
    assert len(router.requests) == (x1 - x0 + 1) * (y1 - y0 + 1)
    assert image.box.width == round(40 / 0.15) or image.box.pixel_size_m >= 0.15
    # A tile the cache lacks (404) is outside coverage; all missing is blank.
    empty = Router()
    gone = await imagery.fetch(empty.client(), src, LAT, LON, extent_m=40, max_px=2048)
    assert gone.blank


def test_tile_zoom_is_the_coarsest_no_coarser_than_the_pixel() -> None:
    assert imagery.tile_zoom(0.2, 20) == 20  # z19 is 0.2986 m, too coarse
    assert imagery.tile_zoom(0.3, 20) == 19
    assert imagery.tile_zoom(0.01, 20) == 20
    assert imagery.tile_zoom(1e6, 20) == 0


async def test_the_year_at_the_point() -> None:
    router = Router()
    router.on("/int/", {"features": [{"attributes": {"Y": 2018}}, {"attributes": {"Y": 2020}}]})
    router.on("/name/", {"features": [{"attributes": {"Name": "COLUMBUS_2025_3in"}}]})
    router.on("/none/", {"features": []})
    router.on("/err/", {"error": {"code": 500}})
    client = router.client()
    for frag, field, want in (("int", "Y", 2020), ("name", "Name", 2025), ("none", "Y", 1999)):
        src = source(
            year=1999, year_query={"url": f"https://q.example.test/{frag}/query", "field": field}
        )
        assert await imagery.year_at(client, src, LAT, LON) == want
    assert await imagery.year_at(client, source(year=2011), LAT, LON) == 2011
    with pytest.raises(SourceError):
        await imagery.year_at(
            client,
            source(year_query={"url": "https://q.example.test/err/query", "field": "Y"}),
            LAT,
            LON,
        )
    sent = params(router.requests[0])
    assert sent["geometry"] == f"{LON},{LAT}" and sent["inSR"] == "4326"
    assert json.loads(json.dumps(sent))["returnGeometry"] == "false"
