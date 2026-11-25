"""MACRS depreciation allowed or allowable from the IRS percentage tables (Publication 946
(2025), Appendix A), for the dispositions of a renovation (RENOVATIONS.md section 4.4).

The schedule (:mod:`basis.report.schedule`) prints each line's method, convention and life
(:mod:`basis.report.tax`); the preparer's software computes the deductions. A disposition needs
the depreciation already taken on the removed line: "the depreciation allowed or allowable for
the disposed portion is computed using the: Depreciation method, Recovery period, and Convention
applicable to the asset in which the disposed portion is included, and Including the portion of
any additional first-year depreciation deduction that is attributable to the disposed portion"
(IRS Practice Unit DCE-P-002, 1/16/2025, p.22). This module computes exactly that, from the
same class terms the schedule prints, with the tables a preparer uses:

* Table A-1 (half-year convention) for 5-, 7- and 15-year property (200 % and 150 % declining
  balance with the switch to straight line built into the table);
* Table A-8 (straight line, half-year) for 15-year qualified improvement property;
* Table A-6 (27.5-year residential rental, mid-month) and Table A-7a (39-year nonresidential
  real, mid-month), by the month placed in service.

Year of disposition (Pub 946 (2025), chapter 4, "Sale or Other Disposition Before the Recovery
Period Ends"): "For property for which you used a half-year
convention, the depreciation deduction for the year of the disposition is half the depreciation
determined for the full year"; under the mid-month convention the full year's depreciation is
multiplied by the months in service, the month of disposition counting as half, over 12. A
property placed in service and disposed of in the same year gets no depreciation and no bonus
(Pub 946, "Excepted Property": "Property placed in service and disposed of in the same year").
Tax years are calendar years (the schedule's assumption, :func:`basis.report.tax.mid_quarter_note`);
mid-quarter is the preparer's 40 % test and is not applied here, as the schedule prints
half-year.

Each year's amount is rounded half up to the cent; the total is their sum. Checked against the
Practice Unit's worked example (p.23): a $150,000 elevator in a 39-year building placed in
service 7/1/2011, disposed of 6/30/2014: "the depreciation allowed on the elevator disposed of is
$11,220" (``tests/unit/report/test_macrs.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from basis.report.tax import ClassTerms

CENT: Final = Decimal("0.01")
HUNDRED: Final = Decimal(100)
HALF: Final = Decimal("0.5")
TWELVE: Final = Decimal(12)


def _d(*values: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(v) for v in values)


# Pub 946 (2025) Table A-1, half-year convention, percent of the unadjusted basis by recovery
# year (1-based).
TABLE_A1: Final[dict[Decimal, tuple[Decimal, ...]]] = {
    Decimal(5): _d("20.00", "32.00", "19.20", "11.52", "11.52", "5.76"),
    Decimal(7): _d("14.29", "24.49", "17.49", "12.49", "8.93", "8.92", "8.93", "4.46"),
    Decimal(15): _d(
        "5.00", "9.50", "8.55", "7.70", "6.93", "6.23", "5.90", "5.90",
        "5.91", "5.90", "5.91", "5.90", "5.91", "5.90", "5.91", "2.95",
    ),
}  # fmt: skip
# Pub 946 (2025) Table A-8, straight line, half-year convention, the 15-year column.
TABLE_A8_15: Final = _d(
    "3.33", "6.67", "6.67", "6.67", "6.67", "6.67", "6.67", "6.66",
    "6.67", "6.66", "6.67", "6.66", "6.67", "6.66", "6.67", "3.33",
)  # fmt: skip
# Table A-6, 27.5-year, mid-month: year 1 by the month placed in service.
_A6_FIRST: Final = _d(
    "3.485", "3.182", "2.879", "2.576", "2.273", "1.970",
    "1.667", "1.364", "1.061", "0.758", "0.455", "0.152",
)  # fmt: skip
_A6_YEAR_28: Final = _d(
    "1.97", "2.273", "2.576", "2.879", "3.182", "3.485",
    "3.636", "3.636", "3.636", "3.636", "3.636", "3.636",
)  # fmt: skip
_A6_YEAR_29: Final = _d(
    "0", "0", "0", "0", "0", "0", "0.152", "0.455", "0.758", "1.061", "1.364", "1.667"
)
# Table A-7a, 39-year, mid-month.
_A7A_FIRST: Final = _d(
    "2.461", "2.247", "2.033", "1.819", "1.605", "1.391",
    "1.177", "0.963", "0.749", "0.535", "0.321", "0.107",
)  # fmt: skip
_A7A_LAST: Final = tuple(reversed(_A7A_FIRST))


def table_a6(year: int, month: int) -> Decimal:
    """Table A-6's percentage for recovery ``year`` of property placed in service in ``month``:
    year 1 by month, 3.636 for years 2-9, then 3.637 and 3.636 alternating (3.637 in the even
    years for months 1-6, in the odd years for months 7-12) through year 27, then 28 and 29."""
    if year == 1:
        return _A6_FIRST[month - 1]
    if 2 <= year <= 9:
        return Decimal("3.636")
    if 10 <= year <= 27:
        high = (year % 2 == 0) == (month <= 6)
        return Decimal("3.637") if high else Decimal("3.636")
    if year == 28:
        return _A6_YEAR_28[month - 1]
    if year == 29:
        return _A6_YEAR_29[month - 1]
    return Decimal(0)


def table_a7a(year: int, month: int) -> Decimal:
    """Table A-7a's percentage: year 1 by month, 2.564 for years 2-39, year 40 by month."""
    if year == 1:
        return _A7A_FIRST[month - 1]
    if 2 <= year <= 39:
        return Decimal("2.564")
    if year == 40:
        return _A7A_LAST[month - 1]
    return Decimal(0)


def rate(terms: ClassTerms, year: int, month_placed: int) -> Decimal:
    """The table percentage for recovery ``year`` (1-based) of a line with these terms."""
    if terms.convention == "MM":
        if terms.gds_life == Decimal("27.5"):
            return table_a6(year, month_placed)
        if terms.gds_life == Decimal(39):
            return table_a7a(year, month_placed)
        raise ValueError(f"no mid-month table for a {terms.gds_life}-year life")
    if terms.method == "SL" and terms.gds_life == Decimal(15):
        column = TABLE_A8_15
    else:
        found = TABLE_A1.get(terms.gds_life)
        if found is None:
            raise ValueError(f"no half-year table for a {terms.gds_life}-year life")
        column = found
    return column[year - 1] if 1 <= year <= len(column) else Decimal(0)


def table_name(terms: ClassTerms) -> str:
    """The Appendix A table a line's deductions come from."""
    if terms.convention == "MM":
        return "A-6" if terms.gds_life == Decimal("27.5") else "A-7a"
    return "A-8" if terms.method == "SL" else "A-1"


def last_year(terms: ClassTerms) -> int:
    if terms.convention == "MM":
        return 29 if terms.gds_life == Decimal("27.5") else 40
    if terms.method == "SL" and terms.gds_life == Decimal(15):
        return len(TABLE_A8_15)
    return len(TABLE_A1[terms.gds_life])


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Allowed:
    """The depreciation allowed or allowable on one disposed basis to its disposition."""

    basis: Decimal  # the unadjusted basis disposed of
    bonus: Decimal  # its share of the bonus taken in the year placed in service
    by_year: tuple[tuple[int, Decimal], ...]  # MACRS deductions, calendar year by year
    total: Decimal  # bonus + every year's deduction
    adjusted_basis: Decimal  # basis - total, never below zero
    note: str


def allowed(
    basis: Decimal,
    terms: ClassTerms,
    placed_in_service: date,
    disposed: date,
    bonus_rate: Decimal | None = None,
) -> Allowed:
    """The depreciation allowed or allowable on ``basis`` (a line placed in service on
    ``placed_in_service`` under ``terms``) from then until ``disposed``, with the share of the
    bonus at ``bonus_rate`` (0..1; None or 0: none; ignored for a class without bonus)."""
    if disposed < placed_in_service:
        raise ValueError("disposed of before it was placed in service")
    basis = _cents(basis)
    if disposed.year == placed_in_service.year:
        return Allowed(
            basis,
            Decimal("0.00"),
            (),
            Decimal("0.00"),
            basis,
            "placed in service and disposed of in the same year: no depreciation and no bonus "
            "(Pub 946, Excepted Property)",
        )
    bonus = Decimal("0.00")
    if terms.bonus_eligible and bonus_rate:
        bonus = _cents(basis * bonus_rate)
    depreciable = basis - bonus
    month = placed_in_service.month
    end = last_year(terms)
    rows: list[tuple[int, Decimal]] = []
    for tax_year in range(placed_in_service.year, disposed.year + 1):
        recovery_year = tax_year - placed_in_service.year + 1
        if recovery_year > end:
            break
        pct = rate(terms, recovery_year, month)
        amount = depreciable * pct / HUNDRED
        if tax_year == disposed.year and recovery_year < end:
            if terms.convention == "MM":
                amount = amount * (Decimal(disposed.month) - HALF) / TWELVE
            else:
                amount = amount * HALF
        rows.append((tax_year, _cents(amount)))
    taken = sum((amount for _, amount in rows), Decimal("0.00"))
    total = bonus + taken
    if total > basis:  # rounding across years can never exceed the basis; a guard
        total = basis
    note = (
        f"{terms.method} {terms.convention} {terms.gds_life.normalize():f}-year from Pub 946 "
        f"(2025) Table {table_name(terms)}"
        + (f", bonus {bonus_rate * HUNDRED:.0f} %" if bonus > 0 and bonus_rate else "")
        + (
            "; the disposition year at half a year"
            if terms.convention == "HY"
            else f"; the disposition year at {Decimal(disposed.month) - HALF} of 12 months"
        )
    )
    return Allowed(basis, bonus, tuple(rows), total, basis - total, note)


__all__ = [
    "TABLE_A1",
    "TABLE_A8_15",
    "Allowed",
    "allowed",
    "last_year",
    "rate",
    "table_a6",
    "table_a7a",
    "table_name",
]
