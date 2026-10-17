"""Cost tables: unit cost x quantity x year x location, with explicit errors (BUILD_PLAN 6.1)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from basis.domain.costs import (
    REGIONAL_COST_FACTORS,
    TYPICAL_UNIT_COSTS,
    YEAR_ADJUSTMENT_FACTORS,
    CostError,
    MissingCostRow,
    UnitMismatch,
    UnknownCostYear,
    estimate_cost,
    has_cost_row,
    location_factor_for_state,
    year_factor,
)
from basis.domain.takeoffs import Takeoff, estimate_quantity


def test_carpet_in_square_yards_uses_the_per_sf_row_times_nine() -> None:
    # standard carpet 4.50 + 2.00 + 0.30 = 6.80/SF = 61.20/SY; x1.22 (2026) = 74.664
    takeoff = estimate_quantity("carpet", 150)  # 18.33 SY
    cost = estimate_cost(takeoff, 2026, 1)
    assert cost.base_unit_cost == Decimal("61.20")
    assert cost.unit_cost == Decimal("74.66")
    assert cost.total == Decimal("1368.52")  # 74.66 x 18.33 = 1368.5178
    assert cost.unit == "SY"
    assert "carpet" in cost.source_row and "per SF" in cost.source_row


def test_cabinets_per_linear_foot() -> None:
    """The legacy per-LF cabinet row still prices a ``cabinet`` run."""
    takeoff = estimate_quantity("cabinet", 144)  # 48 LF
    cost = estimate_cost(takeoff, 2026, "1.0")
    assert cost.unit_cost == Decimal("314.76")  # 258 x 1.22
    assert cost.total == Decimal("15108.48")


def test_kitchen_cabinets_are_units_with_no_legacy_row() -> None:
    """v0.9 (EVAL_REPORT 26): kitchen cabinets are counted in units, which only a cost
    table row prices (tests/unit/domain/test_cost_table.py)."""
    takeoff = estimate_quantity("kitchen_cabinet", 144, 18)
    assert (takeoff.quantity, takeoff.unit) == (Decimal("18.00"), "EA")
    with pytest.raises(MissingCostRow):
        estimate_cost(takeoff, 2026, "1.0")


def test_location_and_year_factors_multiply() -> None:
    takeoff = estimate_quantity("light_fixture", 100, 3)
    cost = estimate_cost(takeoff, 2024, location_factor_for_state("ca"))
    # (85 + 35 + 5) x 1.15 (2024) x 1.15 (CA) = 165.3125
    assert cost.unit_cost == Decimal("165.31")
    assert cost.total == Decimal("495.93")
    assert (cost.year_factor, cost.location_factor, cost.cost_year) == (
        Decimal("1.15"),
        Decimal("1.15"),
        2024,
    )


def test_tile_and_tiers() -> None:
    tile = estimate_quantity("tile", 75)  # 86.25 SF
    assert estimate_cost(tile, 2026, 1).total == Decimal("1762.95")  # 20.44 x 86.25
    premium = estimate_cost(tile, 2020, 1, tier="premium")
    assert premium.unit_cost == Decimal("26.00")


def test_missing_row_is_an_explicit_error() -> None:
    with pytest.raises(MissingCostRow):
        estimate_cost(estimate_quantity("garage_door", 100), 2026, 1)
    assert not has_cost_row("garage_door") and has_cost_row("carpet")


@pytest.mark.parametrize("year", [2019, 2027, 1999])
def test_unknown_cost_year_is_an_explicit_error(year: int) -> None:
    with pytest.raises(UnknownCostYear):
        estimate_cost(estimate_quantity("carpet", 100), year, 1)


def test_unit_mismatch_is_an_explicit_error() -> None:
    odd = Takeoff(component="cabinet", quantity=Decimal(3), unit="EA", formula="count")
    with pytest.raises(UnitMismatch):
        estimate_cost(odd, 2026, 1)


@pytest.mark.parametrize("factor", [0, -1, "nan"])
def test_bad_location_factor_is_refused(factor: object) -> None:
    with pytest.raises(CostError):
        estimate_cost(estimate_quantity("carpet", 100), 2026, factor)  # type: ignore[arg-type]


def test_unknown_state_is_refused() -> None:
    with pytest.raises(CostError):
        location_factor_for_state("ZZ")


def test_tables_are_complete() -> None:
    assert len(REGIONAL_COST_FACTORS) == 51  # 50 states and DC
    for tiers in TYPICAL_UNIT_COSTS.values():
        assert set(tiers) == {"economy", "standard", "premium", "luxury"}
        totals = [tiers[t].total for t in ("economy", "standard", "premium", "luxury")]
        assert totals == sorted(totals)
        assert len({row.unit for row in tiers.values()}) == 1
    factors = [YEAR_ADJUSTMENT_FACTORS[y] for y in sorted(YEAR_ADJUSTMENT_FACTORS)]
    assert factors == sorted(factors) and year_factor(2020) == 1


def test_estimate_is_deterministic() -> None:
    takeoff = estimate_quantity("carpet", 150)
    assert estimate_cost(takeoff, 2026, 1) == estimate_cost(takeoff, 2026, Decimal("1"))
