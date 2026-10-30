"""The rules table: every row complete and consistent; aliases, materials and misses.

Migrated from ``agentic/tests/test_static_rules.py`` (BUILD_PLAN 6.1): normalization,
direct and alias hits with the legacy confidences, unknown and empty labels, row
integrity and coverage. Added: material resolution of generic floor labels, the
structural-attachment conflict, contested rows, property class handling, the deliberate
changes from legacy, and (RULES_VERSION 2026-10-29.1, CLASSIFICATION_CONVENTIONS.md
section 4) the context dimension: the rows the IRS guide names in their guide bucket and
the variants by room and attachment.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest

from basis.classify.verify import MIN_EXCERPT_CHARS, normalize_text
from basis.db.enums import Bucket
from basis.domain.rooms import normalize_room_type as rooms_normalize_room_type
from basis.domain.rules import (
    ALIASES,
    PROPERTY_CLASSES,
    RECOVERY_YEARS,
    RULES,
    RULES_VERSION,
    Rule,
    hit_name,
    lookup,
    normalize_label,
    normalize_room_type,
    table_hits,
)
from basis.domain.validators import VALID_SECTION_BUCKETS


def all_rules() -> Iterator[tuple[str, Rule]]:
    """Every base row and every variant's rule, with the row key it belongs to."""
    for key, rule in RULES.items():
        yield key, rule
        for variant in rule.variants:
            yield key, variant.rule


# ----------------------------------------------------------------- normalization


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CARPET", "carpet"),
        ("light fixture", "light_fixture"),
        ("light-fixture", "light_fixture"),
        ("Kitchen Cabinet", "kitchen_cabinet"),
        ("", ""),
        (None, ""),
        ("  carpet  ", "carpet"),
        ("kitchen  /  cabinet", "kitchen_cabinet"),
        ("Carpet!", "carpet"),
        ("ｃａｒｐｅｔ", "carpet"),  # NFKC folds full-width letters
    ],
)
def test_normalize_label(raw: str | None, expected: str) -> None:
    assert normalize_label(raw) == expected


# ----------------------------------------------------------------- hits


def test_carpet_is_direct_1245_five_year_with_legacy_confidence() -> None:
    hit = lookup("carpet")
    assert hit is not None
    assert (hit.section, hit.irs_section, hit.bucket) == ("1245", "1245", Bucket.Y5)
    assert hit.recovery_period == Decimal("5")
    assert (hit.confidence, hit.via, hit.asset_class) == (0.98, "direct", "57.0")
    assert hit.contested is None


def test_parking_lot_is_15_year_land_improvement() -> None:
    hit = lookup("parking_lot")
    assert hit is not None and (hit.section, hit.bucket) == ("1250", Bucket.Y15)


def test_furniture_is_a_residential_furnishing_5_year_57_0() -> None:
    hit = lookup("furniture")
    assert hit is not None and (hit.bucket, hit.asset_class) == (Bucket.Y5, "57.0")


def test_case_and_spacing_do_not_matter() -> None:
    assert lookup("Kitchen Cabinet") == lookup("kitchen_cabinet")


def test_alias_hit_has_lower_confidence_and_names_the_canonical_row() -> None:
    hit = lookup("carpeting")
    assert hit is not None
    assert (hit.key, hit.via, hit.matched, hit.confidence) == ("carpet", "alias", "carpeting", 0.95)


@pytest.mark.parametrize(
    ("alias", "key"),
    [
        ("chandelier", "light_fixture"),
        ("swimming_pool", "pool"),
        ("cabinetry", "kitchen_cabinet"),
        ("tile_flooring", "tile"),
        ("decorative_lighting", "accent_lighting"),
        ("washing machine", "washer"),
    ],
)
def test_aliases_resolve(alias: str, key: str) -> None:
    hit = lookup(alias)
    assert hit is not None and hit.key == key


def test_trailing_plural_is_retried_as_an_alias() -> None:
    hit = lookup("ceiling_fans")
    assert hit is not None and (hit.key, hit.via) == ("ceiling_fan", "alias")


@pytest.mark.parametrize("label", ["mystery_box", "", "   ", None, "garage_door", "ac_condenser"])
def test_unknown_or_empty_label_is_a_miss(label: str | None) -> None:
    assert lookup(label) is None


# ----------------------------------------------------------------- materials, attachment


@pytest.mark.parametrize(
    ("material", "key"),
    [
        ("carpet", "carpet"),
        ("Vinyl", "vinyl_flooring"),
        ("laminate", "laminate_flooring"),
        ("ceramic tile", "tile"),
        ("hardwood", "hardwood_floor"),
    ],
)
def test_generic_floor_label_resolves_by_material(material: str, key: str) -> None:
    hit = lookup("flooring", material=material)
    assert hit is not None
    assert (hit.key, hit.via, hit.confidence) == (key, "material", 0.9)


@pytest.mark.parametrize("material", [None, "", "stone", "unknown"])
def test_generic_floor_label_without_known_material_is_a_miss(material: str | None) -> None:
    """Legacy aliased ``flooring`` to ``carpet``; a guess is not a classification."""
    assert lookup("flooring", material=material) is None
    assert lookup("floor", material=material) is None


def test_structural_attachment_contradicts_a_1245_row() -> None:
    assert lookup("kitchen_cabinet", attachment="structural") is None
    assert lookup("kitchen_cabinet", attachment="freestanding") is not None
    assert lookup("fence", attachment="structural") is not None  # 1250 row: no conflict


# ----------------------------------------------------------------- contested rows


def test_the_contested_set_is_what_section_4_leaves_contested() -> None:
    """An item the guide names takes the guide's bucket and loses its note; an item no
    document names, or where practitioners commonly differ, keeps its note."""
    contested = {k for k, r in RULES.items() if r.contested}
    assert contested == {
        # no supporting text in the reference documents (re-citation, 2026-10-15); the
        # kitchen row stays (Metro National, ATG p.100), its bathroom variant is a building row
        "kitchen_cabinet",
        "countertop",
        "doorbell",
        "laminate_flooring",
        # the documents' position adopted, studies commonly differ (2026-10-29)
        "hardwood_floor",
        # supported only for freestanding decks
        "deck",
        # rows added 2026-10-16 where the documents and the engineer's report disagree, or
        # no passage names the item (EVAL_REPORT section 19)
        "half_wall",
        "window_guard",
        "shed",
        "concrete_pad",
        # the guide puts door locks on the building side; studies treat access control as
        # 5-year (STRUCTURE_MODEL.md section 1, 2026-10-30)
        "access_control",
    }


def test_no_variant_is_contested() -> None:
    for key, rule in RULES.items():
        for variant in rule.variants:
            assert variant.rule.contested is None, key


@pytest.mark.parametrize("key", ["kitchen_cabinet", "countertop", "doorbell", "laminate_flooring"])
def test_rows_without_supporting_text_say_so_and_keep_their_legacy_bucket(key: str) -> None:
    hit = lookup(key)
    assert hit is not None and hit.contested is not None
    assert hit.contested.startswith("No supporting text was found")
    assert (hit.section, hit.bucket) == ("1245", Bucket.Y5)


def test_hardwood_floor_is_a_building_row_and_stays_contested() -> None:
    hit = lookup("hardwood_floor")
    assert hit is not None and (hit.section, hit.bucket) == ("1250", Bucket.Y27_5)
    assert hit.contested is not None
    assert "glued wood flooring as 5-year" in hit.contested
    assert "p.306" in hit.contested


def test_contested_rows_still_carry_a_citation() -> None:
    # A contested row cites the closest relevant page; resolve_rule_citations still
    # verifies it, and the contested note is what sends the asset to review.
    for key, rule in RULES.items():
        if rule.contested:
            assert rule.citations, key


def test_citations_name_real_pages() -> None:
    # The legacy table cited ATG pages 412-420; the ATG PDF (Pub 5653) has 348 pages.
    pages = {
        "IRS_IRS_COST_SEG_ATG__2024": 348,
        "IRS_REV_PROC_87_56": 42,
        "IRS_IRS_PUB_946__2024": 112,
    }
    for key, rule in all_rules():
        for citation in rule.citations:
            assert citation.doc_id in pages, (key, citation.doc_id)
            assert 1 <= citation.page <= pages[citation.doc_id], (key, citation.page)


# ----------------------------------------------------------------- property class


def test_only_building_rows_differ_by_property_class() -> None:
    """A building row (section 1250 component) is 27.5 years for residential rental and 39
    for nonresidential (ATG Exhibit A gives both); every other row is the same for both."""
    for key, rule in RULES.items():
        residential = lookup(key, property_class="residential_rental")
        nonresidential = lookup(key, property_class="nonresidential")
        assert residential is not None and nonresidential is not None
        assert residential.section == nonresidential.section == rule.section
        assert nonresidential.property_class == "nonresidential"
        if rule.building:
            assert rule.section == "1250" and rule.asset_class is None
            assert (residential.bucket, residential.recovery_period) == (
                Bucket.Y27_5,
                Decimal("27.5"),
            )
            assert (nonresidential.bucket, nonresidential.recovery_period) == (
                Bucket.Y39,
                Decimal("39"),
            )
        else:
            assert residential.bucket == nonresidential.bucket == rule.bucket
            assert residential.recovery_period == nonresidential.recovery_period
    assert {k for k, r in RULES.items() if r.building} == {
        "bathroom_accessory",
        "concrete_floor",
        "half_wall",
        "window_guard",
        # named by ATG Exhibit A, building since 2026-10-29
        *GUIDE_NAMED_BUILDING_ROWS,
        # the structure model's concealed rows (2026-10-30); data_cabling is 57.0
        "foundation",
        "structural_framing",
        "exterior_wall_assembly",
        "roofing_assembly",
        "interior_partition",
        "painting",
        "plumbing_rough_in",
        "hvac_distribution",
        "electrical_wiring",
        "fire_alarm_system",
        "access_control",
        "site_utilities",
        "hardwood_floor",
        "mirror",
    }


def test_variant_rules_differ_by_property_class_only_when_they_are_building_rows() -> None:
    for key, rule in RULES.items():
        for variant in rule.variants:
            assert variant.rule.section == "1250" or not variant.rule.building, key
            buckets = {variant.rule.bucket_for(c) for c in ("residential_rental", "nonresidential")}
            if variant.rule.building:
                assert buckets == {Bucket.Y27_5, Bucket.Y39}, key
            else:
                assert buckets == {variant.rule.bucket}, key


# ------------------------------------------------- rows added 2026-10-16 (EVAL_REPORT 19)


@pytest.mark.parametrize(
    ("label", "section", "bucket", "contested"),
    [
        ("bathroom_accessory", "1250", Bucket.Y27_5, False),
        ("concrete_floor", "1250", Bucket.Y27_5, False),
        ("half_wall", "1250", Bucket.Y27_5, True),
        ("window_guard", "1250", Bucket.Y27_5, True),
        ("shed", "1245", Bucket.Y7, True),
        ("concrete_pad", "1250", Bucket.Y15, True),
    ],
)
def test_the_new_vocabulary_labels_have_rows(
    label: str, section: str, bucket: Bucket, contested: bool
) -> None:
    hit = lookup(label)
    assert hit is not None and hit.via == "direct"
    assert (hit.section, hit.bucket) == (section, bucket)
    assert (hit.contested is not None) is contested
    assert len(hit.citations) == 2
    # the client engineer's report treats every contested one differently or the documents
    # do not name the item; the note says what the documents say instead
    if contested:
        assert "needs engineer confirmation" in (hit.contested or "")


def test_the_new_rows_match_the_engineers_buckets_for_a_nonresidential_property() -> None:
    """The report's buckets (a nonresidential property): Bathroom Accessories and Sealed
    Concrete 39, Movable Shed 7, Equipment Pads 15 agree; Half Walls and Guards (5 in the
    report) are where the documents disagree, so those rows are contested."""
    expected = {
        "bathroom_accessory": Bucket.Y39,
        "concrete_floor": Bucket.Y39,
        "shed": Bucket.Y7,
        "concrete_pad": Bucket.Y15,
        "half_wall": Bucket.Y39,
        "window_guard": Bucket.Y39,
    }
    for label, bucket in expected.items():
        hit = lookup(label, property_class="nonresidential")
        assert hit is not None and hit.bucket == bucket, label


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("towel bar", "bathroom_accessory"),
        ("Toilet-Paper Holder", "bathroom_accessory"),
        ("grab_bars", "bathroom_accessory"),
        ("storage shed", "shed"),
        ("equipment pad", "concrete_pad"),
        ("pony wall", "half_wall"),
        ("security bars", "window_guard"),
        ("garage floor", "concrete_floor"),
    ],
)
def test_the_new_rows_aliases(label: str, key: str) -> None:
    hit = lookup(label)
    assert hit is not None and hit.key == key and hit.via == "alias"


def test_a_generic_concrete_floor_resolves_by_material() -> None:
    hit = lookup("flooring", material="sealed concrete")
    assert hit is not None and hit.key == "concrete_floor" and hit.via == "material"
    assert lookup("floor", material="concrete", property_class="nonresidential").bucket == (  # type: ignore[union-attr]
        Bucket.Y39
    )


def test_a_structural_attachment_does_not_block_a_building_row() -> None:
    # "structural" contradicts a section 1245 row; a building row is section 1250 already
    hit = lookup("concrete_floor", attachment="structural")
    assert hit is not None and hit.section == "1250"
    assert lookup("shed", attachment="structural") is None


def test_unknown_property_class_is_refused() -> None:
    with pytest.raises(ValueError, match="property_class"):
        lookup("carpet", property_class="commercial")


def test_property_classes() -> None:
    assert PROPERTY_CLASSES == ("residential_rental", "nonresidential")


# --------------------------------- the context dimension (RULES_VERSION 2026-10-29.1)

# Rows the ATG's residential rental matrix (Exhibit A) names as building components: they
# take the guide's bucket and lose their contested note (CLASSIFICATION_CONVENTIONS.md 4).
GUIDE_NAMED_BUILDING_ROWS = (
    "toilet",
    "bathtub",
    "shower",
    "sink",
    "water_heater",
    "garbage_disposal",
    "light_fixture",
    "ceiling_fan",
    "smoke_detector",
    "thermostat",
    "tile",
    "bathroom_vanity",
    "closet_shelving",
)


@pytest.mark.parametrize("key", GUIDE_NAMED_BUILDING_ROWS)
def test_rows_the_guide_names_are_building_rows_without_a_note(key: str) -> None:
    residential = lookup(key)
    nonresidential = lookup(key, property_class="nonresidential")
    assert residential is not None and nonresidential is not None
    assert (residential.section, residential.bucket, residential.asset_class) == (
        "1250",
        Bucket.Y27_5,
        None,
    )
    assert (nonresidential.section, nonresidential.bucket) == ("1250", Bucket.Y39)
    assert residential.contested is None and nonresidential.contested is None
    assert residential.context is None
    assert all(c.doc_id == "IRS_IRS_COST_SEG_ATG__2024" for c in residential.citations)


@pytest.mark.parametrize(
    ("key", "section", "residential", "nonresidential", "asset_class"),
    [
        ("hardwood_floor", "1250", Bucket.Y27_5, Bucket.Y39, None),
        ("mirror", "1250", Bucket.Y27_5, Bucket.Y39, None),
        ("accent_lighting", "1245", Bucket.Y5, Bucket.Y5, "57.0"),
        ("furniture", "1245", Bucket.Y5, Bucket.Y5, "57.0"),
        ("chair", "1245", Bucket.Y5, Bucket.Y5, "57.0"),
        ("desk", "1245", Bucket.Y5, Bucket.Y5, "57.0"),
        ("filing_cabinet", "1245", Bucket.Y5, Bucket.Y5, "57.0"),
        # base rows unchanged
        ("kitchen_cabinet", "1245", Bucket.Y5, Bucket.Y5, "57.0"),
        ("countertop", "1245", Bucket.Y5, Bucket.Y5, "57.0"),
        ("doorbell", "1245", Bucket.Y5, Bucket.Y5, "57.0"),
        ("laminate_flooring", "1245", Bucket.Y5, Bucket.Y5, "57.0"),
        ("outdoor_lighting", "1250", Bucket.Y15, Bucket.Y15, "00.3"),
        ("deck", "1250", Bucket.Y15, Bucket.Y15, "00.3"),
        ("half_wall", "1250", Bucket.Y27_5, Bucket.Y39, None),
        ("window_guard", "1250", Bucket.Y27_5, Bucket.Y39, None),
        ("shed", "1245", Bucket.Y7, Bucket.Y7, None),
        ("concrete_pad", "1250", Bucket.Y15, Bucket.Y15, "00.3"),
    ],
)
def test_the_base_row_of_every_row_section_4_names(
    key: str,
    section: str,
    residential: Bucket,
    nonresidential: Bucket,
    asset_class: str | None,
) -> None:
    for property_class, bucket in (
        ("residential_rental", residential),
        ("nonresidential", nonresidential),
    ):
        hit = lookup(key, property_class=property_class)
        assert hit is not None and hit.context is None
        assert (hit.section, hit.bucket, hit.asset_class) == (section, bucket, asset_class)


@pytest.mark.parametrize(
    ("label", "room", "property_class", "section", "bucket", "asset_class", "context"),
    [
        # restroom cabinetry (ATG pp.298, 317)
        (
            "kitchen_cabinet",
            "bathroom",
            "residential_rental",
            "1250",
            Bucket.Y27_5,
            None,
            "room=bathroom",
        ),
        (
            "kitchen_cabinet",
            "bathroom",
            "nonresidential",
            "1250",
            Bucket.Y39,
            None,
            "room=bathroom",
        ),
        (
            "countertop",
            "bathroom",
            "residential_rental",
            "1250",
            Bucket.Y27_5,
            None,
            "room=bathroom",
        ),
        ("countertop", "Bathroom", "nonresidential", "1250", Bucket.Y39, None, "room=bathroom"),
        ("kitchen_cabinet", "kitchen", "residential_rental", "1245", Bucket.Y5, "57.0", None),
        # furniture by the business it serves (ATG pp.307, 308)
        ("furniture", "office", "residential_rental", "1245", Bucket.Y7, "00.11", "room=office"),
        ("furniture", "office", "nonresidential", "1245", Bucket.Y7, "00.11", "room=office"),
        ("chair", "office", "residential_rental", "1245", Bucket.Y7, "00.11", "room=office"),
        ("desk", "office", "residential_rental", "1245", Bucket.Y7, "00.11", "room=office"),
        (
            "filing_cabinet",
            "office",
            "residential_rental",
            "1245",
            Bucket.Y7,
            "00.11",
            "room=office",
        ),
        ("furniture", "bedroom", "residential_rental", "1245", Bucket.Y5, "57.0", None),
        ("furniture", "bedroom", "nonresidential", "1245", Bucket.Y5, "57.0", None),
        ("desk", "living_room", "residential_rental", "1245", Bucket.Y5, "57.0", None),
        # a room no variant names is the base row
        ("toilet", "bathroom", "residential_rental", "1250", Bucket.Y27_5, None, None),
        ("mirror", "bathroom", "residential_rental", "1250", Bucket.Y27_5, None, None),
    ],
)
def test_room_variants(
    label: str,
    room: str,
    property_class: str,
    section: str,
    bucket: Bucket,
    asset_class: str | None,
    context: str | None,
) -> None:
    hit = lookup(label, property_class=property_class, room_type=room)
    assert hit is not None
    assert (hit.section, hit.bucket, hit.asset_class, hit.context) == (
        section,
        bucket,
        asset_class,
        context,
    )
    assert hit.key == label  # the hit names the row, not the variant
    assert hit.recovery_period == RECOVERY_YEARS[bucket]
    assert hit.property_class == property_class


@pytest.mark.parametrize(
    ("attachment", "section", "bucket", "asset_class", "context"),
    [
        ("removable", "1245", Bucket.Y5, "57.0", "attachment=removable"),
        ("Freestanding", "1245", Bucket.Y5, "57.0", "attachment=freestanding"),
        ("permanent", "1250", Bucket.Y27_5, None, None),
        ("unknown", "1250", Bucket.Y27_5, None, None),
        (None, "1250", Bucket.Y27_5, None, None),
        ("structural", "1250", Bucket.Y27_5, None, None),
    ],
)
def test_a_removable_mirror_is_decor_and_a_permanent_one_a_building_component(
    attachment: str | None,
    section: str,
    bucket: Bucket,
    asset_class: str | None,
    context: str | None,
) -> None:
    hit = lookup("mirror", attachment=attachment)
    assert hit is not None
    assert (hit.section, hit.bucket, hit.asset_class, hit.context) == (
        section,
        bucket,
        asset_class,
        context,
    )
    assert hit.contested is None


def test_a_removable_mirror_is_five_years_for_both_property_classes() -> None:
    for property_class in PROPERTY_CLASSES:
        hit = lookup("bathroom mirror", attachment="removable", property_class=property_class)
        assert hit is not None and hit.key == "mirror" and hit.via == "alias"
        assert (hit.bucket, hit.context) == (Bucket.Y5, "attachment=removable")
    permanent = lookup("mirror", attachment="permanent", property_class="nonresidential")
    assert permanent is not None and permanent.bucket is Bucket.Y39


@pytest.mark.parametrize("room", ["restroom", "Restroom", "bath", "powder room", "bathroom"])
def test_room_aliases_resolve_to_the_bathroom_variant(room: str) -> None:
    hit = lookup("cabinet", room_type=room)
    assert hit is not None
    assert (hit.key, hit.via, hit.bucket, hit.context) == (
        "kitchen_cabinet",
        "alias",
        Bucket.Y27_5,
        "room=bathroom",
    )


@pytest.mark.parametrize("room", ["ballroom", "", "   ", "other", "ambiguous", "kitchen"])
def test_an_unknown_or_unnamed_room_falls_to_the_base_row(room: str) -> None:
    for label in ("kitchen_cabinet", "furniture", "mirror"):
        assert lookup(label, room_type=room) == lookup(label)


def test_no_room_type_is_the_base_row_and_existing_calls_are_unchanged() -> None:
    for key in RULES:
        positional = lookup(key, None, None, "residential_rental")
        assert positional == lookup(key, room_type=None)
        assert positional is not None and positional.context is None


def test_a_structural_attachment_is_judged_on_the_effective_rule() -> None:
    # the bathroom variant is a building row, so "structural" does not contradict it
    hit = lookup("kitchen_cabinet", attachment="structural", room_type="bathroom")
    assert hit is not None and hit.section == "1250" and hit.context == "room=bathroom"
    assert lookup("kitchen_cabinet", attachment="structural") is None
    assert lookup("furniture", attachment="structural", room_type="office") is None


def test_a_room_variant_ignores_the_attachment_and_an_attachment_variant_the_room() -> None:
    cabinet = lookup("kitchen_cabinet", attachment="freestanding", room_type="bathroom")
    assert cabinet is not None and cabinet.context == "room=bathroom"
    mirror = lookup("mirror", attachment="removable", room_type="office")
    assert mirror is not None and mirror.context == "attachment=removable"


def test_a_variant_hit_carries_the_variants_citations_and_category() -> None:
    office = lookup("furniture", room_type="office")
    base = lookup("furniture")
    assert office is not None and base is not None
    assert {c.page for c in office.citations} == {4, 307}  # Rev. Proc. 87-56 00.11, ATG p.307
    assert {c.page for c in base.citations} == {297, 308}
    assert office.category != base.category
    bathroom = lookup("countertop", room_type="bathroom")
    assert bathroom is not None and {c.page for c in bathroom.citations} == {298, 317}
    assert bathroom.contested is None  # the base row's note does not carry to the variant


def test_table_hits_reach_every_row_and_every_variant() -> None:
    """The enumeration the rule_citations eval and the real-corpus test walk: a hit per row
    and per variant, each variant reached in its own context (none shadowed)."""
    hits = table_hits()
    assert len(hits) == len(RULES) + sum(len(r.variants) for r in RULES.values())
    names = [hit_name(h) for h in hits]
    assert len(set(names)) == len(names)
    assert "kitchen_cabinet[room=bathroom]" in names and "mirror[attachment=freestanding]" in names
    for hit in hits:
        rule = RULES[hit.key]
        if hit.context is None:
            assert hit.citations == rule.citations
        else:
            assert hit.citations in {v.rule.citations for v in rule.variants}
    assert table_hits("nonresidential")[0].property_class == "nonresidential"


def test_the_room_normalization_is_shared_with_basis_domain_rooms() -> None:
    assert rooms_normalize_room_type is normalize_room_type
    assert normalize_room_type("Restroom") == "bathroom"
    assert normalize_room_type(None) == ""


def test_accent_lighting_is_five_year_57_0() -> None:
    hit = lookup("accent_lighting")
    assert hit is not None
    assert (hit.section, hit.bucket, hit.asset_class, hit.contested) == (
        "1245",
        Bucket.Y5,
        "57.0",
        None,
    )
    assert all(c.page in (297, 313) for c in hit.citations)


@pytest.mark.parametrize(
    "alias", ["track_lighting", "decorative_lighting", "neon_lighting", "landscape_lighting"]
)
def test_accent_lighting_aliases(alias: str) -> None:
    hit = lookup(alias)
    assert hit is not None and (hit.key, hit.via, hit.bucket) == (
        "accent_lighting",
        "alias",
        Bucket.Y5,
    )
    assert ALIASES[alias] == "accent_lighting"


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("chandelier", "light_fixture"),
        ("sconce", "light_fixture"),
        ("recessed_light", "light_fixture"),
        ("lamp", "furniture"),  # a lamp is a furnishing, not a building light fixture
    ],
)
def test_general_lighting_aliases_stay_with_light_fixture(label: str, key: str) -> None:
    hit = lookup(label)
    assert hit is not None and hit.key == key
    assert lookup("decorative_lighting").key != "light_fixture"  # type: ignore[union-attr]


# ----------------------------------------------------------------- table integrity


def test_every_row_has_section_bucket_life_and_a_citation() -> None:
    for key, rule in all_rules():
        assert rule.key == key  # a variant's rule keeps the row's key
        assert rule.category and rule.irs_note
        assert rule.section in ("1245", "1250")
        assert rule.bucket in RECOVERY_YEARS
        assert rule.citations, f"{key} has no citation"
        for citation in rule.citations:
            assert citation.doc_id.startswith("IRS_")
            assert citation.page >= 1
            assert citation.excerpt.strip()


def test_every_citation_excerpt_is_long_enough_to_verify() -> None:
    # resolve_rule_citations verifies an excerpt as a normalized substring of a chunk and
    # refuses anything shorter than MIN_EXCERPT_CHARS, so a shorter excerpt can never cite.
    for key, rule in all_rules():
        for citation in rule.citations:
            assert len(normalize_text(citation.excerpt)) >= MIN_EXCERPT_CHARS, (
                f"{key}: {citation.excerpt!r}"
            )


def test_section_and_bucket_are_consistent_for_every_row() -> None:
    for key, rule in all_rules():
        assert rule.bucket in VALID_SECTION_BUCKETS[rule.section], key


def test_recovery_period_matches_bucket() -> None:
    expected = {Bucket.Y5: 5, Bucket.Y7: 7, Bucket.Y15: 15, Bucket.Y27_5: Decimal("27.5")}
    for _, rule in all_rules():
        assert rule.recovery_period == Decimal(expected[rule.bucket])
        assert rule.bucket is not Bucket.Y27_5 or rule.building  # 27.5 only as a building row


def test_every_alias_points_to_a_row_and_never_shadows_one() -> None:
    for alias, key in ALIASES.items():
        assert key in RULES, alias
        assert alias not in RULES, alias
        assert normalize_label(alias) == alias


def test_coverage_is_at_least_the_legacy_table() -> None:
    assert len(RULES) >= 45
    assert len(ALIASES) >= 50
    for label in ("carpet", "tile", "hardwood_floor", "vinyl_flooring"):
        assert lookup(label) is not None
    for label in ("refrigerator", "dishwasher", "stove", "washer", "dryer"):
        assert lookup(label) is not None
    for label in ("parking_lot", "sidewalk", "landscaping", "fence"):
        hit = lookup(label)
        assert hit is not None and (hit.section, hit.bucket) == ("1250", Bucket.Y15)


def test_rules_version_is_set() -> None:
    assert RULES_VERSION and "|" not in RULES_VERSION


def test_rules_version_changed_with_the_context_dimension() -> None:
    # part of the cache key: a classification cached under an older table is never served;
    # 2026-10-30.1 added the structure model's thirteen concealed rows
    assert RULES_VERSION == "2026-10-30.1"


def test_every_variant_is_well_formed() -> None:
    for key, rule in RULES.items():
        for variant in rule.variants:
            when = variant.when
            assert when.rooms or when.attachments or when.property_classes, key  # never always
            assert not variant.rule.variants, key  # one level only
            assert when.property_classes <= set(PROPERTY_CLASSES), key
            for room in when.rooms:
                assert normalize_room_type(room) == room, (key, room)  # canonical room types
            for attachment in when.attachments:
                assert normalize_label(attachment) == attachment, (key, attachment)
    assert {k for k, r in RULES.items() if r.variants} == {
        "kitchen_cabinet",
        "countertop",
        "mirror",
        "closet_shelving",
        "furniture",
        "chair",
        "desk",
        "filing_cabinet",
    }


# ------------------------------------------------- freestanding shelving (the context dimension)


def test_a_freestanding_shelf_is_a_furnishing_and_a_built_in_one_finish_carpentry() -> None:
    built_in = lookup("shelf", attachment="permanent", property_class="nonresidential")
    assert built_in is not None and (built_in.section, built_in.bucket, built_in.context) == (
        "1250",
        Bucket.Y39,
        None,
    )
    loose = lookup("storage shelf", attachment="freestanding", property_class="nonresidential")
    assert loose is not None and (loose.section, loose.bucket, loose.asset_class) == (
        "1245",
        Bucket.Y5,
        "57.0",
    )
    assert (loose.key, loose.context) == ("closet_shelving", "attachment=freestanding")
    assert lookup("shelves", attachment="unknown") is not None
    assert lookup("shelves", attachment="unknown").context is None  # type: ignore[union-attr]
