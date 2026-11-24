"""A traced measurement on the decision and draft routes (EVAL_REPORT 97, OVERRIDES.md 4).

Accepted alone, with a classification and with a unit price, in either space, and stored
on the correction row as sent; refused with a 422 at ``after`` before anything is written:
a bad shape (each problem named), with the typed quantity's fields, on another entity type,
with a document of another study (``correction_target``), and while ``TRACED_MEASUREMENT``
is off (the typed quantity unchanged); a gate draft is checked the same way; a viewer is
403, a non-member 404, a stale revision 409; ``GET /v1/capabilities`` reports the setting."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_decisions import assert_nothing_written, decision, study_at

from basis.db.enums import OrgRole, Stage
from basis.db.models import Correction
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

PLAN: dict[str, Any] = {
    "unit": "SF",
    "space": "image",
    "shapes": [{"kind": "area", "ring": [[0, 0], [100, 0], [100, 200], [0, 200]]}],
    "scale": {"points": [[0, 0], [100, 0]], "length": 20, "unit": "ft"},
    "image": {"source": "site plan sheet C1", "captured_on": "2020-04-18"},
}
AERIAL: dict[str, Any] = {
    "unit": "LF",
    "space": "lonlat",
    "shapes": [{"kind": "line", "path": [[-88.9, 43.8], [-88.8995, 43.8], [-88.8995, 43.8004]]}],
    "image": {
        "source": "State orthoimagery 2020",
        "captured_on": "2020-04-18",
        "pixel_size_m": 0.15,
    },
}


@pytest.fixture
async def off_api(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> AsyncIterator[Api]:
    settings = api_settings(database_url, traced_measurement=False)
    app = build_api_app(settings, sessionmaker, storage, verifier)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        yield Api(client)


def asset(target: str, after: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
    return {"entity_type": "asset", "entity_id": target, "after": after, "reason": reason}


async def reject(
    api: Api, sid: str, corrections: list[dict[str, Any]], uid: str = "alice"
) -> httpx.Response:
    return await api.post(
        uid,
        f"/v1/studies/{sid}/decisions",
        decision("assets_review", 2, "reject", corrections=corrections),
    )


async def at_assets(api: Api, seed: Seed) -> tuple[str, dict[str, str]]:
    sid = await study_at(api, seed, Stage.ASSETS_REVIEW, revision=2)
    return sid, await seed.outputs(sid, revision=2)


@pytest.mark.parametrize(
    "after",
    [
        {"measurement": PLAN},
        {"measurement": AERIAL},
        {"measurement": PLAN, "bucket": "y15", "recovery_period": 15},
        {"measurement": PLAN, "unit_cost": 6.5, "unit_cost_source": "paving quote 118"},
    ],
    ids=["image", "lonlat", "with a classification", "with a unit price"],
)
async def test_a_measurement_is_accepted_and_stored_as_sent(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], after: dict[str, Any]
) -> None:
    sid, targets = await at_assets(api, seed)
    reason = "quote on file" if "unit_cost" in after else None
    response = await reject(api, sid, [asset(targets["asset"], after, reason)])
    assert response.status_code == 202, response.text
    async with sessionmaker() as session:
        [row] = (await session.scalars(select(Correction))).all()
    assert row.after == after


async def test_the_document_it_names_must_be_a_document_of_the_study(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, targets = await at_assets(api, seed)
    own = {**PLAN, "image": {**PLAN["image"], "document_id": targets["photo"]}}
    other_sid = await study_at(api, seed, Stage.ASSETS_REVIEW, revision=2)
    other = (await seed.outputs(other_sid, revision=2))["photo"]
    foreign = {**PLAN, "image": {**PLAN["image"], "document_id": other}}
    unknown = {**PLAN, "image": {**PLAN["image"], "document_id": str(uuid.uuid4())}}
    for bad in (foreign, unknown):
        response = await reject(
            api,
            sid,
            [
                asset(targets["asset"], {"bucket": "y5"}),
                asset(targets["asset"], {"measurement": bad}),
            ],
        )
        assert response.status_code == 422, response.text
        [error] = response.json()["errors"]
        assert (error["loc"], error["type"]) == (
            ["body", "corrections", 1, "after"],
            "correction_target",
        )
        assert "is not a document of this study" in error["detail"]
        await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)
    assert (
        await reject(api, sid, [asset(targets["asset"], {"measurement": own})])
    ).status_code == 202


@pytest.mark.parametrize(
    ("after", "message"),
    [
        ({"measurement": {**PLAN, "unit": "EA"}}, "measurement.unit must be SF, SY or LF"),
        ({"measurement": {**PLAN, "scale": None}}, "measurement.scale is required for an image"),
        (
            {
                "measurement": {
                    **PLAN,
                    "shapes": [{"kind": "area", "ring": [[0, 0], [100, 100], [100, 0], [0, 100]]}],
                }
            },
            "measurement.shapes[0].ring crosses or touches itself",
        ),
        ({"measurement": {**PLAN, "colour": "red"}}, "measurement has unknown keys: colour"),
        (
            {
                "measurement": PLAN,
                "quantity": "800",
                "quantity_unit": "SF",
                "quantity_source": "plan",
            },
            "one way to state a quantity",
        ),
        ({"measurement": PLAN, "quantity": "800"}, "one way to state a quantity"),
        ({"measurement": "800 SF"}, "measurement must be an object"),
    ],
    ids=[
        "unit",
        "no scale",
        "crossing",
        "unknown key",
        "with the triple",
        "with a quantity",
        "a string",
    ],
)
async def test_a_bad_measurement_is_422_at_after_and_nothing_is_written(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    after: dict[str, Any],
    message: str,
) -> None:
    sid, targets = await at_assets(api, seed)
    response = await reject(
        api, sid, [asset(targets["asset"], {"bucket": "y5"}), asset(targets["asset"], after)]
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (
        ["body", "corrections", 1, "after"],
        "correction_value",
    )
    assert message in error["detail"]
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)


async def test_a_measurement_with_nan_or_nul_is_refused_by_the_schema(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, targets = await at_assets(api, seed)
    body = decision(
        "assets_review", 2, "reject", corrections=[asset(targets["asset"], {"measurement": PLAN})]
    )
    text = json.dumps(body).replace('"length": 20', '"length": NaN')
    assert "NaN" in text
    response = await api.client.post(
        f"/v1/studies/{sid}/decisions",
        content=text,
        headers={
            "Authorization": "Bearer uid:alice",
            "Idempotency-Key": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 422, response.text
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)
    nul = {**PLAN, "image": {"source": "plan\u0000"}}
    response = await reject(api, sid, [asset(targets["asset"], {"measurement": nul})])
    assert response.status_code == 422, response.text
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)


async def test_only_measurement_takes_an_object(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, targets = await at_assets(api, seed)
    response = await reject(api, sid, [asset(targets["asset"], {"bucket": {"value": "y5"}})])
    assert response.status_code == 422, response.text
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)


async def test_a_measurement_on_a_component_is_422(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.COMPONENTS_REVIEW, revision=2, components_gate=True)
    targets = await seed.outputs(sid, revision=2)
    correction = {
        "entity_type": "component",
        "entity_id": targets["component"],
        "after": {"measurement": PLAN},
    }
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("components_review", 2, "reject", corrections=[correction]),
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert "a component correction cannot carry it" in error["detail"]
    await assert_nothing_written(sessionmaker, sid, Stage.COMPONENTS_REVIEW, 2)


async def test_with_the_setting_off_a_measurement_is_422_and_a_typed_quantity_is_not(
    off_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, targets = await at_assets(off_api, seed)
    refused = await reject(off_api, sid, [asset(targets["asset"], {"measurement": PLAN})])
    assert refused.status_code == 422 and "turned off" in refused.json()["errors"][0]["detail"]
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)
    typed = {"quantity": "800", "quantity_unit": "SF", "quantity_source": "site plan sheet C1"}
    assert (await reject(off_api, sid, [asset(targets["asset"], typed)])).status_code == 202


async def test_capabilities_report_the_setting(api: Api, off_api: Api) -> None:
    assert (await api.get("alice", "/v1/capabilities")).json()["traced_measurement"] is True
    assert (await off_api.get("alice", "/v1/capabilities")).json()["traced_measurement"] is False


async def test_a_draft_is_checked_as_a_decision_is(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, targets = await at_assets(api, seed)
    url = f"/v1/studies/{sid}/drafts/assets_review"
    headers = {"Authorization": "Bearer uid:alice"}
    bad = {
        "revision": 2,
        "corrections": [asset(targets["asset"], {"measurement": {**PLAN, "unit": "EA"}})],
    }
    response = await api.client.put(url, json=bad, headers=headers)
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["loc"] == ["body", "corrections", 0, "after"]
    foreign = {**PLAN, "image": {**PLAN["image"], "document_id": str(uuid.uuid4())}}
    stray = {"revision": 2, "corrections": [asset(targets["asset"], {"measurement": foreign})]}
    response = await api.client.put(url, json=stray, headers=headers)
    assert (
        response.status_code == 422 and response.json()["errors"][0]["type"] == "correction_target"
    )
    good = {"revision": 2, "corrections": [asset(targets["asset"], {"measurement": PLAN})]}
    saved = await api.client.put(url, json=good, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft"]["corrections"][0]["after"] == {"measurement": PLAN}


async def test_a_viewer_is_403_a_non_member_404_and_a_stale_revision_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    sid = study["id"]
    await seed.stage(sid, Stage.ASSETS_REVIEW, 2)
    targets = await seed.outputs(sid, revision=2)
    await seed.member(uuid.UUID(study["org_id"]), "vera", OrgRole.VIEWER)
    corrections = [asset(targets["asset"], {"measurement": PLAN})]
    assert (await reject(api, sid, corrections, uid="vera")).status_code == 403
    assert (await reject(api, sid, corrections, uid="mallory")).status_code == 404
    stale = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("assets_review", 1, "reject", corrections=corrections),
    )
    assert stale.status_code == 409, stale.text
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)
