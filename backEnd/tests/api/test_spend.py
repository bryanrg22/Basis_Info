"""``GET /v1/studies/{id}/spend`` and ``/calls``: the paid-call ledger through the API
(EVAL_REPORT section 40; docs/api/SPEND.md). Real Postgres; rows written by the recorder.

Access is the firm role ``SPEND_ACCESS_ROLE`` (owner by default): below it 403, a non-member
404."""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import OrgRole
from basis.errors.codes import E
from basis.llm.call import CallContext, step_entry
from basis.llm.provider import Budget, Usage
from basis.llm.recorder import KIND_DOCUMENT_ANALYSIS, LedgerEntry, LlmCallRecorder
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration


async def seed_calls(sm: async_sessionmaker[AsyncSession], study_id: str) -> list[uuid.UUID]:
    """Three rows: two model calls (one an error) and an Azure DI analysis."""
    sid = uuid.UUID(study_id)
    recorder = LlmCallRecorder(sm, build_sha="deadbeef")
    photo = uuid.uuid4()
    ids = []
    for status, usage in (
        ("ok", Usage(input_tokens=1000, output_tokens=100, cache_read_input_tokens=400)),
        ("error", Usage(input_tokens=500, output_tokens=0)),
    ):
        record = await recorder.record(
            LedgerEntry(
                study_id=sid,
                run_id=None,
                activity="analyze_photo",
                provider="anthropic",
                model="claude-opus-5-5",
                usage=usage,
                latency_ms=800,
                prompt_version="photo_countables/v3",
                status=status,
                error=None if status == "ok" else "ProviderBadResponse: truncated",
                stage="extracting",
                revision=1,
                activity_attempt=1,
                component="basis.vision.analyze.call_until_usable",
                entity_type="photo",
                entity_id=photo,
                requested_model="claude-opus-5-5",
                endpoint="https://api.anthropic.com/",
                response_id=f"msg_{status}",
                finish_reason="end_turn" if status == "ok" else "max_tokens",
                request_params={
                    "reasoning_effort": "medium",
                    "max_output_tokens": 5296,
                    "temperature": None,
                    "response_format": "ModelPhotoAnalysis",
                    "tool_names": [],
                    "image_count": 1,
                    "image_bytes": 1234,
                },
            )
        )
        ids.append(record.id)
    ctx = CallContext(
        study_id=sid,
        run_id=None,
        activity="extract_fields",
        budget=Budget(remaining_usd=0.0),
        recorder=recorder,
        stage="extracting",
        revision=1,
    )
    record = await recorder.record(
        step_entry(
            ctx,
            kind=KIND_DOCUMENT_ANALYSIS,
            provider="azure_di",
            model="prebuilt-layout",
            quantity=6,
            unit="page",
            unit_price_usd=0.01,
            cost_usd=Decimal("0.06"),
            latency_ms=1500,
        )
    )
    ids.append(record.id)
    return ids


async def test_the_rollup_sums_the_rows_by_every_grouping(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    await seed_calls(sessionmaker, study["id"])
    response = await api.get("alice", f"/v1/studies/{study['id']}/spend")
    assert response.status_code == 200, response.text
    body = response.json()
    total = body["total"]
    # 600 x $4 + 400 x $0.20 + 100 x $20 = 0.004480; 500 x $4 = 0.002000; six pages 0.06
    assert Decimal(total["cost_usd"]) == Decimal("0.066480")
    assert (total["calls"], total["errors"]) == (3, 1)
    assert (total["input_tokens"], total["cache_read_input_tokens"]) == (1500, 400)
    assert (total["output_tokens"], total["latency_ms_total"], total["latency_ms_max"]) == (
        100,
        3100,
        1500,
    )
    assert Decimal(total["quantity"]) == Decimal("6")
    assert [g["key"] for g in body["by_kind"]] == ["document_analysis", "chat"]
    assert [g["key"] for g in body["by_activity"]] == ["extract_fields", "analyze_photo"]
    assert [g["key"] for g in body["by_model"]] == ["prebuilt-layout", "claude-opus-5-5"]
    assert [g["key"] for g in body["by_stage"]] == ["extracting"]
    assert {g["key"] for g in body["by_component"]} >= {"basis.vision.analyze.call_until_usable"}
    for grouping in ("by_stage", "by_activity", "by_component", "by_model", "by_kind"):
        assert sum(Decimal(g["cost_usd"]) for g in body[grouping]) == Decimal(total["cost_usd"])
        assert sum(g["calls"] for g in body[grouping]) == 3


async def test_the_calls_page_filters_and_pages_with_every_field(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    ids = await seed_calls(sessionmaker, study["id"])
    first = await api.get("alice", f"/v1/studies/{study['id']}/calls?limit=2")
    assert first.status_code == 200, first.text
    page = first.json()
    assert [i["id"] for i in page["items"]] == [str(i) for i in ids[:2]]
    assert page["next_cursor"]
    second = await api.get(
        "alice", f"/v1/studies/{study['id']}/calls?limit=2&cursor={page['next_cursor']}"
    )
    assert [i["id"] for i in second.json()["items"]] == [str(ids[2])]
    assert second.json()["next_cursor"] is None

    call = page["items"][0]
    assert (call["kind"], call["status"], call["activity"]) == ("chat", "ok", "analyze_photo")
    assert (call["stage"], call["revision"], call["activity_attempt"]) == ("extracting", 1, 1)
    assert call["component"] == "basis.vision.analyze.call_until_usable"
    assert call["entity_type"] == "photo" and call["entity_id"]
    assert (call["provider"], call["endpoint"]) == ("anthropic", "https://api.anthropic.com/")
    assert (call["requested_model"], call["model"]) == ("claude-opus-5-5", "claude-opus-5-5")
    assert (call["response_id"], call["finish_reason"]) == ("msg_ok", "end_turn")
    assert (call["prompt_version"], call["prompt_name"]) == (
        "photo_countables/v3",
        "photo_countables",
    )
    assert (call["reasoning_effort"], call["max_output_tokens"], call["temperature"]) == (
        "medium",
        5296,
        None,
    )
    assert (call["response_format"], call["image_count"], call["image_bytes"]) == (
        "ModelPhotoAnalysis",
        1,
        1234,
    )
    assert call["request_params"]["tool_names"] == []
    assert (call["input_tokens"], call["cache_read_input_tokens"], call["output_tokens"]) == (
        1000,
        400,
        100,
    )
    assert Decimal(call["cost_usd"]) == Decimal("0.004480")
    assert (call["price_row"], call["build_sha"], call["latency_ms"]) == (
        "claude-opus-5-5",
        "deadbeef",
        800,
    )
    assert call["request_uri"] is None  # no storage behind this recorder
    failed = page["items"][1]
    assert failed["status"] == "error" and failed["error"].startswith("ProviderBadResponse")

    pages = await api.get("alice", f"/v1/studies/{study['id']}/calls?kind=document_analysis")
    [analysis] = pages.json()["items"]
    assert (analysis["unit"], Decimal(analysis["quantity"])) == ("page", Decimal("6"))
    assert Decimal(analysis["unit_price_usd"]) == Decimal("0.01")
    assert analysis["price_row"] is None and Decimal(analysis["cost_usd"]) == Decimal("0.06")
    by_entity = await api.get(
        "alice", f"/v1/studies/{study['id']}/calls?entity_id={call['entity_id']}"
    )
    assert len(by_entity.json()["items"]) == 2
    only = await api.get("alice", f"/v1/studies/{study['id']}/calls?activity=extract_fields")
    assert [i["kind"] for i in only.json()["items"]] == ["document_analysis"]


async def test_spend_is_the_owners_by_default(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """``SPEND_ACCESS_ROLE`` defaults to owner: an engineer or viewer of the study's firm is
    403 (the client hiding spend is not access control), a non-member still 404."""
    study = await api.create_study("alice")  # alice owns her firm
    await seed_calls(sessionmaker, study["id"])
    org_id = uuid.UUID(study["org_id"])
    await seed.member(org_id, "eve", OrgRole.ENGINEER)
    await seed.member(org_id, "vic", OrgRole.VIEWER)
    await seed.user("mallory")
    for route in ("spend", "calls"):
        path = f"/v1/studies/{study['id']}/{route}"
        assert (await api.get("alice", path)).status_code == 200
        for uid in ("eve", "vic"):
            forbidden = await api.get(uid, path)
            assert forbidden.status_code == 403, (uid, route)
            assert forbidden.json()["type"].endswith("/forbidden")
            assert "owner" in forbidden.json()["detail"]
        missing = await api.get("mallory", path)
        assert missing.status_code == 404
        assert missing.json()["code"] == E.STUDY_NOT_FOUND
        assert (await api.get("alice", f"/v1/studies/{uuid.uuid4()}/{route}")).status_code == 404
    bad = await api.get("alice", f"/v1/studies/{study['id']}/calls?cursor=not-a-cursor")
    assert bad.status_code == 422
    assert (await api.get("alice", f"/v1/studies/{study['id']}/calls?kind=foo")).status_code == 422


@pytest.mark.parametrize(
    ("setting", "allowed"),
    [
        ("viewer", {"alice", "eve", "vic"}),
        ("engineer", {"alice", "eve"}),
        ("owner", {"alice"}),
    ],
)
async def test_spend_access_role_sets_the_lowest_role(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    setting: str,
    allowed: set[str],
) -> None:
    """``viewer`` restores the behaviour before the setting; a non-member is 404 at every
    setting."""
    app = build_api_app(
        api_settings(database_url, spend_access_role=setting), sessionmaker, storage, verifier
    )
    seed = Seed(sessionmaker)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        api = Api(client)
        study = await api.create_study("alice")
        await seed_calls(sessionmaker, study["id"])
        org_id = uuid.UUID(study["org_id"])
        await seed.member(org_id, "eve", OrgRole.ENGINEER)
        await seed.member(org_id, "vic", OrgRole.VIEWER)
        await seed.user("mallory")
        for route in ("spend", "calls"):
            path = f"/v1/studies/{study['id']}/{route}"
            for uid in ("alice", "eve", "vic"):
                expected = 200 if uid in allowed else 403
                assert (await api.get(uid, path)).status_code == expected, (setting, uid, route)
            assert (await api.get("mallory", path)).status_code == 404
