"""``basis.report.schedule``: the preparer's schedule ties to the report, follows the import
limits, and is the same bytes every time (STUDY_REPORT_SPEC 11.5)."""

from __future__ import annotations

import csv
import io
from dataclasses import replace
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from basis.errors.codes import E
from basis.report.model import ReportError, build_report
from basis.report.schedule import COLUMNS, description, rows, to_csv, to_xlsx

from .support import export


def test_one_row_per_line_summing_to_the_basis_with_the_class_terms() -> None:
    report = build_report(export())
    schedule = rows(report)
    assert [r.values[0] for r in schedule] == [str(i.line) for i in report.items]
    assert sum(r.basis for r in schedule) == report.depreciable_basis
    by = {r.values[1]: dict(zip(COLUMNS, r.values, strict=True)) for r in schedule}
    carpet = by["Carpet"]
    assert (carpet["method"], carpet["convention"], carpet["gds_life"], carpet["ads_life"]) == (
        "200DB",
        "HY",
        "5",
        "9",
    )
    assert (carpet["date_acquired"], carpet["date_placed_in_service"]) == (
        "2024-06-18",
        "2025-03-01",
    )
    assert carpet["statutory_bonus_rate"] == "0.40" and "preparer elects" in carpet["bonus_source"]
    framing = by["Structural framing"]
    assert (framing["method"], framing["convention"], framing["real_property"]) == ("SL", "MM", "Y")
    assert (framing["bonus_eligible"], framing["statutory_bonus_rate"]) == ("N", "")


def test_a_row_total_that_does_not_tie_is_refused() -> None:
    report = build_report(export())
    broken = replace(report, depreciable_basis=report.depreciable_basis + Decimal("0.01"))
    with pytest.raises(ReportError) as e:
        rows(broken)
    assert e.value.code == E.REPORT_TOTALS_DO_NOT_TIE


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('Window, "double hung"', "Window double hung"),
        ("A\nB\tC", "A B C"),
        ("x" * 60, "x" * 45),
        (
            "Kitchen cabinets (wood), upper and lower runs",
            "Kitchen cabinets (wood) upper and lower runs",
        ),
    ],
)
def test_descriptions_fit_the_import_limits(text: str, expected: str) -> None:
    out = description(text)
    assert out == expected[:45].rstrip() and len(out) <= 45
    assert not set(out) & set("\",'\n\t")


def test_csv_and_xlsx_carry_the_same_rows_and_are_stable() -> None:
    schedule = rows(build_report(export()))
    data = to_csv(schedule)
    assert data == to_csv(rows(build_report(export())))
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    assert tuple(parsed[0]) == COLUMNS and [tuple(r) for r in parsed[1:]] == [
        r.values for r in schedule
    ]
    book = to_xlsx(schedule)
    assert book == to_xlsx(rows(build_report(export())))  # byte-identical
    sheet = load_workbook(io.BytesIO(book))["Schedule"]
    values = list(sheet.iter_rows(values_only=True))
    assert tuple(values[0]) == COLUMNS and len(values) == len(schedule) + 1
    basis_col = COLUMNS.index("basis")
    assert sum(Decimal(str(v[basis_col])) for v in values[1:]) == Decimal("61000.00")
    assert values[1][COLUMNS.index("date_acquired")].date().isoformat() == "2024-06-18"
