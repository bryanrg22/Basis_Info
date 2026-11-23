"""The assets step's takeoffs with the appraisal's sketch (``TAKEOFF_SKETCH``; EVAL_REPORT 96).

What ``process_assets`` writes on a takeoff: a patio the sketch dimensions is a measured
property-level quantity instead of a measurement the reviewer must supply; the roof of a
study whose sketch was refused is today's roof with the reason beside it; with the setting
off the sketch fields change nothing. Fixture values are fabricated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from basis.activities.process_assets import TakeoffPlan, _takeoff
from basis.config import Settings
from basis.db.models import Component, Room
from basis.domain.measurements import NEEDS_MEASUREMENT, measurements_from_fields
from basis.domain.money_inputs import MISSING_INPUT
from basis.domain.takeoffs import Takeoff

D = Decimal
STUDY = uuid.uuid4()


@dataclass
class Row:
    section: str
    field: str
    value_num: Decimal | None = None
    value_text: str | None = None
    value_date: date | None = None
    source: str = "azure"
    page: int | None = None


def num(key: str, value: str, page: int | None = None) -> Row:
    section, field = key.split(".", 1)
    return Row(section, field, D(value), page=page)


def fields(gla: str = "2100") -> list[Row]:
    return [
        num("improvements.gla_sqft", gla),
        num("improvements.stories", "2"),
        num("site.area_sqft", "9000"),
        num("sketch.living_area_sqft", "2100", 9),
        num("sketch.ground_floor_area_sqft", "1200", 9),
        num("sketch.garage_area_sqft", "484", 9),
        num("sketch.patio_area_sqft", "120", 9),
    ]


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, basis_env="test", **kw)  # type: ignore[call-arg]


def plan_for(rows: list[Row], rooms: list[Room], **kw: Any) -> TakeoffPlan:
    """The plan as ``process_assets`` builds it: the setting decides whether the sketch
    fields are read."""
    s = settings(**kw)
    measured = measurements_from_fields(rows, sketch_tolerance=s.sketch_tolerance)
    return TakeoffPlan.build(s, measured, rooms)


def room(room_type: str) -> Room:
    return Room(id=uuid.uuid4(), study_id=STUDY, room_type=room_type, floor="1", name=room_type)


def component(label: str, at: Room) -> Component:
    return Component(id=uuid.uuid4(), study_id=STUDY, room_id=at.id, label=label, raw={"count": 1})


def take(
    label: str, at: Room, plan: TakeoffPlan, measured: dict[Any, Any] | None = None
) -> Takeoff:
    return _takeoff(
        component(label, at), at, None, plan, {} if measured is None else measured, uuid.uuid4()
    )


def test_off_the_patio_still_needs_a_measurement_and_the_roof_is_todays() -> None:
    yard = room("exterior")
    off = plan_for(fields(), [yard])
    patio = take("patio", yard, off)
    assert patio.inputs["needs_measurement"] == NEEDS_MEASUREMENT["patio"]
    assert patio.unit == "EA" and "patio" not in off.property
    roof = take("roof", yard, off)
    assert roof.formula.startswith("(2100 SF GLA / 2 stories + ")
    assert "sketch_check" not in roof.inputs
    # The same plan as an appraisal that never had a sketch.
    bare = plan_for([r for r in fields() if r.section != "sketch"], [yard])
    assert off.property == bare.property and off.room_areas == bare.room_areas


def test_on_a_dimensioned_patio_is_measured_once_for_the_property() -> None:
    yard = room("exterior")
    plan = plan_for(fields(), [yard], takeoff_sketch="on")
    measured: dict[Any, Any] = {}
    patio = take("patio", yard, plan, measured)
    assert (patio.quantity, patio.unit) == (D("120.00"), "SF")
    assert patio.formula == "120 SF patio on the appraisal sketch"
    assert patio.inputs["quantity_source"] == "appraisal_derived"
    assert patio.inputs["patio_sf_field"] == "sketch.patio_area_sqft (azure, page 9)"
    assert "needs_measurement" not in patio.inputs and MISSING_INPUT not in patio.inputs
    second = take("patio", yard, plan, measured)
    assert second.quantity == D("0.00")
    assert second.formula.startswith("measured once per property: see asset ")


def test_on_the_roof_and_the_garage_room_are_sized_from_the_sketch() -> None:
    yard, garage = room("exterior"), room("garage")
    plan = plan_for(fields(), [yard, garage], takeoff_sketch="on")
    roof = take("roof", yard, plan)
    assert roof.formula.startswith(
        "((1200 SF ground floor on the appraisal sketch) + 484 SF garage on the appraisal sketch"
    )
    assert roof.inputs["footprint_source"] == "sketch"
    assert str(roof.inputs["sketch_check"]).startswith("appraisal sketch used: ")
    assert plan.room_areas[garage.id].area_sf == D("484.00")


def test_on_a_refused_sketch_leaves_todays_roof_with_the_reason_beside_it() -> None:
    yard = room("exterior")
    off = take("roof", yard, plan_for(fields("2400"), [yard]))
    on_plan = plan_for(fields("2400"), [yard], takeoff_sketch="on")
    on = take("roof", yard, on_plan)
    assert (on.quantity, on.unit, on.formula) == (off.quantity, off.unit, off.formula)
    assert on.inputs["sketch_check"] == (
        "appraisal sketch not used: its living area is 2100 SF (sketch.living_area_sqft "
        "(azure, page 9)) against 2400 SF GLA (improvements.gla_sqft (azure)), 12.50 % apart "
        "(tolerance 1 %)"
    )
    assert {k: v for k, v in on.inputs.items() if k != "sketch_check"} == off.inputs
    # The patio of a refused sketch is not used either.
    patio = take("patio", yard, on_plan)
    assert patio.inputs["needs_measurement"] == NEEDS_MEASUREMENT["patio"]


def test_the_tolerance_setting_admits_it() -> None:
    yard = room("exterior")
    plan = plan_for(fields("2400"), [yard], takeoff_sketch="on", takeoff_sketch_gla_tolerance=0.15)
    assert take("roof", yard, plan).inputs["footprint_source"] == "sketch"
