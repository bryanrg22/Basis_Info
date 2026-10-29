"""The paid-call ledger through a whole fake study (EVAL_REPORT section 40).

Every row written by the run has its calling component, its entity, its prompt (model calls)
and its stored request and response; the non-token steps (the Azure DI analysis, the SAM 3
localizations) are rows too; and the rollup the API and the export serve sums to the rows.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections import Counter
from decimal import Decimal

import pytest
from lifecycle_support import Harness
from lifecycletest_support import make_deps
from sqlalchemy import select

from basis.activities.export_report import calls_uri
from basis.db.models import LlmCall
from basis.db.repositories.llm_call import LlmCallRepository
from basis.db.session import session_scope
from basis.vision.localize import Box, FakeLocalizer, Instance

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

CHAT_ACTIVITIES = {"analyze_photo", "reconcile_rooms", "process_assets", "extract_fields"}


def instance(label: str, score: float) -> Instance:
    return Instance(
        label=label,  # type: ignore[arg-type]
        phrase=label,
        score=score,
        box=Box(x1=0.1, y1=0.1, x2=0.4, y2=0.4),
        area=0.09,
    )


async def test_every_paid_step_of_a_study_is_in_the_ledger(harness: Harness) -> None:
    localizer = FakeLocalizer(script={"toilet": [instance("toilet", 0.95)]}, seconds_per_call=2.0)
    run = dataclasses.replace(
        harness,
        deps=make_deps(
            harness.sessionmaker,
            harness.deps.storage,
            harness.settings,
            chunk_store=harness.deps.chunk_store,
            localizer=localizer,
        ),
        task_queue=f"lifecycle-ledger-{uuid.uuid4().hex[:12]}",
        handles=[],
    )
    try:
        async with run.worker():
            started = await run.start(budget_usd=25.0)
            handle, sid = started.handle, started.study_id
            await run.wait_awaiting(handle, "appraisal_review", 1)
            await run.approve_all(handle)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert result.outcome == "completed"

    async with session_scope(harness.sessionmaker) as session:
        rows = list(
            (
                await session.scalars(
                    select(LlmCall).where(LlmCall.study_id == sid).order_by(LlmCall.created_at)
                )
            ).all()
        )
        spend = await LlmCallRepository(session).spend(sid)
    assert rows, "the run made paid calls"
    kinds = Counter(r.kind for r in rows)
    assert kinds["chat"] >= 3 and kinds["document_analysis"] == 1 and kinds["localization"] == 3

    # 1. Every row names its component, stage, attempt and the worker build.
    for row in rows:
        assert row.component and row.component.startswith("basis."), row.id
        assert row.stage is not None and row.activity_attempt == 1, row.id
        assert row.build_sha == harness.settings.git_sha and row.run_id is not None, row.id
        assert row.pricing_version is not None and row.status == "ok", row.id
        assert row.latency_ms is not None and row.elapsed_ms is not None, row.id
    # 2. Every model call has its prompt, its parameters and its payload references.
    chats = [r for r in rows if r.kind == "chat"]
    for row in chats:
        assert row.activity in CHAT_ACTIVITIES, row.activity
        assert row.prompt_version and row.prompt_name and row.prompt_sha256, row.id
        assert row.requested_model and row.model and row.max_output_tokens, row.id
        assert row.request_params and "tool_names" in row.request_params, row.id
        assert row.price_row not in (None, "unknown"), (row.model, row.price_row)
        assert row.request_uri and row.response_uri, row.id
        assert row.request_sha256 and row.response_sha256, row.id
    # A provider's id and finish reason are kept when it gives them (the scripted
    # classifier does; the fixture's components may all be rule hits, then it never ran).
    classified = [r for r in chats if r.activity == "process_assets"]
    assert all(r.response_id and r.finish_reason for r in classified)
    # 3. Every call is about something: the photo, the room, the component, the appraisal.
    about = {(r.activity, r.entity_type) for r in rows}
    assert ("analyze_photo", "photo") in about and ("extract_fields", "appraisal") in about
    assert ("localize_photo", "photo") in about
    assert all(r.entity_type == "component" for r in classified)
    assert all(r.entity_id is not None for r in rows if r.entity_type is not None)
    photo_ids = set(started.study.photo_ids)
    assert {r.entity_id for r in rows if r.activity == "analyze_photo"} == photo_ids
    assert {r.entity_id for r in rows if r.activity == "localize_photo"} == photo_ids
    # 4. The non-token steps: pages and seconds at their unit prices.
    [analysis] = [r for r in rows if r.kind == "document_analysis"]
    assert (analysis.provider, analysis.unit) == ("azure_di", "page")
    assert analysis.quantity and analysis.quantity > 0
    assert analysis.cost_usd == analysis.quantity * analysis.unit_price_usd
    for row in (r for r in rows if r.kind == "localization"):
        assert (row.provider, row.unit, row.quantity) == ("fake", "second", Decimal("2.0000"))
        assert row.model.startswith("fake-sam3@") and row.cost_usd == Decimal(
            str(round(2.0 * localizer.usd_per_second, 6))
        )
    # 5. The stored payloads are the prompt as sent (no image bytes) and the raw answer.
    storage = harness.deps.storage
    for row in chats[:3]:
        assert row.request_uri is not None and row.response_uri is not None
        request = json.loads(await storage.read(row.request_uri))
        response = json.loads(await storage.read(row.response_uri))
        assert request["call_id"] == str(row.id) and request["system"]
        assert request["prompt"]["sha256"] == row.prompt_sha256
        for message in request["messages"]:
            for image in message.get("images", []):
                assert set(image) == {"ref", "media_type", "detail", "bytes", "sha256"}
        assert response["cost_usd"] == str(row.cost_usd) and response["model"] == row.model
        assert response["usage"]["input_tokens"] == row.input_tokens
    # 6. The rollup sums to the rows, in every grouping.
    total = sum((r.cost_usd for r in rows), Decimal(0))
    assert spend.total.cost_usd == total and spend.total.calls == len(rows)
    assert spend.total.input_tokens == sum(r.input_tokens for r in rows)
    assert spend.total.output_tokens == sum(r.output_tokens for r in rows)
    for lines in (spend.by_stage, spend.by_activity, spend.by_component, spend.by_model):
        assert sum((g.cost_usd for g in lines), Decimal(0)) == total
        assert sum(g.calls for g in lines) == len(rows)
    assert {g.key for g in spend.by_kind} == {"chat", "document_analysis", "localization"}
    # 7. The export carries the rollup and the transcript of every call.
    assert result.export_uri is not None
    export = json.loads(await storage.read(result.export_uri))
    assert export["summary"]["spend"]["total"]["cost_usd"] == str(total)
    assert export["summary"]["spend"]["total"]["calls"] == len(rows)
    transcript_uri = calls_uri("basis-test", sid, 1)
    assert export["files"]["calls_jsonl"]["uri"] == transcript_uri
    lines = (await storage.read(transcript_uri)).decode().splitlines()
    assert len(lines) == len(rows)
    first = json.loads(lines[0])
    assert first["id"] == str(rows[0].id) and first["cost_usd"] == str(rows[0].cost_usd)
    chat_lines = [json.loads(line) for line in lines if json.loads(line)["kind"] == "chat"]
    assert all(line["request"]["system"] and line["response"]["usage"] for line in chat_lines)
    assert all("temperature" in line and "reasoning_effort" in line for line in chat_lines)
