"""The takeoff with the appraisal's sketch (``TAKEOFF_SKETCH``; SKETCH_READER.md, EVAL_REPORT 96).

The regression half compares the takeoff of studies with no sketch, string for string, to a
snapshot taken before the sketch was read (``measurement_snapshot``). The rest computes every
expected number by hand from the formulas of TAKEOFF_RESEARCH section 3 with the sketch's
areas in place of the derived ones. Fixture values are fabricated (no client data).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from fixtures import sketch_fixture

from basis.domain.measurements import (
    SKETCH_KEYS,
    BuildingMeasurements,
    SketchAreas,
    check_sketch,
    footprint,
    garage_area,
    ground_footprint,
    gutter_length,
    landscaping_area,
    measurements_from_fields,
    patio_area,
    perimeter_ft,
    property_missing,
    property_quantities,
    roof_area,
    room_areas,
    siding_area,
)
from basis.extraction.sketch import FIELD_KEYS, read_sketch, sketch_fields

from .measurement_snapshot import CASES, GOLDEN, TWO_STORY, RoomStub, Row, num, render, snapshot_of

TOLERANCE = Decimal("0.01")
PAGES: dict[str, Any] = json.loads(sketch_fixture("pages.json").read_text("utf-8"))


def sketch_rows(name: str = "two_level") -> list[Row]:
    """The ``sketch`` fields the extraction writes for a fixture page, as stored rows."""
    read = read_sketch([(n, lines) for n, lines in PAGES[name]["pages"]], "azure")
    assert read is not None
    fields, _ = sketch_fields(read)
    return [
        Row(f.section, f.field, f.value_num, source=f.source, page=f.page) for f in fields.values()
    ]


def house(gla: str = "2100", stories: str = "2", **extra: str) -> list[Row]:
    """A house whose printed GLA is the two_level sketch's living area unless changed."""
    rows = [
        num("improvements.gla_sqft", gla, "llm"),
        num("improvements.stories", stories, "llm"),
        num("cost_approach.garage_sqft", "500"),
        num("site.area_sqft", "9000"),
    ]
    return [*rows, *(num(key.replace("__", "."), value) for key, value in extra.items())]


def read(rows: list[Row], tolerance: Decimal | None = TOLERANCE) -> BuildingMeasurements:
    return measurements_from_fields(rows, sketch_tolerance=tolerance)


def q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


# ------------------------------------------------------------------ the unchanged path


def test_without_a_sketch_the_takeoff_is_byte_identical_to_before() -> None:
    golden = GOLDEN.read_text(encoding="utf-8")
    off = {name: snapshot_of(measurements_from_fields(rows)) for name, rows in CASES.items()}
    assert render(off) == golden
    # TAKEOFF_SKETCH=on and an appraisal with no sketch: still today's output.
    on = {name: snapshot_of(read(rows)) for name, rows in CASES.items()}
    assert render(on) == golden


def test_with_the_setting_off_sketch_fields_are_not_read() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["two_story_with_garage"]
    measured = measurements_from_fields([*TWO_STORY, *sketch_rows()])
    assert measured.sketch is None
    assert snapshot_of(measured) == golden
    assert measured == measurements_from_fields(TWO_STORY)


def test_the_snapshot_cases_cover_every_property_quantity_and_missing_input() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(golden["two_story_with_garage"]["property"]) == {
        "roof",
        "siding",
        "gutter",
        "landscaping",
    }
    assert golden["no_stories"]["property_missing"]["roof"] == "stories"
    assert golden["no_gla"]["property_missing"]["roof"] == "gla"
    assert golden["half_story_garage_by_cars"]["garage"]["quantity"] == "240.00"


# ------------------------------------------------------------------ the cross-check


def test_a_sketch_that_agrees_with_the_gla_is_used_with_the_check_recorded() -> None:
    sketch = read([*house(), *sketch_rows()]).sketch
    assert sketch is not None and sketch.accepted
    assert sketch.check == (
        "appraisal sketch used: its living area is 2100 SF (sketch.living_area_sqft "
        "(azure, page 9)) against 2100 SF GLA (improvements.gla_sqft (llm)), 0 % apart "
        "(tolerance 1 %)"
    )
    assert sketch.ground_floor_sf is not None and sketch.ground_floor_sf.page == 9
    assert sketch.ground_floor_sf.describe() == "sketch.ground_floor_area_sqft (azure, page 9)"


@pytest.mark.parametrize(
    ("gla", "accepted", "apart"),
    [
        ("2100", True, "0"),
        ("2121", True, "0.99"),  # 21 / 2121
        ("2079", False, "1.01"),  # 21 / 2079
        ("2000", False, "5"),
        ("2400", False, "12.50"),
    ],
)
def test_the_tolerance_is_a_fraction_of_the_printed_gla(
    gla: str, accepted: bool, apart: str
) -> None:
    sketch = read([*house(gla), *sketch_rows()]).sketch
    assert sketch is not None and sketch.accepted is accepted
    assert f", {apart} % apart (tolerance 1 %)" in sketch.check
    assert sketch.check.startswith(
        "appraisal sketch used: " if accepted else "appraisal sketch not used: "
    )


def test_a_sketch_exactly_at_the_tolerance_is_used() -> None:
    rows = [
        num("improvements.gla_sqft", "2000"),
        num("improvements.stories", "1"),
        Row("sketch", "living_area_sqft", Decimal(2020), page=4),
    ]
    at = read(rows).sketch
    assert at is not None and at.accepted and ", 1 % apart (tolerance 1 %)" in at.check
    rows[-1] = Row("sketch", "living_area_sqft", Decimal("2020.01"), page=4)
    over = read(rows).sketch
    assert over is not None and not over.accepted


def test_a_sketch_that_disagrees_falls_back_to_today_with_the_reason_recorded() -> None:
    plain = measurements_from_fields(house("2400"))
    refused = read([*house("2400"), *sketch_rows()])
    assert refused.sketch is not None and not refused.sketch.accepted
    reason = (
        "appraisal sketch not used: its living area is 2100 SF (sketch.living_area_sqft "
        "(azure, page 9)) against 2400 SF GLA (improvements.gla_sqft (llm)), 12.50 % apart "
        "(tolerance 1 %)"
    )
    assert refused.sketch.check == reason
    before, after = property_quantities(plain), property_quantities(refused)
    assert set(after) == set(before) == {"roof", "siding", "gutter", "landscaping"}
    for label, derived in after.items():
        was = before[label]
        assert (derived.quantity, derived.unit, derived.formula) == (
            was.quantity,
            was.unit,
            was.formula,
        )
        assert derived.assumptions == was.assumptions
        # Nothing moved but the reason, which every one of them carries.
        assert derived.inputs == {**was.inputs, "sketch_check": reason}
        assert list(derived.inputs)[: len(was.inputs)] != [] and "sketch_check" in derived.inputs
    assert garage_area(refused) == garage_area(plain)
    assert patio_area(refused) is None
    rooms = [RoomStub("k", "kitchen", "1"), RoomStub("g", "garage", "1")]
    assert room_areas(refused, rooms, "residential_rental") == room_areas(
        plain, rooms, "residential_rental"
    )


def test_a_widened_tolerance_admits_the_same_sketch() -> None:
    admitted = read([*house("2400"), *sketch_rows()], Decimal("0.15"))
    assert admitted.sketch is not None and admitted.sketch.accepted
    assert "(tolerance 15 %)" in admitted.sketch.check


def test_a_sketch_with_no_living_area_or_no_gla_to_check_against_is_not_used() -> None:
    garage_only = [r for r in sketch_rows() if r.field == "garage_area_sqft"]
    unread = read([*house(), *garage_only]).sketch
    assert unread is not None and not unread.accepted
    assert unread.check == (
        "appraisal sketch not used: the sketch's living area could not be read, so it cannot "
        "be checked"
    )
    assert garage_area(read([*house(), *garage_only])) == garage_area(read(house()))

    no_gla = read([num("improvements.stories", "2"), *sketch_rows()])
    assert no_gla.sketch is not None and not no_gla.sketch.accepted
    assert no_gla.sketch.check == (
        "appraisal sketch not used: no printed GLA to check its living area against"
    )
    assert property_quantities(no_gla) == {}
    assert property_missing(no_gla)["roof"] == "gla"


def test_check_sketch_is_pure_and_keeps_the_readings() -> None:
    areas = read([*house(), *sketch_rows()]).sketch
    assert areas is not None
    again = check_sketch(areas, None, TOLERANCE)
    assert not again.accepted and again.living_sf == areas.living_sf
    assert areas.accepted  # the original is untouched


def test_a_zero_or_negative_sketch_value_is_not_a_reading() -> None:
    rows = [*house(), Row("sketch", "living_area_sqft", Decimal(0)), *sketch_rows()[1:]]
    sketch = read(rows).sketch
    assert sketch is not None and sketch.living_sf is None and not sketch.accepted


# ------------------------------------------------------------------ what an accepted sketch sizes


def test_the_ground_floor_and_garage_replace_the_derived_footprint() -> None:
    m = read([*house(), *sketch_rows()])
    check = m.sketch.check  # type: ignore[union-attr]
    base = footprint(m)
    assert base is not None
    assert (base.quantity, base.formula) == (
        Decimal("1200.00"),
        "1200 SF ground floor on the appraisal sketch",
    )
    assert base.inputs == {
        "footprint_source": "sketch",
        "sketch_ground_floor_sf": "1200",
        "sketch_ground_floor_field": "sketch.ground_floor_area_sqft (azure, page 9)",
        "sketch_check": check,
    }
    garage = garage_area(m)
    assert garage is not None
    assert (garage.quantity, garage.formula) == (
        Decimal("484.00"),
        "484 SF garage on the appraisal sketch",
    )
    assert garage.inputs["garage_sf_field"] == "sketch.garage_area_sqft (azure, page 9)"
    ground = ground_footprint(m)
    assert ground is not None and ground.quantity == Decimal("1684.00")
    assert ground.formula == (
        "(1200 SF ground floor on the appraisal sketch) + 484 SF garage on the appraisal sketch"
    )
    # Today's derivation of the same house: 2100 / 2 + the cost approach's 500.
    assert ground_footprint(measurements_from_fields(house())).quantity == Decimal("1550.00")  # type: ignore[union-attr]


def test_roof_siding_gutter_and_landscaping_on_the_sketch_footprint_by_hand() -> None:
    m = read([*house(), *sketch_rows()])
    ground = Decimal(1684)
    perimeter = perimeter_ft(ground)  # still the 1.5:1 rectangle: the table has no outline
    roof = roof_area(m)
    assert roof is not None
    assert roof.quantity == q((ground + perimeter) * Decimal("1.118"))
    assert roof.inputs["aspect_ratio"] == "1.50" and "perimeter_field" not in roof.inputs
    assert "rectangular footprint, 1.50:1" in roof.assumptions

    # The upper floor is the sketch's 2100 - 1200 = 900 SF, not a second 1200.
    upper = perimeter_ft(Decimal(900))
    siding = siding_area(m)
    assert siding is not None
    assert siding.quantity == q((perimeter + upper) * 9 * Decimal("0.85"))
    assert siding.formula == (
        f"{q(perimeter)} ft ground perimeter x 9 ft + {q(upper)} ft x 9 ft x 1 upper stories, "
        "less 15% openings"
    )
    assert siding.inputs["upper_floors_sf"] == "900"
    assert siding.inputs["sketch_living_field"] == "sketch.living_area_sqft (azure, page 9)"
    assert siding.assumptions[-3:] == (
        "upper floors sized on the sketch's living area less its ground floor, as a 1.50:1 "
        "rectangle per story",
        "rectangular footprint, 1.50:1",
        "9 ft of exterior wall per story; 15% openings; gable ends not added",
    )

    gutter = gutter_length(m)
    long_side = (ground / Decimal("1.5")).sqrt() * Decimal("1.5")
    assert gutter is not None and gutter.quantity == q(2 * (long_side + 2))
    lawn = landscaping_area(m)
    assert lawn is not None and lawn.quantity == Decimal("7316.00")  # 9000 - 1684
    for derived in (roof, siding, gutter, lawn):
        assert derived.inputs["sketch_check"].startswith("appraisal sketch used: ")


def test_a_dimensioned_patio_answers_the_patio_measurement() -> None:
    m = read([*house(), *sketch_rows()])
    patio = patio_area(m)
    assert patio is not None
    assert (patio.quantity, patio.unit, patio.formula) == (
        Decimal("120.00"),
        "SF",
        "120 SF patio on the appraisal sketch",
    )
    assert patio.inputs["patio_sf_field"] == "sketch.patio_area_sqft (azure, page 9)"
    assert property_quantities(m)["patio"] == patio
    # No patio row on the sketch: the patio stays a measurement the reviewer supplies.
    without = [r for r in sketch_rows() if r.field != "patio_area_sqft"]
    assert "patio" not in property_quantities(read([*house(), *without]))


def test_the_sketch_garage_sizes_the_garage_room() -> None:
    m = read([*house(), *sketch_rows()])
    areas = room_areas(m, [RoomStub("g", "garage", "1")], "residential_rental")
    assert areas["g"].area_sf == Decimal("484.00") and areas["g"].source == "garage_area"


def test_a_one_level_sketch_has_no_upper_walls() -> None:
    rows = [*house("1650.50", "1"), *sketch_rows("one_level")]
    m = read(rows)
    assert m.sketch is not None and m.sketch.accepted
    assert m.sketch.garage_sf is None  # the sketch's garage is detached: not read
    ground = ground_footprint(m)
    assert ground is not None
    # The cost approach's garage line still applies, as it does today.
    assert ground.quantity == Decimal("2150.50")
    siding = siding_area(m)
    assert siding is not None
    perimeter = perimeter_ft(Decimal("2150.50"))
    assert siding.quantity == q(perimeter * 9 * Decimal("0.85"))
    assert siding.formula == f"{q(perimeter)} ft ground perimeter x 9 ft, less 15% openings"
    assert "upper_perimeter_ft" not in siding.inputs


def test_two_printed_stories_over_a_one_level_sketch_adds_no_upper_wall_and_says_so() -> None:
    m = read([*house("1650.50", "2"), *sketch_rows("one_level")])
    siding = siding_area(m)
    assert siding is not None
    assert "the sketch shows no living area above the ground floor" in siding.assumptions
    assert siding.quantity == q(perimeter_ft(Decimal("2150.50")) * 9 * Decimal("0.85"))


def test_a_sketch_without_a_ground_floor_keeps_the_derived_footprint_but_uses_its_garage() -> None:
    rows = [r for r in sketch_rows() if r.field != "ground_floor_area_sqft"]
    m = read([*house(), *rows])
    assert m.sketch is not None and m.sketch.accepted
    base = footprint(m)
    assert base is not None and base.formula == "2100 SF GLA / 2 stories"
    assert base.inputs["sketch_check"].startswith("appraisal sketch used: ")
    garage = garage_area(m)
    assert garage is not None and garage.quantity == Decimal("484.00")
    siding = siding_area(m)
    assert siding is not None and siding.inputs["dwelling_perimeter_ft"] == str(
        q(perimeter_ft(Decimal(1050)))
    )


def test_without_stories_the_sketch_does_not_size_what_today_cannot() -> None:
    rows = [num("improvements.gla_sqft", "2100"), *sketch_rows()]
    m = read(rows)
    assert m.sketch is not None and m.sketch.accepted
    assert footprint(m) is None
    assert set(property_quantities(m)) == {"patio"}
    assert property_missing(m)["roof"] == "stories"


# ------------------------------------------------------------------ a perimeter, when one is read


def outline_rows(ground: str, upper: str | None = None) -> list[Row]:
    rows = [Row("sketch", "ground_perimeter_ft", Decimal(ground), source="manual", page=9)]
    if upper is not None:
        rows.append(Row("sketch", "upper_perimeter_ft", Decimal(upper), source="manual", page=10))
    return rows


def test_a_read_perimeter_replaces_the_rectangle() -> None:
    m = read([*house(), *sketch_rows(), *outline_rows("220", "140")])
    roof = roof_area(m)
    assert roof is not None
    assert roof.quantity == q((Decimal(1684) + 220) * Decimal("1.118"))
    assert roof.inputs["perimeter_ft"] == "220"
    assert roof.inputs["perimeter_field"] == "sketch.ground_perimeter_ft (manual, page 9)"
    assert "aspect_ratio" not in roof.inputs
    assert "rectangular footprint, 1.50:1" not in roof.assumptions
    assert "perimeter read from the appraisal sketch's outline" in roof.assumptions

    siding = siding_area(m)
    assert siding is not None
    assert siding.quantity == q(Decimal(220 + 140) * 9 * Decimal("0.85"))
    assert siding.formula == (
        "220 ft ground perimeter x 9 ft + 140 ft of upper-floor perimeter x 9 ft, less 15% openings"
    )
    assert siding.inputs["upper_perimeter_field"] == "sketch.upper_perimeter_ft (manual, page 10)"
    assert "rectangular footprint, 1.50:1" not in siding.assumptions
    # The gutters still run on the footprint's long side: an outline does not say which
    # edges are eaves.
    assert gutter_length(m) == gutter_length(read([*house(), *sketch_rows()]))


def test_a_ground_perimeter_alone_leaves_the_upper_floor_a_rectangle() -> None:
    m = read([*house(), *sketch_rows(), *outline_rows("220")])
    siding = siding_area(m)
    assert siding is not None
    upper = perimeter_ft(Decimal(900))
    assert siding.quantity == q((220 + upper) * 9 * Decimal("0.85"))
    assert "perimeter read from the appraisal sketch's outline" in siding.assumptions


def test_a_perimeter_is_not_used_without_the_sketch_footprint_or_when_refused() -> None:
    no_ground = [r for r in sketch_rows() if r.field != "ground_floor_area_sqft"]
    m = read([*house(), *no_ground, *outline_rows("220", "140")])
    roof = roof_area(m)
    assert roof is not None and roof.inputs["aspect_ratio"] == "1.50"
    refused = read([*house("2400"), *sketch_rows(), *outline_rows("220", "140")])
    plain = measurements_from_fields(house("2400"))
    assert roof_area(refused).quantity == roof_area(plain).quantity  # type: ignore[union-attr]
    assert siding_area(refused).quantity == siding_area(plain).quantity  # type: ignore[union-attr]


def test_the_extraction_writes_exactly_the_area_keys_the_takeoff_reads() -> None:
    assert set(FIELD_KEYS) <= set(SKETCH_KEYS.values())
    assert set(SKETCH_KEYS.values()) - set(FIELD_KEYS) == {
        "sketch.ground_perimeter_ft",
        "sketch.upper_perimeter_ft",
    }
    assert SketchAreas().accepted is False
