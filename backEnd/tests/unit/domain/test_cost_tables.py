"""Cost tables: unit cost x quantity x year x location, with explicit errors (BUILD_PLAN 6.1).

The year and location factors are RSMeans' published indices since EVAL_REPORT section 45
(``tests/unit/domain/test_cost_indices.py`` checks the tables themselves): 2018 is
222.9 / 239.1 = 0.9322, 2020 is 1.0000, a year after the book's last index is 1.0000 with
the assumption on the estimate, and a state is the mean of its listed cities.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from basis.domain.cost_indices import indices
from basis.domain.costs import (
    TYPICAL_UNIT_COSTS,
    CostError,
    MissingCostRow,
    UnitMismatch,
    UnknownCostYear,
    estimate_cost,
    has_cost_row,
    location_factor_for_state,
    year_factor,
    year_factor_index,
)
from basis.domain.takeoffs import Takeoff, estimate_quantity

HCI_2018 = (
    "HCI 222.9 (July 2018) / 239.1 (Jan 2020, est.), RSMeans Building Construction Costs 2020 p.778"
)


def test_carpet_in_square_yards_uses_the_per_sf_row_times_nine() -> None:
    # standard carpet 4.50 + 2.00 + 0.30 = 6.80/SF = 61.20/SY; x0.9322 (2018) = 57.05064
    takeoff = estimate_quantity("carpet", 150)  # 18.33 SY
    cost = estimate_cost(takeoff, 2018, 1)
    assert cost.base_unit_cost == Decimal("61.20")
    assert cost.unit_cost == Decimal("57.05")
    assert cost.total == Decimal("1045.73")  # 57.05 x 18.33 = 1045.7265
    assert cost.unit == "SY"
    assert "carpet" in cost.source_row and "per SF" in cost.source_row


def test_cabinets_per_linear_foot() -> None:
    """The legacy per-LF cabinet row still prices a ``cabinet`` run."""
    takeoff = estimate_quantity("cabinet", 144)  # 48 LF
    cost = estimate_cost(takeoff, 2018, "1.0")
    assert cost.unit_cost == Decimal("240.51")  # 258 x 0.9322 = 240.5076
    assert cost.total == Decimal("11544.48")


def test_kitchen_cabinets_are_units_with_no_legacy_row() -> None:
    """v0.9 (EVAL_REPORT 27): kitchen cabinets are counted in units, which only a cost
    table row prices (tests/unit/domain/test_cost_table.py)."""
    takeoff = estimate_quantity("kitchen_cabinet", 144, 18)
    assert (takeoff.quantity, takeoff.unit) == (Decimal("18.00"), "EA")
    with pytest.raises(MissingCostRow):
        estimate_cost(takeoff, 2020, "1.0")


def test_location_and_year_factors_multiply() -> None:
    takeoff = estimate_quantity("light_fixture", 100, 3)
    cost = estimate_cost(takeoff, 2018, location_factor_for_state("ca"))
    # (85 + 35 + 5) x 0.9322 (2018) x 1.001 (CA: the mean of its 37 cities) = 116.64...
    assert cost.unit_cost == Decimal("116.64")
    assert cost.total == Decimal("349.92")
    assert (cost.year_factor, cost.location_factor, cost.cost_year) == (
        Decimal("0.9322"),
        Decimal("1.001"),
        2018,
    )
    assert cost.year_assumption is None


def test_tile_and_tiers() -> None:
    tile = estimate_quantity("tile", 75)  # 86.25 SF
    assert estimate_cost(tile, 2020, 1).total == Decimal("1444.69")  # 16.75 x 86.25
    premium = estimate_cost(tile, 2020, 1, tier="premium")
    assert premium.unit_cost == Decimal("26.00")
    older = estimate_cost(tile, 2010, 1)  # 183.5 / 239.1 = 0.7675
    assert older.unit_cost == Decimal("12.86")  # 16.75 x 0.7675 = 12.8556


def test_missing_row_is_an_explicit_error() -> None:
    with pytest.raises(MissingCostRow):
        estimate_cost(estimate_quantity("garage_door", 100), 2020, 1)
    assert not has_cost_row("garage_door") and has_cost_row("carpet")


@pytest.mark.parametrize("year", [1969, 1900, 1])
def test_a_year_before_the_index_table_is_an_explicit_error(year: int) -> None:
    with pytest.raises(UnknownCostYear, match="1970-2020"):
        estimate_cost(estimate_quantity("carpet", 100), year, 1)
    with pytest.raises(UnknownCostYear):
        year_factor(year)


@pytest.mark.parametrize("year", [2021, 2024, 2026])
def test_a_year_after_the_latest_index_is_priced_at_it_with_the_assumption(year: int) -> None:
    """COST_INDICES.md section 4: the latest published index applies, the estimate says so
    and carries the assumption for the asset's review flag."""
    cost = estimate_cost(estimate_quantity("carpet", 100), year, 1)
    assert cost.year_factor == Decimal("1.0000")
    assert cost.unit_cost == estimate_cost(estimate_quantity("carpet", 100), 2020, 1).unit_cost
    assert cost.year_assumption is not None
    assert f"no published cost index for {year} is loaded" in cost.year_assumption
    assert f"x1.0000 for {year} = HCI 239.1 (Jan 2020, est.) / 239.1 (Jan 2020, est.)" in (
        cost.source_row
    )
    assert f"no {year} index loaded, the latest applied (assumption)" in cost.source_row
    assert year_factor_index(year).extrapolated


def test_unit_mismatch_is_an_explicit_error() -> None:
    odd = Takeoff(component="cabinet", quantity=Decimal(3), unit="EA", formula="count")
    with pytest.raises(UnitMismatch):
        estimate_cost(odd, 2020, 1)


@pytest.mark.parametrize("factor", [0, -1, "nan"])
def test_bad_location_factor_is_refused(factor: object) -> None:
    with pytest.raises(CostError):
        estimate_cost(estimate_quantity("carpet", 100), 2020, factor)  # type: ignore[arg-type]


def test_unknown_state_is_refused() -> None:
    with pytest.raises(CostError, match="no location factor for state 'ZZ'"):
        location_factor_for_state("ZZ")


def test_tables_are_complete() -> None:
    assert len(indices().by_state) == 51  # 50 states and DC
    for state in indices().by_state:
        assert location_factor_for_state(state) > 0
    for tiers in TYPICAL_UNIT_COSTS.values():
        assert set(tiers) == {"economy", "standard", "premium", "luxury"}
        totals = [tiers[t].total for t in ("economy", "standard", "premium", "luxury")]
        assert totals == sorted(totals)
        assert len({row.unit for row in tiers.values()}) == 1
    assert year_factor(2020) == 1
    # The index rises over the decade before the book (2010-2019, each year above the last).
    factors = [year_factor(y) for y in range(2010, 2020)]
    assert factors == sorted(factors) and factors[-1] == Decimal("0.9711")


def test_estimate_is_deterministic() -> None:
    takeoff = estimate_quantity("carpet", 150)
    assert estimate_cost(takeoff, 2020, 1) == estimate_cost(takeoff, 2020, Decimal("1"))


def test_factor_sources_are_written_after_the_factors() -> None:
    """EVAL_REPORT section 35 and 45: the cost row names the index values, book and page
    behind each factor, then where the study's year and location came from."""
    takeoff = estimate_quantity("carpet", 100)
    plain = estimate_cost(takeoff, 2018, "1.05")
    assert plain.source_row.endswith(f"(base 2020, x0.9322 for 2018 = {HCI_2018}; x1.05 location)")
    sourced = estimate_cost(
        takeoff,
        2018,
        "1.05",
        year_source="appraisal effective date 2018-06-01",
        location_source="state IL (intake): mean of 19 IL cities",
    )
    assert sourced.source_row.endswith(
        f"(base 2020, x0.9322 for 2018 = {HCI_2018}, from appraisal effective date "
        "2018-06-01; x1.05 location from state IL (intake): mean of 19 IL cities)"
    )
    assert (sourced.unit_cost, sourced.total) == (plain.unit_cost, plain.total)


def test_a_resolved_year_factor_is_used_when_it_matches_the_cost_year() -> None:
    """``estimate_cost(year=...)`` takes the study's resolved factor (which may carry the
    firm's supplement); a factor for another year is ignored, never misapplied."""
    takeoff = estimate_quantity("carpet", 100)
    resolved = year_factor_index(2018)
    assert estimate_cost(takeoff, 2018, 1, year=resolved) == estimate_cost(takeoff, 2018, 1)
    assert estimate_cost(takeoff, 2019, 1, year=resolved).year_factor == Decimal("0.9711")
