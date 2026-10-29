"""Basis and land allocation (``basis.domain.basis``): hand-computed cases for every formula
and check. Fabricated values only.

* worksheet: ``price + CC − land``; land from the chosen source only; the assessor ratio as a
  check; the hierarchy, share, date and site-value checks; the 80 % test; refused costs.
* allocate: one factor, rows that sum to T to the cent (largest-remainder rule), bucket
  totals and percentages, the pro-rata block that sums to B and CC, the regrouping by
  building system with a weighted split, the uniform-factor check, every refusal.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from basis.domain.basis import (
    HALF_WALL_SPLIT,
    NOT_CAPITALIZED,
    REPORT_ROW_SYSTEMS,
    AcquisitionCost,
    BasisError,
    BasisInputs,
    CostCategory,
    LandInputs,
    LandMethod,
    PropertyUse,
    RcnRow,
    Severity,
    UseInputs,
    allocate,
    capitalized_costs,
    money,
    worksheet,
)

D = Decimal


def inputs(**overrides: object) -> BasisInputs:
    values: dict[str, object] = {
        "purchase_price": D("500000.00"),
        "acquisition_costs": (
            AcquisitionCost(CostCategory.TITLE_INSURANCE_OWNER, D("1000.00")),
            AcquisitionCost(CostCategory.RECORDING_FEES, D("200.00")),
            AcquisitionCost(CostCategory.LEGAL_FEES, D("800.00"), "deed and contract"),
        ),
        "land_method": LandMethod.APPRAISAL_SITE_VALUE,
        "land": LandInputs(
            appraisal_site_value=D("80000.00"), appraisal_effective_date=date(2025, 3, 1)
        ),
        "use": UseInputs(PropertyUse.RESIDENTIAL_RENTAL, dwelling_unit_income_share=D(1)),
        "placed_in_service_date": date(2025, 5, 1),
        "acquisition_date": date(2025, 4, 15),
    }
    values.update(overrides)
    return BasisInputs(**values)  # type: ignore[arg-type]


def check(ws_or_alloc: object, code: str) -> tuple[Severity, str]:
    found = [c for c in ws_or_alloc.checks if c.code == code]  # type: ignore[attr-defined]
    assert len(found) == 1, code
    return found[0].severity, found[0].message


# --------------------------------------------------------------------------- worksheet


def test_worksheet_price_plus_costs_minus_land() -> None:
    ws = worksheet(inputs())
    assert ws.capitalized_costs == D("2000.00")
    assert ws.land == D("80000.00")
    assert ws.building_basis == D("420000.00")
    assert ws.total_basis == D("422000.00")
    assert ws.land_source.startswith("the appraisal's opinion of site value as of 2025-03-01")
    assert ws.recovery_period == D("27.5")
    assert ws.property_use is PropertyUse.RESIDENTIAL_RENTAL
    assert ws.errors == ()
    assert check(ws, "land_share_of_price") == (
        Severity.OK,
        "land is 16.0 % of the price (expected 5–60 %)",
    )
    assert check(ws, "land_hierarchy")[0] is Severity.OK
    assert check(ws, "land_vs_appraisal_site_value") == (Severity.OK, "land is the site value")
    assert check(ws, "land_vs_assessor_ratio")[0] is Severity.NOT_CHECKED
    # 2025-03-01 to the acquisition 2025-04-15: 45 days.
    assert check(ws, "land_value_date") == (
        Severity.OK,
        "the land value is dated 2025-03-01, 45 days from 2025-04-15 (window 365 days)",
    )
    assert check(ws, "recovery_period")[0] is Severity.OK
    assert check(ws, "placed_in_service_date")[0] is Severity.OK
    assert all(isinstance(v, Decimal) for v in (ws.land, ws.building_basis, ws.total_basis))


def test_worksheet_keeps_the_accepted_costs_with_descriptions() -> None:
    ws = worksheet(inputs())
    assert [c.category for c in ws.acquisition_costs] == [
        CostCategory.TITLE_INSURANCE_OWNER,
        CostCategory.RECORDING_FEES,
        CostCategory.LEGAL_FEES,
    ]
    assert ws.acquisition_costs[2].description == "deed and contract"


def test_no_acquisition_costs_means_total_equals_building_basis() -> None:
    ws = worksheet(inputs(acquisition_costs=()))
    assert ws.capitalized_costs == D("0.00")
    assert ws.total_basis == ws.building_basis == D("420000.00")


@pytest.mark.parametrize("category", sorted(NOT_CAPITALIZED, key=str))
def test_non_capitalized_cost_categories_are_refused_with_the_reason(
    category: CostCategory,
) -> None:
    with pytest.raises(
        BasisError, match=f"{category.value}.*not capitalized.*{NOT_CAPITALIZED[category][:20]}"
    ):
        capitalized_costs([AcquisitionCost(category, D("10.00"))])


def test_costs_must_be_whole_non_negative_cents() -> None:
    with pytest.raises(BasisError, match="cannot be negative"):
        capitalized_costs([AcquisitionCost(CostCategory.SURVEY, D("-1.00"))])
    with pytest.raises(BasisError, match="whole cents"):
        capitalized_costs([AcquisitionCost(CostCategory.SURVEY, D("1.005"))])
    assert capitalized_costs([AcquisitionCost(CostCategory.SURVEY, D("0.00"))])[0].amount == 0


def test_flat_percentage_is_price_times_percent_and_flagged() -> None:
    ws = worksheet(
        inputs(
            purchase_price=D("100000.00"),
            acquisition_costs=(),
            land_method=LandMethod.FLAT_PERCENTAGE,
            land=LandInputs(
                flat_percentage=D("12"),
                flat_source="the firm's market percentage",
                appraisal_site_value=D("12500.00"),
                assessor_land_value=D("20000"),
                assessor_improvement_value=D("80000"),
            ),
        )
    )
    assert ws.land == D("12000.00")
    assert ws.building_basis == D("88000.00") == ws.total_basis
    assert ws.land_source == "12 % of the purchase price (the firm's market percentage)"
    severity, message = check(ws, "land_hierarchy")
    assert severity is Severity.WARNING and "appraisal_site_value is available" in message
    assert check(ws, "land_flat_percentage")[0] is Severity.WARNING
    # (12000 − 12500) / 12500 = −4.0 %, inside the 5 % tolerance.
    assert check(ws, "land_vs_appraisal_site_value") == (
        Severity.OK,
        "land 12000.00 differs from the appraisal's site value 12500.00 by -4.0 % "
        "(tolerance 5.0 %)",
    )
    # The assessor's ratio 20 % of the price implies 20000; (12000 − 20000) / 20000 = −40 %.
    severity, message = check(ws, "land_vs_assessor_ratio")
    assert severity is Severity.WARNING
    assert message == (
        "the assessor's ratio 20.0 % of the price implies land 20000.00; the chosen land "
        "differs by -40.0 %"
    )
    assert check(ws, "land_value_date")[0] is Severity.NOT_CHECKED


def test_land_tolerance_is_a_parameter() -> None:
    ws = worksheet(
        inputs(
            purchase_price=D("100000.00"),
            land_method=LandMethod.FLAT_PERCENTAGE,
            land=LandInputs(
                flat_percentage=D("12"), flat_source="firm", appraisal_site_value=D("12500.00")
            ),
        ),
        land_tolerance=D("0.03"),
    )
    assert check(ws, "land_vs_appraisal_site_value")[0] is Severity.WARNING


def test_flat_percentage_rounds_to_the_cent_half_up() -> None:
    ws = worksheet(
        inputs(
            purchase_price=D("100001.00"),
            acquisition_costs=(),
            land_method=LandMethod.FLAT_PERCENTAGE,
            land=LandInputs(flat_percentage=D("12.345"), flat_source="x"),
        )
    )
    # 100001 × 0.12345 = 12345.12345 → 12345.12
    assert ws.land == D("12345.12")
    assert ws.building_basis == D("87655.88")


def test_land_appraisal_is_second_in_the_hierarchy() -> None:
    ws = worksheet(
        inputs(
            land_method=LandMethod.LAND_APPRAISAL,
            land=LandInputs(
                land_appraisal_value=D("90000.00"), land_appraisal_date=date(2023, 1, 1)
            ),
        )
    )
    assert ws.land == D("90000.00")
    assert check(ws, "land_hierarchy")[0] is Severity.OK
    assert check(ws, "land_vs_appraisal_site_value")[0] is Severity.NOT_CHECKED
    severity, message = check(ws, "land_value_date")
    assert severity is Severity.WARNING and "835 days" in message
    # With a site value also present the site value is preferred.
    ws = worksheet(
        inputs(
            land_method=LandMethod.LAND_APPRAISAL,
            land=LandInputs(land_appraisal_value=D("90000.00"), appraisal_site_value=D("80000.00")),
        )
    )
    assert check(ws, "land_hierarchy")[0] is Severity.WARNING
    assert check(ws, "land_vs_appraisal_site_value")[0] is Severity.WARNING  # +12.5 %


def test_land_share_outside_the_band_warns() -> None:
    low = worksheet(inputs(land=LandInputs(appraisal_site_value=D("20000.00"))))  # 4 %
    assert check(low, "land_share_of_price")[0] is Severity.WARNING
    high = worksheet(inputs(land=LandInputs(appraisal_site_value=D("350000.00"))))  # 70 %
    assert check(high, "land_share_of_price")[0] is Severity.WARNING


def test_land_value_date_uses_placed_in_service_without_an_acquisition_date() -> None:
    ws = worksheet(inputs(acquisition_date=None))
    assert "61 days from 2025-05-01" in check(ws, "land_value_date")[1]
    ws = worksheet(inputs(land=LandInputs(appraisal_site_value=D("80000.00"))))
    assert check(ws, "land_value_date")[0] is Severity.NOT_CHECKED


def test_land_is_never_residual_and_must_be_below_the_price() -> None:
    assert not hasattr(LandMethod, "RESIDUAL") and not hasattr(LandMethod, "ASSESSOR_RATIO")
    with pytest.raises(BasisError, match="not below the purchase price"):
        worksheet(inputs(land=LandInputs(appraisal_site_value=D("500000.00"))))


@pytest.mark.parametrize(
    ("method", "land", "message"),
    [
        (LandMethod.APPRAISAL_SITE_VALUE, LandInputs(), "needs appraisal_site_value"),
        (LandMethod.LAND_APPRAISAL, LandInputs(), "needs land_appraisal_value"),
        (LandMethod.FLAT_PERCENTAGE, LandInputs(flat_source="x"), "needs flat_percentage"),
        (LandMethod.FLAT_PERCENTAGE, LandInputs(flat_percentage=D("12")), "needs flat_source"),
        (
            LandMethod.FLAT_PERCENTAGE,
            LandInputs(flat_percentage=D("12"), flat_source="  "),
            "needs flat_source",
        ),
        (
            LandMethod.FLAT_PERCENTAGE,
            LandInputs(flat_percentage=D("100"), flat_source="x"),
            "between 0 and 100",
        ),
        (
            LandMethod.APPRAISAL_SITE_VALUE,
            LandInputs(appraisal_site_value=D("0.00")),
            "must be positive",
        ),
    ],
)
def test_each_land_method_needs_its_input(
    method: LandMethod, land: LandInputs, message: str
) -> None:
    with pytest.raises(BasisError, match=message):
        worksheet(inputs(land_method=method, land=land))


def test_assessor_values_cannot_both_be_zero() -> None:
    with pytest.raises(BasisError, match="cannot both be zero"):
        worksheet(
            inputs(
                land=LandInputs(
                    appraisal_site_value=D("80000.00"),
                    assessor_land_value=D("0"),
                    assessor_improvement_value=D("0"),
                )
            )
        )


def test_price_must_be_positive_whole_cents() -> None:
    with pytest.raises(BasisError, match="must be positive"):
        worksheet(inputs(purchase_price=D("0")))
    with pytest.raises(BasisError, match="whole cents"):
        worksheet(inputs(purchase_price=D("500000.001")))
    with pytest.raises(BasisError, match="must be a Decimal"):
        worksheet(inputs(purchase_price=500000.0))


# --------------------------------------------------------------------------- the use


def test_residential_rental_needs_the_80_percent_test() -> None:
    with pytest.raises(BasisError, match="needs dwelling_unit_income_share"):
        worksheet(inputs(use=UseInputs(PropertyUse.RESIDENTIAL_RENTAL)))
    with pytest.raises(BasisError, match="fails the 80 % test: 70.0 %"):
        worksheet(inputs(use=UseInputs(PropertyUse.RESIDENTIAL_RENTAL, D("0.7"))))
    with pytest.raises(BasisError, match="cannot be transient lodging"):
        worksheet(
            inputs(use=UseInputs(PropertyUse.RESIDENTIAL_RENTAL, D(1), transient_lodging=True))
        )
    ws = worksheet(inputs(use=UseInputs(PropertyUse.RESIDENTIAL_RENTAL, D("0.8"))))
    assert ws.recovery_period == D("27.5")


def test_nonresidential_is_39_years_and_warns_when_it_would_qualify() -> None:
    ws = worksheet(inputs(use=UseInputs(PropertyUse.NONRESIDENTIAL, transient_lodging=True)))
    assert ws.recovery_period == D("39")
    assert check(ws, "recovery_period") == (
        Severity.OK,
        "nonresidential real property, 39 years (transient lodging; §168(e)(2)(B))",
    )
    ws = worksheet(inputs(use=UseInputs(PropertyUse.NONRESIDENTIAL)))
    assert "no dwelling-unit income share given" in check(ws, "recovery_period")[1]
    ws = worksheet(inputs(use=UseInputs(PropertyUse.NONRESIDENTIAL, D("0.9"))))
    severity, message = check(ws, "recovery_period")
    assert severity is Severity.WARNING and "that is residential rental property" in message


def test_income_share_must_be_a_fraction() -> None:
    with pytest.raises(BasisError, match="between 0 and 1"):
        worksheet(inputs(use=UseInputs(PropertyUse.NONRESIDENTIAL, D("80"))))


def test_placed_in_service_before_acquisition_warns() -> None:
    ws = worksheet(inputs(placed_in_service_date=date(2025, 4, 1)))
    severity, message = check(ws, "placed_in_service_date")
    assert severity is Severity.WARNING and "before the acquisition" in message


# --------------------------------------------------------------------------- allocate


def rows(*specs: tuple[str, str, str]) -> list[RcnRow]:
    return [RcnRow(id=i, bucket=b, rcn=D(r), label=i) for i, b, r in specs]


def test_allocate_one_factor_exact_case() -> None:
    schedule = rows(("a", "y5", "100.00"), ("b", "y5", "200.00"), ("c", "y39", "700.00"))
    a = allocate(schedule, D("450.00"), D("50.00"))
    assert (a.total, a.rcn_total, a.factor) == (D("500.00"), D("1000.00"), D("0.500000000"))
    assert [(r.id, r.allocated, r.rounding_cents) for r in a.rows] == [
        ("a", D("50.00"), 0),
        ("b", D("100.00"), 0),
        ("c", D("350.00"), 0),
    ]
    assert a.rows_sum == D("500.00") and a.residual_cents == 0
    y5, y39 = a.by_bucket
    assert (y5.bucket, y5.rows, y5.rcn, y5.allocated) == ("y5", 2, D("300.00"), D("150.00"))
    assert (y5.percent, y5.share) == (D("30.0"), D("0.300000"))
    assert (y5.building, y5.closing_costs) == (D("135.00"), D("15.00"))
    assert (y39.allocated, y39.percent, y39.building, y39.closing_costs) == (
        D("350.00"),
        D("70.0"),
        D("315.00"),
        D("35.00"),
    )
    assert a.by_system is None and a.by_system_reason is not None
    assert "3 of 3 rows are not mapped" in a.by_system_reason
    assert check(a, "uniform_factor")[0] is Severity.WARNING


def test_rows_round_half_up_and_the_residual_cents_go_to_the_largest_remainders() -> None:
    schedule = rows(("a", "y5", "1.00"), ("b", "y5", "1.00"), ("c", "y5", "1.00"))
    # f = 1/3: 0.3333… each, half up 0.33 × 3 = 0.99; the missing cent goes to the first row
    # (equal remainders, schedule order).
    up = allocate(schedule, D("1.00"), D("0.00"))
    assert [(r.allocated, r.rounding_cents) for r in up.rows] == [
        (D("0.34"), 1),
        (D("0.33"), 0),
        (D("0.33"), 0),
    ]
    assert up.rows_sum == D("1.00") and up.residual_cents == 1
    # f = 2/3: 0.6667… each, half up 0.67 × 3 = 2.01; the last row loses a cent.
    down = allocate(schedule, D("2.00"), D("0.00"))
    assert [(r.allocated, r.rounding_cents) for r in down.rows] == [
        (D("0.67"), 0),
        (D("0.67"), 0),
        (D("0.66"), -1),
    ]
    assert down.rows_sum == D("2.00")


def test_largest_remainder_prefers_the_largest_fraction_not_the_first_row() -> None:
    # f = 1.005: 2.010, 3.015, 5.025. Half up gives 2.01, 3.02, 5.03 = 10.06, a cent over.
    # Floors 2.01, 3.01, 5.02 = 10.04 leave one cent; the remainders are 0, .005, .005, so
    # it goes to b (the tie with c is broken by schedule order) and c ends a cent below
    # its half-up value.
    schedule = rows(("a", "y5", "2.00"), ("b", "y5", "3.00"), ("c", "y5", "5.00"))
    a = allocate(schedule, D("10.05"), D("0.00"))
    assert [r.allocated for r in a.rows] == [D("2.01"), D("3.02"), D("5.02")]
    assert [r.rounding_cents for r in a.rows] == [0, 0, -1]
    assert a.rows_sum == D("10.05")


def test_pro_rata_block_sums_to_b_and_cc_per_column_and_to_the_bucket_per_row() -> None:
    schedule = rows(("a", "y5", "1.00"), ("b", "y7", "1.00"), ("c", "y15", "1.00"))
    a = allocate(schedule, D("0.50"), D("0.50"))
    assert [t.allocated for t in a.by_bucket] == [D("0.34"), D("0.33"), D("0.33")]
    # B × bucket / T = 0.17, 0.165, 0.165: half up would give 0.17 + 0.17 + 0.17 = 0.51;
    # floors 0.17, 0.16, 0.16 leave one cent for the largest remainder (y7, then y15 by
    # order), so the building column sums to 0.50 and closing = bucket − building.
    assert [(t.building, t.closing_costs) for t in a.by_bucket] == [
        (D("0.17"), D("0.17")),
        (D("0.17"), D("0.16")),
        (D("0.16"), D("0.17")),
    ]
    assert sum(t.building for t in a.by_bucket) == D("0.50")
    assert sum(t.closing_costs for t in a.by_bucket) == D("0.50")
    assert all(t.building + t.closing_costs == t.allocated for t in a.by_bucket)
    assert [t.percent for t in a.by_bucket] == [D("34.0"), D("33.0"), D("33.0")]


def test_bucket_order_is_fixed_whatever_the_row_order() -> None:
    schedule = rows(
        ("c", "y39", "1.00"), ("a", "y15", "1.00"), ("b", "y5", "1.00"), ("d", "y27_5", "1.00")
    )
    a = allocate(schedule, D("4.00"), D("0.00"))
    assert [t.bucket for t in a.by_bucket] == ["y5", "y15", "y27_5", "y39"]
    assert [r.id for r in a.rows] == ["c", "a", "b", "d"]  # rows keep the schedule order


def test_regrouping_by_system_with_a_weighted_split() -> None:
    schedule = [
        RcnRow("hw", "y5", D("100.00"), "Interior Half Walls", HALF_WALL_SPLIT),
        RcnRow("p", "y39", D("300.00"), "Painting", REPORT_ROW_SYSTEMS["Painting"]),
        RcnRow("c", "y5", D("50.00"), "Carpet", REPORT_ROW_SYSTEMS["Flooring - Carpet"]),
    ]
    a = allocate(schedule, D("450.00"), D("0.00"))  # f = 1
    assert a.by_system is not None and a.by_system_reason is None
    assert [(s.system, s.rcn, s.allocated) for s in a.by_system] == [
        ("Interior Framing / Partitions", D("80.00"), D("80.00")),
        ("Miscellaneous Building Components", D("50.00"), D("50.00")),
        ("Painting", D("320.00"), D("320.00")),
    ]


def test_split_parts_round_half_up_and_the_last_part_takes_the_remainder() -> None:
    schedule = [RcnRow("hw", "y5", D("10.01"), "Interior Half Walls", HALF_WALL_SPLIT)]
    a = allocate(schedule, D("10.01"), D("0.00"))
    assert a.by_system is not None
    # 0.8 × 10.01 = 8.008 → 8.01; the painting part is 10.01 − 8.01 = 2.00 (not 2.002 → 2.00
    # by chance: the rule is remainder, so the parts always sum to the row).
    assert [(s.system, s.allocated) for s in a.by_system] == [
        ("Interior Framing / Partitions", D("8.01")),
        ("Painting", D("2.00")),
    ]


def test_every_report_row_maps_to_one_of_the_twelve_systems() -> None:
    systems = {system for weights in REPORT_ROW_SYSTEMS.values() for system, _ in weights}
    assert len(systems) == 12
    assert len(REPORT_ROW_SYSTEMS) == 46
    assert all(sum(w for _, w in weights) == 1 for weights in REPORT_ROW_SYSTEMS.values())


def test_uniform_factor_check_step_up_is_an_error() -> None:
    schedule = rows(("a", "y5", "100.00"))
    up = allocate(schedule, D("150.00"), D("0.00"))
    severity, message = check(up, "uniform_factor")
    assert severity is Severity.ERROR and "step-up" in message and "1.500000000" in message
    assert [r.allocated for r in up.rows] == [D("150.00")]
    flat = allocate(schedule, D("100.00"), D("0.00"))
    assert check(flat, "uniform_factor")[0] is Severity.OK
    down = allocate(schedule, D("60.00"), D("0.00"))
    severity, message = check(down, "uniform_factor")
    assert severity is Severity.WARNING and "40.0 %" in message


def test_factor_uses_full_precision_not_the_nine_places_shown() -> None:
    # T / ΣRCN = 480201 / 676001 = 0.71035545805405613…: the factor is reported to nine
    # places and applied at full precision (676000 × f = 480200.28964… → 480200.29).
    schedule = rows(("big", "y39", "676000.00"), ("small", "y5", "1.00"))
    a = allocate(schedule, D("480201.00"), D("0.00"))
    assert a.factor == D("0.710355458")
    assert a.rows[0].allocated == D("480200.29")
    assert a.rows[1].allocated == D("0.71")
    assert a.rows_sum == D("480201.00")


def test_zero_rcn_rows_allocate_zero() -> None:
    a = allocate(rows(("a", "y5", "0.00"), ("b", "y5", "10.00")), D("5.00"), D("0.00"))
    assert [r.allocated for r in a.rows] == [D("0.00"), D("5.00")]


def test_rcn_may_carry_more_than_two_decimals() -> None:
    a = allocate([RcnRow("a", "y5", D("162.2003"))], D("115.22"), D("0.00"))
    assert a.rows[0].allocated == D("115.22")


@pytest.mark.parametrize(
    ("schedule", "b", "cc", "message"),
    [
        ([], "1.00", "0.00", "no rows"),
        (rows(("a", "y5", "1.00"), ("a", "y5", "1.00")), "1.00", "0.00", "duplicate row id"),
        (rows(("a", "land", "1.00")), "1.00", "0.00", "land is not depreciable"),
        (rows(("a", "y99", "1.00")), "1.00", "0.00", "expected one of"),
        (rows(("a", "y5", "-1.00")), "1.00", "0.00", "cannot be negative"),
        (rows(("a", "y5", "0.00")), "1.00", "0.00", "replacement cost new is zero"),
        (rows(("a", "y5", "1.00")), "0.00", "0.00", "must be positive"),
        (rows(("a", "y5", "1.00")), "1.00", "-0.01", "cannot be negative"),
        (rows(("a", "y5", "1.00")), "1.005", "0.00", "whole cents"),
        (
            [RcnRow("a", "y5", D("1.00"), None, (("x", D("0.5")),))],
            "1.00",
            "0.00",
            "do not sum to 1",
        ),
    ],
)
def test_allocate_refusals(schedule: list[RcnRow], b: str, cc: str, message: str) -> None:
    with pytest.raises(BasisError, match=message):
        allocate(schedule, D(b), D(cc))


def test_allocation_is_deterministic_and_decimal_throughout() -> None:
    schedule = rows(("a", "y5", "123.45"), ("b", "y15", "67.89"), ("c", "y39", "1000.01"))
    first = allocate(schedule, D("900.00"), D("12.34"))
    second = allocate(list(schedule), D("900.00"), D("12.34"))
    assert first == second
    for row in first.rows:
        assert type(row.rcn) is Decimal and type(row.allocated) is Decimal
    for t in first.by_bucket:
        for v in (t.rcn, t.allocated, t.percent, t.share, t.building, t.closing_costs):
            assert type(v) is Decimal
    assert first.rows_sum == first.total == D("912.34")


def test_money_rounds_half_up() -> None:
    assert money(D("1.005")) == D("1.01")
    assert money(D("1.004")) == D("1.00")
    assert money(D("-1.005")) == D("-1.01")
