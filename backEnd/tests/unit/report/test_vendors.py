"""``basis.report.vendors``: each vendor profile keeps the vendor's columns, codes and limits,
its rows still sum to the basis, its files are the same bytes every time, and the generic
files did not change (STUDY_REPORT_SPEC 11.5.1)."""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any, get_args

import pytest
from openpyxl import load_workbook

from basis.errors.codes import E
from basis.report import schedule as generic
from basis.report import vendors
from basis.report.model import ReportError, build_report
from basis.report.schedule import COLUMNS, Row, rows
from basis.report.vendors import (
    BLOOMBERG,
    CCH,
    FIXED_ASSETS_CS,
    GENERIC,
    PROCONNECT,
    PROFILES,
    SAGE,
    Profile,
    ProfileName,
    to_csv,
    to_xlsx,
    vendor_rows,
)

from .support import export

# The synthetic export's generic files as they were before vendor profiles existed
# (main 396dcbe); the default must stay byte-identical.
GENERIC_CSV_SHA256 = "018de8c77cfb22d9039f408e1d9cc4dd7da207cee93787828186096eab8b4211"
GENERIC_XLSX_SHA256 = "e0d4710058f7b7b3d81f8ada43f5090568ae73ba4617cae5fc2b25b28be10a77"
VENDORS = [FIXED_ASSETS_CS, SAGE, PROCONNECT, CCH, BLOOMBERG]
UNSAFE = set("\",'\n\t")


def schedule() -> tuple[Row, ...]:
    return rows(build_report(export()))


def parsed(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8"))))


def by_description(profile: Profile, data: bytes) -> dict[str, dict[str, str]]:
    table = parsed(data)
    assert tuple(table[0]) == profile.headers
    column = profile.headers.index(next(h for h in profile.headers if h.startswith("Descr")))
    return {r[column]: dict(zip(profile.headers, r, strict=True)) for r in table[1:]}


def generic_row(**changes: str) -> Row:
    """A hand-built generic row (a 7 or 39-year class, a 100 % bonus) for the mappings the
    synthetic export does not reach."""
    fields = dict(zip(COLUMNS, schedule()[0].values, strict=True))
    fields.update(changes)
    return Row(values=tuple(fields[c] for c in COLUMNS), basis=Decimal(fields["basis"]))


# --------------------------------------------------------------------- the default


def test_the_generic_files_did_not_change() -> None:
    s = schedule()
    assert hashlib.sha256(generic.to_csv(s)).hexdigest() == GENERIC_CSV_SHA256
    assert hashlib.sha256(generic.to_xlsx(s)).hexdigest() == GENERIC_XLSX_SHA256
    assert to_csv(s) == to_csv(s, GENERIC) == generic.to_csv(s)
    assert to_xlsx(s) == to_xlsx(s, GENERIC) == generic.to_xlsx(s)
    assert GENERIC.headers == COLUMNS and PROFILES["generic"] is GENERIC


def test_the_route_s_profile_names_are_the_profiles() -> None:
    assert set(get_args(ProfileName)) == set(PROFILES)
    assert [p.name for p in VENDORS] == [n for n in PROFILES if n != "generic"]
    assert not any(p.verified for p in VENDORS)  # built to the published specs only


# --------------------------------------------------------------------- every profile


@pytest.mark.parametrize("profile", VENDORS, ids=lambda p: p.name)
def test_every_profile_ties_fits_and_is_stable(profile: Profile) -> None:
    s = schedule()
    out = vendor_rows(profile, s)
    assert len(out) == len(s) and sum(r.basis for r in out) == Decimal("61000.00")
    money = profile.headers.index(profile.money)
    assert sum(Decimal(r.values[money]) for r in out) == Decimal("61000.00")
    data = to_csv(s, profile)
    assert data == to_csv(schedule(), profile)
    table = parsed(data)
    assert tuple(table[0]) == profile.headers and len(table) == len(s) + 1
    assert all(len(r) == len(profile.headers) for r in table)
    description = next(i for i, h in enumerate(profile.headers) if h.startswith("Descr"))
    for r in table[1:]:
        assert 0 < len(r[description]) <= profile.description_limit
        assert not set(r[description]) & UNSAFE
    book = to_xlsx(s, profile)
    assert book == to_xlsx(schedule(), profile)  # byte-identical
    sheet = load_workbook(io.BytesIO(book))["Schedule"]
    values = list(sheet.iter_rows(values_only=True))
    assert tuple(values[0]) == profile.headers and len(values) == len(s) + 1
    assert sum(Decimal(str(v[money])) for v in values[1:]) == Decimal("61000.00")
    for column, kind in ((c.header, c.kind) for c in profile.columns):
        i = profile.headers.index(column)
        for v in (v[i] for v in values[1:]):
            if v in (None, ""):
                continue
            if kind == "number":
                assert isinstance(v, int | float | Decimal), (column, v)
            elif kind == "date":
                assert (
                    hasattr(v, "date")
                    and v.date() == date(2025, 3, 1)
                    or v.date() == date(2024, 6, 18)
                )
            else:
                assert isinstance(v, str), (column, v)


def test_a_vendor_row_that_does_not_carry_the_line_s_basis_is_refused() -> None:
    broken = replace(schedule()[0], basis=Decimal("999.99"))
    with pytest.raises(ReportError) as e:
        vendor_rows(SAGE, (broken,))
    assert e.value.code == E.REPORT_TOTALS_DO_NOT_TIE


# --------------------------------------------------------------------- Fixed Assets CS


def test_fixed_assets_cs_codes_limits_and_memos() -> None:
    by = by_description(FIXED_ASSETS_CS, to_csv(schedule(), FIXED_ASSETS_CS))
    carpet, framing = by["Carpet"], by["Structural framing"]
    assert (carpet["Method"], carpet["Life"], carpet["ADS Life"]) == ("M", "5", "9")
    assert (carpet["Date In Service"], carpet["Date Acquired"]) == ("03/01/2025", "06/18/2024")
    assert (carpet["Real property"], carpet["Force Mid Qtr"], carpet["New/Used"]) == ("", "N", "N")
    assert carpet["Memo one"] == "Line 1 IRC 1245 class 57.0"
    assert carpet["Memo two"].startswith("Bonus default 40% IRC 168(k)(6)")
    assert (framing["Method"], framing["Life"], framing["Real property"]) == ("M", "27.5", "R")
    assert framing["Memo two"] == "Not bonus eligible"
    for row in by.values():  # "Don't use commas or quotation marks in any cells."
        for value in row.values():
            assert len(value) <= 45 and not set(value) & UNSAFE, value
    undetermined = vendor_rows(FIXED_ASSETS_CS, (generic_row(statutory_bonus_rate=""),))[0]
    assert undetermined.values[-1] == "Bonus default undetermined see schedule"


# --------------------------------------------------------------------- Sage


def test_sage_codes_yymm_lives_and_the_168_allowance() -> None:
    by = by_description(SAGE, to_csv(schedule(), SAGE))
    carpet, landscaping, framing = by["Carpet"], by["Landscaping"], by["Structural framing"]
    assert [carpet[h] for h in SAGE.headers] == [
        "1",
        "Carpet",
        "Bedroom (floor 2)",
        "P",
        "03/01/2025",
        "06/18/2024",
        "1000.00",
        "MA",
        "200",
        "40",
        "0500",
        "0900",
        "N",
    ]
    assert (landscaping["Property Type"], landscaping["Declining Balance %"]) == ("P", "150")
    assert (landscaping["Estimated Life"], landscaping["ADS Life"]) == ("1500", "2000")
    assert [framing[h] for h in SAGE.headers[3:]] == [
        "R",
        "03/01/2025",
        "06/18/2024",
        "50000.00",
        "MF",
        "100",
        "",
        "2706",
        "3000",
        "",
    ]
    full = vendor_rows(SAGE, (generic_row(statutory_bonus_rate="1.00"),))[0].values
    assert (full[7], full[9]) == ("MA", "100")
    none = vendor_rows(SAGE, (generic_row(statutory_bonus_rate=""),))[0].values
    assert (none[7], none[9]) == ("MF", "")
    zero = vendor_rows(SAGE, (generic_row(statutory_bonus_rate="0.00"),))[0].values
    assert (zero[7], zero[9]) == ("MF", "")
    assert vendors._yymm("39") == "3900" and vendors._yymm("7") == "0700"
    sheet = load_workbook(io.BytesIO(to_xlsx(schedule(), SAGE)))["Schedule"]
    first = next(sheet.iter_rows(min_row=2, max_row=2, values_only=True))
    assert first[SAGE.headers.index("Estimated Life")] == "0500"  # text keeps its zero
    assert first[SAGE.headers.index("Placed-In-Service Date")].date() == date(2025, 3, 1)


# --------------------------------------------------------------------- ProConnect / Lacerte


def test_proconnect_follows_intuit_s_template() -> None:
    assert PROCONNECT.headers == (
        "Description of Property",
        "Form Number",
        "Activity Number",
        "Category Number (1-8)",
        "Date Placed in Service",
        "Cost or Basis",
        "Current Sec 179",
        "Method",
        "Prior Sec 179",
        "Prior Special Depreciation Allowance",
        "Prior Depreciation or Amortization",
        "Prior Bonus Depreciation",
        "AMT Prior Depreciation (MACRS only)",
        "Book Cost Basis",
        "Book Method",
        "Book Life",
        "Book Prior Depreciation",
    )
    by = by_description(PROCONNECT, to_csv(schedule(), PROCONNECT))
    assert by["Carpet"]["Category Number (1-8)"] == "3" and by["Carpet"]["Method"] == "53"
    assert by["Landscaping"]["Category Number (1-8)"] == "5"
    assert by["Landscaping"]["Method"] == "66"
    assert by["Structural framing"]["Category Number (1-8)"] == "4"
    assert by["Structural framing"]["Method"] == "85"
    assert by["Carpet"]["Date Placed in Service"] == "03/01/2025"
    assert by["Carpet"]["Cost or Basis"] == "1000.00"
    for row in by.values():
        assert all(row[h] == "" for h in PROCONNECT.headers[6:7] + PROCONNECT.headers[8:])
        assert (row["Form Number"], row["Activity Number"]) == ("", "")
        assert len(row["Description of Property"]) <= 25
    long = generic_row(description="Kitchen cabinets (wood) upper and lower runs")
    assert vendor_rows(PROCONNECT, (long,))[0].values[0] == "Kitchen cabinets (wood) u"
    seven = vendor_rows(PROCONNECT, (generic_row(recovery_period="7"),))[0].values
    thirty_nine = vendor_rows(PROCONNECT, (generic_row(recovery_period="39"),))[0].values
    assert (seven[3], seven[7], thirty_nine[3], thirty_nine[7]) == ("2", "57", "4", "87")
    with pytest.raises(ReportError):
        vendor_rows(PROCONNECT, (generic_row(recovery_period="10"),))


# --------------------------------------------------------------------- CCH and Bloomberg


@pytest.mark.parametrize("profile", [CCH, BLOOMBERG], ids=lambda p: p.name)
def test_mapped_layouts_name_each_distinct_method_and_convention(profile: Profile) -> None:
    table = parsed(to_csv(schedule(), profile))
    method = next(i for i, h in enumerate(profile.headers) if "Method" in h)
    convention = profile.headers.index("Convention")
    assert [(r[method], r[convention]) for r in table[1:]] == [
        ("MACRS 200DB", "Half-Year"),
        ("MACRS 200DB", "Half-Year"),
        ("MACRS 150DB", "Half-Year"),
        ("MACRS SL", "Mid-Month"),
        ("MACRS SL", "Mid-Month"),
    ]
    bonus = profile.headers.index("Bonus Default %")
    assert [r[bonus] for r in table[1:]] == ["40", "40", "40", "", ""]
    assert table[1][-1] == "IRS_IRS_COST_SEG_ATG__2024, p.298"  # quoted in the CSV, kept whole
    assert table[1][3:6] == ["03/01/2025", "06/18/2024", "1000.00"]


def test_bloomberg_is_a_csv_layout_and_cch_a_spreadsheet() -> None:
    assert BLOOMBERG.file_types == ("csv",) and "xlsx" in CCH.file_types
    assert BLOOMBERG.headers != CCH.headers and len(BLOOMBERG.headers) == len(CCH.headers)


def test_the_xlsx_profiles_do_not_carry_the_save_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as real

    import openpyxl.writer.excel as excel

    class Clock:
        timezone = real.timezone
        calls = 0

        class datetime:
            @staticmethod
            def now(tz: Any = None) -> real.datetime:
                Clock.calls += 1
                return real.datetime(2030, 1, 1, 0, 0, Clock.calls, tzinfo=tz)

    monkeypatch.setattr(excel, "datetime", Clock)
    s = schedule()
    assert to_xlsx(s, SAGE) == to_xlsx(s, SAGE) and Clock.calls == 2
