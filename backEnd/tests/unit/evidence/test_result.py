"""The study result (``basis.evidence.result.compute_result``): the schedule with and without
an intake, land and unpriced assets kept out of the allocation, the not-produced list, the
regrouping by system (every row mapped by its label), and the structure model's path: the
depreciated schedule reconciled to the basis and allocated pro rata on RCNLD only within
tolerance (docs/review_2026-10-15/STRUCTURE_MODEL.md section 3.8). Fabricated values."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

import pytest

from basis.domain.basis import ONE, REPORT_ROW_SYSTEMS, RcnRow, allocate
from basis.domain.cost_indices import YearIndex
from basis.domain.costs import year_factor_index
from basis.domain.intake import StudyIntakeIn, with_location
from basis.domain.measurements import measurements_from_fields
from basis.domain.structure import (
    ENGINEER_ROWS,
    STRUCTURE_LABELS,
    SYSTEM_LABELS,
    StructureInputs,
    cost_approach_from_fields,
    mapping_for,
)
from basis.evidence.result import (
    NOT_PRODUCED,
    AllocationOut,
    ScheduleRow,
    allocation_out,
    compute_result,
    concealed_label,
)

D = Decimal


def _id(n: int) -> str:
    return str(uuid.UUID(int=n))


def intake(**overrides: object) -> object:
    values: dict[str, object] = {
        "purchase_price": "100000.00",
        "acquisition_costs": [],
        "land_method": "appraisal_site_value",
        "appraisal_site_value": "20000.00",
        "property_use": "nonresidential",
        "transient_lodging": True,
        "placed_in_service_date": date(2025, 5, 1),
    }
    values.update(overrides)
    return with_location(
        StudyIntakeIn.model_validate(values), appraisal_state="CA", appraisal_zip="90001"
    )


ROWS = [
    ScheduleRow(_id(3), "Toilet", "y39", None),
    ScheduleRow(_id(1), "Carpet", "y5", D("1368.52")),
    ScheduleRow(_id(2), "Kitchen cabinets", "y5", D("15108.48")),
    ScheduleRow(_id(4), "Site", "land", D("5.00")),
]


def test_without_an_intake_the_schedule_alone_is_returned() -> None:
    result = compute_result(ROWS, None, 2)
    assert (result.revision, result.intake, result.missing) == (2, False, ["intake"])
    assert result.worksheet is None and result.allocation is None
    assert result.structure is None and result.reconciliation is None
    schedule = result.schedule
    assert (schedule.assets, schedule.priced, schedule.rcn_total) == (3, 2, D("16477.00"))
    assert (schedule.concealed, schedule.concealed_rcn, schedule.rcnld_total) == (0, D(0), None)
    assert [(b.bucket.value, b.assets, b.rcn) for b in schedule.by_bucket] == [
        ("y5", 2, D("16477.00")),
        ("y39", 1, D("0.00")),
    ]
    assert schedule.unpriced == [uuid.UUID(int=3)]
    assert schedule.land == [uuid.UUID(int=4)]
    # Every concealed system is "not produced" until a structure row prices it.
    assert [n.system for n in result.not_produced] == [s for s, _ in NOT_PRODUCED]
    assert [n.system for n in result.not_produced][:2] == ["Foundations", "Structural Framing"]
    assert all("STRUCTURE_MODEL.md" in n.reason for n in result.not_produced)


def test_without_structure_inputs_the_allocation_is_withheld() -> None:
    """EVAL_REPORT 78: with no appraisal fields nothing reconciles the schedule against the
    price; the uniform factor once scaled the priced rows up to the basis (the ATG's rejected
    step-up), and an unpriced row's share went to the others. Both are blockers now."""
    result = compute_result(ROWS, intake(), 1)  # type: ignore[arg-type]
    assert result.missing == ["blockers", "allocation"]
    ws = result.worksheet
    assert ws is not None
    assert (ws.building_basis, ws.capitalized_costs, ws.total_basis) == (
        D("80000.00"),
        D("0.00"),
        D("80000.00"),
    )
    assert (ws.state, ws.zip, ws.location_source) == ("CA", "90001", "appraisal")
    assert ws.recovery_period == D("39")
    assert result.allocation is None
    assert [(b.code, b.assets) for b in result.blockers] == [
        ("unpriced", 1),
        ("appraisal_fields", 0),
    ]
    assert all(b.message and b.fix for b in result.blockers)


def grouped(rows: list[ScheduleRow], basis: Decimal) -> AllocationOut:
    """The allocation's grouping on its own: :func:`allocate` and :func:`allocation_out`, the
    code ``compute_result`` runs once nothing blocks it (whose gates are tested above)."""
    rcn_rows = [
        RcnRow(
            id=r.asset_id,
            bucket=r.bucket,
            rcn=r.rcn,  # type: ignore[arg-type]
            label=r.category,
            systems=REPORT_ROW_SYSTEMS.get(r.category) or ((mapping_for(r.label).system, ONE),),
        )
        for r in rows
    ]
    return allocation_out(
        allocate(rcn_rows, basis, D("0.00"), depreciated=False), {r.asset_id: r for r in rows}
    )


def test_report_row_categories_regroup_by_system() -> None:
    rows = [
        ScheduleRow(_id(1), "Interior Half Walls", "y5", D("100.00")),
        ScheduleRow(_id(2), "Painting", "y39", D("300.00")),
    ]
    a = grouped(rows, D("400.00"))
    assert a.by_system is not None
    assert [(s.system, s.allocated) for s in a.by_system] == [
        ("Interior Framing / Partitions", D("80.00")),
        ("Painting", D("320.00")),
    ]


def test_labels_regroup_by_system_when_the_category_is_not_a_report_row() -> None:
    rows = [
        ScheduleRow(_id(1), "Hardwood flooring", "y5", D("100.00"), label="hardwood_floor"),
        ScheduleRow(_id(2), "Window (structural component)", "y39", D("300.00"), label="window"),
        ScheduleRow(_id(3), "Light fixtures", "y39", D("100.00"), label="light_fixture"),
    ]
    a = grouped(rows, D("500.00"))
    assert a.by_system is not None
    assert [(s.system, s.allocated) for s in a.by_system] == [
        ("Electrical", D("100.00")),
        ("Miscellaneous Building Components", D("100.00")),
        ("Windows", D("300.00")),
    ]


def test_an_intake_with_nothing_priced_has_no_allocation() -> None:
    rows = [ScheduleRow(_id(3), "Toilet", "y39", None), ScheduleRow(_id(4), "Site", "land", D("5"))]
    result = compute_result(rows, intake(), 1)  # type: ignore[arg-type]
    assert result.missing == ["blockers", "allocation"]
    assert result.worksheet is not None and result.allocation is None
    assert [b.code for b in result.blockers] == ["unpriced", "appraisal_fields"]
    empty = compute_result([], intake(), 1)  # type: ignore[arg-type]
    assert empty.missing == ["blockers", "allocation"]
    assert compute_result([], None, 1).missing == ["intake"]


def test_rows_are_taken_in_asset_id_order_and_the_document_is_json() -> None:
    result = compute_result(ROWS, intake(), 3)  # type: ignore[arg-type]
    document = result.model_dump(mode="json")
    assert document["revision"] == 3
    assert document["worksheet"]["total_basis"] == "80000.00"
    assert document["allocation"] is None
    assert [b["code"] for b in document["blockers"]] == ["unpriced", "appraisal_fields"]
    assert document["schedule"]["unpriced"] == [_id(3)]
    assert document["structure"] is None and document["depreciation"] is None


# ----------------------------------------------------------------- the structure model's path


@dataclass(frozen=True)
class Row:
    section: str
    field: str
    value_num: Decimal | None = None
    value_text: str | None = None
    value_date: date | None = None
    source: str = "llm"


def fields(**values: object) -> list[Row]:
    out = []
    for key, value in values.items():
        section, name = key.split("__")
        if isinstance(value, str):
            out.append(Row(section, name, value_text=value))
        else:
            out.append(Row(section, name, value_num=D(str(value))))
    return out


# A 2,000 SF two-story Q4 house: cost new 300,000 as of 2024, physical depreciation 25 %.
HOUSE = fields(
    improvements__gla_sqft=2000,
    improvements__stories=2,
    improvements__quality="Q4",
    improvements__bedrooms=3,
    improvements__total_rooms=7,
    improvements__effective_age_years=15,
    cost_approach__total_cost_new=300000,
    cost_approach__dwelling_cost=300000,
    cost_approach__depreciation_physical=75000,
    cost_approach__depreciated_cost_improvements=225000,
    cost_approach__remaining_economic_life=45,
    cost_approach__site_value=20000,
    cost_approach__effective_date_of_cost_data="2024-03",
)


def structure(
    field_rows: list[Row] = HOUSE, rcn_source: str = "appraisal_total"
) -> StructureInputs:
    m = measurements_from_fields(field_rows)
    return StructureInputs(
        measurements=None if m.is_empty else m,
        cost_approach=cost_approach_from_fields(field_rows),
        cost_year=2024,
        year_source="placed in service 2024-12-03 (intake)",
        rcn_source=rcn_source,  # type: ignore[arg-type]
    )


def concealed(n: int, label: str, bucket: str, rcn: Decimal | None) -> ScheduleRow:
    return ScheduleRow(_id(n), ENGINEER_ROWS[label], bucket, rcn, label=label, concealed=True)


# Itemized rows 20,000 (carpet, a window) plus concealed rows 200,000: RCN 220,000, RCNLD
# at 25 % = 165,000, against a basis of 160,000 (price 180,000 less the site value).
PRODUCED = [
    ScheduleRow(_id(1), "Carpet", "y5", D("5000.00"), label="carpet"),
    ScheduleRow(_id(2), "Window", "y39", D("15000.00"), label="window"),
    ScheduleRow(_id(3), "Toilet", "y39", None, label="toilet"),
    concealed(11, "foundation", "y39", D("60000.00")),
    concealed(12, "structural_framing", "y39", D("80000.00")),
    concealed(13, "interior_partition", "y39", D("40000.00")),
    concealed(14, "painting", "y39", D("20000.00")),
    concealed(15, "data_cabling", "y5", None),  # unpriced: no cost row
]


def test_the_structure_path_depreciates_and_reconciles_and_withholds_on_its_blockers() -> None:
    """The model's numbers are all shown, and the allocation is withheld: the fixture's 2024
    has no loaded index (the released book stops at 2020), its location factor is the
    setting's (no ZIP or state on the structure inputs), and two rows are unpriced (EVAL_REPORT
    78; before it, all three still allocated, the unpriced rows' share going to the others)."""
    result = compute_result(
        PRODUCED,
        intake(purchase_price="180000.00"),
        1,
        structure(),  # type: ignore[arg-type]
    )
    assert result.missing == ["cost_index", "blockers", "allocation"]
    assert [(b.code, b.assets) for b in result.blockers] == [
        ("unpriced", 2),
        ("input:location", 0),
        ("cost_index", 0),
    ]
    assert result.cost_index is not None and result.cost_index.status == "missing"
    assert result.cost_index.factor == D("1.0000")
    assert "2024 RSMeans Historical Cost Index" in (result.cost_index.needed or "")
    assert "POST /v1/cost-indices" in (result.cost_index.how or "")
    schedule = result.schedule
    assert (schedule.priced, schedule.concealed, schedule.concealed_rcn) == (6, 5, D("200000.00"))
    assert (schedule.rcn_total, schedule.rcnld_total) == (D("220000.00"), D("165000.00"))
    assert result.depreciation is not None
    assert (result.depreciation.rate, result.depreciation.method) == (
        D("0.250000"),
        "appraisal_physical_depreciation",
    )
    r = result.reconciliation
    assert r is not None
    assert (r.status, r.building_basis, r.rcnld_total, r.gap) == (
        "reconciled",
        D("160000.00"),
        D("165000.00"),
        D("-5000.00"),
    )
    assert r.gap_share == D("-0.031250") and r.tolerance == D("0.10")
    assert r.implied_uniform_factor == D("0.727272727")  # T / ΣRCN, the old method's number
    assert [c.component for c in r.by_component][:2] == ["site_work", "foundation"]
    foundation = r.by_component[1]
    assert foundation.concealed == D("60000.00")  # the schedule's foundation row
    assert r.checks[0].severity == "ok"
    assert result.allocation is None
    # The structure block: the appraisal's RCN split into the model's components, the
    # itemized rows carved out, the concealed rows listed; what no row priced is not produced.
    st = result.structure
    assert st is not None and st.building_rcn is not None
    assert (st.building_rcn.source, st.building_rcn.amount) == (
        "appraisal_cost_approach",
        D("300000"),
    )
    assert st.building_rcn.model.name == "Average 2 Story"
    components = {c.component: c for c in st.components}
    assert components["exterior_walls"].itemized == D("15000.00")  # the window
    assert (
        components["interiors"].itemized_assets == 1
        and components["mechanical"].unpriced_assets == 1
    )
    assert sum((c.top_down for c in st.components), D(0)) == D("300000.00")
    assert [c.label for c in st.concealed] == [
        "foundation",
        "structural_framing",
        "interior_partition",
        "painting",
        "data_cabling",
    ]
    assert st.concealed[4].rcn is None and st.concealed[4].system == "Communication / Data"
    produced = {"foundation", "structural_framing", "interior_partition", "painting"}
    # The Specialties residual is a remainder, not a system the engineer reports: it is
    # never listed as not produced (SYSTEM_LABELS).
    assert [n.system for n in result.not_produced] == [
        ENGINEER_ROWS[label] for label in SYSTEM_LABELS if label not in produced
    ]
    assert "Specialties residual" not in {n.system for n in result.not_produced}
    assert set(STRUCTURE_LABELS) - set(SYSTEM_LABELS) == {"specialties_residual"}


def test_with_every_input_the_structure_path_allocates_on_rcnld() -> None:
    """Every row priced, the location from a ZIP and the cost year's index loaded: nothing
    blocks, so the allocation is produced on RCNLD and ties to the building basis."""
    priced = [r for r in PRODUCED if r.rcn is not None]
    loaded = {2024: YearIndex(2024, D("294.8"), date(2024, 7, 1), "test value")}
    inputs = replace(
        structure(),
        location_source="zip 90001 (intake): Los Angeles",
        year=year_factor_index(2024, loaded),
    )
    result = compute_result(priced, intake(purchase_price="180000.00"), 1, inputs)  # type: ignore[arg-type]
    assert result.blockers == [] and "blockers" not in result.missing
    r = result.reconciliation
    assert r is not None and r.status == "reconciled"
    a = result.allocation
    assert a is not None and a.depreciated is True
    assert a.rows_sum == a.total == result.worksheet.building_basis  # type: ignore[union-attr]
    assert {x.label for x in a.assets} == {r.label for r in priced}
    assert a.by_system is not None


def test_the_default_source_is_the_square_foot_model() -> None:
    result = compute_result(
        PRODUCED,
        intake(purchase_price="180000.00"),  # type: ignore[arg-type]
        1,
        structure(rcn_source="square_foot"),
    )
    st = result.structure
    assert st is not None and st.building_rcn is not None
    assert st.building_rcn.source == "square_foot_model"
    # Average 2 Story at 2,000 SF: 118.75/SF x 2,000 x 1.0000 (2024: no published index in
    # the 2020 book, the Jan 2020 one applies) = 237,500; the appraisal's own depreciated
    # cost still checks the price independently.
    assert st.building_rcn.amount == D("237500.00")
    assert result.reconciliation is not None
    assert result.reconciliation.appraisal_check.code == "appraisal_reconciles"


def test_outside_tolerance_the_allocation_is_withheld_with_the_diagnosis() -> None:
    # The same schedule against a basis of 100,000: RCNLD 165,000 is 65 % over.
    result = compute_result(
        PRODUCED,
        intake(purchase_price="120000.00"),
        1,
        structure(),  # type: ignore[arg-type]
    )
    assert result.missing == ["cost_index", "blockers", "allocation"]
    assert result.allocation is None
    r = result.reconciliation
    assert r is not None and r.status == "unreconciled"
    assert (r.gap, r.gap_share) == (D("-65000.00"), D("-0.650000"))
    assert r.checks[0].severity == "error" and "withheld" in r.checks[0].message
    assert [c.code for c in r.causes] == [
        "land_fmv",
        "assets_missing",
        "cost_factors",
        "depreciation_factors",
        "indirect_costs",
    ]
    assert "2 assets are unpriced" in r.causes[1].finding
    # The schedule is complete all the same: RCN and RCNLD per bucket.
    assert result.schedule.rcnld_total == D("165000.00")
    assert [(b.bucket.value, b.rcnld) for b in result.schedule.by_bucket] == [
        ("y5", D("3750.00")),
        ("y39", D("161250.00")),
    ]


def test_without_a_building_rcn_or_a_rate_the_result_says_what_is_missing() -> None:
    bare = structure(fields(improvements__stories=2))  # no GLA, no cost approach
    result = compute_result(PRODUCED, intake(purchase_price="180000.00"), 1, bare)  # type: ignore[arg-type]
    assert result.missing == [
        "cost_index",
        "building_rcn",
        "depreciation",
        "blockers",
        "allocation",
    ]
    assert result.reconciliation is not None and result.reconciliation.status == "not_checked"
    assert result.structure is not None and result.structure.building_rcn is None
    assert result.structure.components == [] and len(result.structure.concealed) == 5
    assert result.schedule.rcnld_total is None
    # No intake: the structure block still describes the model; nothing is reconciled.
    alone = compute_result(PRODUCED, None, 1, structure())
    assert alone.missing == ["cost_index", "intake"] and alone.structure is not None
    assert alone.reconciliation is None and alone.depreciation is not None


def test_concealed_label_reads_the_takeoff_inputs() -> None:
    assert concealed_label({"concealed": True, "label": "painting"}) == ("painting", True)
    assert concealed_label({"label": "carpet"}) == ("carpet", False)
    assert concealed_label(None) == (None, False)
    assert concealed_label({"concealed": "yes", "label": 3}) == (None, False)


# ------------------------------------------------- blockers: nothing assumed reaches dollars


def without(*names: str) -> list[Row]:
    """HOUSE without the named ``section__field`` rows."""
    drop = {tuple(n.split("__")) for n in names}
    return [r for r in HOUSE if (r.section, r.field) not in drop]


def clean(field_rows: list[Row] = HOUSE, **kw: object) -> StructureInputs:
    loaded = {
        2024: YearIndex(2024, D("294.8"), date(2024, 7, 1), "test value"),
    }
    return replace(
        structure(field_rows, **kw),  # type: ignore[arg-type]
        location_source="zip 90001 (intake): Los Angeles",
        year=year_factor_index(2024, loaded),
    )


def blocker_codes(
    field_rows: list[Row], rows: list[ScheduleRow] | None = None, **kw: object
) -> list[str]:
    priced = rows if rows is not None else [r for r in PRODUCED if r.rcn is not None]
    result = compute_result(priced, intake(purchase_price="180000.00"), 1, clean(field_rows, **kw))  # type: ignore[arg-type]
    if result.blockers:
        assert result.allocation is None and "blockers" in result.missing
    return [b.code for b in result.blockers]


def test_a_complete_house_has_no_blockers() -> None:
    assert blocker_codes(HOUSE) == []


@pytest.mark.parametrize(
    ("dropped", "code"),
    [
        # The square-foot model's class without a quality rating, its type without stories.
        (("improvements__quality",), "input:model_class"),
        (("improvements__stories",), "input:stories"),
        # The appraiser's cost new cannot be brought to the cost year without its date.
        (("cost_approach__effective_date_of_cost_data",), "input:cost_data_date"),
    ],
)
def test_an_assumed_building_input_blocks_the_allocation(
    dropped: tuple[str, ...], code: str
) -> None:
    assert code in blocker_codes(without(*dropped))


def test_a_house_with_a_basement_but_no_finish_blocks_on_the_square_foot_model() -> None:
    rows = [*HOUSE, *fields(improvements__basement_area_sqft=800)]
    assert "input:basement_finish" in blocker_codes(rows, rcn_source="square_foot")


def test_without_physical_depreciation_the_age_life_ratio_applies_not_the_total() -> None:
    """EVAL_REPORT 78: the total depreciation (physical, functional and external) stood in for
    the physical depreciation when it was not printed."""
    rows = [
        *without("cost_approach__depreciation_physical"),
        *fields(cost_approach__depreciation_total=120000),
    ]
    result = compute_result(
        [r for r in PRODUCED if r.rcn is not None],
        intake(purchase_price="180000.00"),
        1,
        clean(rows),  # type: ignore[arg-type]
    )
    assert result.depreciation is not None
    assert result.depreciation.method == "age_life"
    assert result.depreciation.rate == D("0.250000")  # 15 / (15 + 45), not 120,000 / 300,000


def test_without_any_depreciation_input_the_allocation_is_withheld() -> None:
    rows = without(
        "cost_approach__depreciation_physical",
        "improvements__effective_age_years",
        "cost_approach__remaining_economic_life",
    )
    assert "depreciation" in blocker_codes(rows)


def test_a_location_or_cost_year_from_the_settings_blocks() -> None:
    priced = [r for r in PRODUCED if r.rcn is not None]
    settings_location = replace(
        clean(),
        location_source="COST_LOCATION_FACTOR setting: no location factor for state 'PR' (intake)",
    )
    settings_year = replace(
        clean(),
        year_source="COST_YEAR setting: placed in service 1950-01-01 (intake) is before the "
        "1970-2020 Historical Cost Index",
    )
    for inputs, code in ((settings_location, "input:location"), (settings_year, "input:cost_year")):
        result = compute_result(priced, intake(purchase_price="180000.00"), 1, inputs)  # type: ignore[arg-type]
        assert code in [b.code for b in result.blockers] and result.allocation is None


def test_an_unclassified_row_blocks_even_when_priced() -> None:
    rows = [
        *[r for r in PRODUCED if r.rcn is not None],
        ScheduleRow(_id(9), "Unclassified: widget", "y39", D("100.00"), label="widget"),
    ]
    codes = blocker_codes(HOUSE, rows)
    assert codes == ["unclassified"]


def test_rows_missing_an_input_are_counted_per_input() -> None:
    rows = [
        *[r for r in PRODUCED if r.rcn is not None],
        ScheduleRow(_id(7), "Carpet", "y5", None, label="carpet", review_reason="input:gla"),
        ScheduleRow(_id(8), "Tile", "y5", None, label="tile", review_reason="rule:x; input:gla"),
        ScheduleRow(
            _id(9),
            "Driveway",
            "y15",
            None,
            label="driveway",
            review_reason="cross:COST_NEEDS_QUANTITY",
        ),
    ]
    result = compute_result(rows, intake(purchase_price="180000.00"), 1, clean())  # type: ignore[arg-type]
    assert [(b.code, b.assets) for b in result.blockers] == [("input:gla", 2), ("unpriced", 1)]
    gla = result.blockers[0]
    assert "gross living area" in gla.message and "improvements.gla_sqft" in gla.fix
