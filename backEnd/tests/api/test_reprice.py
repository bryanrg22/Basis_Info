"""``/v1/studies/{id}/assets/reprice`` (docs/api/REPRICE.md): the preview writes nothing and
returns the fingerprint; the POST needs the engineer role, the open assets review of that
revision, the preview's fingerprint and a reason; it records an assets-review rejection
with the ``reprice`` (and ``structure_model``) correction, the event and the outbox signal,
replays with its key, and the decisions route refuses both types. Real Postgres; synthetic
rows. The pricing itself is tested at the activity (``test_process_assets``) and in
``tests/unit/evidence/test_pricing.py``."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from apitest_support import Api, Seed
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import Bucket, ClassificationMethod, OrgRole, Stage
from basis.db.models import Correction, OutboxMessage, Review, Study, StudyEvent
from basis.db.repositories.asset import AssetRepository
from basis.db.session import session_scope

pytestmark = pytest.mark.integration


async def at_assets_review(api: Api, seed: Seed, sm: async_sessionmaker[AsyncSession]) -> str:
    study = await api.create_study("alice")
    sid = str(study["id"])
    await seed.stage(sid, Stage.ASSETS_REVIEW, 1, components_gate=True)
    from decimal import Decimal

    async with session_scope(sm) as session:
        repo = AssetRepository(session)
        asset = await repo.create(
            study_id=uuid.UUID(sid),
            revision=1,
            component_id=None,
            category="Carpet",
            bucket=Bucket.Y5,
            recovery_period=Decimal("5"),
            irs_section="1245",
            method=ClassificationMethod.RULE,
            confidence=None,
        )
        await repo.add_cost_estimate(
            asset=asset, unit_cost=None, total=Decimal("100.00"), source_row="synthetic"
        )
    return sid


async def counts(sm: async_sessionmaker[AsyncSession], sid: str) -> tuple[int, int, int, int]:
    study_id = uuid.UUID(sid)
    async with session_scope(sm) as session:
        out = []
        for model in (Review, Correction, StudyEvent):
            out.append(
                int(
                    await session.scalar(
                        select(func.count()).select_from(model).where(model.study_id == study_id)
                    )
                    or 0
                )
            )
        out.append(int(await session.scalar(select(func.count()).select_from(OutboxMessage)) or 0))
    return out[0], out[1], out[2], out[3]


async def preview(api: Api, sid: str, uid: str = "alice", **query: Any) -> dict[str, Any]:
    response = await api.get(uid, f"/v1/studies/{sid}/assets/reprice", params=query)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_preview_writes_nothing_and_is_repeatable(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await at_assets_review(api, seed, sessionmaker)
    before = await counts(sessionmaker, sid)
    first = await preview(api, sid)
    assert (first["revision"], first["to_revision"], first["allowed"]) == (1, 2, True)
    assert len(first["fingerprint"]) == 64 and first["blocked"] is None
    assert (await preview(api, sid))["fingerprint"] == first["fingerprint"]
    assert await counts(sessionmaker, sid) == before
    async with session_scope(sessionmaker) as session:
        study = await session.get(Study, uuid.UUID(sid))
        assert study is not None and study.revision == 1
    # Previewing a square-foot model changes the fingerprint.
    luxury = await preview(api, sid, model_class="luxury")
    assert luxury["fingerprint"] != first["fingerprint"]
    bad = await api.get("alice", f"/v1/studies/{sid}/assets/reprice", params={"model_class": "x"})
    assert bad.status_code == 422


async def test_preview_says_why_a_rebuild_is_blocked(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await at_assets_review(api, seed, sessionmaker)
    await seed.stage(sid, Stage.ASSETS_PROCESSING, 1)
    body = await preview(api, sid)
    assert body["allowed"] is False and body["blocked_code"] == "BASIS-API-042"


async def test_reprice_records_a_rejection_with_the_correction(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await at_assets_review(api, seed, sessionmaker)
    shown = await preview(api, sid, model_class="custom")
    path, key = f"/v1/studies/{sid}/assets/reprice", str(uuid.uuid4())
    body = {
        "revision": 1,
        "fingerprint": shown["fingerprint"],
        "reason": "The 2024 index was entered",
        "structure_model": {"model_class": "custom"},
    }
    response = await api.post("alice", path, body, key=key)
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert (accepted["current_stage"], accepted["revision"]) == ("assets_processing", 2)
    assert accepted["review"]["decision"] == "reject"
    assert len(accepted["correction_ids"]) == 2
    async with session_scope(sessionmaker) as session:
        rows = (
            await session.scalars(select(Correction).where(Correction.study_id == uuid.UUID(sid)))
        ).all()
        events = (
            await session.scalars(
                select(StudyEvent).where(
                    StudyEvent.study_id == uuid.UUID(sid),
                    StudyEvent.event_type == "assets.reprice_requested",
                )
            )
        ).all()
    assert sorted(r.entity_type for r in rows) == ["reprice", "structure_model"]
    assert all(r.entity_id == uuid.UUID(sid) and r.reason == body["reason"] for r in rows)
    by_type = {r.entity_type: r for r in rows}
    assert by_type["reprice"].after == {"fingerprint": shown["fingerprint"]}
    assert by_type["structure_model"].after == {"model_class": "custom"}
    [event] = events
    assert event.actor_type == "engineer" and event.detail["fingerprint"] == shown["fingerprint"]
    assert event.detail["to_revision"] == 2
    # The same key and body replay the 202; nothing is written twice.
    written = await counts(sessionmaker, sid)
    replay = await api.post("alice", path, body, key=key)
    assert replay.status_code == 202 and replay.json() == accepted
    assert await counts(sessionmaker, sid) == written


async def test_a_changed_fingerprint_is_409_and_writes_nothing(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await at_assets_review(api, seed, sessionmaker)
    shown = await preview(api, sid)
    before = await counts(sessionmaker, sid)
    # A structure model the preview did not show is another set of inputs.
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/assets/reprice",
        {
            "revision": 1,
            "fingerprint": shown["fingerprint"],
            "reason": "r",
            "structure_model": {"model_class": "luxury"},
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BASIS-API-040"
    assert await counts(sessionmaker, sid) == before


@pytest.mark.parametrize(
    "change",
    [
        {"revision": 2},
        {"reason": "   "},
        {"reason": ""},
        {"fingerprint": "nothex"},
        {"structure_model": {}},
        {"structure_model": {"model_class": "palace"}},
        {"extra": 1},
    ],
)
async def test_bad_requests_are_refused(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], change: dict[str, Any]
) -> None:
    sid = await at_assets_review(api, seed, sessionmaker)
    shown = await preview(api, sid)
    body = {"revision": 1, "fingerprint": shown["fingerprint"], "reason": "r", **change}
    before = await counts(sessionmaker, sid)
    response = await api.post("alice", f"/v1/studies/{sid}/assets/reprice", body)
    assert response.status_code in (409, 422), response.text
    assert await counts(sessionmaker, sid) == before


async def test_a_study_not_at_the_assets_review_is_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = await at_assets_review(api, seed, sessionmaker)
    shown = await preview(api, sid)
    await seed.stage(sid, Stage.COMPLETED, 1)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/assets/reprice",
        {"revision": 1, "fingerprint": shown["fingerprint"], "reason": "r"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BASIS-API-042"


async def test_roles(api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    sid = await at_assets_review(api, seed, sessionmaker)
    async with session_scope(sessionmaker) as session:
        study = await session.get(Study, uuid.UUID(sid))
        assert study is not None
        org = study.org_id
    await seed.member(org, "vera", OrgRole.VIEWER)
    path = f"/v1/studies/{sid}/assets/reprice"
    assert (await api.get("vera", path)).status_code == 403
    assert (await api.get("mallory", path)).status_code == 404
    shown = await preview(api, sid)
    body = {"revision": 1, "fingerprint": shown["fingerprint"], "reason": "r"}
    before = await counts(sessionmaker, sid)
    assert (await api.post("vera", path, body)).status_code == 403
    assert (await api.post("mallory", path, body)).status_code == 404
    assert await counts(sessionmaker, sid) == before


@pytest.mark.parametrize("entity_type", ["reprice", "structure_model"])
async def test_the_decisions_route_refuses_the_rebuild_types(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], entity_type: str
) -> None:
    sid = await at_assets_review(api, seed, sessionmaker)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        {
            "stage": "assets_review",
            "revision": 1,
            "decision": "reject",
            "corrections": [
                {
                    "entity_type": entity_type,
                    "entity_id": sid,
                    "after": {"fingerprint": "a" * 64}
                    if entity_type == "reprice"
                    else {"model_class": "custom"},
                    "reason": "r",
                }
            ],
        },
    )
    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert error["loc"] == ["body", "corrections", 0, "entity_type"]
