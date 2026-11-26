"""A facade measurement on the decision route (docs/api/OVERRIDES.md section 4.1, EVAL_REPORT
111): accepted while ``GEO_FACADE`` is on and stored as sent, with an engineer's edit of a
draft kept as its ``original`` (the deepest body the request schema takes); refused with a 422
at ``after`` while it is off, for a bad body (plain words, the metrology's code), and when the
photo is not a document of the study; nothing is written on a refusal."""

from __future__ import annotations

import copy
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_decisions import assert_nothing_written, decision, study_at

from basis.db.enums import Stage
from basis.db.models import Correction
from basis.errors.codes import E
from basis.storage import LocalStorage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit" / "domain"))
from test_traced_facade import body as facade_body  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture
async def facade_api(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> AsyncIterator[Api]:
    app = build_api_app(
        api_settings(database_url, geo_facade=True), sessionmaker, storage, verifier
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        yield Api(client)


def on_photo(photo: str, **over: Any) -> dict[str, Any]:
    value = facade_body(**over)
    value["image"]["document_id"] = photo
    value["view"]["id"] = photo
    return value


async def reject(api: Api, sid: str, asset: str, after: dict[str, Any]) -> httpx.Response:
    correction = {"entity_type": "asset", "entity_id": asset, "after": after, "reason": None}
    return await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision("assets_review", 2, "reject", corrections=[correction]),
    )


async def test_a_facade_is_accepted_and_stored_as_sent_with_its_original(
    facade_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(facade_api, seed, Stage.ASSETS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    draft = on_photo(targets["photo"], drawn_by="model:gpt-6.1-sol@facade-v1", verified=False)
    edit = on_photo(targets["photo"], drawn_by="engineer", verified=True, original=draft)
    edit["facade"]["wall"][2][1] -= 3
    response = await reject(facade_api, sid, targets["asset"], {"measurement": edit})
    assert response.status_code == 202, response.text
    async with sessionmaker() as session:
        [row] = (await session.scalars(select(Correction))).all()
    assert row.after == {"measurement": edit}


async def test_off_is_refused_and_the_typed_quantity_still_works(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(api, seed, Stage.ASSETS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    response = await reject(api, sid, targets["asset"], {"measurement": on_photo(targets["photo"])})
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert error["type"] == "correction_value"
    assert error["detail"].startswith("facade measurements are turned off on this deployment")
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)
    caps = (await api.get("alice", "/v1/capabilities")).json()
    assert caps["facade_measure"] is False


async def test_a_bad_facade_and_a_foreign_photo_are_422(
    facade_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await study_at(facade_api, seed, Stage.ASSETS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    flipped = on_photo(targets["photo"])
    wall = flipped["facade"]["wall"]
    flipped["facade"]["wall"] = [wall[3], wall[2], wall[1], wall[0]]
    no_lens = on_photo(targets["photo"])
    del no_lens["facade"]["lens"]
    no_lens["facade"]["openings"] = []
    no_lens["facade"]["wall"] = [
        [100, 600],
        [700, 560],
        [700, 140],
        [100, 100],
    ]  # verticals parallel
    for bad, says in (
        (flipped, "measurement.facade.wall must run bottom-left"),
        (no_lens, f"[{E.GEO_FACADE_NO_FOCAL}]"),
    ):
        response = await reject(facade_api, sid, targets["asset"], {"measurement": bad})
        assert response.status_code == 422, response.text
        [error] = response.json()["errors"]
        assert error["type"] == "correction_value" and says in error["detail"]
    foreign = copy.deepcopy(on_photo(targets["photo"]))
    foreign["image"]["document_id"] = str(uuid.uuid4())
    response = await reject(facade_api, sid, targets["asset"], {"measurement": foreign})
    assert response.status_code == 422
    assert response.json()["errors"][0]["type"] == "correction_target"
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)
