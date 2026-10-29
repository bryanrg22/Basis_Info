"""``LlmCallRecorder`` writes the ledger rows (real Postgres), archives the payloads through
storage and sums the spend; ``LlmCallRepository`` rolls them up and pages them."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import pytest
from dbtest_support import db_settings, truncate_all
from lifecycletest_support import seed_run, seed_study
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.engine import create_engine
from basis.db.models import LlmCall
from basis.db.repositories.llm_call import LlmCallRepository
from basis.db.session import create_sessionmaker, session_scope
from basis.llm import Budget, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.call import CallContext, call_model, step_entry
from basis.llm.cost import PRICING_VERSION
from basis.llm.provider import ChatRequest, ImagePart, Message
from basis.llm.recorder import (
    KIND_DOCUMENT_ANALYSIS,
    STATUS_ERROR,
    LedgerEntry,
    LlmCallRecorder,
)
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(db_settings(database_url))
    await truncate_all(engine)
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


def request(study_id: uuid.UUID, **changes: object) -> ChatRequest:
    values: dict[str, object] = {
        "model": "gpt-4o-mini",
        "system": "You classify.",
        "messages": (
            Message(
                role="user",
                content="u",
                images=(ImagePart(data_base64="QUJD", ref="photo-1"),),
            ),
        ),
        "prompt_version": "photo_countables/v3",
        "study_id": str(study_id),
        "activity": "analyze_photo",
    }
    values.update(changes)
    return ChatRequest(**values)  # type: ignore[arg-type]


async def test_rows_are_written_with_cost_identity_and_payload_references(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    study = await seed_study(sessionmaker, photos=())
    run = await seed_run(sessionmaker, study.study_id)
    storage = LocalStorage(tmp_path / "storage")
    recorder = LlmCallRecorder(
        sessionmaker, storage=storage, bucket="ledger-test", build_sha="abc1234"
    )
    photo = uuid.uuid4()
    ctx = CallContext(
        study_id=study.study_id,
        run_id=run.id,
        activity="analyze_photo",
        budget=Budget(remaining_usd=1.0),
        recorder=recorder,
        stage="extracting",
        revision=2,
        activity_attempt=3,
        entity_type="photo",
        entity_id=photo,
    )
    usage = Usage(input_tokens=1200, output_tokens=300, cache_read_input_tokens=200)
    provider = ScriptedProvider(
        [ScriptedTurn(text="ok", usage=usage)], model="gpt-4o-mini-2024-07-18"
    )
    await call_model(provider, request(study.study_id), ctx)

    async with session_scope(sessionmaker) as session:
        [row] = (await session.scalars(select(LlmCall))).all()
    # 1000 uncached at $0.15, 200 cached at $0.075, 300 out at $0.60 per million.
    assert row.cost_usd == Decimal("0.000345")
    assert (row.study_id, row.run_id, row.activity) == (study.study_id, run.id, "analyze_photo")
    assert (row.kind, row.status, row.error) == ("chat", "ok", None)
    assert (row.stage, row.revision, row.activity_attempt) == ("extracting", 2, 3)
    assert (
        row.component
        == f"{__name__}.{test_rows_are_written_with_cost_identity_and_payload_references.__name__}"
    )
    assert (row.entity_type, row.entity_id) == ("photo", photo)
    assert (row.requested_model, row.model, row.provider) == (
        "gpt-4o-mini",
        "gpt-4o-mini-2024-07-18",
        "scripted",
    )
    assert (row.response_id, row.finish_reason) == ("scripted-1", "stop")
    assert (row.prompt_version, row.prompt_name) == ("photo_countables/v3", "photo_countables")
    assert row.prompt_sha256 is not None and len(row.prompt_sha256) == 64
    assert (row.input_tokens, row.cache_read_input_tokens, row.output_tokens) == (1200, 200, 300)
    assert (row.max_output_tokens, row.response_format, row.image_count) == (2048, None, 1)
    assert row.image_bytes == 3 and row.request_params["tool_names"] == []
    assert (row.price_row, row.pricing_version, row.build_sha) == (
        "gpt-4o-mini",
        PRICING_VERSION,
        "abc1234",
    )
    assert row.elapsed_ms is not None and row.latency_ms == 0
    assert row.request_uri == f"gs://ledger-test/studies/{study.study_id}/llm/{row.id}.request.json"
    assert (
        row.response_uri == f"gs://ledger-test/studies/{study.study_id}/llm/{row.id}.response.json"
    )
    stored = json.loads(await storage.read(row.request_uri))
    assert stored["system"] == "You classify." and stored["call_id"] == str(row.id)
    [image] = stored["messages"][0]["images"]
    assert image == {
        "ref": "photo-1",
        "media_type": "image/jpeg",
        "detail": "auto",
        "bytes": 3,
        "sha256": "b5d4045c3f466fa91fe2cc6abe79232a1a57cdf104f7a26e716e0a1e2789df78",
    }
    assert "QUJD" not in json.dumps(stored)  # never the image bytes
    answer = json.loads(await storage.read(row.response_uri))
    assert answer["cost_usd"] == "0.000345" and answer["usage"]["cache_read_input_tokens"] == 200
    assert answer["model"] == "gpt-4o-mini-2024-07-18" and answer["text"] == "ok"
    assert (await storage.stat(row.request_uri)).size > 0
    assert row.request_sha256 is not None and row.response_sha256 is not None


async def test_spend_is_the_sum_and_survives_a_rolled_back_caller(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    study = await seed_study(sessionmaker, photos=())
    other = await seed_study(sessionmaker, photos=())
    recorder = LlmCallRecorder(sessionmaker)
    assert await recorder.spent_usd(study.study_id) == Decimal(0)

    provider = ScriptedProvider(
        [ScriptedTurn(text="a", usage=Usage(input_tokens=2000, output_tokens=500))] * 2,
        model="gpt-4o",
    )
    ctx = CallContext(
        study_id=study.study_id,
        run_id=None,
        activity="process_assets",
        budget=Budget(remaining_usd=1.0),
        recorder=recorder,
    )
    req = request(study.study_id, model="gpt-4o", activity="process_assets")
    with pytest.raises(RuntimeError):
        async with session_scope(sessionmaker) as session:  # the activity's work transaction
            await call_model(provider, req, ctx)
            await call_model(provider, req, ctx)
            await session.flush()
            raise RuntimeError("activity failed after the calls")
    # Spent money stays recorded although the caller's transaction rolled back.
    assert await recorder.spent_usd(study.study_id) == Decimal("0.020000")
    assert await recorder.spent_usd(other.study_id) == Decimal(0)
    assert ctx.budget.spent_usd == pytest.approx(0.02)
    async with session_scope(sessionmaker) as session:
        rows = (await session.scalars(select(LlmCall))).all()
    assert {r.request_uri for r in rows} == {None}  # no storage: rows without payloads


async def test_non_token_steps_rollup_and_paging(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    study = await seed_study(sessionmaker, photos=())
    recorder = LlmCallRecorder(sessionmaker)
    ctx = CallContext(
        study_id=study.study_id,
        run_id=None,
        activity="extract_fields",
        budget=Budget(remaining_usd=1.0),
        recorder=recorder,
        stage="extracting",
    )
    await recorder.record(
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
    await recorder.record(
        LedgerEntry(
            study_id=study.study_id,
            run_id=None,
            activity="analyze_photo",
            provider="anthropic",
            model="claude-opus-5-5",
            usage=Usage(input_tokens=1000, output_tokens=100, cache_read_input_tokens=400),
            stage="extracting",
            status=STATUS_ERROR,
            error="ProviderBadResponse: output truncated",
            latency_ms=900,
        )
    )
    await recorder.record(
        LedgerEntry(
            study_id=study.study_id,
            run_id=None,
            activity="analyze_photo",
            provider="anthropic",
            model="claude-opus-5-5",
            usage=Usage(input_tokens=500, output_tokens=50),
            stage="rooms_review",
            latency_ms=100,
        )
    )
    async with session_scope(sessionmaker) as session:
        repo = LlmCallRepository(session)
        spend = await repo.spend(study.study_id)
        first = await repo.page(study.study_id, limit=2)
        rest = await repo.page(study.study_id, limit=2, cursor=first.next_cursor)
        photos_only = await repo.page(study.study_id, limit=10, activity="analyze_photo")
    # 600 uncached x $4 + 400 cached x $0.20 + 100 out x $20 = 0.0024 + 0.00008 + 0.002
    # then 500 x $4 + 50 x $20 = 0.002 + 0.001, plus the six pages.
    assert spend.total.cost_usd == Decimal("0.060000") + Decimal("0.004480") + Decimal("0.003000")
    assert (spend.total.calls, spend.total.errors) == (3, 1)
    assert spend.total.input_tokens == 1500 and spend.total.cache_read_input_tokens == 400
    assert spend.total.quantity == Decimal("6.0000")
    assert spend.total.latency_ms_total == 2500 and spend.total.latency_ms_max == 1500
    assert [g.key for g in spend.by_kind] == ["document_analysis", "chat"]
    assert [g.key for g in spend.by_stage] == ["extracting", "rooms_review"]
    assert sum(g.cost_usd for g in spend.by_activity) == spend.total.cost_usd
    assert len(first.items) == 2 and first.next_cursor is not None
    assert len(rest.items) == 1 and rest.next_cursor is None
    assert [r.kind for r in first.items + rest.items] == ["document_analysis", "chat", "chat"]
    assert first.items[0].quantity == Decimal("6.0000") and first.items[0].unit == "page"
    assert first.items[0].unit_price_usd == Decimal("0.01000000")
    assert first.items[0].price_row is None  # a metered step has no token price row
    assert len(photos_only.items) == 2
