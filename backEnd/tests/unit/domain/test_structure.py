"""The structure model (``basis.domain.structure``, docs/review_2026-10-15/STRUCTURE_MODEL.md):
the transcribed RSMeans models foot, the class and type selection, the building RCN from
the appraisal (printed total against its parts, the year factor) and from the square-foot
model, the split with Overhead spread, every label mapped, the carve-out with its clamp,
the concealed rows' quantities and shares, depreciation, and the reconciliation's verdicts.
Fabricated values only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest

from basis.domain import structure as s
from basis.domain.measurements import BuildingMeasurements, Reading, measurements_from_fields
from basis.domain.rules import RULES
from basis.vision.schemas import COMPONENT_LABELS

D = Decimal


@dataclass(frozen=True)
class Row:
    section: str
    field: str
    value_num: Decimal | None = None
    value_text: str | None = None
    value_date: date | None = None
    source: str = "llm"


def rows(**values: object) -> list[Row]:
    out = []
    for key, value in values.items():
        section, field = key.split("__")
        if isinstance(value, str):
            out.append(Row(section, field, value_text=value))
        else:
            out.append(Row(section, field, value_num=D(str(value))))
    return out


# A 2-story, Q3 house with a developed cost approach (fabricated, in the shape of a URAR).
HOUSE = rows(
    improvements__gla_sqft=3000,
    improvements__stories=2,
    improvements__basement_area_sqft=1500,
    improvements__basement_finish_percent=80,
    improvements__garage_cars=2,
    improvements__quality="Q3",
    improvements__effective_age_years=15,
    improvements__bedrooms=5,
    improvements__total_rooms=10,
    improvements__year_built=1950,
    cost_approach__dwelling_cost=400000,
    cost_approach__dwelling_sqft=3000,
    cost_approach__dwelling_cost_per_sqft="133.33",
    cost_approach__basement_cost=120000,
    cost_approach__garage_cost=40000,
    cost_approach__mechanicals_misc=40000,
    cost_approach__total_cost_new=600000,
    cost_approach__depreciation_physical=150000,
    cost_approach__depreciation=150000,
    cost_approach__depreciated_cost_improvements=450000,
    cost_approach__remaining_economic_life=45,
    cost_approach__site_improvements_value=20000,
    cost_approach__site_value=100000,
    cost_approach__effective_date_of_cost_data="2025-01",
    site__utilities_water="Public",
    site__utilities_sewer="Public",
    site__utilities_gas="Public",
    site__utilities_electric="Public",
)


def inputs(field_rows: list[Row] = HOUSE, **overrides: object) -> s.StructureInputs:
    m = measurements_from_fields(field_rows)
    values: dict[str, object] = {
        "measurements": None if m.is_empty else m,
        "cost_approach": s.cost_approach_from_fields(field_rows),
        "cost_year": 2024,
        "year_source": "placed in service 2024-12-03 (intake)",
        "location_factor": D("1.15"),
        "location_source": "state CA (intake)",
    }
    values.update(overrides)
    return s.StructureInputs(**values)  # type: ignore[arg-type]


# ----------------------------------------------------------------- the transcribed models


def test_every_model_page_foots_to_its_bottom_line() -> None:
    assert len(s.MODELS) == 18
    for (cls, type_), model in s.MODELS.items():
        assert (model.cls, model.type) == (cls, type_)
        assert sum(model.per_sf.values(), D(0)) + model.overhead == model.total, model.name
        assert set(model.per_sf) == set(s.COMPONENTS)
        assert model.finished_basement_add > model.unfinished_basement_add > 0
        assert model.garage_modification > 0 and model.garage_cars in (1, 2)
        assert model.printed_page == model.pdf_page - 14


def test_component_shares_spread_overhead_and_sum_to_one() -> None:
    model = s.MODELS[("custom", "2 story")]
    shares = s.component_shares(model)
    assert shares["foundation"] == D("0.085138")  # 9.24 / 108.53
    assert shares["interiors"] == D("0.317977")
    assert abs(sum(shares.values(), D(0)) - 1) <= D("0.000005")


def test_split_foots_to_the_rcn_to_the_cent() -> None:
    model = s.MODELS[("average", "2 story")]
    parts = s.split_components(D("123456.78"), model)
    assert sum(parts.values(), D(0)) == D("123456.78")
    assert parts["site_work"] == D("1739.17")  # 123456.78 x 1.43 / 101.51


# ----------------------------------------------------------------- class and type


@pytest.mark.parametrize(
    ("quality", "service", "expected", "defaulted"),
    [
        ("Q1", None, "luxury", False),
        ("Q2", None, "luxury", False),
        ("Q3", None, "custom", False),
        ("Q4", None, "average", False),
        ("Q5", None, "economy", False),
        ("Q6 ", None, "economy", False),
        (None, "Good", "custom", False),
        (None, "Very Good", "custom", False),
        (None, "Average Quality", "average", False),
        (None, "Excellent", "luxury", False),
        (None, "Fair", "economy", False),
        (None, None, "average", True),
        ("Q9", "unreadable", "average", True),
    ],
)
def test_class_from_quality(
    quality: str | None, service: str | None, expected: str, defaulted: bool
) -> None:
    values: dict[str, object] = {}
    if quality is not None:
        values["improvements__quality"] = quality
    if service is not None:
        values["cost_approach__quality_rating_from_cost_service"] = service
    ca = s.cost_approach_from_fields(rows(**values))
    cls, source, was_defaulted = s.class_of(ca)
    assert (cls, was_defaulted) == (expected, defaulted)
    assert ("assumed" in source) == defaulted


@pytest.mark.parametrize(
    ("stories", "expected"),
    [
        ("1", "1 story"),
        ("1.5", "1-1/2 story"),
        ("2", "2 story"),
        ("2.5", "2-1/2 story"),
        ("3", "3 story"),
        ("4", "3 story"),
    ],
)
def test_type_from_stories(stories: str, expected: str) -> None:
    # ``measurements_from_fields`` keeps integral story counts only; a half story reaches
    # ``type_of`` as a reading (the UAD prints "1.5" for a one-and-a-half story house).
    m = BuildingMeasurements(stories=Reading(D(stories), "improvements.stories", "llm"))
    assert s.type_of(m)[0] == expected


def test_integral_stories_read_from_the_fields() -> None:
    m = measurements_from_fields(rows(improvements__stories=2, improvements__gla_sqft=1000))
    chosen, source, defaulted = s.type_of(m)
    assert (chosen, defaulted) == ("2 story", False) and "improvements.stories" in source


def test_type_defaults_to_one_story_with_review() -> None:
    choice = s.select_model(None, None)
    assert (choice.model.cls, choice.model.type, choice.needs_review) == (
        "average",
        "1 story",
        True,
    )
    assert len(choice.assumptions) == 2


# ----------------------------------------------------------------- the building RCN


APPRAISAL = "appraisal_total"


# The same house with the cost approach's data dated 2015 (inside the index table).
HOUSE_2015 = [r for r in HOUSE if r.field != "effective_date_of_cost_data"] + rows(
    cost_approach__effective_date_of_cost_data="2015-01"
)


def test_appraisal_rcn_uses_the_printed_total_and_checks_the_parts() -> None:
    i = inputs(HOUSE_2015)
    rcn = s.building_rcn(
        i.measurements, i.cost_approach, cost_year=2018, year_source="intake", rcn_source=APPRAISAL
    )
    assert rcn is not None and rcn.source == "appraisal_cost_approach"
    # 600,000 printed, parts also 600,000; from 2015 cost data to 2018 by the Historical
    # Cost Index: 222.9 / 206.2 = 1.0810.
    assert rcn.amount == D("648600.00")
    assert rcn.inputs["year_factor_formula"] == "HCI 222.9 (July 2018) / HCI 206.2 (July 2015)"
    assert {c.code: c.severity for c in rcn.checks} == {"cost_approach_parts": "ok"}
    assert rcn.needs_review is False
    assert rcn.model.name == "Custom 2 Story"
    assert "no location factor applied" in " ".join(rcn.assumptions)
    assert rcn.appraisal_depreciated_cost == D(450000) and rcn.appraisal_site_value == D(100000)


def test_appraisal_rcn_without_a_published_index_is_taken_as_of_the_cost_year() -> None:
    """Cost data dated 2025 and a 2024 cost year: neither year has a published index in
    the 2020 book, so the appraiser's figure is used as printed and the assumption says
    why (COST_INDICES.md section 4)."""
    i = inputs()
    rcn = s.building_rcn(
        i.measurements, i.cost_approach, cost_year=2024, year_source="intake", rcn_source=APPRAISAL
    )
    assert rcn is not None and rcn.amount == D("600000.00")
    assert "year_factor" not in rcn.inputs
    assert (
        "the appraisal's cost new is taken as of the cost year 2024: no published cost index "
        "for 2025 or 2024"
    ) in rcn.assumptions


def test_appraisal_rcn_warns_when_the_parts_disagree_and_keeps_the_total() -> None:
    # Fabricated: the parts sum to 650,000 against a printed total of 641,000 (+1.4 %).
    field_rows = rows(
        improvements__gla_sqft=3000,
        cost_approach__dwelling_cost=400000,
        cost_approach__basement_cost=150000,
        cost_approach__garage_cost=40000,
        cost_approach__mechanicals_misc=60000,
        cost_approach__total_cost_new=641000,
    )
    i = inputs(field_rows)
    rcn = s.building_rcn(i.measurements, i.cost_approach, cost_year=2024, rcn_source=APPRAISAL)
    assert rcn is not None
    check = rcn.checks[0]
    assert (check.code, check.severity) == ("cost_approach_parts", "warning")
    assert "650000" in check.message and "+1.4 %" in check.message
    assert rcn.needs_review is True
    # No cost data date: taken as of the cost year, said so.
    assert rcn.amount == D("641000")
    assert any("prints no cost data date" in a for a in rcn.assumptions)


def test_appraisal_rcn_sums_the_parts_without_a_printed_total() -> None:
    field_rows = rows(
        cost_approach__dwelling_sqft=2000,
        cost_approach__dwelling_cost_per_sqft=150,
        cost_approach__garage_sqft=400,
        cost_approach__garage_cost_per_sqft=50,
        cost_approach__effective_date_of_cost_data="2024-06",
    )
    i = inputs(field_rows)
    rcn = s.building_rcn(i.measurements, i.cost_approach, cost_year=2024, rcn_source=APPRAISAL)
    assert rcn is not None
    # The garage per-SF line is used only beside a dwelling cost line; here the dwelling is
    # area x rate, so the garage line is not: 2000 x 150 = 300,000, factor 1.
    assert rcn.amount == D("300000.00")
    assert rcn.checks[0].severity == "not_checked"


def test_square_foot_model_when_the_cost_approach_is_not_developed() -> None:
    field_rows = rows(
        improvements__gla_sqft=2000,
        improvements__stories=2,
        improvements__quality="Q4",
        improvements__basement_area_sqft=1000,
        improvements__basement_finish_percent=50,
        improvements__garage_cars=1,
    )
    i = inputs(field_rows)
    rcn = s.building_rcn(
        i.measurements,
        i.cost_approach,
        cost_year=2024,
        location_factor=D("1.15"),
        location_source="state CA (intake)",
    )
    assert rcn is not None and rcn.source == "square_foot_model"
    # The default source is the square-foot model (the sweep's pick) whenever a living area
    # sizes it, cost approach or not.
    i2 = inputs()
    default = s.building_rcn(i2.measurements, i2.cost_approach, cost_year=2024)
    assert default is not None and default.source == "square_foot_model"
    # Average 2 Story: 118.75 x 2000 + basement (21.65 x 0.5 + 7.50 x 0.5) x 2000 x 1.0
    # + one-car garage 15,712, then x 1.0000 (2024: no published index, the Jan 2020 one
    # applies, flagged) x 1.15 (the location factor given).
    base = D("118.75") * 2000 + (D("21.65") + D("7.50")) / 2 * 2000 + D(15712)
    assert rcn.amount == (base * D("1.15")).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    assert rcn.needs_review is True
    assert [c.code for c in rcn.checks] == ["building_rcn_source", "cost_year_index"]
    assert any("no published cost index for 2024" in a for a in rcn.assumptions)
    assert rcn.inputs["year_factor_formula"].startswith("HCI 239.1 (Jan 2020, est.)")
    # A cost year inside the table: its index ratio, no assumption, one check.
    dated = s.building_rcn(
        i.measurements, i.cost_approach, cost_year=2018, location_factor=D("1.15")
    )
    assert dated is not None and dated.amount == (base * D("0.9322") * D("1.15")).quantize(
        D("0.01"), rounding=ROUND_HALF_UP
    )
    assert [c.code for c in dated.checks] == ["building_rcn_source"]
    assert dated.inputs["year_factor_formula"] == (
        "HCI 222.9 (July 2018) / 239.1 (Jan 2020, est.), "
        "RSMeans Building Construction Costs 2020 p.778"
    )
    assert rcn.inputs["base_cost_per_sf"] == "118.75"  # the matrix's 2,000 SF column
    assert "wood siding on wood frame" in " ".join(rcn.assumptions)


def test_the_size_matrix_meets_the_model_pages() -> None:
    """The wood-siding cell at the model's representative living area is the model page's
    bottom line, except the Economy 1 Story model, which is the stucco row."""
    for key, model in s.MODELS.items():
        cells = dict(s.BASE_COST_BY_AREA[key])
        assert len(cells) == 11 and list(cells) == sorted(cells)
        costs = list(cells.values())
        assert all(a > b for a, b in zip(costs, costs[1:], strict=False)), model.name
        if key == ("economy", "1 story"):
            assert cells[model.living_area_sf] == D("117.85") and model.total == D("110.25")
        else:
            assert cells[model.living_area_sf] == model.total, model.name


def test_base_cost_interpolates_and_clamps() -> None:
    custom = s.MODELS[("custom", "2 story")]
    cost, formula, assumptions = s.base_cost_per_sf(custom, D(3000))
    # Between 2,800 SF (130.25) and 3,200 SF (124.15).
    assert (cost, assumptions) == (D("127.20"), ())
    assert "interpolated between 2800 SF" in formula
    assert s.base_cost_per_sf(custom, D(2800)) == (D("130.25"), "130.25/SF at 2800 SF", ())
    low = s.base_cost_per_sf(custom, D(900))
    assert low[0] == D("184.90") and "below the matrix" in low[2][0]
    high = s.base_cost_per_sf(custom, D(9000))
    assert high[0] == D("113.55") and "above the matrix" in high[2][0]


def test_rcn_source_variants() -> None:
    i = inputs()
    parts = s.building_rcn(
        i.measurements, i.cost_approach, cost_year=2024, rcn_source="appraisal_parts"
    )
    total = s.building_rcn(i.measurements, i.cost_approach, cost_year=2024, rcn_source=APPRAISAL)
    assert parts is not None and total is not None
    # HOUSE's parts and printed total agree (600,000 each), so the amounts agree too.
    assert parts.amount == total.amount and parts.formula.startswith("the cost approach's parts")
    assert parts.checks[0].code == "cost_approach_parts" and "are used" in parts.checks[0].message
    sqft = s.building_rcn(i.measurements, i.cost_approach, cost_year=2024, rcn_source="square_foot")
    assert sqft is not None and sqft.source == "square_foot_model"
    # Custom 2 Story at 3,000 SF: 127.20/SF; the appraisal path is used when no GLA sizes a model.
    assert sqft.inputs["base_cost_per_sf"] == "127.20"
    no_gla = inputs(rows(cost_approach__total_cost_new=1000, cost_approach__dwelling_cost=1000))
    fallback = s.building_rcn(
        no_gla.measurements, no_gla.cost_approach, cost_year=2024, rcn_source="square_foot"
    )
    assert fallback is not None and fallback.source == "appraisal_cost_approach"


def test_split_methods() -> None:
    model = s.MODELS[("custom", "2 story")]
    rsmeans = s.split_shares(model, "rsmeans")
    nahb = s.split_shares(model, "nahb")
    for shares in (rsmeans, nahb):
        assert set(shares) == set(s.COMPONENTS)
        assert abs(sum(shares.values(), D(0)) - 1) <= D("0.00001")
    # NAHB: foundations 10.5 of the 83.8 building stages, split site work / foundation by the
    # model's 1.14 : 9.24.
    stage = D("10.5") / D("83.8")
    assert nahb["foundation"] == (stage * D("9.24") / D("10.38")).quantize(D("0.000001"))
    assert nahb["site_work"] + nahb["foundation"] == pytest.approx(stage, abs=1e-6)
    parts = s.split_components(D("100000.00"), model, "nahb")
    assert sum(parts.values(), D(0)) == D("100000.00")
    with pytest.raises(s.StructureError, match="not available"):
        s.split_shares(model, "marshall_swift")
    assert "NAHB" in s.split_source(model, "nahb") and "Overhead" in s.split_source(model)


def test_top_down_only_does_not_carve() -> None:
    top = dict.fromkeys(s.COMPONENTS, D("1000.00"))
    carve = s.carve_out(top, [s.ItemizedRow("window", D("600.00"))], carve=False)
    walls = carve.get("exterior_walls")
    assert (walls.itemized, walls.residual, walls.clamped, carve.carved) == (
        D("600.00"),
        D("1000.00"),
        D(0),
        False,
    )


def test_depreciation_methods() -> None:
    ca = s.cost_approach_from_fields(HOUSE)
    assert s.depreciation(ca, "none") is None
    by_age = s.depreciation(ca, "age_life")
    assert by_age is not None and by_age.method == "age_life" and by_age.rate == D("0.250000")
    physical = s.depreciation(ca, "appraisal_physical")
    assert physical is not None and physical.method == "appraisal_physical_depreciation"


def test_no_rcn_without_a_cost_approach_or_a_living_area() -> None:
    i = inputs(rows(improvements__stories=2))
    assert s.building_rcn(i.measurements, i.cost_approach, cost_year=2024) is None
    assert s.building_rcn(None, None, cost_year=2024) is None


# ----------------------------------------------------------------- the mapping


def test_every_label_is_mapped_to_a_system_and_a_component_or_none() -> None:
    for label in (*COMPONENT_LABELS, *s.STRUCTURE_LABELS):
        mapping = s.LABEL_SYSTEMS[label]
        assert mapping.system in s.SYSTEMS, label
        assert mapping.component is None or mapping.component in s.COMPONENTS, label
    assert set(s.LABEL_SYSTEMS) == {*COMPONENT_LABELS, *s.STRUCTURE_LABELS}


def test_every_structure_label_has_a_rules_row_a_category_and_an_engineer_row() -> None:
    for label in s.STRUCTURE_LABELS:
        assert label in RULES, label
        assert label in s.CATEGORIES and label in s.ENGINEER_ROWS
    assert s.ENGINEER_ROWS["foundation"] == "Foundations"
    assert s.ENGINEER_ROWS["interior_partition"] == "Interior Framing / Partitions"
    assert s.ENGINEER_ROWS["data_cabling"] == "Communication / Data"


def test_aliases_map_through_the_rules_table_and_unknowns_are_outside() -> None:
    assert s.mapping_for("cabinetry") == s.LABEL_SYSTEMS["kitchen_cabinet"]
    assert s.mapping_for("tile_flooring").component == "interiors"
    assert s.mapping_for(None).component is None
    assert s.mapping_for("no_such_label").component is None
    assert s.mapping_for("other").system == s.MISCELLANEOUS


# ----------------------------------------------------------------- the carve-out


def test_carve_out_subtracts_the_itemized_rows_and_clamps() -> None:
    top = dict.fromkeys(s.COMPONENTS, D("1000.00"))
    carve = s.carve_out(
        top,
        [
            s.ItemizedRow("window", D("600.00")),
            s.ItemizedRow("exterior_door", D("700.00")),  # exterior walls: 1300 > 1000
            s.ItemizedRow("carpet", D("250.00")),
            s.ItemizedRow("interior_door", None),  # unpriced
            s.ItemizedRow("refrigerator", D("900.00")),  # outside the model
            s.ItemizedRow(None, D("5.00")),  # no label: outside
        ],
    )
    walls = carve.get("exterior_walls")
    assert (walls.itemized, walls.residual, walls.clamped) == (D("1300.00"), D(0), D("300.00"))
    interiors = carve.get("interiors")
    assert (interiors.itemized, interiors.unpriced_assets, interiors.residual) == (
        D("250.00"),
        1,
        D("750.00"),
    )
    assert carve.get("foundation").residual == D("1000.00")
    assert (carve.outside_model, carve.outside_model_assets) == (D("905.00"), 2)


def test_concealed_rows_split_interiors_and_mechanical_by_the_sourced_shares() -> None:
    i = inputs()
    rcn = s.building_rcn(i.measurements, i.cost_approach, cost_year=2024, rcn_source=APPRAISAL)
    assert rcn is not None
    carve = s.carve_out(s.split_components(rcn.amount, rcn.model), [])
    small = s.small_system_inputs(i.measurements, i.cost_approach, [])
    produced = {r.label: r for r in s.concealed_rows(rcn, carve, i.living_area, small)}
    assert set(produced) == set(s.STRUCTURE_LABELS)
    interiors = carve.get("interiors").residual
    assert produced["interior_partition"].rcn + produced["painting"].rcn == interiors
    assert produced["painting"].rcn == (
        interiors * s.PAINTING_SHARE_OF_INTERIORS_RESIDUAL
    ).quantize(D("0.01"))
    mechanical = carve.get("mechanical").residual
    assert produced["plumbing_rough_in"].rcn + produced["hvac_distribution"].rcn == mechanical
    assert (
        produced["foundation"].rcn
        == carve.get("site_work").residual + carve.get("foundation").residual
    )
    # The nine building rows sum to the eight components they come from (not Specialties).
    building_sum = sum(
        (produced[k].rcn or D(0) for k in s.STRUCTURE_LABELS if produced[k].rcn is not None),
        D(0),
    )
    assert building_sum == rcn.amount - carve.get("specialties").residual
    foundation = produced["foundation"]
    assert (foundation.quantity, foundation.unit, foundation.method) == (D(3000), "SF", "area")
    assert "ATG ch. 4 C.11" in str(foundation.inputs["overhead_spread"])
    assert foundation.needs_review is False and foundation.review_reasons == ()
    assert "Custom 2 Story" in foundation.source
    assert foundation.inputs["split"] == "rsmeans" and foundation.inputs["carved"] is True


def test_small_system_quantities_follow_the_rules() -> None:
    i = inputs()
    small = s.small_system_inputs(
        i.measurements,
        i.cost_approach,
        [s.ItemizedRow("smoke_detector", D("50.00"), 2), s.ItemizedRow("exterior_door", None, 3)],
    )
    produced = {r.label: r for r in s.small_system_rows(small)}
    fire = produced["fire_alarm_system"]
    # 5 bedrooms + 2 stories + 1 basement = 8 required, 2 found.
    assert (fire.quantity, fire.unit, fire.inputs["required_alarms"]) == (D(6), "EA", 8)
    assert fire.rcn is None and fire.needs_review is False
    assert produced["data_cabling"].quantity == D(10)
    access = produced["access_control"]
    assert (access.quantity, access.needs_review) == (D(3), False)
    utilities = produced["site_utilities"]
    assert (utilities.quantity, utilities.unit, utilities.needs_review) == (D(150), "LF", True)
    assert utilities.inputs["public_utilities"] == "water, sewer, gas"


def test_small_systems_assume_two_doors_and_skip_what_the_appraisal_lacks() -> None:
    small = s.small_system_inputs(None, None, [])
    produced = {r.label: r for r in s.small_system_rows(small)}
    assert set(produced) == {"access_control"}
    access = produced["access_control"]
    assert (access.quantity, access.needs_review, access.review_reasons) == (
        D(2),
        True,
        ("structure:assumed_quantity",),
    )


def test_a_clamped_component_flags_its_row() -> None:
    i = inputs()
    rcn = s.building_rcn(i.measurements, i.cost_approach, cost_year=2024, rcn_source=APPRAISAL)
    assert rcn is not None
    carve = s.carve_out(
        s.split_components(rcn.amount, rcn.model), [s.ItemizedRow("roof", D("999999.00"))]
    )
    small = s.small_system_inputs(i.measurements, i.cost_approach, [])
    produced = {r.label: r for r in s.concealed_rows(rcn, carve, i.living_area, small)}
    roofing = produced["roofing_assembly"]
    assert roofing.rcn == D(0)
    assert set(roofing.review_reasons) == {"structure:component_clamped", "structure:residual_zero"}


# ----------------------------------------------------------------- depreciation


def test_depreciation_is_the_appraisers_rate_checked_against_age_life() -> None:
    d = s.depreciation(s.cost_approach_from_fields(HOUSE))
    assert d is not None
    assert (d.rate, d.method) == (D("0.250000"), "appraisal_physical_depreciation")
    codes = {c.code: c.severity for c in d.checks}
    assert codes == {"depreciation_age_life": "ok", "depreciation_uniform": "warning"}
    assert "25.0 %" in d.checks[0].message and "15 / 60 years" in d.checks[0].message


def test_depreciation_falls_back_to_age_life_and_then_to_nothing() -> None:
    d = s.depreciation(
        s.cost_approach_from_fields(
            rows(improvements__effective_age_years=12, cost_approach__remaining_economic_life=48)
        )
    )
    assert d is not None and (d.rate, d.method) == (D("0.200000"), "age_life")
    assert s.depreciation(s.cost_approach_from_fields(rows(improvements__gla_sqft=1))) is None
    assert s.depreciation(None) is None
    # A rate of 100 % or more is refused (nothing left to allocate).
    gone = rows(cost_approach__total_cost_new=100, cost_approach__depreciation_physical=100)
    assert s.depreciation(s.cost_approach_from_fields(gone)) is None


def test_depreciate_foots_to_the_cent() -> None:
    values = s.depreciate([D("100.00"), D("0.01"), D("33.33")], D("0.333333"))
    assert sum(values, D(0)) == (D("133.34") * (1 - D("0.333333"))).quantize(D("0.01"))
    assert s.depreciate([], D("0.5")) == []


# ----------------------------------------------------------------- reconciliation


def _reconcile(rcnld: Decimal | None, **overrides: object) -> s.Reconciliation:
    i = inputs()
    rcn = s.building_rcn(i.measurements, i.cost_approach, cost_year=2024, rcn_source=APPRAISAL)
    assert rcn is not None
    carve = s.carve_out(s.split_components(rcn.amount, rcn.model), [])
    values: dict[str, object] = {
        "building_basis": D("450000.00"),
        "total_basis": D("450500.00"),
        "rcn_total": D("600000.00"),
        "rcnld_total": rcnld,
        "building": rcn,
        "carve": carve,
        "concealed_by_component": {"foundation": D("50000.00")},
        "unpriced_assets": 3,
        "land_checks": [s.Check("land_share_of_price", "ok", "fine")],
        "depreciation_": s.depreciation(i.cost_approach),
        "purchase_price": D("550000.00"),
    }
    values.update(overrides)
    return s.reconcile(**values)  # type: ignore[arg-type]


def test_reconciled_within_tolerance() -> None:
    r = _reconcile(D("430000.00"))
    assert r.status == "reconciled"
    assert (r.gap, r.gap_share) == (D("20000.00"), D("0.044444"))
    assert r.implied_uniform_factor == D("0.750833333")
    assert r.checks[0].severity == "ok" and "RSMeans Residential 2020 p.15" in r.checks[0].message
    assert [c.component for c in r.by_component] == list(s.COMPONENTS)
    assert r.by_component[1].concealed == D("50000.00")
    assert [c for c, _ in r.unassigned_remainders] == ["specialties"]
    # The appraisal's own figures: 450,000 + 20,000 against 550,000 - 100,000.
    assert (r.appraisal_check.severity, "+4.4 %" in r.appraisal_check.message) == ("ok", True)
    assert [c[0] for c in r.causes] == [code for code, _ in s.ATG_CAUSES]
    assert "3 assets are unpriced" in r.causes[1][2]
    assert "appraisal cost approach" in r.causes[2][2]
    assert "25.0 %" in r.causes[3][2]


def test_unreconciled_outside_tolerance_names_the_land_warning() -> None:
    r = _reconcile(
        D("300000.00"), land_checks=[s.Check("land_flat_percentage", "warning", "rule of thumb")]
    )
    assert r.status == "unreconciled" and r.gap_share == D("0.333333")
    assert r.checks[0].severity == "error" and "withheld" in r.checks[0].message
    assert "land_flat_percentage: rule of thumb" in r.causes[0][2]


def test_not_checked_without_a_building_rcn_or_a_rate() -> None:
    r = _reconcile(None, building=None, carve=None, depreciation_=None)
    assert r.status == "not_checked" and r.by_component == ()
    assert "no building RCN" in r.checks[0].message
    r2 = _reconcile(None, depreciation_=None)
    assert r2.status == "not_checked" and "no depreciation rate" in r2.checks[0].message
    assert r2.appraisal_check.severity == "ok"


def test_the_assumption_constants_are_the_documented_ones() -> None:
    assert s.RECONCILIATION_TOLERANCE == D("0.10")
    assert s.PAINTING_SHARE_OF_INTERIORS_RESIDUAL == D("0.75") / D("2.22")
    assert s.PLUMBING_SHARE_OF_MECHANICAL_RESIDUAL == D("5041.55") / D("10971.45")
    assert (s.LATERAL_LENGTH_FT, s.EXTERIOR_DOORS_DEFAULT) == (D(50), 2)
