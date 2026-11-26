"""ONE real SAM 3 draft through ``POST .../property/site-drafts`` (EVAL_REPORT 111): the route on
the real app and a test database, the real localizer (Replicate, the deployment's version), the
Fairfax County 3-inch ortho of parcel ffx01 (public data, ``$BASIS_EVAL_DATA_DIR/site_truth``)
stored as the study's public view, and the engineer's box round the county's own driveway
outline plus 0.5 m a side (EVAL_REPORT 106.3's "box alone"). It records the run (the answer,
the ledger row, the area against the county's) under
``$BASIS_EVAL_DATA_DIR/verification/site_drafts/route/``.

It spends money, so it never runs by itself: it needs ``BASIS_PAID_RUN=site_draft_route``,
the private data and ``REPLICATE_API_TOKEN`` in ``backEnd/.env`` (or the environment), and is
deselected in CI (``eval_data``). One prediction is about 1 to 3 billed seconds at $0.000975
(EVAL_REPORT 106.6); the route's timeout (30 s) bounds it to $0.03, under the $0.05 approved.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_decisions import study_at

from basis.config import load_settings
from basis.db.enums import Stage
from basis.db.models import GeoGather, GeoView, LlmCall
from basis.db.session import session_scope
from basis.domain import traced
from basis.geo.geometry import lonlat_to_pixel
from basis.storage import LocalStorage
from basis.vision.localizer_config import build_localizer
from evals import record
from evals import site_truth as st

pytestmark = [pytest.mark.integration, pytest.mark.eval_data]

PARCEL, ITEM = "ffx01", "driveway-1"
PAD_M = 0.5
MAX_USD = 0.05
DATA = Path(os.environ.get("BASIS_EVAL_DATA_DIR", str(Path.home() / "basis-eval-data")))
MAIN_ENV = Path(__file__).resolve().parents[2] / ".env"


def _wanted() -> bool:
    return os.environ.get("BASIS_PAID_RUN") == "site_draft_route"


@pytest.mark.skipif(not _wanted(), reason="a paid call: set BASIS_PAID_RUN=site_draft_route")
async def test_one_real_draft_through_the_route(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    seed: Seed,
) -> None:
    env_file = Path(os.environ.get("BASIS_LIVE_ENV_FILE", str(MAIN_ENV)))
    # Only dev reads an env file (basis.config.load_settings); the test session runs as test.
    was = os.environ.get("BASIS_ENV")
    os.environ["BASIS_ENV"] = "dev"
    try:
        real = load_settings(env_file)
    finally:
        if was is None:
            os.environ.pop("BASIS_ENV", None)
        else:
            os.environ["BASIS_ENV"] = was
    localizer = build_localizer(real, timeout_s=real.object_prompt_timeout_s)
    if localizer is None:
        pytest.skip("no localizer configured (REPLICATE_API_TOKEN)")
    views = DATA / "site_truth" / "views" / PARCEL
    meta = json.loads((views / "county.json").read_text())
    image = (views / meta["file"]).read_bytes()
    parcel = st.load(st.FIXTURES / f"{PARCEL}.json")
    item = next(i for i in parcel.items if i.id == ITEM)
    out = (
        DATA
        / "verification"
        / "site_drafts"
        / "route"
        / f"site_draft_route_{datetime.now(UTC):%Y-%m-%dT%H%M%S}"
    )
    rec = record.EvalRun.begin(
        out, harness=__name__, component="site_drafts", version="route", tree=DATA / "verification"
    )
    settings = api_settings(
        database_url, site_drafts="on", sam3_replicate_version=real.sam3_replicate_version
    )
    app = build_api_app(settings, sessionmaker, storage, verifier, localizer=localizer)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE_URL, timeout=90
        ) as client:
            api = Api(client)
            sid = await study_at(api, seed, Stage.ASSETS_REVIEW, revision=2)
            asset = (await seed.outputs(sid, revision=2))["asset"]
            user = await seed.user("alice")
            async with session_scope(sessionmaker) as session:
                gather = GeoGather(
                    study_id=uuid.UUID(sid),
                    requested_by=user.id,
                    status="succeeded",
                    pin_version=1,
                    workflow_id=f"geo-{uuid.uuid4()}",
                    lat=meta["lat"],
                    lon=meta["lon"],
                )
                session.add(gather)
                await session.flush()
                uri = f"gs://test-bucket/studies/{sid}/public/{gather.id}/county.jpg"
                await storage.put(uri, image, "image/jpeg")
                view = GeoView(
                    study_id=uuid.UUID(sid),
                    gather_id=gather.id,
                    source_id=meta["source_id"],
                    kind="current",
                    label="current-county",
                    year=meta["year"],
                    leaf_off=None,
                    lat=meta["lat"],
                    lon=meta["lon"],
                    pixel_size_m=meta["pixel_size_m"],
                    native_pixel_m=0.0762,
                    bbox=meta["bbox"],
                    width=meta["width"],
                    height=meta["height"],
                    uri=uri,
                    content_type="image/jpeg",
                    sha256=record.sha256_file(views / meta["file"]),
                    bytes=len(image),
                    credit="Fairfax County 2023 3-inch orthoimagery",
                    terms_class="public-domain",
                    terms_url="https://www.fairfaxcounty.gov/maps/",
                    captured_at=datetime.now(UTC),
                )
                session.add(view)
                await session.flush()
                view_id = str(view.id)
            px = [
                lonlat_to_pixel(meta["bbox"], meta["width"], meta["height"], lon, lat)
                for lon, lat in item.ring
            ]
            pad = PAD_M / meta["pixel_size_m"]
            box = {
                "x1": max(0.0, min(x for x, _ in px) - pad),
                "y1": max(0.0, min(y for _, y in px) - pad),
                "x2": min(float(meta["width"]), max(x for x, _ in px) + pad),
                "y2": min(float(meta["height"]), max(y for _, y in px) + pad),
            }
            body = {
                "asset_id": asset,
                "view": {"kind": "public", "id": view_id},
                "box": box,
                "family": "driveway",
            }
            response = await api.post("alice", f"/v1/studies/{sid}/property/site-drafts", body)
            async with sessionmaker() as session:
                rows = (
                    await session.scalars(select(LlmCall).where(LlmCall.study_id == uuid.UUID(sid)))
                ).all()
            ledger = [
                {
                    "activity": r.activity,
                    "kind": r.kind,
                    "provider": r.provider,
                    "model": r.model,
                    "status": r.status,
                    "quantity": float(r.quantity or 0),
                    "unit": r.unit,
                    "cost_usd": float(r.cost_usd or 0),
                    "latency_ms": r.latency_ms,
                    "error": r.error,
                }
                for r in rows
            ]
    finally:
        await localizer.client.aclose()  # type: ignore[union-attr]
    answer = response.json()
    (out / "answer.json").write_text(json.dumps(answer, indent=1) + "\n")
    (out / "api_llm_calls.json").write_text(json.dumps(ledger, indent=1) + "\n")
    truth_sf = traced.measure(
        {
            "unit": "SF",
            "space": "lonlat",
            "shapes": [{"kind": "area", "ring": [list(p) for p in item.ring]}],
            "image": {"source": "county outline"},
        }
    ).quantity
    cost = sum(r["cost_usd"] for r in ledger)
    detail: dict[str, Any] = {
        "status_code": response.status_code,
        "status": answer.get("status"),
        "quantity_sf": answer.get("quantity"),
        "truth_sf": format(truth_sf, "f"),
        "signed_pct": None
        if answer.get("quantity") is None
        else round((float(answer["quantity"]) / float(truth_sf) - 1) * 100, 2),
        "expected_error_pct": answer.get("expected_error_pct"),
        "corners": None
        if answer.get("measurement") is None
        else answer["measurement"]["corner_count"],
        "refusal": answer.get("refusal"),
        "box_px": box,
        "cost_usd": cost,
        "calls": len(ledger),
    }
    rec.case(
        f"{PARCEL}/{ITEM}", "passed" if response.status_code == 200 else "failed", detail=detail
    )
    rec.finish({"spent_usd": cost, "parcel": PARCEL, "item": ITEM, **detail})
    print(json.dumps(detail, indent=1))
    assert response.status_code == 200, response.text
    assert len(ledger) == 1 and cost < MAX_USD
