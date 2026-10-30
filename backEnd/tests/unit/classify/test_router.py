"""Router: convention -> rule -> model; the six-part context key; firm and room scoping.

BUILD_PLAN 6.1 ``unit/classify/test_router.py``; the firm's conventions and the context key
(docs/review_2026-10-15/CLASSIFICATION_CONVENTIONS.md sections 3, 5 and 6): a convention
beats a rule row and says whose ruling it is, and never matches another room or firm.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

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
from basis.domain.rules import RULES_VERSION, RuleHit
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


FIRM = uuid.UUID("0000000a-0000-4000-8000-000000000001")
OTHER_FIRM = uuid.UUID("0000000a-0000-4000-8000-000000000002")
SEEDING_STUDY = uuid.UUID("0000000b-0000-4000-8000-000000000001")
REVIEWER = uuid.UUID("0000000c-0000-4000-8000-000000000001")
APPROVED_AT = datetime(2026, 10, 29, 18, 40, tzinfo=UTC)


def cached(
    key: str, chunk: str | None = str(STRUCTURAL_CHUNK), **changes: Any
) -> CachedClassification:
    values: dict[str, Any] = {
        "key": key,
        "category": "Garage door",
        "section": "1250",
        "bucket": Bucket.Y27_5,
        "asset_class": None,
        "citations": (
            ()
            if chunk is None
            else (CitationClaim(chunk_id=chunk, excerpt="A garage door is a door of the building"),)
        ),
        "source": "approved_asset:x",
        "org_id": FIRM,
        "study_id": SEEDING_STUDY,
        "reviewer_id": REVIEWER,
        "approved_at": APPROVED_AT,
        "approvals": 3,
    }
    values.update(changes)
    return CachedClassification(**values)


def garage_key(room_type: str | None = "garage") -> str:
    return cache_key("garage_door", None, None, "residential_rental", room_type)


async def test_cache_hit_makes_no_model_call_and_reverifies_citations() -> None:
    key = garage_key()
    provider = ScriptedProvider([])
    ctx = make_ctx(provider, cache={key: cached(key)}, org_id=FIRM)
    result = await classify(component(), ctx)
    assert provider.requests == []
    assert (result.method, result.cache_key, result.bucket) == ("cache", key, Bucket.Y27_5)
    assert [c.chunk_id for c in result.citations] == [STRUCTURAL_CHUNK]
    assert not result.needs_review and result.confidence == 0.95


async def test_cache_hit_whose_citation_no_longer_resolves_is_flagged() -> None:
    key = garage_key()
    stale = cached(key, chunk="00000000-0000-4000-8000-00000000dead")
    ctx = make_ctx(ScriptedProvider([]), cache={key: stale}, org_id=FIRM)
    result = await classify(component(), ctx)
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
    residential = garage_key()
    nonresidential = cache_key("garage_door", None, None, "nonresidential", "garage")
    assert residential != nonresidential
    ctx = make_ctx(
        ScriptedProvider([]),
        property_class="nonresidential",
        cache={residential: cached(residential)},
        org_id=FIRM,
    )
    method, found = route(component(), ctx)
    assert (method, found) == ("llm", None)


def test_cache_key_has_six_parts_normalized_and_versioned() -> None:
    key = cache_key(" Garage Door ", "Steel", "Built-In", "residential_rental", "Garage")
    assert key == f"garage_door|steel|built_in|garage|residential_rental|{RULES_VERSION}"
    assert len(key.split("|")) == 6
    # The room is normalized like the rules table's (aliases included); unknown is empty.
    assert cache_key("x", None, None, "nonresidential") == f"x||||nonresidential|{RULES_VERSION}"
    assert cache_key("x", None, None, "nonresidential", "Restroom") == cache_key(
        "x", None, None, "nonresidential", "bathroom"
    )
    with pytest.raises(ValueError):
        cache_key("x", None, None, "commercial")


async def test_a_convention_beats_a_rule_row_and_says_whose_ruling_it_is() -> None:
    # carpet is a 5-year rule row; the firm's engineer ruled it building in bedrooms.
    key = cache_key("carpet", None, None, "residential_rental", "bedroom")
    ruling = cached(
        key,
        chunk=None,
        category="Glued wall-to-wall carpet",
        rule_key="carpet",
        rule_bucket=Bucket.Y5,
        agrees_with_rules=False,
        rationale="glued down over the slab",
    )
    provider = ScriptedProvider([])
    ctx = make_ctx(provider, cache={key: ruling}, org_id=FIRM)
    assert route(component("carpet", room_type="bedroom"), ctx)[0] == "cache"
    result = await classify(component("carpet", room_type="bedroom"), ctx)
    assert provider.requests == []
    assert (result.method, result.bucket, result.section) == ("cache", Bucket.Y27_5, "1250")
    # Disagreeing with the rules table is the point of a ruling, not a reason for review;
    # an uncited ruling is not citation_unverified.
    assert (result.needs_review, result.review_reasons, result.citations) == (False, (), ())
    assert result.rationale == (
        f"firm convention (approved by {REVIEWER} on study {SEEDING_STUDY}, 2026-10-29; "
        "3 approvals); agrees with the rules table: no (carpet says y5); uncited; "
        "reason: glued down over the slab"
    )
    assert result.convention == {
        "key": key,
        "study_id": str(SEEDING_STUDY),
        "reviewer_id": str(REVIEWER),
        "approved_at": APPROVED_AT.isoformat(),
        "approvals": 3,
        "rule_key": "carpet",
        "agrees_with_rules": False,
        "citation": "firm convention, uncited",
    }
    # The rules table still answers the same label in another room.
    assert route(component("carpet", room_type="bathroom"), ctx)[0] == "rule"


async def test_a_convention_that_agrees_with_the_rules_says_so() -> None:
    key = cache_key("carpet", None, None, "residential_rental", "bedroom")
    ruling = cached(
        key,
        bucket=Bucket.Y5,
        section="1245",
        approvals=1,
        rule_key="carpet",
        rule_bucket=Bucket.Y5,
        agrees_with_rules=True,
    )
    result = await classify(
        component("carpet", room_type="bedroom"),
        make_ctx(ScriptedProvider([]), cache={key: ruling}, org_id=FIRM),
    )
    assert result.method == "cache" and result.recovery_period == Decimal("5")
    assert "1 approval); agrees with the rules table: yes (carpet says y5)" in result.rationale
    assert result.convention is not None and result.convention["citation"] is None


async def test_an_inconsistent_convention_still_goes_to_review() -> None:
    # The label heuristics' warnings are answered by the ruling; an impossible section and
    # bucket pair is not.
    key = garage_key()
    broken = cached(key, section="1245")  # 1245 cannot hold y27_5
    result = await classify(
        component(), make_ctx(ScriptedProvider([]), cache={key: broken}, org_id=FIRM)
    )
    assert result.method == "cache" and result.needs_review
    assert [r for r in result.review_reasons if r.startswith("validation:")]


async def test_no_rule_row_is_named_in_the_rationale() -> None:
    key = garage_key()
    result = await classify(
        component(), make_ctx(ScriptedProvider([]), cache={key: cached(key)}, org_id=FIRM)
    )
    assert result.rationale.endswith("agrees with the rules table: n/a (no rule row)")
    assert result.convention is not None and result.convention["agrees_with_rules"] is None


async def test_a_convention_does_not_match_another_room() -> None:
    key = garage_key("garage")
    ctx = make_ctx(ScriptedProvider([]), cache={key: cached(key)}, org_id=FIRM)
    assert route(component(room_type="garage"), ctx)[0] == "cache"
    assert route(component(room_type="basement"), ctx) == ("llm", None)
    assert route(component(room_type=None), ctx) == ("llm", None)


async def test_a_convention_of_another_firm_is_never_served() -> None:
    key = garage_key()
    ctx = make_ctx(ScriptedProvider([]), cache={key: cached(key)}, org_id=OTHER_FIRM)
    assert route(component(), ctx) == ("llm", None)
    unscoped = make_ctx(ScriptedProvider([]), cache={key: cached(key)})
    assert route(component(), unscoped) == ("llm", None)


async def test_the_rule_variant_for_the_room_fires_in_production() -> None:
    # The router passes the component's room to lookup: a bathroom cabinet is building
    # (ATG p.298/p.317), a kitchen cabinet keeps the 5-year base row.
    ctx = make_ctx(ScriptedProvider([]))
    _, bathroom = route(component("kitchen_cabinet", room_type="Restroom"), ctx)
    _, kitchen = route(component("kitchen_cabinet", room_type="kitchen"), ctx)
    assert isinstance(bathroom, RuleHit) and isinstance(kitchen, RuleHit)
    assert (bathroom.bucket, bathroom.context) == (Bucket.Y27_5, "room=bathroom")
    assert (kitchen.bucket, kitchen.context) == (Bucket.Y5, None)


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
