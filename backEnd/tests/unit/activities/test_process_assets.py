"""``process_assets``: classify, measure, price and cross-check every component.

Activity mechanics with the Stage 2 photo stub's components, a scripted classifier and an
empty in-memory store (no reference corpus): rows per component, the rule/model split,
budget refusal, idempotency, corrections, and the "no provider" path. The full pipeline
with a reference corpus and verified citations is ``tests/node/test_process_assets.py``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fixtures import fixture_path
from lifecycletest_support import (
    SeededStudy,
    count_rows,
    intake_document,
    make_deps,
    scripted_classifier,
    set_intake,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.ingest_appraisal import IngestAppraisal
from basis.activities.process_assets import ProcessAssets
from basis.activities.stub_rules import components_for, room_for_filename
from basis.db.enums import Bucket, ClassificationMethod
from basis.db.models import (
    Asset,
    Citation,
    Component,
    CostEstimate,
    LlmCall,
    Study,
    StudyEvent,
    Takeoff,
)
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.domain.cost_trail import TRAIL_VERSION, check
from basis.domain.structure import STRUCTURE_LABELS
from basis.llm.cost import cost_usd
from basis.llm.errors import ProviderAccountError
from basis.llm.provider import ChatRequest, ChatResponse, ScriptedProvider, ScriptedTurn, Usage
from basis.workflow.contracts import (
    ApplyCorrectionsInput,
    CorrectionInput,
    IngestAppraisalInput,
    ProcessAssetsInput,
)

from .conftest import Env

pytestmark = pytest.mark.integration

# Stub components: kitchen_1/kitchen_2 -> cabinetry, flooring; bathroom_1 ->
# plumbing_fixtures, tile_flooring. cabinetry and tile_flooring hit rules; flooring
# (no material) and plumbing_fixtures go to the model.
MODEL_CALLS = 3
# The scripted provider reports model "scripted-model" (100 in / 50 out tokens per call), which
# the cost table prices at its conservative unknown-model row.
CALL_COST = cost_usd("scripted-model", Usage(input_tokens=100, output_tokens=50))


@pytest.fixture
async def analyzed(deps: Deps, seeded: SeededStudy, activity_env: Env) -> SeededStudy:
    await activity_env.run(
        IngestAppraisal(deps).run,
        IngestAppraisalInput(study_id=str(seeded.study_id), revision=1, idempotency_key="k-i"),
    )
    # The Stage 2 photo outputs (room from the file name, two template components),
    # written directly: the stub classifier here only knows the stub labels, while the
    # real analyze_photo (S3-B) emits the vision vocabulary. S3-C replaces both.
    async with session_scope(deps.sessionmaker) as session:
        rooms = RoomRepository(session)
        documents = await DocumentRepository(session).list_for_study(seeded.study_id)
        for document in documents:
            if document.id not in seeded.photo_ids:
                continue
            name, room_type = room_for_filename(document.filename)
            room, _ = await rooms.get_or_create(
                study_id=seeded.study_id, revision=1, name=name, room_type=room_type, source="stub"
            )
            await rooms.assign_photo(
                study_id=seeded.study_id, document_id=document.id, room_id=room.id, revision=1
            )
            for label in components_for(room_type):
                await rooms.add_component(
                    study_id=seeded.study_id,
                    revision=1,
                    document_id=document.id,
                    room_id=room.id,
                    label=label,
                    material=None,
                    attachment=None,
                    confidence=0.5,
                    raw={"source": "stub"},
                )
    activity_env.heartbeats.clear()
    return seeded


def process(
    seeded: SeededStudy,
    revision: int = 1,
    budget: float = 1.0,
    corrections: tuple[CorrectionInput, ...] = (),
    key: str | None = None,
) -> ProcessAssetsInput:
    return ProcessAssetsInput(
        study_id=str(seeded.study_id),
        revision=revision,
        idempotency_key=key or f"k-assets-{revision}",
        budget_remaining_usd=budget,
        corrections=corrections,
    )


async def assets(deps: Deps, study_id: uuid.UUID, revision: int) -> list[Asset]:
    async with session_scope(deps.sessionmaker) as session:
        stmt = select(Asset).where(Asset.study_id == study_id, Asset.revision == revision)
        return list((await session.scalars(stmt)).all())


async def labelled(deps: Deps, study_id: uuid.UUID, revision: int) -> list[tuple[str, Asset]]:
    async with session_scope(deps.sessionmaker) as session:
        stmt = (
            select(Component.label, Asset)
            .join(Asset, Asset.component_id == Component.id)
            .where(Asset.study_id == study_id, Asset.revision == revision)
            .order_by(Component.label, Asset.id)
        )
        return [(label, asset) for label, asset in (await session.execute(stmt)).all()]


async def test_every_component_gets_asset_takeoff_and_cost(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    assert output.assets == 6
    assert output.cost_usd == pytest.approx(MODEL_CALLS * CALL_COST)
    rows = await labelled(deps, analyzed.study_id, 1)
    methods = {label: a.method for label, a in rows}
    assert methods == {
        "cabinetry": ClassificationMethod.RULE,
        "tile_flooring": ClassificationMethod.RULE,
        "flooring": ClassificationMethod.LLM,
        "plumbing_fixtures": ClassificationMethod.LLM,
    }
    for model in (Takeoff, CostEstimate):
        assert await count_rows(deps.sessionmaker, model, study_id=analyzed.study_id) == 6
    # No reference corpus: no citation can be verified, so nothing is cited and
    # every asset is in front of the engineer.
    assert await count_rows(deps.sessionmaker, Citation, study_id=analyzed.study_id) == 0
    assert output.needs_review == 6
    for label, asset in rows:
        assert asset.review_reason and "citation_unverified" in asset.review_reason, label
    assert await count_rows(deps.sessionmaker, LlmCall, study_id=analyzed.study_id) == MODEL_CALLS
    assert len(activity_env.heartbeats) == 12  # one per classification, one per write


async def test_rule_and_model_values_and_takeoffs(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    rows = await labelled(deps, analyzed.study_id, 1)
    cabinet = next(a for label, a in rows if label == "cabinetry")
    assert (cabinet.bucket, cabinet.irs_section, cabinet.recovery_period) == (
        Bucket.Y5,
        "1245",
        Decimal("5.0"),
    )
    # tile is an uncontested building row since RULES_VERSION 2026-10-29.1 (ATG p.306)
    tile = next(a for label, a in rows if label == "tile_flooring")
    assert (tile.bucket, tile.irs_section) == (Bucket.Y27_5, "1250")
    assert tile.review_reason and "rule_contested" not in tile.review_reason
    floor = next(a for label, a in rows if label == "flooring")
    assert (floor.bucket, floor.confidence) == (Bucket.Y27_5, pytest.approx(0.3))
    assert floor.review_reason and floor.review_reason.startswith("missing_evidence")
    async with session_scope(deps.sessionmaker) as session:
        takeoffs = {t.asset_id: t for t in (await session.scalars(select(Takeoff))).all()}
    cabinets = sorted(
        (takeoffs[a.id] for label, a in rows if label == "cabinetry"), key=lambda t: t.quantity
    )
    # v0.9 (EVAL_REPORT 27): cabinets are counted in units, per row (these stub rows carry
    # no count, so one each). With the room step off (this suite's baseline) each photo's
    # row counts its cabinets; the room step's one inventory per room is what dedupes.
    assert [t.quantity for t in cabinets] == [Decimal("1"), Decimal("1")]
    assert {(t.unit, t.formula) for t in cabinets} == {("EA", "max(count=1, min_per_room=1)")}


async def test_unknown_label_goes_to_the_model_and_is_flagged(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    async with session_scope(deps.sessionmaker) as session:
        await RoomRepository(session).add_component(
            study_id=analyzed.study_id,
            revision=1,
            document_id=None,
            room_id=None,
            label="mystery_box",
            material=None,
            attachment=None,
            confidence=0.2,
        )
    output = await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    assert output.assets == 7
    rows = await labelled(deps, analyzed.study_id, 1)
    [mystery] = [asset for label, asset in rows if label == "mystery_box"]
    assert mystery.method is ClassificationMethod.LLM and mystery.needs_review


async def test_the_cost_table_prices_labels_the_legacy_rows_do_not(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """EVAL_REPORT 27, item 6: with COST_TABLE_PATH (here the synthetic table) kitchen
    cabinets are priced per unit; a row in another unit than the takeoff is
    COST_NEEDS_QUANTITY, a label without a row is still COST_ROW_MISSING."""
    settings = deps.settings.model_copy(
        update={"cost_table_path": str(fixture_path("cost_table.json")), "cost_year": 2025}
    )
    priced = make_deps(deps.sessionmaker, deps.storage, settings)
    async with session_scope(deps.sessionmaker) as session:
        repo = RoomRepository(session)
        for label, count in (("kitchen_cabinet", 18), ("patio", 1), ("driveway", 1)):
            await repo.add_component(
                study_id=analyzed.study_id,
                revision=1,
                document_id=None,
                room_id=None,
                label=label,
                material=None,
                attachment=None,
                confidence=0.9,
                raw={"source": "vision", "count": count},
            )
    await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    rows = dict(await labelled(deps, analyzed.study_id, 1))
    async with session_scope(deps.sessionmaker) as session:
        costs = {c.asset_id: c for c in (await session.scalars(select(CostEstimate))).all()}
    cabinets = costs[rows["kitchen_cabinet"].id]
    # 425 per unit x 1.0000 (the settings' 2026 is after the last published index) x 18.
    assert (cabinets.unit_cost, cabinets.total) == (Decimal("425.00"), Decimal("7650.00"))
    assert cabinets.source_row is not None and cabinets.source_row.startswith(
        "synthetic_2020[kitchen_cabinet]"
    )
    assert "COST_ROW_MISSING" not in (rows["kitchen_cabinet"].review_reason or "")
    patio = rows["patio"]
    assert patio.review_reason and "cross:COST_NEEDS_QUANTITY" in patio.review_reason
    assert costs[patio.id].total is None
    driveway = rows["driveway"]
    assert driveway.review_reason and "cross:COST_ROW_MISSING" in driveway.review_reason


async def test_budget_too_small_writes_nothing(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    # 3 components need the model; worst case 3 x 4 calls x $0.002 = $0.024.
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ProcessAssets(deps).run, process(analyzed, budget=0.02))
    assert caught.value.type == "BudgetExceededError" and caught.value.non_retryable
    assert await count_rows(deps.sessionmaker, Asset, study_id=analyzed.study_id) == 0
    assert isinstance(deps.chat_provider, ScriptedProvider)
    assert deps.chat_provider.requests == []


async def test_an_account_error_fails_the_activity_and_writes_nothing(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """The residual components would otherwise all be written ``provider_error`` rows."""
    error = ProviderAccountError("quota", provider="openai", status=429, code="insufficient_quota")
    provider = ScriptedProvider([ScriptedTurn(raise_error=error)])
    broke = make_deps(deps.sessionmaker, deps.storage, deps.settings, chat_provider=provider)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ProcessAssets(broke).run, process(analyzed))
    assert caught.value.type == "ProviderAccountError" and caught.value.non_retryable
    assert "openai account error (HTTP 429, insufficient_quota)" in str(caught.value)
    assert await count_rows(deps.sessionmaker, Asset, study_id=analyzed.study_id) == 0


async def test_without_a_provider_nothing_is_reserved_and_residuals_are_flagged(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    bare = make_deps(deps.sessionmaker, deps.storage, deps.settings, chat_provider=None)
    output = await activity_env.run(ProcessAssets(bare).run, process(analyzed, budget=0.0001))
    assert (output.assets, output.cost_usd) == (6, 0.0)
    rows = await labelled(deps, analyzed.study_id, 1)
    floor = next(a for label, a in rows if label == "flooring")
    assert floor.review_reason and floor.review_reason.startswith("classifier_unavailable")
    assert floor.confidence == 0.0


async def test_replay_and_reset_do_not_duplicate(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    activity = ProcessAssets(deps)
    first = await activity_env.run(activity.run, process(analyzed))
    assert await activity_env.run(activity.run, process(analyzed)) == first
    reset = await activity_env.run(activity.run, process(analyzed, key="k-reset"))
    assert (reset.assets, reset.needs_review, reset.cost_usd) == (6, first.needs_review, 0.0)
    assert await count_rows(deps.sessionmaker, Asset, study_id=analyzed.study_id) == 6
    assert await count_rows(deps.sessionmaker, LlmCall, study_id=analyzed.study_id) == MODEL_CALLS


async def start_revision_two(deps: Deps, seeded: SeededStudy, activity_env: Env) -> None:
    await activity_env.run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(seeded.study_id),
            stage="assets_review",
            from_revision=1,
            to_revision=2,
            corrections=(),
        ),
    )


async def test_asset_correction_applies_to_the_carried_component(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    sid = analyzed.study_id
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    target = next(a for label, a in await labelled(deps, sid, 1) if label == "plumbing_fixtures")
    await start_revision_two(deps, analyzed, activity_env)
    correction = CorrectionInput(
        entity_type="asset",
        entity_id=str(target.id),
        after={"bucket": "y15", "recovery_period": "15", "category": "Land improvement"},
    )
    output = await activity_env.run(
        ProcessAssets(deps).run, process(analyzed, revision=2, corrections=(correction,))
    )
    assert output.assets == 6
    [manual] = [a for a in await assets(deps, sid, 2) if a.method is ClassificationMethod.MANUAL]
    assert (manual.bucket, str(manual.recovery_period), manual.category) == (
        Bucket.Y15,
        "15.0",
        "Land improvement",
    )
    assert manual.irs_section == "1250"  # kept from the corrected asset
    async with session_scope(deps.sessionmaker) as session:
        component = await session.get(Component, manual.component_id)
        takeoff = (
            await session.scalars(select(Takeoff).where(Takeoff.asset_id == manual.id))
        ).one()
    assert component is not None and component.raw["carried_from"] == str(target.component_id)
    assert takeoff.inputs["asset_correction"] == {
        "from_asset_id": str(target.id),
        "after": correction.after,
    }


async def test_asset_correction_applies_to_a_concealed_row(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """STRUCTURE_MODEL.md: a concealed row has no component; a correction of it is matched
    by its label and the new revision's row takes the engineer's classification."""
    sid = analyzed.study_id
    # The structure model reads the appraisal's cost approach from the extraction fields,
    # which the workflow's extract_fields writes (and an assets-gate rejection carries to
    # the next revision); this harness seeds them.
    await seed_extraction(deps, analyzed, STRUCTURE_FIELDS)
    output = await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    assert (output.assets, output.concealed) == (6, 13)
    async with session_scope(deps.sessionmaker) as session:
        rows = (
            await session.execute(
                select(Asset, Takeoff)
                .join(Takeoff, Takeoff.asset_id == Asset.id)
                .where(Asset.study_id == sid, Asset.revision == 1, Asset.component_id.is_(None))
            )
        ).all()
    by_label = {takeoff.inputs["label"]: asset for asset, takeoff in rows}
    assert set(by_label) == set(STRUCTURE_LABELS)
    target = by_label["access_control"]
    assert target.bucket is Bucket.Y27_5 and target.needs_review  # contested
    await start_revision_two(deps, analyzed, activity_env)
    correction = CorrectionInput(
        entity_type="asset",
        entity_id=str(target.id),
        after={"bucket": "y5", "irs_section": "1245", "category": "Access control"},
    )
    output = await activity_env.run(
        ProcessAssets(deps).run, process(analyzed, revision=2, corrections=(correction,))
    )
    assert (output.assets, output.concealed) == (6, 13)
    [manual] = [a for a in await assets(deps, sid, 2) if a.method is ClassificationMethod.MANUAL]
    assert (manual.bucket, manual.recovery_period, manual.irs_section, manual.category) == (
        Bucket.Y5,
        Decimal("5.0"),
        "1245",
        "Access control",
    )
    assert manual.component_id is None
    # The engineer settled the classification (no contested note); the assumed door count
    # and the missing cost row still ask for a number.
    reason = manual.review_reason or ""
    assert "contested" not in reason and "structure:assumed_quantity" in reason
    async with session_scope(deps.sessionmaker) as session:
        takeoff = (
            await session.scalars(select(Takeoff).where(Takeoff.asset_id == manual.id))
        ).one()
        event = (
            await session.scalars(
                select(StudyEvent).where(
                    StudyEvent.study_id == sid, StudyEvent.event_type == "entity.corrected"
                )
            )
        ).one()
    assert takeoff.inputs["concealed"] is True and takeoff.inputs["label"] == "access_control"
    assert takeoff.inputs["asset_correction"] == {
        "from_asset_id": str(target.id),
        "after": correction.after,
    }
    assert event.detail["carried_from"] == str(target.id)


async def test_bucket_only_correction_derives_the_recovery_period(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    sid = analyzed.study_id
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    target = next(a for label, a in await labelled(deps, sid, 1) if label == "flooring")
    await start_revision_two(deps, analyzed, activity_env)
    correction = CorrectionInput(
        entity_type="asset",
        entity_id=str(target.id),
        after={"bucket": "y5", "irs_section": "1245", "category": "Vinyl flooring"},
    )
    await activity_env.run(
        ProcessAssets(deps).run, process(analyzed, revision=2, corrections=(correction,))
    )
    [manual] = [a for a in await assets(deps, sid, 2) if a.method is ClassificationMethod.MANUAL]
    assert (manual.bucket, manual.recovery_period, manual.irs_section) == (
        Bucket.Y5,
        Decimal("5.0"),
        "1245",
    )
    # The classification is the engineer's; the missing cost row still needs a number.
    assert manual.review_reason is not None
    assert "validation" not in manual.review_reason
    assert "cross:COST_ROW_MISSING" in manual.review_reason


@pytest.mark.parametrize(
    "after",
    [
        {"bucket": "y99"},
        {"recovery_period": "soon"},
        {"colour": "red"},
        {"bucket": "y5", "recovery_period": "39"},
        {"bucket": "y5", "irs_section": "1250"},
        {"irs_section": "section 1245"},
    ],
)
async def test_bad_asset_correction_is_not_retried(
    deps: Deps, analyzed: SeededStudy, activity_env: Env, after: dict[str, Any]
) -> None:
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    target = (await assets(deps, analyzed.study_id, 1))[0]
    await start_revision_two(deps, analyzed, activity_env)
    bad = (CorrectionInput(entity_type="asset", entity_id=str(target.id), after=after),)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ProcessAssets(deps).run, process(analyzed, 2, corrections=bad))
    assert caught.value.non_retryable
    assert await assets(deps, analyzed.study_id, 2) == []


async def _residual_request(
    deps: Deps, analyzed: SeededStudy, activity_env: Env, **settings: Any
) -> Any:
    async with session_scope(deps.sessionmaker) as session:
        await RoomRepository(session).add_component(
            study_id=analyzed.study_id,
            revision=1,
            document_id=None,
            room_id=None,
            label="mystery_box",
            material=None,
            attachment=None,
            confidence=0.2,
        )
    provider = scripted_classifier()
    configured = deps.settings.model_copy(update=settings)
    activity = ProcessAssets(
        make_deps(deps.sessionmaker, deps.storage, configured, chat_provider=provider)
    )
    await activity_env.run(activity.run, process(analyzed))
    assert provider.requests
    return provider.requests[0]


async def test_the_classifier_runs_on_section_17s_model_and_effort(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """EVAL_REPORT section 17.3: claude-sonnet-5-5 at low effort (routed to Anthropic)."""
    request = await _residual_request(deps, analyzed, activity_env)
    assert (request.model, request.reasoning_effort) == ("claude-sonnet-5-5", "low")
    assert request.tools  # the tool loop


async def test_the_previous_classifier_is_one_setting_away(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """Regression: CLASSIFY_MODEL=gpt-4o-mini; its provider drops the effort (no
    reasoning model), so the wire request is the pre-17 one."""
    from basis.llm.providers import build_request_body

    request = await _residual_request(deps, analyzed, activity_env, classify_model="gpt-4o-mini")
    assert request.model == "gpt-4o-mini"
    assert "reasoning_effort" not in build_request_body(request)


@pytest.mark.parametrize("search", ["bm25", "hybrid"])
async def test_the_classifier_searches_with_retrieval_search(
    deps: Deps,
    analyzed: SeededStudy,
    activity_env: Env,
    monkeypatch: pytest.MonkeyPatch,
    search: str,
) -> None:
    """EVAL_REPORT section 25: RETRIEVAL_SEARCH picks the store's BM25 (the classifier's
    own default, ``ctx.search`` None) or ``Retrieval.hybrid`` bound to the chunk store."""
    from dataclasses import replace

    from basis.activities import process_assets as module
    from basis.classify.store_protocol import InMemoryChunkStore, StoredChunk

    seen: list[Any] = []
    original = module._classify_all

    async def spy(views: Any, ctx: Any, concurrency: int) -> Any:
        seen.append(ctx)
        return await original(views, ctx, concurrency)

    monkeypatch.setattr(module, "_classify_all", spy)
    store = InMemoryChunkStore()
    version = uuid.uuid4()
    store.add(
        StoredChunk(
            id=uuid.uuid4(),
            corpus_version_id=version,
            doc_id="D",
            text="asset class 57.0 distributive trades",
            page_start=1,
            page_end=1,
            embedding=tuple([1.0] + [0.0] * 383),
        )
    )
    base = make_deps(deps.sessionmaker, deps.storage, deps.settings, chunk_store=store)
    assert base.retrieval is not None
    bound = replace(base, retrieval=replace(base.retrieval, search=search))
    await activity_env.run(ProcessAssets(bound).run, process(analyzed))
    [ctx] = seen
    if search == "bm25":
        assert ctx.search is None
        return
    assert ctx.search is not None
    store.calls.clear()
    hits = await ctx.search(version, "asset class 57.0", 3)
    assert [c[0] for c in store.calls] == ["search_bm25", "search_vector"]
    assert [h.doc_id for h in hits] == ["D"]


class PoolWatchingClassifier:
    """The scripted classifier, recording the connections checked out at each model call."""

    name = "pool-watching"

    def __init__(self, engine: AsyncEngine) -> None:
        self.inner = scripted_classifier()
        self.engine = engine
        self.checked_out: list[int] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.checked_out.append(self.engine.sync_engine.pool.checkedout())
        return await self.inner.complete(request)


@pytest.mark.regression
async def test_no_connection_is_held_while_the_model_classifies(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """EVAL_REPORT 32.4: the classification (minutes of model calls on a live study) ran
    inside run_once's transaction, holding a pooled connection and the activity key's row
    lock throughout while each classifier search needed a second connection. Now the
    revision is read first, the model runs with nothing checked out, then everything is
    written in one transaction."""
    engine = deps.sessionmaker.kw["bind"]
    watcher = PoolWatchingClassifier(engine)
    # One component at a time: the ledger writes each call's row in its own short session
    # as the call returns (EVAL_REPORT section 40), so with concurrent components another
    # component's write could be in flight at the moment of a call; serialized, the
    # invariant under test is exact: nothing is held while the model answers.
    settings = deps.settings.model_copy(update={"classify_concurrency": 1})
    watched = make_deps(deps.sessionmaker, deps.storage, settings, chat_provider=watcher)
    output = await activity_env.run(ProcessAssets(watched).run, process(analyzed))
    assert watcher.checked_out == [0] * MODEL_CALLS
    assert output.assets == 6
    assert await count_rows(deps.sessionmaker, LlmCall, study_id=analyzed.study_id) == MODEL_CALLS


# --------------------------------------------------------------- measured quantities (35)


async def seed_extraction(deps: Deps, seeded: SeededStudy, fields: dict[str, Any]) -> None:
    """A fabricated appraisal extraction for the study's appraisal (revision 1)."""
    from datetime import date

    from basis.db.repositories.extraction import ExtractionRepository

    assert seeded.appraisal_id is not None
    async with session_scope(deps.sessionmaker) as session:
        repo = ExtractionRepository(session)
        extraction = await repo.create(
            study_id=seeded.study_id,
            document_id=seeded.appraisal_id,
            revision=1,
            method="test",
            overall_confidence=0.99,
            needs_review=False,
            raw={},
        )
        for key, value in fields.items():
            section, field = key.split(".", 1)
            await repo.set_field(
                extraction=extraction,
                section=section,
                field=field,
                value_text=str(value),
                value_num=Decimal(str(value)) if isinstance(value, int | float) else None,
                value_date=value if isinstance(value, date) else None,
                confidence=0.99,
                source="azure",
            )


# The cost index citations every priced row carries since EVAL_REPORT 45.
HCI_2020 = (
    "HCI 239.1 (Jan 2020, est.) / 239.1 (Jan 2020, est.), RSMeans Building Construction "
    "Costs 2020 p.778"
)
LOCATION_TABLE = "RSMeans Residential 2020 Location Factors - Residential"
SCHENECTADY = f"zip 12345 (intake): 123 Schenectady 0.98, {LOCATION_TABLE} p.720"
SETTING_2026 = (
    f"(base 2020, x1.0000 for 2026 = {HCI_2020}; no 2026 index loaded, the latest applied "
    "(assumption); x1.0 location)"
)
ROOF_IL_2025 = (
    f"(base 2020, x1.0000 for 2025 = {HCI_2020}; no 2025 index loaded, the latest applied "
    "(assumption), from appraisal effective date 2025-06-01 "
    "(reconciliation.effective_date_of_appraisal (azure)); x0.959 location from "
    f"state IL (subject.state (azure)): mean of 19 IL cities (0.92-1.00), {LOCATION_TABLE} "
    "pp.718-722)"
)
SCHENECTADY_2024 = (
    f"(base 2020, x1.0000 for 2024 = {HCI_2020}; no 2024 index loaded, the latest applied "
    f"(assumption), from placed in service 2024-03-01 (intake); x0.98 location from {SCHENECTADY})"
)

MEASURED_FIELDS: dict[str, Any] = {
    "improvements.gla_sqft": 2400,
    "improvements.stories": 2,
    "cost_approach.garage_sqft": 440,
    "site.area_sqft": 8000,
    "improvements.total_rooms": 8,
    "improvements.bedrooms": 4,
    "improvements.bathrooms": 2.5,
    "subject.state": "IL",
}


# A two-story Q4 house with a developed cost approach (STRUCTURE_MODEL.md section 3.1).
STRUCTURE_FIELDS: dict[str, Any] = {
    "improvements.gla_sqft": 2400,
    "improvements.stories": 2,
    "improvements.quality": "Q4",
    "improvements.total_rooms": 8,
    "improvements.bedrooms": 4,
    "improvements.effective_age_years": 12,
    "cost_approach.total_cost_new": 300000,
    "cost_approach.dwelling_cost": 300000,
    "cost_approach.depreciation_physical": 60000,
    "cost_approach.remaining_economic_life": 48,
    "site.utilities_water": "Public",
    "site.utilities_sewer": "Public",
    "subject.state": "IL",
}


async def add_site_components(deps: Deps, seeded: SeededStudy) -> None:
    async with session_scope(deps.sessionmaker) as session:
        repo = RoomRepository(session)
        exterior, _ = await repo.get_or_create(
            study_id=seeded.study_id, revision=1, name="Front", room_type="exterior", source="stub"
        )
        for label, count in (
            ("roof", 1),
            ("roof", 2),
            ("patio", 1),
            ("landscaping", 1),
            ("concrete_floor", 1),  # a building rule row: 27.5 or 39 years by property class
        ):
            await repo.add_component(
                study_id=seeded.study_id,
                revision=1,
                document_id=None,
                room_id=exterior.id,
                label=label,
                material=None,
                attachment=None,
                confidence=0.9,
                raw={"source": "vision", "count": count},
            )


async def takeoffs_and_costs(
    deps: Deps, study_id: uuid.UUID
) -> tuple[dict[uuid.UUID, Takeoff], dict[uuid.UUID, CostEstimate]]:
    async with session_scope(deps.sessionmaker) as session:
        takeoffs = {t.asset_id: t for t in (await session.scalars(select(Takeoff))).all()}
        costs = {c.asset_id: c for c in (await session.scalars(select(CostEstimate))).all()}
    return takeoffs, costs


async def test_measured_areas_price_the_property_items_and_size_the_rooms(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """EVAL_REPORT section 35: with the appraisal's fields, room areas are GLA shares,
    the roof is derived from the footprint and priced from the table, a site label the
    appraisal cannot size stays COST_NEEDS_QUANTITY with the measurement named, and the
    cost row carries the state's factor and the appraisal's year with their sources."""
    from datetime import date

    settings = deps.settings.model_copy(
        update={"cost_table_path": str(fixture_path("cost_table.json")), "cost_year": 2026}
    )
    priced = make_deps(deps.sessionmaker, deps.storage, settings)
    await seed_extraction(
        deps,
        analyzed,
        {**MEASURED_FIELDS, "reconciliation.effective_date_of_appraisal": date(2025, 6, 1)},
    )
    await add_site_components(deps, analyzed)
    output = await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    assert output.assets == 11
    rows = await labelled(deps, analyzed.study_id, 1)
    takeoffs, costs = await takeoffs_and_costs(deps, analyzed.study_id)

    # Bathroom tile (the stub's tile_flooring): 2400 x 75 / 1540 = 116.88 SF; x 1.15 waste.
    tile = next(a for label, a in rows if label == "tile_flooring")
    assert (takeoffs[tile.id].quantity, takeoffs[tile.id].unit) == (Decimal("134.41"), "SF")
    assert takeoffs[tile.id].inputs["room_area_source"] == "gla_share"
    assert takeoffs[tile.id].inputs["room_area_formula"] == (
        "2400 SF GLA x 75 / 1540 (bathroom weight / program weight)"
    )
    assert takeoffs[tile.id].inputs["gla_field"] == "improvements.gla_sqft (azure)"

    # Roof: ground footprint 1,200 + 440 = 1,640; (1,640 + 165.33) x 1.118 = 2,018.36 SF,
    # measured once per property; the second roof row is 0 and points at the first.
    roofs = sorted(
        (takeoffs[a.id] for label, a in rows if label == "roof"), key=lambda t: -t.quantity
    )
    assert [(t.quantity, t.unit) for t in roofs] == [(Decimal("2018.36"), "SF"), (Decimal(0), "SF")]
    assert roofs[0].inputs["quantity_source"] == "appraisal_derived"
    assert roofs[0].inputs["pitch_factor"] == "1.118"
    assert roofs[1].formula == f"measured once per property: see asset {roofs[0].asset_id}"
    roof_cost = costs[roofs[0].asset_id]
    # synthetic_2020 roof: (100 + 50) x 0.01 = 1.50/SF x 1.0000 (2025: after the book's
    # last index, flagged) x 0.959 (IL: the mean of its 19 cities) = 1.4385.
    assert (roof_cost.unit_cost, roof_cost.total) == (Decimal("1.44"), Decimal("2906.44"))
    assert (roof_cost.cost_year, roof_cost.location_factor) == (2025, Decimal("0.959"))
    assert roof_cost.source_row is not None and roof_cost.source_row.endswith(ROOF_IL_2025)
    roof_assets = [a for label, a in rows if label == "roof"]
    assert not any("COST_NEEDS_QUANTITY" in (a.review_reason or "") for a in roof_assets)
    # Every priced row stands on the 2020 index until the firm loads 2025's (EVAL_REPORT 45).
    assert "cost:YEAR_INDEX_MISSING" in (roof_assets[0].review_reason or "")
    assert "no published cost index for 2025" in roofs[0].inputs["cost_year_assumption"]

    # Patio: no appraisal input; still a count, COST_NEEDS_QUANTITY, the measurement named.
    patio = next(a for label, a in rows if label == "patio")
    assert patio.review_reason and "cross:COST_NEEDS_QUANTITY" in patio.review_reason
    assert takeoffs[patio.id].inputs["needs_measurement"] == (
        "patio area in SF (site plan or aerial measurement)"
    )
    assert costs[patio.id].total is None

    # Landscaping: site 8,000 - 1,640 = 6,360 SF, an upper bound the engineer must confirm
    # (no row in the synthetic table, so unpriced here; the reason is the takeoff's).
    landscaping = next(a for label, a in rows if label == "landscaping")
    assert takeoffs[landscaping.id].quantity == Decimal("6360.00")
    assert takeoffs[landscaping.id].inputs["quantity_bound"] == "upper"
    assert landscaping.review_reason and "takeoff:upper_bound" in landscaping.review_reason


async def test_without_extraction_fields_the_takeoffs_are_the_legacy_ones(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """Regression: no extraction (the seeded study has none) keeps today's typical areas,
    the settings' year and location, and the source row text unchanged."""
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    rows = await labelled(deps, analyzed.study_id, 1)
    takeoffs, costs = await takeoffs_and_costs(deps, analyzed.study_id)
    tile = next(a for label, a in rows if label == "tile_flooring")
    assert takeoffs[tile.id].quantity == Decimal("86.25")  # 75 x 1.15
    assert takeoffs[tile.id].inputs["room_area_source"] == "typical_for_room_type"
    assert "room_area_formula" not in takeoffs[tile.id].inputs
    assert costs[tile.id].source_row is not None and costs[tile.id].source_row.endswith(
        SETTING_2026
    )
    assert (costs[tile.id].cost_year, costs[tile.id].location_factor) == (2026, Decimal("1.0"))


async def test_takeoff_areas_typical_and_cost_index_setting_ignore_the_appraisal(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    settings = deps.settings.model_copy(
        update={"takeoff_areas": "typical", "cost_index": "setting"}
    )
    legacy = make_deps(deps.sessionmaker, deps.storage, settings)
    await seed_extraction(deps, analyzed, MEASURED_FIELDS)
    await add_site_components(deps, analyzed)
    await activity_env.run(ProcessAssets(legacy).run, process(analyzed))
    rows = await labelled(deps, analyzed.study_id, 1)
    takeoffs, costs = await takeoffs_and_costs(deps, analyzed.study_id)
    tile = next(a for label, a in rows if label == "tile_flooring")
    assert takeoffs[tile.id].quantity == Decimal("86.25")
    assert takeoffs[tile.id].inputs["room_area_source"] == "typical_for_room_type"
    roof = next(a for label, a in rows if label == "roof")
    assert (takeoffs[roof.id].unit, takeoffs[roof.id].inputs["method"]) == ("EA", "count")
    assert "needs_measurement" not in takeoffs[roof.id].inputs
    assert costs[tile.id].source_row is not None and costs[tile.id].source_row.endswith(
        SETTING_2026
    )


async def test_cost_index_from_the_study_with_typical_areas(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """COST_INDEX=study alone: the state's factor applies, the areas stay typical."""
    settings = deps.settings.model_copy(update={"takeoff_areas": "typical"})
    mixed = make_deps(deps.sessionmaker, deps.storage, settings)
    await seed_extraction(deps, analyzed, {"subject.state": "NY"})
    await activity_env.run(ProcessAssets(mixed).run, process(analyzed))
    rows = await labelled(deps, analyzed.study_id, 1)
    takeoffs, costs = await takeoffs_and_costs(deps, analyzed.study_id)
    tile = next(a for label, a in rows if label == "tile_flooring")
    assert takeoffs[tile.id].inputs["room_area_source"] == "typical_for_room_type"
    assert costs[tile.id].location_factor == Decimal("0.976")
    assert costs[tile.id].source_row is not None and costs[tile.id].source_row.endswith(
        f"(base 2020, x1.0000 for 2026 = {HCI_2020}; no 2026 index loaded, the latest applied "
        "(assumption), from COST_YEAR setting; x0.976 location from state NY "
        f"(subject.state (azure)): mean of 32 NY cities (0.91-1.04), {LOCATION_TABLE} pp.718-722)"
    )


# ------------------------------------------------------------------ the intake (EVAL_REPORT 37)


async def test_the_intake_sets_the_cost_index_and_the_property_class(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """EVAL_REPORT section 37: the intake's placed-in-service year and state price the
    rows ahead of the appraisal's effective date (2025) and state (IL); its use
    (nonresidential) makes the building rows 39-year; the areas still come from the
    appraisal's GLA; every takeoff records which sources applied."""
    from datetime import date

    settings = deps.settings.model_copy(
        update={"cost_table_path": str(fixture_path("cost_table.json")), "cost_year": 2026}
    )
    priced = make_deps(deps.sessionmaker, deps.storage, settings)
    await seed_extraction(
        deps,
        analyzed,
        {**MEASURED_FIELDS, "reconciliation.effective_date_of_appraisal": date(2025, 6, 1)},
    )
    await add_site_components(deps, analyzed)
    await set_intake(
        deps.sessionmaker,
        analyzed.study_id,
        intake_document(
            property_use="nonresidential",
            dwelling_unit_income_share=None,
            transient_lodging=True,
            placed_in_service_date="2024-03-01",
            state="NY",
        ),
    )
    output = await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    assert output.assets == 11
    rows = await labelled(deps, analyzed.study_id, 1)
    takeoffs, costs = await takeoffs_and_costs(deps, analyzed.study_id)

    # The index: the intake's year (2024: after the book's last index, so the Jan 2020
    # index applies, flagged) and the intake's ZIP 12345 (123 Schenectady, 0.98).
    roof = max((takeoffs[a.id] for label, a in rows if label == "roof"), key=lambda t: t.quantity)
    roof_cost = costs[roof.asset_id]
    assert (roof_cost.cost_year, roof_cost.location_factor) == (2024, Decimal("0.98"))
    assert roof_cost.source_row is not None and roof_cost.source_row.endswith(SCHENECTADY_2024)
    # The areas are still the appraisal's: 2400 x 75 / 1540 = 116.88 SF x 1.15 waste.
    tile = next(a for label, a in rows if label == "tile_flooring")
    assert takeoffs[tile.id].quantity == Decimal("134.41")
    assert takeoffs[tile.id].inputs["room_area_source"] == "gla_share"
    # The use: the building rule row (concrete_floor, section 1250) is 39-year, and the
    # scripted model's fixed 27.5 answer is flagged against the class.
    floor = next(a for label, a in rows if label == "concrete_floor")
    assert (floor.bucket, floor.recovery_period, floor.method) == (
        Bucket.Y39,
        Decimal("39"),
        ClassificationMethod.RULE,
    )
    modelled = [a for label, a in rows if label in ("flooring", "plumbing_fixtures")]
    assert modelled and all(
        a.bucket is Bucket.Y27_5
        and "validation:BUILDING_BUCKET_PROPERTY_CLASS" in (a.review_reason or "")
        for a in modelled
    )
    # Every takeoff says where the index and the class came from.
    for takeoff in takeoffs.values():
        assert takeoff.inputs["cost_year_source"] == "placed in service 2024-03-01 (intake)"
        assert takeoff.inputs["location_source"] == SCHENECTADY
        assert "no published cost index for 2024" in takeoff.inputs["cost_year_assumption"]
        assert (takeoff.inputs["property_class"], takeoff.inputs["property_class_source"]) == (
            "nonresidential",
            "intake property_use",
        )


async def test_an_intake_without_a_state_falls_back_to_the_appraisal_state(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """A study started before the state was required (or one whose intake predates its
    extraction): the appraisal's subject.state still applies, and the source says so."""
    await seed_extraction(deps, analyzed, {"subject.state": "IL"})
    await set_intake(
        deps.sessionmaker,
        analyzed.study_id,
        intake_document(placed_in_service_date="2023-07-01", state=None, zip=None),
    )
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    rows = await labelled(deps, analyzed.study_id, 1)
    takeoffs, costs = await takeoffs_and_costs(deps, analyzed.study_id)
    tile = next(a for label, a in rows if label == "tile_flooring")
    assert (costs[tile.id].cost_year, costs[tile.id].location_factor) == (2023, Decimal("0.959"))
    assert costs[tile.id].source_row is not None and costs[tile.id].source_row.endswith(
        f"(base 2020, x1.0000 for 2023 = {HCI_2020}; no 2023 index loaded, the latest applied "
        "(assumption), from placed in service 2023-07-01 (intake); x0.959 location from "
        f"state IL (subject.state (azure)): mean of 19 IL cities (0.92-1.00), {LOCATION_TABLE} "
        "pp.718-722)"
    )
    assert takeoffs[tile.id].inputs["property_class"] == "residential_rental"
    assert takeoffs[tile.id].inputs["property_class_source"] == "intake property_use"
    assert Bucket.Y39 not in {a.bucket for _, a in rows}
    assert not any("BUILDING_BUCKET_PROPERTY_CLASS" in (a.review_reason or "") for _, a in rows)


async def test_the_intake_alone_prices_a_study_with_no_extraction_fields(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """No extraction fields (the seeded study has none): the areas stay typical, as in the
    regression test, while the intake still fixes the year and the state."""
    await set_intake(
        deps.sessionmaker,
        analyzed.study_id,
        intake_document(placed_in_service_date="2024-03-01", state="NY"),
    )
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    rows = await labelled(deps, analyzed.study_id, 1)
    takeoffs, costs = await takeoffs_and_costs(deps, analyzed.study_id)
    tile = next(a for label, a in rows if label == "tile_flooring")
    assert takeoffs[tile.id].quantity == Decimal("86.25")  # 75 x 1.15: the typical area
    assert takeoffs[tile.id].inputs["room_area_source"] == "typical_for_room_type"
    assert (costs[tile.id].cost_year, costs[tile.id].location_factor) == (2024, Decimal("0.98"))
    assert costs[tile.id].source_row is not None and costs[tile.id].source_row.endswith(
        SCHENECTADY_2024
    )


async def test_cost_index_setting_ignores_the_intake_but_its_use_still_classifies(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """COST_INDEX=setting: the deployment's year and factor, written as before; the use is
    not an index setting, so the intake's nonresidential still gives 39-year building rows,
    and the takeoff inputs say the index came from the settings."""
    settings = deps.settings.model_copy(update={"cost_index": "setting"})
    legacy = make_deps(deps.sessionmaker, deps.storage, settings)
    await set_intake(
        deps.sessionmaker,
        analyzed.study_id,
        intake_document(
            property_use="nonresidential",
            dwelling_unit_income_share=None,
            transient_lodging=True,
            placed_in_service_date="2024-03-01",
            state="NY",
        ),
    )
    await activity_env.run(ProcessAssets(legacy).run, process(analyzed))
    rows = await labelled(deps, analyzed.study_id, 1)
    takeoffs, costs = await takeoffs_and_costs(deps, analyzed.study_id)
    tile = next(a for label, a in rows if label == "tile_flooring")
    assert costs[tile.id].source_row is not None and costs[tile.id].source_row.endswith(
        SETTING_2026
    )
    assert (
        takeoffs[tile.id].inputs["cost_year_source"],
        takeoffs[tile.id].inputs["location_source"],
    ) == (
        "COST_YEAR setting",
        "COST_LOCATION_FACTOR setting",
    )
    assert takeoffs[tile.id].inputs["property_class"] == "nonresidential"
    modelled = [a for label, a in rows if label in ("flooring", "plumbing_fixtures")]
    assert modelled and all(
        "validation:BUILDING_BUCKET_PROPERTY_CLASS" in (a.review_reason or "") for a in modelled
    )


async def test_without_an_intake_or_fields_no_source_keys_are_written(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """Regression (section 35's byte-identical claim holds): a revision with no intake and
    no extraction fields writes the takeoff inputs it wrote before section 37."""
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    takeoffs, _ = await takeoffs_and_costs(deps, analyzed.study_id)
    for takeoff in takeoffs.values():
        assert not {"cost_year_source", "location_source", "property_class"} & set(takeoff.inputs)
        # Except the one fact that is new since EVAL_REPORT 45: the settings' 2026 is after
        # the book's last published index, and every row says so.
        assert "no published cost index for 2026" in takeoff.inputs["cost_year_assumption"]


# ------------------------------------------------------------------ the cost trail (0018)


def assert_trail_matches(cost: CostEstimate, takeoff: Takeoff) -> dict[str, Any]:
    """A cost row's trail agrees with its columns and its takeoff; returns the trail."""
    trail = cost.trail
    assert isinstance(trail, dict) and trail["version"] == TRAIL_VERSION
    for key in ("unit_cost", "total", "location_factor"):
        column = getattr(cost, key)
        assert (None if trail[key] is None else Decimal(trail[key])) == column, key
    assert trail["cost_year"] == cost.cost_year
    assert Decimal(trail["quantity"]["value"]) == takeoff.quantity
    assert trail["quantity"]["unit"] == takeoff.unit
    assert trail["quantity"]["formula"] == takeoff.formula
    assert trail["quantity"]["inputs"] == takeoff.inputs
    assert check(trail) == []
    if trail["kind"] == "unpriced":
        assert cost.total is None and trail["missing"] == cost.source_row
    return trail


async def test_every_cost_row_carries_a_trail_that_matches_its_columns(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """Migration 0018 / docs/api/COST_INDICES.md "Per-asset cost trail": every cost row,
    itemized, unpriced and concealed, is written with the trail of how it was priced, and
    the trail's amounts, factors and quantity are the row's own."""
    from datetime import date

    from basis.db.models import Study

    settings = deps.settings.model_copy(
        update={"cost_table_path": str(fixture_path("cost_table.json")), "cost_year": 2026}
    )
    priced = make_deps(deps.sessionmaker, deps.storage, settings)
    await seed_extraction(
        deps,
        analyzed,
        {
            **MEASURED_FIELDS,
            **STRUCTURE_FIELDS,
            "reconciliation.effective_date_of_appraisal": date(2025, 6, 1),
        },
    )
    await add_site_components(deps, analyzed)
    await set_intake(
        deps.sessionmaker,
        analyzed.study_id,
        intake_document(placed_in_service_date="2024-03-01", state="NY"),
    )
    output = await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    assert output.concealed > 0
    takeoffs, costs = await takeoffs_and_costs(deps, analyzed.study_id)
    async with session_scope(deps.sessionmaker) as session:
        study = await session.get(Study, analyzed.study_id)
    assert study is not None
    kinds: set[str] = set()
    for asset_id, cost in costs.items():
        trail = assert_trail_matches(cost, takeoffs[asset_id])
        kinds.add(trail["kind"])
        assert trail["intake_version"] == study.intake_version
        assert trail["property_class"] == "residential_rental"
        concealed = bool(takeoffs[asset_id].inputs.get("concealed"))
        assert (trail["structure"] is not None) == concealed
        if trail["kind"] != "structure_residual":
            # Priced from the study's index: the intake's 2024 and ZIP 12345.
            assert trail["year"]["cost_year"] == 2024
            assert trail["year"]["from"] == "placed in service 2024-03-01 (intake)"
            assert trail["location"]["zip"] == "12345"
            assert trail["location"]["lookup_from"] == "intake"
    assert {"unit_price", "unpriced", "structure_residual"} <= kinds
    # A table row: the base the trail names is the table's row for the label.
    roof = max(
        (t for t in takeoffs.values() if t.inputs.get("component") == "roof"),
        key=lambda t: t.quantity,
    )
    base = costs[roof.asset_id].trail["base"]
    assert (base["table"], base["row"], base["unit"]) == ("synthetic_2020", "roof", "SF")


async def test_a_corrected_row_and_a_legacy_revision_carry_trails(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """An asset correction's row is priced like any row and its trail's takeoff inputs carry
    the correction; with no intake the trail's intake version is null, and the row's old
    columns are what they were."""
    sid = analyzed.study_id
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    takeoffs, costs = await takeoffs_and_costs(deps, sid)
    for asset_id, cost in costs.items():
        trail = assert_trail_matches(cost, takeoffs[asset_id])
        assert trail["intake_version"] is None
        assert trail["location"]["from"] == "COST_LOCATION_FACTOR setting"
        assert cost.source_row is not None and (
            cost.source_row.endswith(SETTING_2026) or cost.source_row.startswith("missing:")
        )
    target = next(a for label, a in await labelled(deps, sid, 1) if label == "plumbing_fixtures")
    await start_revision_two(deps, analyzed, activity_env)
    correction = CorrectionInput(
        entity_type="asset",
        entity_id=str(target.id),
        after={"bucket": "y15", "recovery_period": "15"},
    )
    await activity_env.run(
        ProcessAssets(deps).run, process(analyzed, revision=2, corrections=(correction,))
    )
    [manual] = [a for a in await assets(deps, sid, 2) if a.method is ClassificationMethod.MANUAL]
    takeoffs, costs = await takeoffs_and_costs(deps, sid)
    trail = assert_trail_matches(costs[manual.id], takeoffs[manual.id])
    assert trail["quantity"]["inputs"]["asset_correction"]["from_asset_id"] == str(target.id)


# ------------------------------------------------------------------ the reprice (REPRICE.md)


def reprice(study_id: uuid.UUID, fingerprint: str = "f" * 64) -> CorrectionInput:
    return CorrectionInput(
        entity_type="reprice", entity_id=str(study_id), after={"fingerprint": fingerprint}
    )


async def start_rebuild(
    deps: Deps, seeded: SeededStudy, activity_env: Env, *corrections: CorrectionInput
) -> None:
    await activity_env.run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(seeded.study_id),
            stage="assets_review",
            from_revision=1,
            to_revision=2,
            corrections=corrections,
        ),
    )


async def add_2024_index(deps: Deps) -> None:
    from datetime import date

    from basis.costs.refresh import record_manual
    from basis.db.repositories.cost_index import IndexEntry

    async with session_scope(deps.sessionmaker) as session:
        await record_manual(
            session,
            [
                IndexEntry(
                    source="manual",
                    series="historical",
                    year=2024,
                    region="US",
                    value=Decimal("294.8"),
                    as_of=date(2024, 7, 1),
                    citation="Gordian, the actual July 2024 Historical Index = 294.8",
                )
            ],
            actor_id=None,
            actor_name="test",
        )


async def rows_of(deps: Deps, study_id: uuid.UUID, revision: int) -> list[tuple[Asset, Any, Any]]:
    async with session_scope(deps.sessionmaker) as session:
        return [
            (asset, takeoff, cost)
            for asset, takeoff, cost in (
                await session.execute(
                    select(Asset, Takeoff, CostEstimate)
                    .join(Takeoff, Takeoff.asset_id == Asset.id)
                    .join(CostEstimate, CostEstimate.asset_id == Asset.id)
                    .where(Asset.study_id == study_id, Asset.revision == revision)
                )
            ).all()
        ]


async def test_reprice_carries_every_classification_and_applies_a_new_index(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """REPRICE.md: revision 1 is priced at 2024 with no 2024 index loaded (the latest
    applied, flagged); the firm then enters July 2024 = 294.8. The reprice carries every
    classification (no model call, citations kept) and prices with the new value; revision
    1's rows are untouched, and its result reports the new index as not applied."""
    from datetime import date

    from basis.evidence.result import load_result_inputs

    settings = deps.settings.model_copy(
        update={"cost_table_path": str(fixture_path("cost_table.json")), "cost_year": 2026}
    )
    priced = make_deps(deps.sessionmaker, deps.storage, settings)
    sid = analyzed.study_id
    await seed_extraction(
        deps,
        analyzed,
        {
            **MEASURED_FIELDS,
            **STRUCTURE_FIELDS,
            "reconciliation.effective_date_of_appraisal": date(2025, 6, 1),
        },
    )
    await set_intake(
        deps.sessionmaker, sid, intake_document(placed_in_service_date="2024-03-01", state="NY")
    )
    first = await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    calls = await count_rows(deps.sessionmaker, LlmCall, study_id=sid)
    before = {a.id: (a, t, c) for a, t, c in await rows_of(deps, sid, 1)}
    assert any("cost:YEAR_INDEX_MISSING" in (a.review_reason or "") for a, _, _ in before.values())

    await add_2024_index(deps)
    async with session_scope(deps.sessionmaker) as session:
        study = await session.get(Study, sid)
        assert study is not None
        _, pricing = await load_result_inputs(
            session, sid, 1, study.intake, study.intake_version, settings
        )
    assert pricing is not None and pricing.stale and pricing.reasons == ["index_updated"]
    assert pricing.remedy == "reprice"

    await start_rebuild(deps, analyzed, activity_env, reprice(sid))
    second = await activity_env.run(
        ProcessAssets(priced).run, process(analyzed, revision=2, corrections=(reprice(sid),))
    )
    assert (second.assets, second.concealed) == (first.assets, first.concealed)
    assert await count_rows(deps.sessionmaker, LlmCall, study_id=sid) == calls  # no model call
    after = await rows_of(deps, sid, 2)
    assert len(after) == len(before)
    async with session_scope(deps.sessionmaker) as session:
        old_cites = (await session.scalars(select(Citation).where(Citation.study_id == sid))).all()
    cites: dict[uuid.UUID, int] = {}
    for c in old_cites:
        cites[c.asset_id] = cites.get(c.asset_id, 0) + 1
    changed = 0
    for asset, takeoff, cost in after:
        record = takeoff.inputs["carried_classification"]
        old, old_takeoff, old_cost = before[uuid.UUID(record["from_asset_id"])]
        assert record["from_revision"] == 1 and record["method"] == old.method.value
        assert (asset.category, asset.bucket, asset.recovery_period, asset.irs_section) == (
            old.category,
            old.bucket,
            old.recovery_period,
            old.irs_section,
        )
        assert (asset.method, asset.confidence, asset.convention) == (
            old.method,
            old.confidence,
            old.convention,
        )
        assert cites.get(asset.id, 0) == cites.get(old.id, 0)
        assert "cost:YEAR_INDEX_MISSING" not in (asset.review_reason or "")
        assert takeoff.quantity == old_takeoff.quantity
        assert cost.trail is not None and check(cost.trail) == []
        if cost.trail.get("year") is not None:
            index = cost.trail["year"]["index"]
            assert (index["value"], index["store"]["version"]) == ("294.8", 1)
            assert cost.trail["year"]["assumption"] is None
        changed += cost.total != old_cost.total
    assert changed > 0
    # Revision 1 is history: its rows are what they were.
    assert {a.id: c.total for a, _, c in await rows_of(deps, sid, 1)} == {
        k: c.total for k, (_, _, c) in before.items()
    }
    async with session_scope(deps.sessionmaker) as session:
        study = await session.get(Study, sid)
        assert study is not None
        _, rebuilt = await load_result_inputs(
            session, sid, 2, study.intake, study.intake_version, settings
        )
    assert rebuilt is not None and not rebuilt.stale and rebuilt.reasons == []


async def test_structure_model_correction_sets_the_square_foot_model(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """A ``structure_model`` correction carries the classifications like a reprice and
    sizes the building with the engineer's class; the concealed rows record it and the
    result reads it back."""
    from basis.evidence.pricing import load_recorded

    sid = analyzed.study_id
    settings = deps.settings.model_copy(update={"structure_rcn_source": "square_foot"})
    priced = make_deps(deps.sessionmaker, deps.storage, settings)
    await seed_extraction(deps, analyzed, {**MEASURED_FIELDS, **STRUCTURE_FIELDS})
    await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    calls = await count_rows(deps.sessionmaker, LlmCall, study_id=sid)
    model = CorrectionInput(
        entity_type="structure_model", entity_id=str(sid), after={"model_class": "luxury"}
    )
    await start_rebuild(deps, analyzed, activity_env, reprice(sid), model)
    await activity_env.run(
        ProcessAssets(priced).run,
        process(analyzed, revision=2, corrections=(reprice(sid), model)),
    )
    assert await count_rows(deps.sessionmaker, LlmCall, study_id=sid) == calls
    concealed = [(a, t, c) for a, t, c in await rows_of(deps, sid, 2) if a.component_id is None]
    assert concealed
    for _, takeoff, _ in concealed:
        assert takeoff.inputs["structure_model"]["model_class"] == "luxury"
    residual = [c for _, _, c in concealed if c.trail and c.trail["kind"] == "structure_residual"]
    assert residual and "Luxury" in residual[0].trail["structure"]["building"]["inputs"]["model"]
    async with session_scope(deps.sessionmaker) as session:
        recorded = await load_recorded(session, sid, 2)
        first = await load_recorded(session, sid, 1)
    assert recorded is not None and recorded.model_override is not None
    assert recorded.model_override.model_class == "luxury"
    assert first is not None and first.model_override is None


async def test_reprice_classifies_again_when_the_property_class_changed(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """The API refuses a reprice after a property-use change; if the intake changes between
    the request and the rebuild, nothing is carried (a 39-year building cannot keep 27.5-year
    classifications) and the event says so."""
    sid = analyzed.study_id
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    calls = await count_rows(deps.sessionmaker, LlmCall, study_id=sid)
    await set_intake(
        deps.sessionmaker,
        sid,
        intake_document(
            property_use="nonresidential",
            dwelling_unit_income_share=None,
            transient_lodging=True,
        ),
    )
    await start_rebuild(deps, analyzed, activity_env, reprice(sid))
    await activity_env.run(
        ProcessAssets(deps).run, process(analyzed, revision=2, corrections=(reprice(sid),))
    )
    assert await count_rows(deps.sessionmaker, LlmCall, study_id=sid) > calls
    async with session_scope(deps.sessionmaker) as session:
        events = (
            await session.scalars(
                select(StudyEvent).where(
                    StudyEvent.study_id == sid,
                    StudyEvent.event_type == "assets.reprice_reclassified",
                )
            )
        ).all()
    assert [e.code for e in events] == ["BASIS-ASSETS-020"]
    assert events[0].detail == {"classified_as": "residential_rental", "now": "nonresidential"}
    assert all("carried_classification" not in t.inputs for _, t, _ in await rows_of(deps, sid, 2))
