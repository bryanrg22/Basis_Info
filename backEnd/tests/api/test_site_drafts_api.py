"""``POST /v1/studies/{id}/property/site-drafts`` over HTTP (docs/api/MEASURE_DRAFTS.md,
EVAL_REPORT 111), on the real app and database with a scripted segmenter:

* a box on a capture's top-down view is drafted: the measurement record (lon/lat,
  ``drawn_by: model:sam3@site-v1``, ``verified: false``, the view and its date), its quantity in
  the asset's unit, the expected error; one SAM 3 call with the box normalized to the view and
  no phrase; the call in the paid-call ledger (activity ``site_draft``, the asset) and a
  ``measurement.drafted`` event;
* refusals in plain words with their code: nothing found (a call, recorded), a fence and a
  coarse public view (no call, nothing recorded), a call that fails or does not answer in
  ``SITE_DRAFT_TIMEOUT_S`` (recorded as an error);
* refused before any call: ``SITE_DRAFTS`` or ``TRACED_MEASUREMENT`` off (409, after the
  access check), a viewer (403), a stranger (404), a view or asset not the study's (404), the
  gate not open (409), the budget below ``SITE_DRAFT_RESERVE_USD`` (409), no segmenter (503),
  more than ``SITE_DRAFTS_PER_MIN`` (429), a box that is not one (422);
* the same ``Idempotency-Key`` answers again without a second call;
* ``GET /v1/capabilities`` says ``site_drafts`` and ``facade_measure``.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx
import numpy as np
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_decisions import study_at

from basis.db.enums import OrgRole, Stage
from basis.db.models import CaptureView, GeoGather, GeoView, LlmCall, PropertyCapture, StudyEvent
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.geo.geometry import view_box
from basis.storage import LocalStorage
from basis.vision.localize import Box, Instance, LocalizeCall, LocalizerUnavailable, Rle
from basis.vision.prep import PreparedImage
from basis.vision.schemas import ComponentLabel

pytestmark = pytest.mark.integration

W, H, PIXEL_M = 400, 300, 0.084
LAT, LON = 38.87, -77.43
BOX = {"x1": 100, "y1": 80, "x2": 300, "y2": 220}
MASK = (110, 90, 290, 210)  # x1, y1, x2, y2 of the driveway in the prepared image
SQFT_PER_M2 = 10.763910416709722


def png(w: int = W, h: int = H) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), "grey").save(out, format="PNG")
    return out.getvalue()


def driveway(image: PreparedImage) -> Instance:
    m = np.zeros((image.height, image.width), dtype=np.uint8)
    x1, y1, x2, y2 = MASK
    m[y1:y2, x1:x2] = 1
    return Instance(
        label=ComponentLabel.DRIVEWAY,
        phrase="",
        score=0.91,
        box=Box.from_pixels(x1, y1, x2, y2, image.width, image.height),
        mask=Rle.encode(m),
        area=float(m.mean()),
    )


@dataclass
class Sam:
    """Answers every prompt with the driveway (or nothing), records the prompts."""

    found: bool = True
    fail: Exception | None = None
    delay_s: float = 0.0
    provider: str = "fake"
    model: str = "fake-sam3"
    version: str = "v-test"
    usd_per_second: float = 0.000975
    calls: list[list[Any]] = field(default_factory=list)

    async def run_prompts(
        self, image: PreparedImage, prompts: Sequence[Any], *, threshold: float = 0.5
    ) -> tuple[list[list[Instance]], LocalizeCall]:
        self.calls.append(list(prompts))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail is not None:
            raise self.fail
        call = LocalizeCall(
            prompts=tuple(p.name for p in prompts),
            predict_seconds=2.0,
            cost_usd=0.00195,
            status="succeeded",
        )
        return [[driveway(image)] if self.found else [] for _ in prompts], call


@pytest.fixture
def sam() -> Sam:
    return Sam()


def app_api(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    localizer: Any,
    **overrides: Any,
) -> httpx.AsyncClient:
    # The drafts limit is raised out of the way (its own test sets it low, for its own user).
    settings = api_settings(
        database_url,
        **{"site_drafts": "on", "geo_facade": True, "site_drafts_per_min": 120, **overrides},
    )
    app = build_api_app(settings, sessionmaker, storage, verifier, localizer=localizer)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)


@pytest.fixture
async def on(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    sam: Sam,
) -> AsyncIterator[Api]:
    async with app_api(database_url, sessionmaker, storage, verifier, sam) as client:
        yield Api(client)


async def capture_view(
    seed: Seed, storage: LocalStorage, sid: str, *, imagery_on: date | None = date(2025, 6, 2)
) -> str:
    """A stored top-down capture view of the study (the close aerial)."""
    user = await seed.user("alice")
    async with session_scope(seed.sessionmaker) as session:
        capture = PropertyCapture(
            study_id=uuid.UUID(sid),
            requested_by=user.id,
            status="succeeded",
            pin_version=1,
            workflow_id=f"capture-{uuid.uuid4()}",
        )
        session.add(capture)
        await session.flush()
        uri = f"gs://test-bucket/studies/{sid}/captures/{capture.id}/aerial-close.png"
        await storage.put(uri, png(), "image/png")
        view = CaptureView(
            study_id=uuid.UUID(sid),
            capture_id=capture.id,
            pin_version=1,
            kind="aerial",
            label="aerial-close",
            lat=LAT,
            lon=LON,
            ground_alt_m=100,
            range_m=150,
            fov_deg=35,
            heading_deg=0,
            tilt_deg=0,
            pixel_size_m=PIXEL_M,
            width=W,
            height=H,
            uri=uri,
            sha256="b" * 64,
            bytes=1,
            source="Aerial imagery",
            captured_at=datetime.now(UTC),
            imagery_on=imagery_on,
        )
        session.add(view)
        await session.flush()
        return str(view.id)


async def coarse_public_view(seed: Seed, storage: LocalStorage, sid: str) -> str:
    user = await seed.user("alice")
    box = view_box(LAT, LON, extent_m=160, pixel_m=0.6, max_px=2048)
    async with session_scope(seed.sessionmaker) as session:
        gather = GeoGather(
            study_id=uuid.UUID(sid),
            requested_by=user.id,
            status="succeeded",
            pin_version=1,
            workflow_id=f"geo-{uuid.uuid4()}",
            lat=LAT,
            lon=LON,
        )
        session.add(gather)
        await session.flush()
        uri = f"gs://test-bucket/studies/{sid}/public/{gather.id}/current-naip.png"
        await storage.put(uri, png(box.width, box.height), "image/png")
        view = GeoView(
            study_id=uuid.UUID(sid),
            gather_id=gather.id,
            source_id="naip",
            kind="current",
            label="current-naip",
            year=2023,
            leaf_off=False,
            lat=LAT,
            lon=LON,
            pixel_size_m=box.pixel_size_m,
            native_pixel_m=0.6,
            bbox=list(box.bbox),
            width=box.width,
            height=box.height,
            uri=uri,
            content_type="image/png",
            sha256="c" * 64,
            bytes=1,
            credit="USDA NAIP",
            terms_class="public-domain",
            terms_url="https://naip.usda.gov",
            captured_at=datetime.now(UTC),
        )
        session.add(view)
        await session.flush()
        return str(view.id)


async def at_assets(api: Api, seed: Seed, storage: LocalStorage) -> tuple[str, str, str]:
    sid = await study_at(api, seed, Stage.ASSETS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    return sid, targets["asset"], await capture_view(seed, storage, sid)


def draft_body(asset: str, view_id: str, **over: Any) -> dict[str, Any]:
    return {
        "asset_id": asset,
        "view": {"kind": "capture", "id": view_id},
        "box": BOX,
        "family": "driveway",
        **over,
    }


async def draft(api: Api, sid: str, body: dict[str, Any], uid: str = "alice", **kw: Any) -> Any:
    return await api.post(uid, f"/v1/studies/{sid}/property/site-drafts", body, **kw)


async def ledger(sessionmaker: async_sessionmaker[AsyncSession], sid: str) -> list[LlmCall]:
    async with sessionmaker() as session:
        rows = await session.scalars(select(LlmCall).where(LlmCall.study_id == uuid.UUID(sid)))
        return list(rows.all())


async def drafted_events(
    sessionmaker: async_sessionmaker[AsyncSession], sid: str
) -> list[StudyEvent]:
    async with sessionmaker() as session:
        rows = await session.scalars(
            select(StudyEvent).where(
                StudyEvent.study_id == uuid.UUID(sid),
                StudyEvent.event_type == "measurement.drafted",
            )
        )
        return list(rows.all())


async def test_a_box_is_drafted_recorded_and_answered_as_a_measurement(
    on: Api,
    seed: Seed,
    storage: LocalStorage,
    sam: Sam,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sid, asset, view = await at_assets(on, seed, storage)
    response = await draft(on, sid, draft_body(asset, view))
    assert response.status_code == 200, response.text
    got = response.json()
    assert (got["status"], got["family"], got["unit"], got["refusal"]) == (
        "drafted",
        "driveway",
        "SF",
        None,
    )
    m = got["measurement"]
    assert (m["space"], m["unit"], m["drawn_by"], m["verified"]) == (
        "lonlat",
        "SF",
        "model:sam3@site-v1",
        False,
    )
    assert m["view"] == {
        "kind": "capture",
        "width": W,
        "height": H,
        "id": view,
        "label": "aerial-close",
    }
    assert m["image"]["captured_on"] == "2025-06-02" and m["image"]["pixel_size_m"] == PIXEL_M
    truth = (MASK[2] - MASK[0]) * (MASK[3] - MASK[1]) * PIXEL_M**2 * SQFT_PER_M2
    assert float(got["quantity"]) == pytest.approx(truth, abs=0.006)
    assert got["quantity"] == m["quantity"]
    # The method's 90th percentile for driveways (EVAL_REPORT 106.3) is above the one-pixel bound.
    assert got["expected_error_pct"] == "33"
    [prompts] = sam.calls
    [prompt] = prompts
    assert prompt.text is None and prompt.points == ()
    assert prompt.boxes[0].model_dump() == pytest.approx(
        {"x1": 0.25, "y1": 80 / 300, "x2": 0.75, "y2": 220 / 300}
    )
    [row] = await ledger(sessionmaker, sid)
    assert (row.activity, row.kind, row.status, row.entity_type, str(row.entity_id)) == (
        "site_draft",
        "localization",
        "ok",
        "asset",
        asset,
    )
    assert float(row.cost_usd) == pytest.approx(0.00195) and row.run_id is None
    [event] = await drafted_events(sessionmaker, sid)
    assert (event.actor_type, str(event.entity_id), event.code) == ("engineer", asset, None)
    assert event.detail["status"] == "drafted" and event.detail["quantity"] == got["quantity"]


async def test_the_draft_is_in_the_assets_unit(on: Api, seed: Seed, storage: LocalStorage) -> None:
    sid, asset, view = await at_assets(on, seed, storage)
    sf = (await draft(on, sid, draft_body(asset, view))).json()
    sy = (await draft(on, sid, draft_body(asset, view, unit="SY"))).json()
    assert sy["unit"] == "SY" and sy["measurement"]["unit"] == "SY"
    assert float(sy["quantity"]) == pytest.approx(float(sf["quantity"]) / 9, abs=0.006)
    assert sy["measurement"]["shapes"] == sf["measurement"]["shapes"]


async def test_nothing_found_is_a_recorded_refusal_in_plain_words(
    on: Api,
    seed: Seed,
    storage: LocalStorage,
    sam: Sam,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sid, asset, view = await at_assets(on, seed, storage)
    sam.found = False
    got = (await draft(on, sid, draft_body(asset, view))).json()
    assert got["status"] == "refused" and got["measurement"] is None
    assert got["refusal"]["code"] == E.SITE_NOTHING_FOUND
    assert got["refusal"]["message"].startswith("Basis found nothing to outline in the box.")
    assert "SAM" not in got["refusal"]["message"]
    [row] = await ledger(sessionmaker, sid)
    assert row.status == "ok"  # the call was made and billed
    [event] = await drafted_events(sessionmaker, sid)
    assert event.code == E.SITE_NOTHING_FOUND


async def test_a_fence_and_a_coarse_view_are_refused_with_no_call(
    on: Api,
    seed: Seed,
    storage: LocalStorage,
    sam: Sam,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sid, asset, view = await at_assets(on, seed, storage)
    fence = (await draft(on, sid, draft_body(asset, view, family="fence"))).json()
    assert fence["refusal"]["code"] == E.SITE_NOT_DRAFTED
    assert fence["refusal"]["message"].startswith("Fences are not drafted from above")
    public = await coarse_public_view(seed, storage, sid)
    coarse = (
        await draft(
            on,
            sid,
            draft_body(
                asset,
                public,
                view={"kind": "public", "id": public},
                box={"x1": 20, "y1": 20, "x2": 120, "y2": 100},  # inside its 267 px
            ),
        )
    ).json()
    assert coarse["refusal"]["code"] == E.SITE_VIEW_TOO_COARSE
    assert "too coarse to draft on (about 60 cm a pixel)" in coarse["refusal"]["message"]
    assert sam.calls == [] and await ledger(sessionmaker, sid) == []


async def test_a_view_that_is_not_top_down_is_refused_with_no_call(
    on: Api,
    seed: Seed,
    storage: LocalStorage,
    sam: Sam,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sid, asset, view = await at_assets(on, seed, storage)
    async with session_scope(sessionmaker) as session:
        row = await session.get(CaptureView, uuid.UUID(view))
        assert row is not None
        oblique = CaptureView(
            **{
                c.name: getattr(row, c.name)
                for c in CaptureView.__table__.columns
                if c.name not in ("id", "created_at")
            }
        )
        oblique.kind, oblique.label, oblique.tilt_deg, oblique.heading_deg = (
            "oblique",
            "side-south",
            60.0,
            0.0,
        )
        oblique.pixel_size_m = None
        session.add(oblique)
        await session.flush()
        oblique_id = str(oblique.id)
    got = (await draft(on, sid, draft_body(asset, oblique_id))).json()
    assert got["refusal"]["code"] == E.SITE_NOT_TOP_DOWN
    assert got["refusal"]["message"].startswith("This picture is not a straight-down view")
    assert sam.calls == [] and await ledger(sessionmaker, sid) == []


@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_a_failed_or_slow_call_is_refused_and_recorded_as_an_error(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    seed: Seed,
    failure: str,
) -> None:
    sam = (
        Sam(fail=LocalizerUnavailable("throttled", status=429))
        if failure == "error"
        else Sam(delay_s=1.0)
    )
    async with app_api(
        database_url, sessionmaker, storage, verifier, sam, site_draft_timeout_s=0.2
    ) as client:
        api = Api(client)
        sid, asset, view = await at_assets(api, seed, storage)
        got = (await draft(api, sid, draft_body(asset, view))).json()
    assert got["refusal"]["code"] == E.SITE_SEGMENTER_FAILED
    assert got["refusal"]["message"] == (
        "Drafting is unavailable right now. Trace it by hand, or try again in a minute."
    )
    [row] = await ledger(sessionmaker, sid)
    assert row.status == "error" and row.error


async def test_off_is_409_after_the_access_check(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    seed: Seed,
    sam: Sam,
) -> None:
    for overrides in ({"site_drafts": "off"}, {"traced_measurement": False}):
        async with app_api(
            database_url, sessionmaker, storage, verifier, sam, **overrides
        ) as client:
            api = Api(client)
            sid, asset, view = await at_assets(api, seed, storage)
            off = await draft(api, sid, draft_body(asset, view))
            assert off.status_code == 409 and off.json()["code"] == E.MEASURE_SITE_DRAFTS_OFF
            stranger = await draft(api, sid, draft_body(asset, view), uid="mallory")
            assert stranger.status_code == 404
            caps = (await api.get("alice", "/v1/capabilities")).json()
            assert caps["site_drafts"] is False
    assert sam.calls == []


async def test_roles_views_assets_and_the_gate(
    on: Api, seed: Seed, storage: LocalStorage, sam: Sam
) -> None:
    sid, asset, view = await at_assets(on, seed, storage)
    study = (await on.get("alice", f"/v1/studies/{sid}")).json()
    await seed.member(uuid.UUID(study["org_id"]), "victor", OrgRole.VIEWER)
    assert (await draft(on, sid, draft_body(asset, view), uid="victor")).status_code == 403
    assert (await draft(on, sid, draft_body(asset, view), uid="mallory")).status_code == 404
    unknown_view = await draft(on, sid, draft_body(asset, str(uuid.uuid4())))
    assert unknown_view.status_code == 404 and unknown_view.json()["code"] == (
        E.MEASURE_VIEW_NOT_FOUND
    )
    other_sid, other_asset, other_view = await at_assets(on, seed, storage)
    foreign_view = await draft(on, sid, draft_body(asset, other_view))
    assert foreign_view.json()["code"] == E.MEASURE_VIEW_NOT_FOUND
    foreign_asset = await draft(on, sid, draft_body(other_asset, view))
    assert foreign_asset.status_code == 404
    assert foreign_asset.json()["code"] == E.MEASURE_ASSET_NOT_FOUND
    old = (await seed.outputs(sid, revision=1))["asset"]
    stale = await draft(on, sid, draft_body(old, view))
    assert stale.json()["code"] == E.MEASURE_ASSET_NOT_FOUND
    elsewhere = await study_at(on, seed, Stage.COMPONENTS_REVIEW, revision=2)
    targets = await seed.outputs(elsewhere, revision=2)
    closed_view = await capture_view(seed, storage, elsewhere)
    closed = await draft(on, elsewhere, draft_body(targets["asset"], closed_view))
    assert closed.status_code == 409 and closed.json()["code"] == E.MEASURE_GATE_NOT_OPEN
    bad_box = await draft(on, sid, draft_body(asset, view, box={**BOX, "x2": 90}))
    assert bad_box.status_code == 422
    outside = await draft(on, sid, draft_body(asset, view, box={**BOX, "x2": W + 1}))
    assert outside.status_code == 422 and "outside the 400 x 300 view" in outside.json()["detail"]
    assert sam.calls == []


async def test_the_budget_and_a_missing_segmenter_refuse_before_a_call(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    seed: Seed,
    sam: Sam,
) -> None:
    async with app_api(
        database_url, sessionmaker, storage, verifier, sam, study_budget_usd=0.04
    ) as client:
        api = Api(client)
        sid, asset, view = await at_assets(api, seed, storage)
        poor = await draft(api, sid, draft_body(asset, view))
        assert poor.status_code == 409 and poor.json()["code"] == E.MEASURE_BUDGET_EXHAUSTED
        assert "under the $0.05 a draft needs" in poor.json()["detail"]
    async with app_api(database_url, sessionmaker, storage, verifier, None) as client:
        api = Api(client)
        sid, asset, view = await at_assets(api, seed, storage)
        none = await draft(api, sid, draft_body(asset, view))
        assert none.status_code == 503
        assert none.json()["code"] == E.MEASURE_DRAFTING_UNAVAILABLE
        assert "replicate" not in none.text.lower() and "sam" not in none.json()["detail"].lower()
    assert sam.calls == []


async def test_drafts_a_minute_are_limited_per_person(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    seed: Seed,
    sam: Sam,
) -> None:
    async with app_api(
        database_url, sessionmaker, storage, verifier, sam, site_drafts_per_min=2
    ) as client:
        api = Api(client)
        rita = f"rita-{uuid.uuid4().hex[:8]}"  # a person of her own: the limit is per person
        sid = await study_at(api, seed, Stage.ASSETS_REVIEW, revision=2, uid=rita)
        asset = (await seed.outputs(sid, revision=2))["asset"]
        view = await capture_view(seed, storage, sid)
        for _ in range(2):
            assert (await draft(api, sid, draft_body(asset, view), uid=rita)).status_code == 200
        third = await draft(api, sid, draft_body(asset, view), uid=rita)
        assert third.status_code == 429 and third.json()["code"] == E.MEASURE_DRAFTS_RATE_LIMITED
        assert int(third.headers["Retry-After"]) >= 1
    assert len(sam.calls) == 2


async def test_a_replayed_key_answers_again_without_a_second_call(
    on: Api,
    seed: Seed,
    storage: LocalStorage,
    sam: Sam,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sid, asset, view = await at_assets(on, seed, storage)
    key = str(uuid.uuid4())
    first = await draft(on, sid, draft_body(asset, view), key=key)
    again = await draft(on, sid, draft_body(asset, view), key=key)
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert len(sam.calls) == 1 and len(await ledger(sessionmaker, sid)) == 1


async def test_capabilities_report_both_measure_flags(on: Api) -> None:
    caps = (await on.get("alice", "/v1/capabilities")).json()
    assert (caps["site_drafts"], caps["facade_measure"]) == (True, True)
