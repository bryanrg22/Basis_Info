"""``analyze_photo`` with per-class count fusion (``VISION_COUNT_FUSION=per_class``).

Real rows (Postgres container) and storage; the providers are offline fakes keyed by model.
The production default sends three calls per photo and stores the fused counts, both raw
answers and the source per class; a failed second model never fails the photo; with fusion
off the activity is the two-call step it was.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from lifecycletest_support import SeededStudy, make_deps
from llm_fakes import PHOTO_COST_USD, FixtureVisionProvider, ModelKeyedProvider
from sqlalchemy import select

from basis.activities import Deps
from basis.activities.analyze_photo import AnalyzePhoto, analysis_uri
from basis.db.models import Component, LlmCall
from basis.db.session import session_scope
from basis.llm import ScriptedTurn, Usage
from basis.llm.errors import ProviderUnavailable
from basis.vision import analyze, fusion
from basis.vision.schemas import Component as VisionComponent
from basis.vision.schemas import ModelPhotoAnalysis
from basis.workflow.contracts import AnalyzePhotoInput

from .conftest import Env

pytestmark = pytest.mark.integration

BASE = "gpt-6.1-sol"
SURFACES_MODEL = "gpt-6-luna"  # EVAL_REPORT 28.1
SECOND = "claude-opus-5-5"
USAGE = Usage(input_tokens=1000, output_tokens=200)


def production(deps: Deps, llm: Any = None) -> Deps:
    """Deps with the per-photo step's production settings (the suite pins the baseline)."""
    settings = deps.settings.model_copy(
        update={
            "vision_model": BASE,
            "vision_surfaces_model": SURFACES_MODEL,
            "vision_photo_mode": "decomposed",
            "vision_count_fusion": "per_class",
        }
    )
    assert settings.vision_count_fusion_active
    if llm is None:
        return make_deps(deps.sessionmaker, deps.storage, settings)
    return make_deps(deps.sessionmaker, deps.storage, settings, llm=llm)


def photo_input(seeded: SeededStudy, index: int = 0) -> AnalyzePhotoInput:
    doc = str(seeded.photo_ids[index])
    return AnalyzePhotoInput(
        study_id=str(seeded.study_id),
        document_id=doc,
        revision=1,
        idempotency_key=f"k-fusion-{doc}",
        budget_remaining_usd=5.0,
    )


def kitchen(cabinets: int, windows: int) -> ModelPhotoAnalysis:
    def c(label: str, count: int) -> VisionComponent:
        return VisionComponent.model_validate(
            {
                "label": label,
                "count": count,
                "material": "wood",
                "attachment": "permanent",
                "confidence": 0.9,
            }
        )

    return ModelPhotoAnalysis(
        room_type="kitchen",  # type: ignore[arg-type]
        room_confidence=0.9,
        floor=1,
        room_ordinal=1,
        components=[
            c("kitchen_cabinet", cabinets),
            c("window", windows),
            c("electrical_outlet", 3),
        ],
        description="",
    )


SURFACES = ModelPhotoAnalysis(
    room_type="kitchen",  # type: ignore[arg-type]
    room_confidence=0.9,
    floor=1,
    room_ordinal=1,
    components=[],
    description="",
)


def keyed(second: list[ScriptedTurn]) -> ModelKeyedProvider:
    return ModelKeyedProvider(
        turns={
            (BASE, "photo_countables"): [ScriptedTurn(parsed=kitchen(6, 2), usage=USAGE)],
            (SURFACES_MODEL, "photo_surfaces"): [ScriptedTurn(parsed=SURFACES, usage=USAGE)],
            (SECOND, "photo_countables"): second,
        }
    )


async def components(deps: Deps, seeded: SeededStudy) -> dict[str, Component]:
    async with session_scope(deps.sessionmaker) as session:
        rows = (
            await session.scalars(select(Component).where(Component.study_id == seeded.study_id))
        ).all()
    return {c.label: c for c in rows}


async def calls(deps: Deps, seeded: SeededStudy) -> list[LlmCall]:
    async with session_scope(deps.sessionmaker) as session:
        return list(
            (
                await session.scalars(select(LlmCall).where(LlmCall.study_id == seeded.study_id))
            ).all()
        )


async def test_the_production_default_fuses_three_calls(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    fixture = FixtureVisionProvider()
    prod = production(deps, fixture)
    output = await activity_env.run(AnalyzePhoto(prod).run, photo_input(seeded))
    assert sorted(r.model for r in fixture.requests) == sorted([BASE, SURFACES_MODEL, SECOND])
    assert output.cost_usd == pytest.approx(3 * PHOTO_COST_USD)
    assert not output.needs_review
    assert len(await calls(prod, seeded)) == 3
    rows = await components(prod, seeded)
    assert rows["kitchen_cabinet"].raw["fusion"] == {
        "table_version": fusion.TABLE_VERSION,
        "status": "fused",
        "count_sources": {
            c: SECOND for c in ("window", "door", "light_fixture", "kitchen_cabinet")
        },
    }


async def test_fused_counts_are_written_and_both_answers_stored(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    second = ScriptedTurn(parsed=kitchen(10, 4), usage=USAGE)
    prod = production(deps, keyed([second]))
    output = await activity_env.run(AnalyzePhoto(prod).run, photo_input(seeded))
    assert not output.needs_review
    rows = await components(prod, seeded)
    # Cabinets and windows from the second model, outlets from the base.
    assert rows["kitchen_cabinet"].raw["count"] == 10
    assert rows["window"].raw["count"] == 4
    assert rows["electrical_outlet"].raw["count"] == 3
    uri = analysis_uri(prod.bucket, seeded.study_id, seeded.photo_ids[0], 1)
    stored = json.loads(await prod.storage.read(uri))
    assert stored["fusion"]["status"] == "fused"
    assert stored["fusion"]["classes"]["kitchen_cabinet"] == {
        "source": "second",
        "model": SECOND,
        "base": 6,
        "second": 10,
        "count": 10,
    }
    assert stored["fusion"]["classes"]["electrical_outlet"]["model"] == BASE
    assert stored["fusion"]["second_answer"]["components"][0]["count"] == 10
    assert [c["model"] for c in stored["calls"]] == [BASE, SURFACES_MODEL, SECOND]
    assert len(stored["responses"]) == 3


async def test_a_failed_second_model_flags_the_photo_and_keeps_the_base_counts(
    deps: Deps, seeded: SeededStudy, activity_env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(analyze, "FUSION_RETRY_DELAYS_S", (0.0, 0.0))
    down = ScriptedTurn(raise_error=ProviderUnavailable("down", provider="x", status=503))
    prod = production(deps, keyed([down, down, down]))
    output = await activity_env.run(AnalyzePhoto(prod).run, photo_input(seeded))
    assert output.needs_review  # the workflow flags the photo; the photo did not fail
    rows = await components(prod, seeded)
    assert rows["kitchen_cabinet"].raw["count"] == 6
    assert rows["kitchen_cabinet"].raw["fusion"]["status"] == "fallback"
    assert rows["kitchen_cabinet"].raw["fusion"]["count_sources"] == {}
    stored = json.loads(
        await prod.storage.read(analysis_uri(prod.bucket, seeded.study_id, seeded.photo_ids[0], 1))
    )
    assert stored["analysis"]["review_reason"] == "count_fusion_fallback:provider_unavailable"
    assert stored["fusion"]["second_answer"] is None
    assert len(stored["fusion"]["classes"]) == len(fusion.PER_CLASS_SOURCE)


async def test_fusion_off_is_the_previous_two_call_step(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    """Regression: VISION_COUNT_FUSION=off sends exactly the two decomposed calls."""
    fixture = FixtureVisionProvider()
    settings = deps.settings.model_copy(
        update={"vision_photo_mode": "decomposed", "vision_count_fusion": "off"}
    )
    off = make_deps(deps.sessionmaker, deps.storage, settings, llm=fixture)
    output = await activity_env.run(AnalyzePhoto(off).run, photo_input(seeded))
    assert len(fixture.requests) == 2 and output.cost_usd == pytest.approx(2 * PHOTO_COST_USD)
    rows = await components(off, seeded)
    assert "fusion" not in rows["kitchen_cabinet"].raw
    stored = json.loads(
        await off.storage.read(analysis_uri(off.bucket, seeded.study_id, seeded.photo_ids[0], 1))
    )
    assert "fusion" not in stored and len(stored["calls"]) == 2
