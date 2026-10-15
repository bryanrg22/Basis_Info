"""Takeoff formulas against hand-computed quantities (BUILD_PLAN 6.1).

The engineer's report quantities are not in the repository (client data lives under
``BASIS_EVAL_DATA_DIR``); the quantity checks against that report belong to the
orchestrator-run evaluation. Here every expected value is computed by hand from the
formula in the docstring of ``basis.domain.takeoffs``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from basis.domain.takeoffs import (
    MissingTakeoffInput,
    Takeoff,
    estimate_quantity,
    labor_hours,
    takeoff_key,
)


@pytest.mark.parametrize(
    ("component", "area", "quantity", "unit"),
    [
        ("carpet", 150, "18.33", "SY"),  # 150 / 9 x 1.10 = 18.333
        ("carpet", 135, "16.50", "SY"),  # 135 / 9 x 1.10 = 16.5
        ("tile", 75, "86.25", "SF"),  # 75 x 1.15
        ("tile", "0.3", "0.35", "SF"),  # 0.345 rounds half up (banker's would give 0.34)
        ("kitchen_cabinet", 144, "48.00", "LF"),  # 4 x sqrt(144)
        ("countertop", 144, "100.00", "SF"),  # 48 LF x 25/12 ft
        ("baseboard", 100, "40.00", "LF"),  # 4 x sqrt(100)
        ("paint", 100, "350.00", "SF"),  # 100 x 3.5
        ("hardwood_floor", 200, "200.00", "SF"),  # floor area, no waste
        ("vinyl_flooring", 80, "80.00", "SF"),
    ],
)
def test_measured_quantities(component: str, area: object, quantity: str, unit: str) -> None:
    takeoff = estimate_quantity(component, area, 1)  # type: ignore[arg-type]
    assert (takeoff.quantity, takeoff.unit) == (Decimal(quantity), unit)
    assert takeoff.is_measured


@pytest.mark.parametrize(
    ("component", "count", "quantity"),
    [
        ("light_fixture", 3, "3.00"),
        ("light_fixture", 1, "1.00"),
        ("electrical_outlet", 1, "2.00"),  # min 2 per room
        ("electrical outlets", 5, "5.00"),
        ("window", 1, "1.00"),  # min 0 per room
        ("door", 2, "2.00"),
        ("garage_door", 1, "1.00"),  # no row: detection count
        ("refrigerator", 2, "2.00"),
    ],
)
def test_counted_quantities(component: str, count: int, quantity: str) -> None:
    takeoff = estimate_quantity(component, Decimal(150), count)
    assert (takeoff.quantity, takeoff.unit, takeoff.method) == (Decimal(quantity), "EA", "count")
    assert not takeoff.is_measured


def test_formula_and_inputs_are_recorded() -> None:
    takeoff = estimate_quantity("carpet", 150, 2)
    assert takeoff.formula == "150 SF / 9 x 1.1 waste"
    assert takeoff.inputs == {
        "component": "carpet",
        "count": 2,
        "room_area_sf": "150",
        "waste_factor": "1.1",
        "per_sf": "1/9",
    }
    assert takeoff.assumptions == ("Add 10% for seams and waste",)
    cabinet = estimate_quantity("kitchen_cabinet", 150, 1)
    assert cabinet.formula == "4 x sqrt(150 SF)"
    assert "perimeter" in cabinet.assumptions[0]
    counter = estimate_quantity("countertop", 150, 1)
    assert counter.inputs["depth_in"] == "25"


def test_area_component_without_area_is_an_explicit_error() -> None:
    """Legacy fell back to the count and kept the area unit ("2 SY of carpet")."""
    with pytest.raises(MissingTakeoffInput):
        estimate_quantity("carpet", None, 2)
    with pytest.raises(MissingTakeoffInput):
        estimate_quantity("countertop", None)  # legacy raised TypeError here
    assert estimate_quantity("light_fixture", None, 2).quantity == Decimal("2.00")


@pytest.mark.parametrize("area", [0, -10, float("nan"), float("inf")])
def test_bad_area_is_refused(area: float) -> None:
    with pytest.raises(ValueError, match="room_area_sf"):
        estimate_quantity("carpet", area)


def test_count_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="count"):
        estimate_quantity("light_fixture", 100, 0)


def test_takeoff_keys() -> None:
    assert takeoff_key("kitchen_cabinet") == "cabinet"
    assert takeoff_key("Hardwood Floor") == "flooring"
    assert takeoff_key("carpet") == "carpet"


def test_labor_hours_converts_square_yards() -> None:
    carpet = estimate_quantity("carpet", 150)  # 18.33 SY = 164.97 SF x 0.025 h
    assert labor_hours(carpet) == Decimal("4.12")
    assert labor_hours(estimate_quantity("kitchen_cabinet", 144)) == Decimal("48.00")
    assert labor_hours(estimate_quantity("garage_door", 100)) is None
    odd = Takeoff(component="door", quantity=Decimal(10), unit="LF", formula="x")
    assert labor_hours(odd) is None


def test_estimates_are_deterministic() -> None:
    assert estimate_quantity("countertop", 150) == estimate_quantity("countertop", 150.0)
