"""Router: rule hit -> no model; cache hit -> no model; miss -> model; property class in key.

BUILD_PLAN 6.1 ``unit/classify/test_router.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from classifytest_support import (
    ASSET_CLASS_CHUNK,
    CARPET_CHUNK,
    STRUCTURAL_CHUNK,
    answer_turn,
    call,
    classification,
    component,
    happy_turns,
    make_ctx,
    tools_turn,
)

from basis.classify.cache import CachedClassification, cache_key
from basis.classify.router import classify, route
from basis.classify.schemas import CitationClaim
from basis.db.enums import Bucket
from basis.domain.rules import RULES_VERSION
from basis.llm.provider import ScriptedProvider


async def test_rule_hit_makes_no_model_call_and_resolves_its_citations() -> None:
    provider = ScriptedProvider([])
    result = await classify(component("carpet", room_type="bedroom"), make_ctx(provider))
    assert provider.requests == []
    assert (result.method, result.rule_key, result.bucket, result.section) == (
        "rule",
        "carpet",
        Bucket.Y5,
        "1245",
    )
    assert result.recovery_period == Decimal("5")
    assert {c.chunk_id for c in result.citations} == {CARPET_CHUNK, ASSET_CLASS_CHUNK}
    assert all(c.method.startswith("rule:") for c in result.citations)
    assert (result.needs_review, result.review_reasons, result.dropped) == (False, (), ())
    assert result.confidence == 0.98


async def test_rule_citation_that_does_not_resolve_flags_the_asset() -> None:
    # The test reference corpus has no chunk for parking_lot's citation (ATG p.314).
    result = await classify(component("parking_lot"), make_ctx(ScriptedProvider([])))
    assert result.method == "rule"
    assert result.citations == ()
    assert [d.reason for d in result.dropped] == ["not_found"]
    assert result.needs_review and result.review_reasons == ("citation_unverified",)


async def test_rule_without_a_reference_corpus_flags_citation_unverified() -> None:
    ctx = make_ctx(ScriptedProvider([]), reference_version_id=None)
    result = await classify(component("carpet"), ctx)
    assert result.citations == () and len(result.dropped) == 2
    assert result.review_reason == "citation_unverified"


async def test_contested_rule_goes_to_review() -> None:
    # toilet was the contested example until RULES_VERSION 2026-10-29.1 made it a building
    # row; doorbell is still contested (no document names it)
    result = await classify(component("doorbell"), make_ctx(ScriptedProvider([])))
    assert result.method == "rule" and result.bucket is Bucket.Y5
    assert "rule_contested:doorbell" in result.review_reasons


def cached(key: str, chunk: str = str(STRUCTURAL_CHUNK)) -> CachedClassification:
    return CachedClassification(
        key=key,
        category="Garage door",
        section="1250",
        bucket=Bucket.Y27_5,
        asset_class=None,
        citations=(
            CitationClaim(chunk_id=chunk, excerpt="A garage door is a door of the building"),
        ),
        source="approved_asset:x",
    )


async def test_cache_hit_makes_no_model_call_and_reverifies_citations() -> None:
    key = cache_key("garage_door", None, None, "residential_rental")
    provider = ScriptedProvider([])
    result = await classify(component(), make_ctx(provider, cache={key: cached(key)}))
    assert provider.requests == []
    assert (result.method, result.cache_key, result.bucket) == ("cache", key, Bucket.Y27_5)
    assert [c.chunk_id for c in result.citations] == [STRUCTURAL_CHUNK]
    assert not result.needs_review and result.confidence == 0.95


async def test_cache_hit_whose_citation_no_longer_resolves_is_flagged() -> None:
    key = cache_key("garage_door", None, None, "residential_rental")
    stale = cached(key, chunk="00000000-0000-4000-8000-00000000dead")
    result = await classify(component(), make_ctx(ScriptedProvider([]), cache={key: stale}))
    assert result.method == "cache" and result.citations == ()
    assert result.review_reasons == ("citation_unverified",)


async def test_miss_goes_to_the_model() -> None:
    provider = ScriptedProvider(happy_turns())
    result = await classify(component(), make_ctx(provider))
    assert len(provider.requests) == 2 and provider.remaining_turns == 0
    assert result.method == "llm" and result.bucket is Bucket.Y27_5
    assert [c.chunk_id for c in result.citations] == [STRUCTURAL_CHUNK]
    assert not result.needs_review
    assert len(result.calls) == 2 and result.cost_usd > 0


async def test_property_class_is_part_of_the_cache_key() -> None:
    residential = cache_key("garage_door", None, None, "residential_rental")
    nonresidential = cache_key("garage_door", None, None, "nonresidential")
    assert residential != nonresidential
    ctx = make_ctx(
        ScriptedProvider([]),
        property_class="nonresidential",
        cache={residential: cached(residential)},
    )
    method, found = route(component(), ctx)
    assert (method, found) == ("llm", None)


def test_cache_key_is_normalized_and_versioned() -> None:
    key = cache_key(" Garage Door ", "Steel", "Built-In", "residential_rental")
    assert key == f"garage_door|steel|built_in|residential_rental|{RULES_VERSION}"
    assert cache_key("x", None, None, "nonresidential") == f"x|||nonresidential|{RULES_VERSION}"
    with pytest.raises(ValueError):
        cache_key("x", None, None, "commercial")


async def test_rule_wins_over_cache() -> None:
    key = cache_key("carpet", None, None, "residential_rental")
    ctx = make_ctx(ScriptedProvider([]), cache={key: cached(key)})
    assert route(component("carpet"), ctx)[0] == "rule"


async def test_no_provider_means_review_with_the_building_bucket() -> None:
    result = await classify(component(), make_ctx(None))
    assert (result.method, result.bucket, result.section, result.confidence) == (
        "llm",
        Bucket.Y27_5,
        "1250",
        0.0,
    )
    assert result.review_reasons == ("classifier_unavailable",)
    nonres = await classify(component(), make_ctx(None, property_class="nonresidential"))
    assert nonres.bucket is Bucket.Y39


async def test_inconsistent_model_answer_is_flagged_by_the_validators() -> None:
    provider = ScriptedProvider(
        [
            tools_turn(call("search_reference", {"query": "garage door"})),
            answer_turn(classification(bucket="y39")),  # residential rental uses 27.5
        ]
    )
    result = await classify(component(), make_ctx(provider))
    assert result.needs_review
    assert "validation:BUILDING_BUCKET_PROPERTY_CLASS" in result.review_reasons
