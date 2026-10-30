"""Cross-stage checks between classification, takeoff and cost for one asset."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from basis.db.enums import Bucket
from basis.domain.costs import estimate_cost
from basis.domain.cross_validation import (
    check_classification_takeoff,
    check_takeoff_cost,
    cross_validate,
)
from basis.domain.takeoffs import Takeoff, estimate_quantity


def codes(issues: list) -> list[str]:  # type: ignore[type-arg]
    return [i.code for i in issues]


def test_clean_asset_has_no_review_issues() -> None:
    takeoff = estimate_quantity("light_fixture", 100, 2)
    cost = estimate_cost(takeoff, 2020, 1)
    assert cross_validate(section="1245", bucket=Bucket.Y5, takeoff=takeoff, cost=cost) == []


def test_large_sf_area_under_1245_is_a_warning() -> None:
    takeoff = estimate_quantity("tile", 200)  # 230 SF
    [first, *rest] = check_classification_takeoff("1245", Bucket.Y5, takeoff)
    assert (first.code, first.severity) == ("SECTION_1245_SF_LARGE", "warning")
    assert codes(rest) == ["UNIT_BUCKET_MISMATCH"]  # informational
    small = estimate_quantity("tile", 50)  # 57.5 SF
    assert "SECTION_1245_SF_LARGE" not in codes(
        check_classification_takeoff("1245", Bucket.Y5, small)
    )


def test_39_year_in_each_is_informational() -> None:
    takeoff = estimate_quantity("door", 100, 1)
    issues = check_classification_takeoff("1250", Bucket.Y39, takeoff)
    assert codes(issues) == ["39_YEAR_EA_UNIT", "UNIT_BUCKET_MISMATCH"]
    assert not any(i.needs_review for i in issues)


def test_unit_bucket_table_uses_the_enum_not_substrings() -> None:
    """Legacy matched '5_year' inside '15_year' and applied the 5-year row."""
    takeoff = estimate_quantity("sidewalk", 100, 1)  # EA
    assert check_classification_takeoff("1250", Bucket.Y15, takeoff) == []


def test_missing_cost_is_a_warning() -> None:
    takeoff = estimate_quantity("garage_door", 100, 1)
    [issue] = check_takeoff_cost(takeoff, None)
    assert (issue.code, issue.needs_review) == ("COST_ROW_MISSING", True)


def test_unit_cost_outside_the_typical_range_is_a_warning() -> None:
    takeoff = estimate_quantity("light_fixture", 100, 1)
    cost = estimate_cost(takeoff, 2020, 1)
    cheap = replace(cost, base_unit_cost=Decimal("5"), unit_cost=Decimal("5"), total=Decimal("5"))
    assert codes(check_takeoff_cost(takeoff, cheap)) == ["COST_BELOW_RANGE"]
    dear = replace(
        cost, base_unit_cost=Decimal("2500"), unit_cost=Decimal("2500"), total=Decimal("2500")
    )
    assert codes(check_takeoff_cost(takeoff, dear)) == ["COST_ABOVE_RANGE"]


def test_carpet_range_compares_per_square_foot() -> None:
    takeoff = estimate_quantity("carpet", 150)  # SY; base 61.20/SY = 6.80/SF, in range
    assert check_takeoff_cost(takeoff, estimate_cost(takeoff, 2020, 1)) == []


def test_total_that_does_not_recompute_is_an_error() -> None:
    takeoff = estimate_quantity("light_fixture", 100, 2)
    cost = estimate_cost(takeoff, 2020, 1)
    wrong = replace(cost, total=cost.total + 10)
    [issue] = check_takeoff_cost(takeoff, wrong)
    assert (issue.code, issue.severity) == ("COST_TOTAL_MISMATCH", "error")


def test_large_adjustment_is_informational() -> None:
    takeoff = estimate_quantity("light_fixture", 100, 1)
    cost = estimate_cost(takeoff, 2010, "0.85")  # x0.7675 (2010) x 0.85 = x0.652
    issues = check_takeoff_cost(takeoff, cost)
    assert codes(issues) == ["COST_MULTIPLIER_DETECTED"]
    assert not issues[0].needs_review


def test_zero_quantity_takeoff_skips_the_total_check() -> None:
    takeoff = Takeoff(component="light_fixture", quantity=Decimal(0), unit="EA", formula="dup")
    cost = estimate_cost(takeoff, 2020, 1)
    assert cost.total == 0 and check_takeoff_cost(takeoff, cost) == []
