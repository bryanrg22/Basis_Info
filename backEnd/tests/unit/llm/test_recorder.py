"""``LlmCallRecorder`` writes ``llm_calls`` rows (real Postgres) and sums the spend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from dbtest_support import db_settings, truncate_all
from lifecycletest_support import seed_run, seed_study
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.engine import create_engine
from basis.db.models import LlmCall
from basis.db.session import create_sessionmaker, session_scope
from basis.llm import Budget, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.call import CallContext, call_model
from basis.llm.provider import ChatRequest, Message
from basis.llm.recorder import LlmCallRecorder

pytestmark = pytest.mark.integration


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(db_settings(database_url))
    await truncate_all(engine)
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


async def test_rows_are_written_with_cost_and_identity(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    study = await seed_study(sessionmaker, photos=())
    run = await seed_run(sessionmaker, study.study_id)
    recorder = LlmCallRecorder(sessionmaker)
    cost = await recorder.record(
        study_id=study.study_id,
        run_id=run.id,
        activity="analyze_photo",
        prompt_version="photo_analysis/v1",
        provider="azure",
        model="gpt-4o-mini-2024-07-18",
        usage=Usage(input_tokens=1200, output_tokens=300),
        latency_ms=850,
    )
    assert cost == Decimal("0.000360")
    async with session_scope(sessionmaker) as session:
        [row] = (await session.scalars(select(LlmCall))).all()
    assert (row.study_id, row.run_id, row.activity) == (study.study_id, run.id, "analyze_photo")
    assert (row.provider, row.model, row.prompt_version) == (
        "azure",
        "gpt-4o-mini-2024-07-18",
        "photo_analysis/v1",
    )
    assert (row.input_tokens, row.output_tokens, row.latency_ms) == (1200, 300, 850)
    assert row.cost_usd == Decimal("0.000360")


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
    req = ChatRequest(
        model="gpt-4o",
        system="s",
        messages=(Message(role="user", content="u"),),
        prompt_version="p/v1",
        study_id=str(study.study_id),
        activity="process_assets",
    )
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
