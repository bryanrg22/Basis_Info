"""No substitute value reaches dollars (EVAL_REPORT 78): the assets step's takeoffs and prices.

A measured item whose room has no measured area, a property item the appraisal cannot size,
a counted item without a count, and a guessed cabinet run are written unpriced, with the
missing input named, instead of being priced on a typical area, a 1-story footprint, a count
of 1 or the room's perimeter. A counted item in a room without an area still prices (the area
is not its quantity). The engineer's measured quantity (an asset correction) replaces the
computed one and clears the missing input.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from costtest_support import SUITE_TABLE
from temporalio.exceptions import ApplicationError

from basis.activities.corrections import check_quantity
from basis.activities.process_assets import (
    TakeoffPlan,
    _cost,
    _source_row,
    _takeoff,
    measured_by_engineer,
)
from basis.config import Settings
from basis.db.models import Component, Room
from basis.domain.cost_table import parse_cost_table
from basis.domain.measurements import BuildingMeasurements, Reading
from basis.domain.money_inputs import (
    INPUTS,
    MISSING_INPUT,
    codes_in,
    row_blockers,
    with_missing_input,
)
from basis.domain.takeoffs import Takeoff
from basis.errors.codes import E

D = Decimal
STUDY = uuid.uuid4()


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, basis_env="test", **kw)  # type: ignore[call-arg]


def reading(value: str, key: str = "field") -> Reading:
    return Reading(D(value), key, "test")


def house(**kw: Reading | None) -> BuildingMeasurements:
    values: dict[str, Reading | None] = {
        "gla_sf": reading("1800", "improvements.gla_sqft"),
        "stories": reading("2", "improvements.stories"),
        "site_sf": reading("8000", "site.area_sqft"),
    }
    values.update(kw)
    return BuildingMeasurements(**values)  # type: ignore[arg-type]


def room(room_type: str, floor: str = "1") -> Room:
    return Room(id=uuid.uuid4(), study_id=STUDY, room_type=room_type, floor=floor, name=room_type)


def component(label: str, at: Room | None, count: Any = 1) -> Component:
    raw = {} if count is None else {"count": count}
    return Component(
        id=uuid.uuid4(),
        study_id=STUDY,
        room_id=None if at is None else at.id,
        label=label,
        raw=raw,
    )


def index_for(m: BuildingMeasurements | None, at: Room) -> Any:
    return TakeoffPlan.build(settings(), m, [at]).index


def takeoff_for(
    label: str, at: Room | None, m: BuildingMeasurements | None, rooms: list[Room], **kw: Any
) -> Takeoff:
    plan = TakeoffPlan.build(settings(**kw), m, rooms)
    c = component(label, at, kw.pop("count", 1) if "count" in kw else 1)
    return _takeoff(c, at, None, plan, {}, uuid.uuid4())


# ------------------------------------------------------------------ the module


def test_every_input_says_what_is_missing_and_where_it_is_fixed() -> None:
    for code, found in INPUTS.items():
        assert found.code == code and found.reason == f"input:{code}"
        assert found.what and found.fix


def test_reasons_are_read_back_and_counted_per_input() -> None:
    reason = "rule:x; input:gla; cross:COST_ROW_MISSING; input:stories"
    assert codes_in(reason) == ["gla", "stories"]
    assert codes_in(None) == [] and codes_in("") == []
    blockers = row_blockers(["stories", "gla", "gla", "mystery"])
    assert [(b.code, b.assets) for b in blockers] == [
        ("input:gla", 2),
        ("input:stories", 1),
        ("input:mystery", 1),
    ]
    assert all(b.fix for b in blockers)


def test_a_row_missing_an_input_records_no_guessed_quantity() -> None:
    guessed = Takeoff("carpet", D("16.67"), "SY", "150 SF typical", method="area")
    row = with_missing_input(guessed, "gla")
    assert (row.quantity, row.unit, row.inputs[MISSING_INPUT]) == (D("0.00"), "SY", "gla")
    assert row.formula.startswith("not measured: the gross living area")


# ------------------------------------------------------------------ room-level items


@pytest.mark.parametrize(
    ("label", "rtype", "floor", "m", "missing"),
    [
        ("carpet", "bedroom", "1", None, "gla"),  # no extraction fields
        ("carpet", "bedroom", "1", BuildingMeasurements(), "gla"),  # no GLA printed
        ("carpet", "garage", "1", "house", "garage_area"),  # no garage SF and no cars
        ("concrete_floor", "exterior", "1", "house", "exterior_area"),  # an exterior room
        ("carpet", "ambiguous", "1", "house", "room_type"),  # a room of unknown type
        ("carpet", "bedroom", "0", "house", "basement_area"),  # basement with no area
    ],
)
def test_a_measured_item_without_a_measured_area_is_not_priced(
    label: str, rtype: str, floor: str, m: Any, missing: str
) -> None:
    at = room(rtype, floor)
    measurements = house() if m == "house" else m
    t = takeoff_for(label, at, measurements, [at])
    assert t.inputs[MISSING_INPUT] == missing and t.quantity == D("0.00")
    cost, priced_per, why = _cost(t, index_for(measurements, at), SUITE_TABLE, label)
    assert cost is None and priced_per is None
    assert why is not None and why.reason == f"input:{missing}"
    assert _source_row(t, cost, why).startswith("missing input: ")


def test_a_measured_item_in_no_room_is_not_priced() -> None:
    t = takeoff_for("carpet", None, house(), [])
    assert t.inputs[MISSING_INPUT] == "room"


def test_typical_areas_by_setting_are_not_a_measurement() -> None:
    at = room("bedroom")
    t = takeoff_for("carpet", at, house(), [at], takeoff_areas="typical")
    assert t.inputs[MISSING_INPUT] == "takeoff_areas_typical"


def test_a_measured_item_with_a_measured_area_prices() -> None:
    at = room("bedroom")
    t = takeoff_for("carpet", at, house(), [at])
    assert MISSING_INPUT not in t.inputs and t.quantity > 0
    cost, _, why = _cost(t, index_for(house(), at), SUITE_TABLE, "carpet")
    assert why is None and cost is not None and cost.total > 0


def test_a_counted_item_prices_on_its_count_even_without_a_room_area() -> None:
    """The area is not a light fixture's quantity: no GLA does not hold it."""
    at = room("bedroom")
    t = takeoff_for("light_fixture", at, None, [at])
    assert MISSING_INPUT not in t.inputs and (t.quantity, t.unit) == (D("1.00"), "EA")


@pytest.mark.parametrize("count", [None, 0, -2, "three", True, 1.5])
def test_a_counted_item_without_a_valid_count_is_not_counted_as_one(count: Any) -> None:
    at = room("bedroom")
    plan = TakeoffPlan.build(settings(), house(), [at])
    c = component("light_fixture", at, count)
    t = _takeoff(c, at, None, plan, {}, uuid.uuid4())
    assert t.inputs[MISSING_INPUT] == "count" and t.quantity == D("0.00")


@pytest.mark.parametrize("label", ["cabinet", "countertop"])
def test_a_cabinet_run_is_not_guessed_from_the_perimeter(label: str) -> None:
    at = room("kitchen")
    t = takeoff_for(label, at, house(), [at])
    assert t.inputs[MISSING_INPUT] == "cabinet_run"


# ------------------------------------------------------------------ property-level items


@pytest.mark.parametrize("label", ["roof", "siding", "gutter", "landscaping"])
def test_without_stories_the_property_items_name_the_missing_input(label: str) -> None:
    at = room("exterior")
    t = takeoff_for(label, at, house(stories=None), [at])
    assert t.inputs[MISSING_INPUT] == "stories"


def test_without_a_site_area_landscaping_names_it() -> None:
    at = room("exterior")
    t = takeoff_for("landscaping", at, house(site_sf=None), [at])
    assert t.inputs[MISSING_INPUT] == "site_area"
    roof = takeoff_for("roof", at, house(site_sf=None), [at])
    assert MISSING_INPUT not in roof.inputs and roof.quantity > 0


def test_a_one_and_a_half_story_house_prices_its_roof_on_its_own_footprint() -> None:
    at = room("exterior")
    one = takeoff_for("roof", at, house(stories=reading("1")), [at])
    one_and_half = takeoff_for("roof", at, house(stories=reading("1.5")), [at])
    assert one_and_half.quantity < one.quantity  # 1,800 / 1.5 = 1,200 SF footprint, not 1,800


# ------------------------------------------------------------------ pricing errors are named


def test_a_pricing_error_is_named_not_reported_as_a_missing_row() -> None:
    """A table on another base year (the year factors are on 2020) was reported as a
    missing cost row before EVAL_REPORT 78."""
    at = room("bedroom")
    table_2019 = parse_cost_table(
        {
            "schema_version": 1,
            "name": "synthetic_2019",
            "base_year": 2019,
            "rows": {
                "light_fixture": {
                    "unit": "EA",
                    "material": 1,
                    "labor": 1,
                    "source": "SYNTHETIC",
                }
            },
        }
    )
    t = Takeoff("light_fixture", D("1.00"), "EA", "1 counted")
    cost, priced_per, why = _cost(t, index_for(house(), at), table_2019, "light_fixture")
    assert cost is None and priced_per is None
    assert why is not None and why.reason == "cost:not_priced" and "2019" in why.source


def test_a_label_the_table_lacks_is_a_missing_row() -> None:
    at = room("bedroom")
    plan = TakeoffPlan.build(settings(), house(), [at])
    t = Takeoff("garage_door", D("1.00"), "EA", "1 counted")
    assert _cost(t, plan.index, SUITE_TABLE, "garage_door") == (None, None, None)
    assert _source_row(t, None).startswith("missing: no cost row")


# ------------------------------------------------------------------ the engineer's measurement


def measured(**kw: Any) -> dict[str, Any]:
    return {"quantity": "640", "quantity_unit": "SF", "quantity_source": "site plan sheet C1", **kw}


def test_the_engineers_measurement_replaces_a_missing_quantity_and_prices() -> None:
    at = room("bedroom")
    missing = takeoff_for("carpet", at, None, [at])
    assert missing.inputs[MISSING_INPUT] == "gla"
    t = measured_by_engineer(missing, measured(quantity="70", quantity_unit="SY"))
    assert (t.quantity, t.unit, t.method) == (D("70.00"), "SY", "area")
    assert (
        MISSING_INPUT not in t.inputs and t.inputs["measured_by_engineer"] == "site plan sheet C1"
    )
    assert t.formula == "70.00 SY measured by the engineer (site plan sheet C1)"
    cost, _, why = _cost(t, index_for(None, at), SUITE_TABLE, "carpet")
    assert why is None and cost is not None and cost.total > 0


def test_a_measurement_in_another_unit_than_the_row_needs_that_unit() -> None:
    at = room("bedroom")
    t = measured_by_engineer(takeoff_for("carpet", at, None, [at]), measured())  # SF; row per SY
    cost, priced_per, why = _cost(t, index_for(None, at), SUITE_TABLE, "carpet")
    assert cost is None and priced_per == "SY" and why is None


def test_a_valid_measurement_is_accepted() -> None:
    assert check_quantity(measured()) == D("640")
    assert check_quantity({"bucket": "y5"}) is None


@pytest.mark.parametrize(
    "after",
    [
        {"quantity": "640"},
        {"quantity": "640", "quantity_unit": "SF"},
        measured(quantity="0"),
        measured(quantity="-3"),
        measured(quantity="abc"),
        measured(quantity="NaN"),
        measured(quantity="20000000"),
        measured(quantity_unit="acres"),
        measured(quantity_source=""),
        measured(quantity_source="   "),
        measured(quantity_source="x" * 301),
        measured(quantity_source=7),
    ],
)
def test_an_incomplete_or_invalid_measurement_is_refused(after: dict[str, Any]) -> None:
    with pytest.raises(ApplicationError) as raised:
        check_quantity(after)
    assert E.CORRECT_QUANTITY in str(raised.value)


# ------------------------------------------------------------------ the structure model's rows


def test_the_fire_alarm_without_stories_is_written_unpriced_not_one_story() -> None:
    """EVAL_REPORT 78: the smoke-alarm count assumed 1 story when none was printed."""
    from basis.activities.process_assets import _small_takeoff
    from basis.domain.structure import SmallSystemInputs, small_system_rows

    def fire_alarm(stories: Decimal | None) -> Any:
        rows = small_system_rows(
            SmallSystemInputs(
                bedrooms=D(3),
                total_rooms=D(7),
                stories=stories,
                has_basement=False,
                public_utilities=(),
                smoke_detectors_found=0,
                exterior_doors_found=0,
            )
        )
        return next(r for r in rows if r.label == "fire_alarm_system")

    unknown = fire_alarm(None)
    assert unknown.review_reasons == ("input:stories",) and unknown.needs_review
    t = _small_takeoff(unknown)
    assert t.inputs[MISSING_INPUT] == "stories" and t.quantity == D("0.00")
    known = fire_alarm(D(2))
    assert known.review_reasons == () and not known.needs_review
    assert MISSING_INPUT not in _small_takeoff(known).inputs
    assert known.quantity == D(5)  # 3 bedrooms + 2 stories + no basement, less none found
