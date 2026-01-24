"""
Unit tests for static classification rules (Phase 3 optimization).

Tests the static rules engine for:
- Direct component lookups
- Alias matching
- Section/bucket validation
- Miss behavior (fall through to cache/LLM)
"""

import pytest

from agentic.agents.static_classification_rules import (
    get_static_classification,
    get_all_static_components,
    get_all_aliases,
    ASSET_CLASSIFICATION_RULES,
    COMPONENT_ALIASES,
    _normalize_component_name,
)


class TestNormalization:
    """Tests for component name normalization."""

    def test_normalize_lowercase(self):
        assert _normalize_component_name("CARPET") == "carpet"

    def test_normalize_spaces(self):
        assert _normalize_component_name("light fixture") == "light_fixture"

    def test_normalize_hyphens(self):
        assert _normalize_component_name("light-fixture") == "light_fixture"

    def test_normalize_mixed(self):
        assert _normalize_component_name("Kitchen Cabinet") == "kitchen_cabinet"

    def test_normalize_empty(self):
        assert _normalize_component_name("") == ""

    def test_normalize_whitespace(self):
        assert _normalize_component_name("  carpet  ") == "carpet"


class TestDirectLookup:
    """Tests for direct component lookups (no aliases)."""

    def test_carpet_classification(self):
        result = get_static_classification("carpet")
        assert result is not None
        assert result["classification"]["depreciation_bucket"] == "5-year"
        assert result["classification"]["irs_section"] == "1245"
        assert result["classification"]["recovery_period_years"] == 5
        assert result["confidence"] == 0.98
        assert result["from_static_rules"] is True

    def test_parking_lot_is_15_year(self):
        result = get_static_classification("parking_lot")
        assert result is not None
        assert result["classification"]["depreciation_bucket"] == "15-year"
        assert result["classification"]["irs_section"] == "1250"  # Land improvement

    def test_furniture_is_7_year(self):
        result = get_static_classification("furniture")
        assert result is not None
        assert result["classification"]["depreciation_bucket"] == "7-year"
        assert result["classification"]["asset_class"] == "00.11"

    def test_light_fixture_has_citations(self):
        result = get_static_classification("light_fixture")
        assert result is not None
        assert len(result["citations"]) > 0
        assert result["citations"][0]["doc_id"].startswith("IRS_")

    def test_case_insensitive(self):
        result = get_static_classification("CARPET")
        assert result is not None
        assert result["classification"]["depreciation_bucket"] == "5-year"


class TestAliasLookup:
    """Tests for alias matching."""

    def test_carpeting_alias(self):
        result = get_static_classification("carpeting")
        assert result is not None
        assert result["classification"]["depreciation_bucket"] == "5-year"
        assert result.get("matched_alias") == "carpeting"
        assert result.get("canonical_name") == "carpet"
        assert result["confidence"] == 0.95  # Lower confidence for alias

    def test_chandelier_alias(self):
        result = get_static_classification("chandelier")
        assert result is not None
        assert result["classification"]["depreciation_bucket"] == "5-year"
        assert result.get("canonical_name") == "light_fixture"

    def test_refrigerator_direct_match(self):
        # refrigerator has its own rule now, not just an alias
        result = get_static_classification("refrigerator")
        assert result is not None
        assert result["classification"]["depreciation_bucket"] == "5-year"

    def test_swimming_pool_alias(self):
        result = get_static_classification("swimming_pool")
        assert result is not None
        assert result["classification"]["depreciation_bucket"] == "15-year"
        assert result.get("canonical_name") == "pool"


class TestMissBehavior:
    """Tests for components not in static rules."""

    def test_unknown_returns_none(self):
        result = get_static_classification("custom_antique_fixture")
        assert result is None

    def test_empty_returns_none(self):
        result = get_static_classification("")
        assert result is None

    def test_whitespace_returns_none(self):
        result = get_static_classification("   ")
        assert result is None


class TestPropertyType:
    """Tests for property type handling."""

    def test_residential_default(self):
        # Currently property_type doesn't affect rules, but API supports it
        result = get_static_classification("carpet", property_type="residential")
        assert result is not None

    def test_commercial_supported(self):
        # API accepts commercial, rules are same for now
        result = get_static_classification("carpet", property_type="commercial")
        assert result is not None


class TestRulesIntegrity:
    """Tests for rules data integrity."""

    def test_all_rules_have_required_fields(self):
        required_fields = {"section", "bucket", "life_years", "irs_note", "citations"}
        for component, rule in ASSET_CLASSIFICATION_RULES.items():
            for field in required_fields:
                assert field in rule, f"{component} missing {field}"

    def test_all_rules_have_valid_section(self):
        valid_sections = {"1245", "1250"}
        for component, rule in ASSET_CLASSIFICATION_RULES.items():
            assert rule["section"] in valid_sections, f"{component} invalid section"

    def test_all_rules_have_valid_bucket(self):
        valid_buckets = {"5-year", "7-year", "15-year", "27.5-year", "39-year"}
        for component, rule in ASSET_CLASSIFICATION_RULES.items():
            assert rule["bucket"] in valid_buckets, f"{component} invalid bucket"

    def test_section_bucket_consistency(self):
        """Verify section/bucket combinations are valid per IRS rules."""
        section_1245_buckets = {"5-year", "7-year", "15-year"}
        section_1250_buckets = {"15-year", "27.5-year", "39-year"}

        for component, rule in ASSET_CLASSIFICATION_RULES.items():
            if rule["section"] == "1245":
                assert rule["bucket"] in section_1245_buckets, (
                    f"{component}: Section 1245 cannot have {rule['bucket']}"
                )
            elif rule["section"] == "1250":
                assert rule["bucket"] in section_1250_buckets, (
                    f"{component}: Section 1250 cannot have {rule['bucket']}"
                )

    def test_life_years_matches_bucket(self):
        bucket_to_years = {
            "5-year": 5,
            "7-year": 7,
            "15-year": 15,
            "27.5-year": 27,  # or 28 rounded
            "39-year": 39,
        }
        for component, rule in ASSET_CLASSIFICATION_RULES.items():
            expected_years = bucket_to_years.get(rule["bucket"])
            if expected_years:
                assert rule["life_years"] == expected_years, (
                    f"{component}: life_years {rule['life_years']} != {expected_years}"
                )

    def test_all_aliases_point_to_valid_rules(self):
        for alias, canonical in COMPONENT_ALIASES.items():
            assert canonical in ASSET_CLASSIFICATION_RULES, (
                f"Alias '{alias}' points to non-existent rule '{canonical}'"
            )


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_all_static_components(self):
        components = get_all_static_components()
        assert len(components) > 40  # At least 40+ rules
        assert "carpet" in components
        assert "parking_lot" in components

    def test_get_all_aliases(self):
        aliases = get_all_aliases()
        assert len(aliases) > 50  # At least 50+ aliases
        assert "carpeting" in aliases
        assert aliases["carpeting"] == "carpet"


class TestCoverageMetrics:
    """Tests to verify rule coverage meets Phase 3 goals."""

    def test_minimum_rule_count(self):
        """Phase 3 target: ~50 common components."""
        assert len(ASSET_CLASSIFICATION_RULES) >= 45

    def test_minimum_alias_count(self):
        """Aliases should expand coverage significantly."""
        assert len(COMPONENT_ALIASES) >= 50

    def test_common_flooring_covered(self):
        flooring_types = ["carpet", "tile", "hardwood_floor", "vinyl_flooring"]
        for f in flooring_types:
            result = get_static_classification(f)
            assert result is not None, f"{f} not covered"

    def test_common_appliances_covered(self):
        appliances = ["refrigerator", "dishwasher", "stove", "washer", "dryer"]
        for a in appliances:
            result = get_static_classification(a)
            assert result is not None, f"{a} not covered"

    def test_land_improvements_covered(self):
        land_improvements = ["parking_lot", "sidewalk", "landscaping", "fence"]
        for li in land_improvements:
            result = get_static_classification(li)
            assert result is not None, f"{li} not covered"
            assert result["classification"]["irs_section"] == "1250"
            assert result["classification"]["depreciation_bucket"] == "15-year"
