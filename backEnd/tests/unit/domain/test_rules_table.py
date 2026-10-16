"""The rules table: every row complete and consistent; aliases, materials and misses.

Migrated from ``agentic/tests/test_static_rules.py`` (BUILD_PLAN 6.1): normalization,
direct and alias hits with the legacy confidences, unknown and empty labels, row
integrity and coverage. Added: material resolution of generic floor labels, the
structural-attachment conflict, contested rows, property class handling, and the
deliberate changes from legacy.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from basis.classify.verify import MIN_EXCERPT_CHARS, normalize_text
from basis.db.enums import Bucket
from basis.domain.rules import (
    ALIASES,
    PROPERTY_CLASSES,
    RECOVERY_YEARS,
    RULES,
    RULES_VERSION,
    lookup,
    normalize_label,
)
from basis.domain.validators import VALID_SECTION_BUCKETS

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


def test_furniture_is_7_year_00_11() -> None:
    hit = lookup("furniture")
    assert hit is not None and (hit.bucket, hit.asset_class) == (Bucket.Y7, "00.11")


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
        ("decorative_lighting", "light_fixture"),
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


@pytest.mark.parametrize("material", [None, "", "concrete", "unknown"])
def test_generic_floor_label_without_known_material_is_a_miss(material: str | None) -> None:
    """Legacy aliased ``flooring`` to ``carpet``; a guess is not a classification."""
    assert lookup("flooring", material=material) is None
    assert lookup("floor", material=material) is None


def test_structural_attachment_contradicts_a_1245_row() -> None:
    assert lookup("kitchen_cabinet", attachment="structural") is None
    assert lookup("kitchen_cabinet", attachment="freestanding") is not None
    assert lookup("fence", attachment="structural") is not None  # 1250 row: no conflict


# ----------------------------------------------------------------- contested rows


@pytest.mark.parametrize(
    "key", ["toilet", "sink", "bathtub", "shower", "light_fixture", "tile", "hardwood_floor"]
)
def test_rows_that_conflict_with_the_regulation_are_contested(key: str) -> None:
    hit = lookup(key)
    assert hit is not None and hit.contested is not None
    assert "1.48-1(e)(2)" in hit.contested
    assert hit.bucket is Bucket.Y5  # legacy bucket kept; the engineer decides


def test_uncontested_rows_have_no_note() -> None:
    contested = {k for k, r in RULES.items() if r.contested}
    assert contested == {
        # Treas. Reg. 1.48-1(e)(2) rows (contested since the migration)
        "toilet",
        "sink",
        "bathtub",
        "shower",
        "light_fixture",
        "tile",
        "hardwood_floor",
        # no supporting text in the reference documents (re-citation, 2026-10-15)
        "kitchen_cabinet",
        "countertop",
        "garbage_disposal",
        "ceiling_fan",
        "bathroom_vanity",
        "mirror",
        "smoke_detector",
        "thermostat",
        "doorbell",
        "laminate_flooring",
        "closet_shelving",
        "water_heater",
        # supported only for office furniture / freestanding decks
        "furniture",
        "chair",
        "deck",
    }


@pytest.mark.parametrize(
    "key",
    [
        "kitchen_cabinet",
        "countertop",
        "garbage_disposal",
        "ceiling_fan",
        "bathroom_vanity",
        "mirror",
        "smoke_detector",
        "thermostat",
        "doorbell",
        "laminate_flooring",
        "closet_shelving",
        "water_heater",
    ],
)
def test_rows_without_supporting_text_say_so_and_keep_their_legacy_bucket(key: str) -> None:
    hit = lookup(key)
    assert hit is not None and hit.contested is not None
    assert hit.contested.startswith("No supporting text was found")
    assert (hit.section, hit.bucket) == ("1245", Bucket.Y5)


def test_contested_rows_still_carry_a_citation() -> None:
    # A contested row cites the closest relevant page; resolve_rule_citations still
    # verifies it, and the contested note is what sends the asset to review.
    for key, rule in RULES.items():
        if rule.contested:
            assert rule.citations, key


def test_citations_name_real_pages() -> None:
    # The legacy table cited ATG pages 412-420; the ATG PDF (Pub 5653) has 348 pages.
    pages = {"IRS_IRS_COST_SEG_ATG__2024": 348, "IRS_REV_PROC_87_56": 42}
    for key, rule in RULES.items():
        for citation in rule.citations:
            assert citation.doc_id in pages, (key, citation.doc_id)
            assert 1 <= citation.page <= pages[citation.doc_id], (key, citation.page)


# ----------------------------------------------------------------- property class


def test_no_current_row_differs_by_property_class() -> None:
    for key in RULES:
        residential = lookup(key, property_class="residential_rental")
        nonresidential = lookup(key, property_class="nonresidential")
        assert residential is not None and nonresidential is not None
        assert (residential.bucket, residential.section) == (
            nonresidential.bucket,
            nonresidential.section,
        )
        assert nonresidential.property_class == "nonresidential"


def test_unknown_property_class_is_refused() -> None:
    with pytest.raises(ValueError, match="property_class"):
        lookup("carpet", property_class="commercial")


def test_property_classes() -> None:
    assert PROPERTY_CLASSES == ("residential_rental", "nonresidential")


# ----------------------------------------------------------------- table integrity


def test_every_row_has_section_bucket_life_and_a_citation() -> None:
    for key, rule in RULES.items():
        assert rule.key == key
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
    for key, rule in RULES.items():
        for citation in rule.citations:
            assert len(normalize_text(citation.excerpt)) >= MIN_EXCERPT_CHARS, (
                f"{key}: {citation.excerpt!r}"
            )


def test_section_and_bucket_are_consistent_for_every_row() -> None:
    for key, rule in RULES.items():
        assert rule.bucket in VALID_SECTION_BUCKETS[rule.section], key


def test_recovery_period_matches_bucket() -> None:
    expected = {Bucket.Y5: 5, Bucket.Y7: 7, Bucket.Y15: 15}
    for rule in RULES.values():
        assert rule.recovery_period == Decimal(expected[rule.bucket])


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
