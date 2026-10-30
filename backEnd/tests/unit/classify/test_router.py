"""Router: convention -> rule -> model; the six-part context key; firm scoping; the tiers.

BUILD_PLAN 6.1 ``unit/classify/test_router.py``; the firm's conventions and the context key
(docs/review_2026-10-15/CLASSIFICATION_CONVENTIONS.md sections 3, 5, 6 and 9): a convention
beats a rule row and says whose ruling it is and which tier matched; the exact key wins,
then the same label and room, then the label alone (never an uncontested rule row from
another room, never a catch-all label, never another firm); rulings that disagree with
equal weight inside a tier serve nothing and flag the asset.
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

from basis.classify.cache import (
    CachedClassification,
    ConventionConflict,
    ConventionMatch,
    cache_key,
    tier_key,
    tier_keys,
)
from basis.classify.router import Route, classify, route
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
    assert route(component(), ctx) == Route("llm", None, None)


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
        "3 approvals); agrees with the rules table: no (carpet says y5); matched: exact; "
        "uncited; reason: glued down over the slab"
    )
    assert result.convention == {
        "key": key,
        "matched_tier": "exact",
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
    assert result.rationale.endswith(
        "agrees with the rules table: n/a (no rule row); matched: exact"
    )
    assert result.convention is not None and result.convention["agrees_with_rules"] is None


def test_tier_keys_most_specific_first() -> None:
    key = cache_key("fireplace", "wood", "permanent", "nonresidential", "living_room")
    assert tier_keys(key) == (
        ("exact", key),
        ("label_room", f"fireplace|||living_room|nonresidential|{RULES_VERSION}"),
        ("label", f"fireplace||||nonresidential|{RULES_VERSION}"),
    )
    # No room: the label-and-room tier would be the label tier, so it is not repeated.
    assert [t for t, _ in tier_keys(cache_key("fireplace", "wood", None, "nonresidential"))] == [
        "exact",
        "label",
    ]
    # A generic floor keeps its material (the rules table resolves it by material).
    floor = cache_key("flooring", "vinyl", "removable", "residential_rental", "kitchen")
    assert (
        tier_key(floor, "label_room")
        == f"flooring|vinyl||kitchen|residential_rental|{RULES_VERSION}"
    )
    assert tier_key(floor, "label") == f"flooring|vinyl|||residential_rental|{RULES_VERSION}"
    # A surface finish keeps its material too: wallpaper and drywall are different answers.
    wall = cache_key("wall_finish", "wallpaper", "permanent", "nonresidential", "hallway")
    assert tier_key(wall, "label") == f"wall_finish|wallpaper|||nonresidential|{RULES_VERSION}"
    # The catch-all label never generalizes.
    assert tier_keys(cache_key("other", "wood", "freestanding", "nonresidential", "exterior")) == (
        ("exact", f"other|wood|freestanding|exterior|nonresidential|{RULES_VERSION}"),
    )
    with pytest.raises(ValueError):
        tier_keys("not|a|key")


def door(material: str | None, attachment: str | None, room: str | None = "garage") -> str:
    return cache_key("garage_door", material, attachment, "residential_rental", room)


async def test_each_tier_matches_and_the_more_specific_one_wins() -> None:
    # garage_door has no rule row. Three rulings: the exact context, the same room read
    # with another material, and another room.
    exact, same_room, other_room = (
        door("steel", "permanent"),
        door("wood", "permanent"),
        door("steel", "permanent", "basement"),
    )
    rows = {
        exact: cached(exact, category="Exact", approvals=1),
        same_room: cached(same_room, category="Same room", approvals=5),
        other_room: cached(other_room, category="Other room", approvals=9),
    }
    ctx = make_ctx(ScriptedProvider([]), cache=rows, org_id=FIRM)

    served = route(component(material="steel", attachment="permanent"), ctx)
    assert served.method == "cache" and isinstance(served.found, ConventionMatch)
    assert (served.found.tier, served.found.cached.key) == ("exact", exact)
    # The exact ruling wins even with fewer approvals than the coarser ones.
    result = await classify(component(material="steel", attachment="permanent"), ctx)
    assert (result.category, result.cache_key) == ("Exact", exact)
    assert result.convention is not None and result.convention["matched_tier"] == "exact"
    assert "; matched: exact" in result.rationale

    # No exact row for a glass door read as removable: the same label and room serves.
    glass = await classify(component(material="glass", attachment="removable"), ctx)
    assert (glass.method, glass.category, glass.cache_key) == ("cache", "Same room", same_room)
    assert glass.convention is not None and glass.convention["matched_tier"] == "label_room"
    assert glass.convention["key"] == same_room
    assert f"; matched: label and room (ruling keyed {same_room})" in glass.rationale
    assert not glass.needs_review

    # A room the firm never ruled on: the label alone serves (no rule row to outrank).
    attic = await classify(component(room_type="attic"), ctx)
    assert attic.method == "cache" and attic.convention is not None
    assert attic.convention["matched_tier"] == "label"
    assert "; matched: label only (ruling keyed " in attic.rationale
    # Inside the label tier the most approved ruling serves (9 beats 5 beats 1).
    assert attic.cache_key == other_room
    # An unknown room is the label tier too.
    assert route(component(room_type=None), ctx).found.tier == "label"  # type: ignore[union-attr]


async def test_a_label_only_match_does_not_outrank_an_uncontested_rule_row() -> None:
    # The firm ruled the kitchen sink 5-year (against the guide); a bathroom sink is a
    # building row the guide names (ATG p.315), so the ruling from another room does not
    # outrank it. The same ruling does serve a kitchen sink read with another material.
    kitchen = cache_key("sink", "ceramic", "permanent", "residential_rental", "kitchen")
    ruling = cached(kitchen, chunk=None, category="Utility sink", bucket=Bucket.Y5, section="1245")
    ctx = make_ctx(ScriptedProvider([]), cache={kitchen: ruling}, org_id=FIRM)
    bath = route(component("sink", material="porcelain", room_type="bathroom"), ctx)
    assert bath.method == "rule" and isinstance(bath.found, RuleHit)
    assert (bath.found.key, bath.found.bucket, bath.conflict) == ("sink", Bucket.Y27_5, None)
    served = await classify(component("sink", material="porcelain", room_type="kitchen"), ctx)
    assert (served.method, served.bucket) == ("cache", Bucket.Y5)
    assert served.convention is not None and served.convention["matched_tier"] == "label_room"
    # A contested rule row (hardwood_floor: the guide names no passage) is outranked.
    hallway = cache_key("hardwood_floor", "wood", "permanent", "residential_rental", "hallway")
    glued = cached(hallway, chunk=None, category="Glued hardwood", bucket=Bucket.Y5, section="1245")
    ctx = make_ctx(ScriptedProvider([]), cache={hallway: glued}, org_id=FIRM)
    lobby = await classify(component("hardwood_floor", room_type="lobby"), ctx)
    assert (lobby.method, lobby.bucket) == ("cache", Bucket.Y5)
    assert lobby.convention is not None and lobby.convention["matched_tier"] == "label"
    assert "rule_contested" not in lobby.review_reasons


async def test_rulings_that_disagree_with_equal_weight_fall_to_the_rules_table() -> None:
    # Two bedroom carpet rulings, one approval each: glued (building) and loose-laid (5-year).
    glued = cache_key("carpet", "wool", "permanent", "residential_rental", "bedroom")
    loose = cache_key("carpet", "nylon", "removable", "residential_rental", "bedroom")
    rows = {
        glued: cached(glued, chunk=None, category="Glued carpet", approvals=1),
        loose: cached(
            loose,
            chunk=None,
            category="Loose carpet",
            bucket=Bucket.Y5,
            section="1245",
            approvals=1,
        ),
    }
    ctx = make_ctx(ScriptedProvider([]), cache=rows, org_id=FIRM)
    decided = route(component("carpet", material="polyester", room_type="bedroom"), ctx)
    assert decided.method == "rule" and isinstance(decided.found, RuleHit)
    assert isinstance(decided.conflict, ConventionConflict)
    assert decided.conflict.tier == "label_room"
    assert sorted(r.key for r in decided.conflict.rows) == sorted([glued, loose])
    result = await classify(component("carpet", material="polyester", room_type="bedroom"), ctx)
    assert (result.method, result.bucket, result.needs_review) == ("rule", Bucket.Y5, True)
    assert result.review_reasons[-1] == "convention_conflict:label_room"
    # No coarser tier is consulted once a tier disagrees: a hallway carpet ruling that
    # would agree with neither is not reached.
    hallway = cache_key("carpet", "wool", "permanent", "residential_rental", "hallway")
    rows[hallway] = cached(hallway, chunk=None, category="Hallway", approvals=7)
    ctx = make_ctx(ScriptedProvider([]), cache=rows, org_id=FIRM)
    again = route(component("carpet", material="polyester", room_type="bedroom"), ctx)
    assert again.method == "rule" and again.conflict is not None

    # With no rule row the model decides, and the asset is flagged the same way.
    a, b = door("steel", "permanent"), door("wood", "permanent")
    split = {
        a: cached(a, chunk=None, approvals=2),
        b: cached(b, chunk=None, bucket=Bucket.Y5, section="1245", approvals=2),
    }
    provider = ScriptedProvider(happy_turns())
    modelled = await classify(
        component(material="glass", attachment="permanent"),
        make_ctx(provider, cache=split, org_id=FIRM),
    )
    assert modelled.method == "llm" and len(provider.requests) == 2
    assert "convention_conflict:label_room" in modelled.review_reasons and modelled.needs_review


async def test_the_most_approved_answer_wins_a_disagreement() -> None:
    a, b = door("steel", "permanent"), door("wood", "permanent")
    rows = {
        a: cached(a, chunk=None, category="Building", approvals=3),
        b: cached(
            b, chunk=None, category="Personal", bucket=Bucket.Y5, section="1245", approvals=2
        ),
    }
    ctx = make_ctx(ScriptedProvider([]), cache=rows, org_id=FIRM)
    result = await classify(component(material="glass", attachment="permanent"), ctx)
    assert (result.method, result.category, result.cache_key) == ("cache", "Building", a)
    assert not result.needs_review and result.convention is not None
    assert result.convention["approvals"] == 3


async def test_a_generic_floor_keeps_its_material_in_every_tier() -> None:
    vinyl = cache_key("flooring", "vinyl", "removable", "residential_rental", "kitchen")
    ruling = cached(vinyl, chunk=None, category="Sheet vinyl", bucket=Bucket.Y5, section="1245")
    ctx = make_ctx(ScriptedProvider([]), cache={vinyl: ruling}, org_id=FIRM)
    # The same material read as permanent: served (label and room).
    kitchen = route(
        component("flooring", material="vinyl", attachment="permanent", room_type="kitchen"), ctx
    )
    assert isinstance(kitchen.found, ConventionMatch) and kitchen.found.tier == "label_room"
    # In another room the vinyl rule row (uncontested) outranks the label tier.
    bedroom = route(component("flooring", material="vinyl", room_type="bedroom"), ctx)
    assert bedroom.method == "rule" and isinstance(bedroom.found, RuleHit)
    assert (bedroom.found.key, bedroom.conflict) == ("vinyl_flooring", None)
    # Another material is another floor: the rules table resolves it by material.
    carpet = route(component("flooring", material="carpet", room_type="kitchen"), ctx)
    assert carpet.method == "rule" and isinstance(carpet.found, RuleHit)
    assert (carpet.found.key, carpet.conflict) == ("carpet", None)
    # No material at all: no ruling, no rule row, the model.
    assert route(component("flooring", room_type="kitchen"), ctx) == Route("llm", None, None)
    # Laminate's rule row is contested, so the firm's laminate ruling serves in any room,
    # and only for laminate.
    laminate = cache_key("flooring", "laminate", "removable", "residential_rental", "kitchen")
    glued = cached(laminate, chunk=None, category="Glued laminate")
    ctx = make_ctx(ScriptedProvider([]), cache={laminate: glued}, org_id=FIRM)
    hall = route(component("flooring", material="laminate", room_type="hallway"), ctx)
    assert isinstance(hall.found, ConventionMatch) and hall.found.tier == "label"
    tile = route(component("flooring", material="tile", room_type="hallway"), ctx)
    assert tile.method == "rule" and isinstance(tile.found, RuleHit) and tile.found.key == "tile"


async def test_the_catch_all_label_matches_at_the_exact_tier_only() -> None:
    bench = cache_key("other", "wood", "freestanding", "residential_rental", "exterior")
    ruling = cached(bench, chunk=None, category="Bench", bucket=Bucket.Y5, section="1245")
    ctx = make_ctx(ScriptedProvider([]), cache={bench: ruling}, org_id=FIRM)
    exact = route(
        component("other", material="wood", attachment="freestanding", room_type="exterior"), ctx
    )
    assert exact.method == "cache"
    assert route(component("other", material="fabric", room_type="exterior"), ctx) == Route(
        "llm", None, None
    )
    assert route(component("other", room_type="kitchen"), ctx) == Route("llm", None, None)


async def test_a_convention_of_another_firm_is_never_served_at_any_tier() -> None:
    key = garage_key()
    ctx = make_ctx(ScriptedProvider([]), cache={key: cached(key)}, org_id=OTHER_FIRM)
    assert route(component(), ctx) == Route("llm", None, None)
    assert route(component(material="wood"), ctx) == Route("llm", None, None)
    assert route(component(room_type="attic"), ctx) == Route("llm", None, None)
    unscoped = make_ctx(ScriptedProvider([]), cache={key: cached(key)})
    assert route(component(), unscoped) == Route("llm", None, None)


async def test_the_rule_variant_for_the_room_fires_in_production() -> None:
    # The router passes the component's room to lookup: a bathroom cabinet is building
    # (ATG p.298/p.317), a kitchen cabinet keeps the 5-year base row.
    ctx = make_ctx(ScriptedProvider([]))
    bathroom = route(component("kitchen_cabinet", room_type="Restroom"), ctx).found
    kitchen = route(component("kitchen_cabinet", room_type="kitchen"), ctx).found
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
