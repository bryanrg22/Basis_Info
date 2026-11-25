"""Depreciation allowed or allowable from the Pub 946 (2025) tables (EVAL_REPORT 102,
``basis.report.macrs``): every table column sums to 100 %, Table A-6's alternation, the
Practice Unit's worked example, hand-computed answers for half-year and mid-month lines,
bonus, the same-year rule, a disposition after the recovery period, QIP. Fabricated values."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from basis.db.enums import Bucket
from basis.report.macrs import TABLE_A1, TABLE_A8_15, allowed, table_a6, table_a7a
from basis.report.tax import QIP_TERMS, TERMS

D = Decimal


def test_every_table_column_sums_to_one_hundred_percent() -> None:
    for column in TABLE_A1.values():
        assert sum(column) == D(100)
    assert sum(TABLE_A8_15) == D(100)
    for month in range(1, 13):
        assert sum(table_a6(y, month) for y in range(1, 30)) == D(100)
        assert sum(table_a7a(y, month) for y in range(1, 41)) == D(100)


@pytest.mark.parametrize(
    ("year", "month", "value"),
    [
        (1, 1, "3.485"), (1, 12, "0.152"), (9, 6, "3.636"),
        (10, 1, "3.637"), (10, 7, "3.636"), (11, 1, "3.636"), (11, 7, "3.637"),
        (27, 6, "3.636"), (27, 7, "3.637"), (28, 1, "1.97"), (28, 7, "3.636"),
        (29, 6, "0"), (29, 12, "1.667"), (30, 1, "0"),
    ],
)  # fmt: skip
def test_table_a6_cells_as_printed(year: int, month: int, value: str) -> None:
    assert table_a6(year, month) == D(value)


def test_the_practice_units_elevator() -> None:
    """DCE-P-002 p.23: $150,000 of a 39-year building placed in service 7/1/2011, disposed of
    6/30/2014; "the depreciation allowed on the elevator disposed of is $11,220" and the
    adjusted depreciable basis $138,780."""
    a = allowed(D(150000), TERMS[Bucket.Y39], date(2011, 7, 1), date(2014, 6, 30))
    # 1.177 % + 2.564 % + 2.564 % + 2.564 % x 5.5/12
    assert a.by_year == (
        (2011, D("1765.50")),
        (2012, D("3846.00")),
        (2013, D("3846.00")),
        (2014, D("1762.75")),
    )
    assert a.total.quantize(D(1)) == D(11220)
    assert a.adjusted_basis.quantize(D(1)) == D(138780)


def test_a_five_year_line_with_bonus_by_hand() -> None:
    """9,173.55 placed in service April 2023 with 80 % bonus, disposed of March 2025: bonus
    7,338.84; MACRS on 1,834.71: 20 % (366.94), 32 % (587.11), half of 19.20 % (176.13)."""
    a = allowed(D("9173.55"), TERMS[Bucket.Y5], date(2023, 4, 1), date(2025, 3, 3), D("0.80"))
    assert a.bonus == D("7338.84")
    assert a.by_year == ((2023, D("366.94")), (2024, D("587.11")), (2025, D("176.13")))
    assert a.total == D("8469.02") and a.adjusted_basis == D("704.53")


def test_a_residential_line_by_hand_ignores_the_bonus() -> None:
    """4,586.78 of 27.5-year property placed in service in April 2023 (Table A-6 month 4:
    2.576 %), disposed of in March 2025 (3.636 % x 2.5/12). Real property has no bonus."""
    a = allowed(D("4586.78"), TERMS[Bucket.Y27_5], date(2023, 4, 1), date(2025, 3, 3), D("0.80"))
    assert a.bonus == D("0.00")
    assert a.by_year == ((2023, D("118.16")), (2024, D("166.78")), (2025, D("34.74")))
    assert a.total == D("319.68")


def test_placed_in_service_and_disposed_of_in_the_same_year_gets_nothing() -> None:
    a = allowed(D(1000), TERMS[Bucket.Y5], date(2025, 2, 1), date(2025, 11, 30), D(1))
    assert a.total == D("0.00") and a.bonus == D("0.00") and a.adjusted_basis == D("1000.00")
    assert "same year" in a.note


def test_a_disposition_after_the_recovery_period_is_fully_depreciated() -> None:
    a = allowed(D(1000), TERMS[Bucket.Y5], date(2015, 6, 1), date(2025, 6, 1))
    assert a.total == D("1000.00") and a.adjusted_basis == D("0.00")
    assert len(a.by_year) == 6  # the sixth year's half-year amount is allowed in full


def test_a_disposition_in_the_last_table_year_takes_its_whole_amount() -> None:
    a = allowed(D(1000), TERMS[Bucket.Y5], date(2020, 6, 1), date(2025, 2, 1))
    assert a.by_year[-1] == (2025, D("57.60")) and a.total == D("1000.00")


def test_full_bonus_leaves_nothing_to_depreciate() -> None:
    a = allowed(D(500), TERMS[Bucket.Y7], date(2024, 1, 5), date(2026, 1, 5), D(1))
    assert a.bonus == D("500.00") and a.total == D("500.00") and a.adjusted_basis == D(0)


def test_qip_uses_the_straight_line_half_year_column() -> None:
    a = allowed(D(10000), QIP_TERMS, date(2025, 5, 30), date(2027, 8, 1), D(0))
    assert a.by_year == ((2025, D("333.00")), (2026, D("667.00")), (2027, D("333.50")))


def test_a_disposition_before_service_is_refused() -> None:
    with pytest.raises(ValueError):
        allowed(D(1), TERMS[Bucket.Y5], date(2025, 2, 1), date(2025, 1, 1))


def test_pub_946s_own_mid_month_disposition_example() -> None:
    """Pub 946 (2025), chapter 4: residential rental property costing $100,000 placed in
    service July 2, 2023 and sold March 2, 2025: "Multiply $3,636 by the fraction, 2.5 over
    12, to get your 2025 depreciation deduction of $757.50"."""
    a = allowed(D(100000), TERMS[Bucket.Y27_5], date(2023, 7, 2), date(2025, 3, 2))
    assert a.by_year[-1] == (2025, D("757.50"))
    # 2023 is Table A-6 month 7 (1.667 %), 2024 a full 3.636 %.
    assert a.by_year[:2] == ((2023, D("1667.00")), (2024, D("3636.00")))
