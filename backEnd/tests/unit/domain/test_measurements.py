"""Measured takeoff inputs from the appraisal (TAKEOFF_RESEARCH.md; EVAL_REPORT section 35).

Every expected number is computed by hand from the formulas in the research note with the
constants of its section 4; the fixture values are fabricated (no client data).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from basis.domain.measurements import (
    NEEDS_MEASUREMENT,
    BuildingMeasurements,
    Reading,
    bath_counts,
    footprint,
    garage_area,
    gutter_length,
    landscaping_area,
    measurements_from_fields,
    perimeter_ft,
    program_weight,
    property_quantities,
    resolve_cost_index,
    roof_area,
    room_areas,
    room_level,
    siding_area,
)
from basis.domain.rooms import ROOM_WEIGHTS_SF, room_weight_sf


@dataclass
class Row:
    section: str
    field: str
    value_num: Decimal | None = None
    value_text: str | None = None
    value_date: date | None = None
    source: str = "azure"


@dataclass
class RoomStub:
    id: str
    room_type: str
    floor: str | None = None


def num(key: str, value: str, source: str = "azure") -> Row:
    section, field = key.split(".", 1)
    return Row(section, field, Decimal(value), source=source)


# A fabricated two-story house: 2,400 SF GLA, 1,000 SF basement 60% finished, a 440 SF
# garage on the cost approach (two cars printed too), an 8,000 SF lot, 8 rooms / 4 bedrooms
# / 2.5 baths, in Illinois, appraised 2025-06-01.
FULL = [
    num("improvements.gla_sqft", "2400"),
    num("improvements.stories", "2", "llm"),
    num("improvements.basement_area_sqft", "1000"),
    num("improvements.basement_finish_percent", "60"),
    num("cost_approach.garage_sqft", "440", "llm"),
    num("improvements.garage_cars", "2"),
    num("site.area_sqft", "8000"),
    num("improvements.total_rooms", "8"),
    num("improvements.bedrooms", "4"),
    num("improvements.bathrooms", "2.5"),
    num("improvements.year_built", "1990"),
    Row("subject", "state", value_text=" il "),
    Row("subject", "zip", value_text="60601"),
    Row("reconciliation", "effective_date_of_appraisal", value_date=date(2025, 6, 1)),
]
# Program weight: kitchen 150 + 4 x 150 + 2 x 75 + 1 x 40 + 3 x 200 = 1,540.
PROGRAM = Decimal(1540)


@pytest.fixture
def full() -> BuildingMeasurements:
    return measurements_from_fields(FULL)


def test_fields_are_read_with_their_source(full: BuildingMeasurements) -> None:
    assert full.gla_sf == Reading(Decimal(2400), "improvements.gla_sqft", "azure")
    assert full.stories == Reading(Decimal(2), "improvements.stories", "llm")
    assert full.garage_sf == Reading(Decimal(440), "cost_approach.garage_sqft", "llm")
    assert full.state is not None and full.state.value == "il"
    assert full.effective_date is not None and full.effective_date.value == date(2025, 6, 1)
    assert not full.is_empty
    assert measurements_from_fields([]).is_empty


def test_fallback_keys_and_bad_values() -> None:
    """The second key fills in; a non-positive or non-integer value is ignored; a site
    area printed only in acres is converted at 43,560 SF per acre; a date row with no
    ``value_date`` is not a date."""
    m = measurements_from_fields(
        [
            num("improvements.gla_sqft", "-5"),
            num("cost_approach.dwelling_sqft", "1800", "llm"),
            num("improvements.stories", "0"),
            num("improvements.basement_finish_percent", "150"),
            num("improvements.garage_cars", "1.5"),
            num("site.area_acres", "0.5"),
            Row("subject", "state", value_text="   "),
            Row("reconciliation", "effective_date_of_appraisal", value_text="06/01/2025"),
            Row("appraiser", "effective_date", value_date=date(2025, 6, 2)),
        ]
    )
    assert m.gla_sf == Reading(Decimal(1800), "cost_approach.dwelling_sqft", "llm")
    assert m.stories is None and m.basement_finish_pct is None and m.garage_cars is None
    assert m.site_sf == Reading(Decimal("21780.00"), "site.area_acres", "azure")
    assert m.state is None
    assert m.effective_date is not None and m.effective_date.field == "appraiser.effective_date"


@pytest.mark.parametrize(
    ("baths", "full_half"),
    [("2.1", (2, 1)), ("2.5", (2, 1)), ("3", (3, 0)), ("1.2", (1, 2)), ("0.1", (0, 1))],
)
def test_bath_counts(baths: str, full_half: tuple[int, int]) -> None:
    assert bath_counts(Decimal(baths)) == full_half


def test_program_weight(full: BuildingMeasurements) -> None:
    program = program_weight(full)
    assert program is not None and program.quantity == PROGRAM
    assert program.inputs["program_other_rooms"] == "3"
    assert program.inputs["program_half_baths"] == "1"
    assert program_weight(measurements_from_fields([num("improvements.gla_sqft", "2400")])) is None


def test_footprint_perimeter_and_garage(full: BuildingMeasurements) -> None:
    fp = footprint(full)
    assert fp is not None and (fp.quantity, fp.formula) == (
        Decimal("1200.00"),
        "2400 SF GLA / 2 stories",
    )
    # 1.5:1 rectangle of 1,640 SF: short side sqrt(1640 / 1.5) = 33.0656, perimeter
    # 2 x 33.0656 x 2.5 = 165.33.
    assert perimeter_ft(Decimal(1640)).quantize(Decimal("0.01")) == Decimal("165.33")
    assert perimeter_ft(Decimal(100)).quantize(Decimal("0.01")) == Decimal("40.82")
    garage = garage_area(full)
    assert garage is not None and garage.quantity == Decimal("440.00")  # the cost approach wins
    assert garage.inputs["garage_sf_field"] == "cost_approach.garage_sqft (llm)"
    by_cars = garage_area(measurements_from_fields([num("improvements.garage_cars", "2")]))
    assert by_cars is not None and (by_cars.quantity, by_cars.formula) == (
        Decimal("480.00"),
        "2 cars x 240 SF",
    )
    assert by_cars.assumptions and "240 SF per car" in by_cars.assumptions[0]
    assert garage_area(measurements_from_fields([])) is None


def test_roof_siding_gutter_landscaping_by_hand(full: BuildingMeasurements) -> None:
    # Ground footprint 1,200 + 440 = 1,640 SF; perimeter 165.328 ft.
    # Roof: (1,640 + 165.328 x 1) x 1.118 = 1,805.328 x 1.118 = 2,018.36.
    roof = roof_area(full)
    assert roof is not None and (roof.quantity, roof.unit) == (Decimal("2018.36"), "SF")
    assert roof.inputs["pitch_factor"] == "1.118" and roof.inputs["gla_field"] == (
        "improvements.gla_sqft (azure)"
    )
    # Siding: 165.328 x 9 + 141.421 (dwelling perimeter, 2 x sqrt(800) x 2.5) x 9 x 1
    # = 1,487.95 + 1,272.79 = 2,760.74; less 15% = 2,346.63.
    siding = siding_area(full)
    assert siding is not None and (siding.quantity, siding.unit) == (Decimal("2346.63"), "SF")
    assert "less 15% openings" in siding.formula
    # Gutters: long side sqrt(1640 / 1.5) x 1.5 = 49.598; 2 x (49.598 + 2) = 103.20.
    gutter = gutter_length(full)
    assert gutter is not None and (gutter.quantity, gutter.unit) == (Decimal("103.20"), "LF")
    # Landscaping: 8,000 - 1,640 = 6,360 (hardscape not deducted).
    landscaping = landscaping_area(full)
    assert landscaping is not None and landscaping.quantity == Decimal("6360.00")
    assert any("not deducted" in a for a in landscaping.assumptions)
    assert landscaping.inputs["quantity_bound"] == "upper"
    assert set(property_quantities(full)) == {"roof", "siding", "gutter", "landscaping"}


def test_one_story_is_assumed_and_a_small_lot_has_no_landscaping() -> None:
    m = measurements_from_fields(
        [num("improvements.gla_sqft", "1500"), num("site.area_sqft", "1500")]
    )
    fp = footprint(m)
    assert fp is not None and fp.quantity == Decimal("1500.00")
    assert fp.assumptions == ("1 story assumed: stories not printed",)
    siding = siding_area(m)
    assert siding is not None and "upper stories" not in siding.formula
    # 1,500 x 9 x 0.85 ... on the perimeter 2 x sqrt(1000) x 2.5 = 158.114: 1,423.03 x 0.85
    assert siding.quantity == Decimal("1209.57")
    assert landscaping_area(m) is None
    assert set(property_quantities(m)) == {"roof", "siding", "gutter"}


def test_nothing_is_derived_without_a_gla() -> None:
    m = measurements_from_fields([num("site.area_sqft", "8000"), num("improvements.stories", "2")])
    assert property_quantities(m) == {}
    assert footprint(m) is None and roof_area(m) is None and landscaping_area(m) is None


def test_needs_measurement_names_the_site_items() -> None:
    assert set(NEEDS_MEASUREMENT) == {
        "driveway",
        "sidewalk",
        "patio",
        "fence",
        "retaining_wall",
        "concrete_pad",
        "parking_lot",
    }


@pytest.mark.parametrize(
    ("room_type", "floor", "level"),
    [
        ("kitchen", "1", "above_grade"),
        ("bedroom", None, "above_grade"),
        ("bedroom", "0", "basement"),
        ("basement", None, "basement"),
        ("garage", "1", "garage"),
        ("exterior", None, "exterior"),
        ("exterior", "0", "exterior"),
        ("parking_lot", None, "exterior"),
        ("mechanical", None, "above_grade"),
    ],
)
def test_room_level(room_type: str, floor: str | None, level: str) -> None:
    assert room_level(room_type, floor) == level


def test_weights_cover_the_vision_vocabulary() -> None:
    from basis.vision.schemas import RoomType

    for room_type in RoomType:
        assert room_weight_sf(room_type.value) > 0
    assert room_weight_sf("closet") == Decimal(30)  # aliased to storage
    assert room_weight_sf("mystery") == Decimal(150)
    # The legacy residential typical areas are the weights where they exist.
    assert ROOM_WEIGHTS_SF["kitchen"] == 150 and ROOM_WEIGHTS_SF["bathroom"] == 75


ROOMS = [
    RoomStub("k", "kitchen", "1"),
    RoomStub("b2", "bedroom", "2"),
    RoomStub("bb1", "bedroom", "0"),
    RoomStub("bb2", "bedroom", "0"),
    RoomStub("bath0", "bathroom", "0"),
    RoomStub("g", "garage", "1"),
    RoomStub("x", "exterior", None),
    RoomStub("m", "mystery", "1"),
]


def test_room_areas_share_the_gla_by_program_weight(full: BuildingMeasurements) -> None:
    areas = room_areas(full, ROOMS, "residential_rental")
    # Above grade: study weight 150 + 150 + 150 (mystery, default) = 450 < 1,540 program:
    # scale 2400 / 1540 = 1.5584; kitchen and bedroom 2400 x 150 / 1540 = 233.77.
    assert (areas["k"].area_sf, areas["k"].source) == (Decimal("233.77"), "gla_share")
    assert areas["k"].formula == "2400 SF GLA x 150 / 1540 (kitchen weight / program weight)"
    assert areas["k"].inputs["weight_total_from"] == "program weight"
    assert areas["k"].inputs["scale"] == "1.5584"
    assert areas["b2"].area_sf == Decimal("233.77")
    assert areas["m"].area_sf == Decimal("233.77")  # an unknown type at the default weight
    # Basement: weights 150 + 150 + 75 = 375 at the house scale = 584.4 SF, under the
    # 600 SF finished (1,000 x 60%): bedrooms 150 x 1.5584 = 233.77, bath 75 x 1.5584 = 116.88.
    assert (areas["bb1"].area_sf, areas["bb1"].source) == (Decimal("233.77"), "basement_share")
    assert areas["bath0"].area_sf == Decimal("116.88")
    assert areas["bb1"].inputs["finished_basement_sf"] == "600"
    # Garage: the garage area; exterior: today's typical, recorded as such.
    assert (areas["g"].area_sf, areas["g"].source) == (Decimal("440.00"), "garage_area")
    assert (areas["x"].area_sf, areas["x"].source) == (Decimal(150), "default_150_sf")
    assert sum(a.area_sf for a in areas.values() if a.source == "gla_share") < Decimal(2400)


def test_basement_rooms_are_capped_by_the_finished_area() -> None:
    rows = [r for r in FULL if r.field != "basement_finish_percent"]
    rows.append(num("improvements.basement_finish_percent", "30"))
    areas = room_areas(measurements_from_fields(rows), ROOMS, "residential_rental")
    # Finished 300 SF < 584.4 at the house scale: 300 x 150 / 375 = 120; 300 x 75 / 375 = 60.
    assert areas["bb1"].area_sf == Decimal("120.00")
    assert areas["bath0"].area_sf == Decimal("60.00")
    assert "would exceed the finished area" in areas["bb1"].formula
    assert areas["bb1"].area_sf + areas["bb2"].area_sf + areas["bath0"].area_sf == Decimal(300)


def test_basement_finish_defaults_to_100_percent() -> None:
    rows = [r for r in FULL if r.field != "basement_finish_percent"]
    areas = room_areas(measurements_from_fields(rows), ROOMS, "residential_rental")
    assert areas["bb1"].inputs["basement_finish_field"] == "assumed"
    assert "basement finish not printed: 100% assumed" in areas["bb1"].assumptions


def test_without_a_room_count_the_study_rooms_are_the_denominator() -> None:
    m = measurements_from_fields([num("improvements.gla_sqft", "2400")])
    rooms = [RoomStub("k", "kitchen"), *(RoomStub(f"b{i}", "bedroom") for i in range(3))]
    areas = room_areas(m, rooms, "residential_rental")
    # 150 + 3 x 150 = 600: kitchen 2400 x 150 / 600 = 600.
    assert areas["k"].area_sf == Decimal("600.00")
    assert areas["k"].inputs["weight_total_from"] == "study rooms' weight"
    assert "program_weight_sf" not in areas["k"].inputs


def test_more_photographed_rooms_than_the_program_conserve_the_gla(
    full: BuildingMeasurements,
) -> None:
    rooms = [RoomStub(f"b{i}", "bedroom") for i in range(12)]  # 1,800 > 1,540
    areas = room_areas(full, rooms, "residential_rental")
    assert all(a.area_sf == Decimal("200.00") for a in areas.values())  # 2400 x 150 / 1800
    assert sum(a.area_sf for a in areas.values()) == Decimal(2400)


def test_without_a_gla_every_room_is_typical_except_the_garage() -> None:
    m = measurements_from_fields([num("improvements.garage_cars", "1")])
    areas = room_areas(m, ROOMS, "residential_rental")
    assert (areas["k"].area_sf, areas["k"].source) == (Decimal(150), "typical_for_room_type")
    assert (areas["bath0"].area_sf, areas["bath0"].source) == (Decimal(75), "typical_for_room_type")
    assert (areas["m"].area_sf, areas["m"].source) == (Decimal(150), "default_150_sf")
    assert (areas["g"].area_sf, areas["g"].source) == (Decimal("240.00"), "garage_area")
    assert room_areas(measurements_from_fields([]), ROOMS, "residential_rental")["g"].source == (
        "default_150_sf"
    )


def test_two_garage_rooms_share_the_garage(full: BuildingMeasurements) -> None:
    rooms = [RoomStub("g1", "garage"), RoomStub("g2", "garage")]
    areas = room_areas(full, rooms, "residential_rental")
    assert [a.area_sf for a in areas.values()] == [Decimal("220.00"), Decimal("220.00")]
    assert areas["g1"].formula == "440 SF garage / 2 garage rooms"


def test_cost_index_from_the_study(full: BuildingMeasurements) -> None:
    index = resolve_cost_index(full, cost_year=2026, location_factor=Decimal(1))
    assert (index.cost_year, index.location_factor) == (2025, Decimal("1.05"))
    assert index.year_source == (
        "appraisal effective date 2025-06-01 (reconciliation.effective_date_of_appraisal (azure))"
    )
    assert index.location_source == "state IL (subject.state (azure))"


def test_cost_index_falls_back_to_the_settings_with_the_reason() -> None:
    m = measurements_from_fields(
        [
            Row("subject", "state", value_text="XX"),
            Row("reconciliation", "effective_date_of_appraisal", value_date=date(2019, 3, 1)),
        ]
    )
    index = resolve_cost_index(m, cost_year=2024, location_factor=Decimal("0.9"))
    assert (index.cost_year, index.location_factor) == (2024, Decimal("0.9"))
    assert index.year_source == (
        "COST_YEAR setting: appraisal effective date 2019-03-01 is outside the 2020-2026 "
        "factor table"
    )
    assert index.location_source == "COST_LOCATION_FACTOR setting: no factor for state 'XX'"
    bare = resolve_cost_index(None, cost_year=2024, location_factor=Decimal("0.9"))
    assert (bare.cost_year, bare.year_source, bare.location_source) == (
        2024,
        "COST_YEAR setting",
        "COST_LOCATION_FACTOR setting",
    )
    empty = resolve_cost_index(
        measurements_from_fields([]), cost_year=2024, location_factor=Decimal(1)
    )
    assert (empty.year_source, empty.location_source) == (
        "COST_YEAR setting",
        "COST_LOCATION_FACTOR setting",
    )


def test_placed_in_service_wins_over_the_appraisal_date(full: BuildingMeasurements) -> None:
    index = resolve_cost_index(
        full, cost_year=2026, location_factor=Decimal(1), placed_in_service=date(2023, 1, 15)
    )
    assert (index.cost_year, index.year_source) == (
        2023,
        "placed in service 2023-01-15 (intake)",
    )
    outside = resolve_cost_index(
        full, cost_year=2026, location_factor=Decimal(1), placed_in_service=date(2019, 1, 15)
    )
    # The appraisal's year applies; the source says why the intake's date did not.
    assert outside.cost_year == 2025
    assert outside.year_source == (
        "appraisal effective date 2025-06-01 (reconciliation.effective_date_of_appraisal "
        "(azure)); placed in service 2019-01-15 (intake) is outside the 2020-2026 factor table"
    )


def test_the_intake_state_wins_over_the_appraisal_state(full: BuildingMeasurements) -> None:
    """EVAL_REPORT 37: the intake's state (NY, 1.20) ahead of the appraisal's (IL, 1.05)."""
    index = resolve_cost_index(full, cost_year=2026, location_factor=Decimal(1), state="NY")
    assert (index.location_factor, index.location_source) == (Decimal("1.20"), "state NY (intake)")
    # An intake state the table lacks: the appraisal's applies and the miss is recorded.
    unknown = resolve_cost_index(full, cost_year=2026, location_factor=Decimal(1), state="XX")
    assert (unknown.location_factor, unknown.location_source) == (
        Decimal("1.05"),
        "state IL (subject.state (azure)); no factor for state 'XX' (intake)",
    )


def test_the_intake_alone_sets_the_index_without_any_appraisal_field() -> None:
    """A study with an intake but no extraction fields (TAKEOFF_AREAS=typical, or a
    failed extraction): the intake still fixes the year and the state."""
    index = resolve_cost_index(
        None,
        cost_year=2026,
        location_factor=Decimal(1),
        placed_in_service=date(2024, 3, 1),
        state="ny",
    )
    assert (index.cost_year, index.location_factor) == (2024, Decimal("1.20"))
    assert (index.year_source, index.location_source) == (
        "placed in service 2024-03-01 (intake)",
        "state NY (intake)",
    )
    # Neither usable: the settings, with both reasons.
    neither = resolve_cost_index(
        measurements_from_fields([]),
        cost_year=2026,
        location_factor=Decimal("0.9"),
        placed_in_service=date(2030, 1, 1),
        state="ZZ",
    )
    assert (neither.cost_year, neither.location_factor) == (2026, Decimal("0.9"))
    assert neither.year_source == (
        "COST_YEAR setting: placed in service 2030-01-01 (intake) is outside the 2020-2026 "
        "factor table"
    )
    assert (
        neither.location_source == "COST_LOCATION_FACTOR setting: no factor for state 'ZZ' (intake)"
    )
