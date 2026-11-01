"""Vendor import profiles for the preparer's schedule (STUDY_REPORT_SPEC 11.5.1).

A profile is a column mapping over the generic schedule rows (:mod:`basis.report.schedule`):
the vendor's column names in the vendor's order, its codes for method, convention and life,
its date format and its description limit. Each is built from the vendor's own published
import documentation (cited in the spec); **none is verified against the real software**.
Where the vendor publishes only a mapping dialog and no layout (CCH ProSystem fx Fixed Assets,
Bloomberg Tax Fixed Assets), the profile is a plainly headed file for that dialog.

Every profile keeps the line's allocated cost unchanged, so its rows sum to the depreciable
basis to the cent exactly as the generic rows do (:func:`vendor_rows` checks it), and every
value is a pure function of the generic row, so the files are byte-identical between runs.
The generic profile delegates to :mod:`basis.report.schedule` and is the default.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final, Literal, get_args

from basis.errors.codes import E
from basis.report import schedule as generic
from basis.report.model import ReportError
from basis.report.schedule import COLUMNS, Row, description

Kind = Literal["text", "number", "date"]
ProfileName = Literal["generic", "fixed_assets_cs", "sage", "proconnect", "cch", "bloomberg"]
Fields = Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Column:
    header: str
    kind: Kind = "text"


@dataclass(frozen=True, slots=True)
class Profile:
    """One vendor layout: its columns, and how a generic row becomes one of its rows."""

    name: str
    vendor: str
    file_types: tuple[str, ...]
    description_limit: int
    date_format: str
    money: str  # the column that carries the line's allocated cost
    columns: tuple[Column, ...]
    values: Callable[[Fields], tuple[str, ...]]
    verified: bool = False  # against the real software

    @property
    def headers(self) -> tuple[str, ...]:
        return tuple(c.header for c in self.columns)


# ------------------------------------------------------------------ shared pieces

US_DATE: Final = "%m/%d/%Y"
# Class method and convention by recovery period (Pub 946 Table 4-1), as the generic row says.
PERIODS: Final = ("5", "7", "15", "27.5", "39")


def _date(iso: str, fmt: str) -> str:
    return date.fromisoformat(iso).strftime(fmt) if iso else ""


def _cut(text: str, limit: int) -> str:
    """The generic cleaning (no quotes, commas or breaks) at the vendor's limit."""
    return description(text)[:limit].rstrip()


def _yymm(life: str) -> str:
    """Sage's life format: years and months as YYMM (27.5 is ``2706``)."""
    value = Decimal(life)
    years = int(value)
    months = int(((value - years) * 12).to_integral_value())
    return f"{years:02d}{months:02d}"


def _bonus_percent(fields: Fields) -> str:
    """The statutory default rate as a whole percentage (``0.40`` is ``40``), blank when the
    class is not eligible or the rate is undetermined."""
    rate = fields["statutory_bonus_rate"]
    if fields["bonus_eligible"] != "Y" or not rate:
        return ""
    return f"{(Decimal(rate) * 100).normalize():f}"


def _bonus_applies(fields: Fields) -> bool:
    percent = _bonus_percent(fields)
    return bool(percent) and Decimal(percent) > 0


def _period(fields: Fields) -> str:
    period = fields["recovery_period"]
    if period not in PERIODS:
        raise ReportError(
            E.REPORT_TOTALS_DO_NOT_TIE,  # never expected: the buckets fix the periods
            f"no vendor mapping for a {period}-year recovery period",
        )
    return period


# ------------------------------------------------------------------ Fixed Assets CS

FACS_LIMIT: Final = 45  # description and memos: "0-45 characters, no quotation marks or commas"


def _facs_values(f: Fields) -> tuple[str, ...]:
    if f["bonus_eligible"] != "Y":
        memo_bonus = "Not bonus eligible"
    elif not f["statutory_bonus_rate"]:
        memo_bonus = "Bonus default undetermined see schedule"
    else:
        memo_bonus = f"Bonus default {_bonus_percent(f)}% {f['bonus_source']}"
    return (
        _cut(f["description"], FACS_LIMIT),
        _date(f["date_placed_in_service"], US_DATE),
        _date(f["date_acquired"], US_DATE),
        f["basis"],
        "M",  # MACRS: the package applies the class method from the life and the R mark
        f["gds_life"],
        f["ads_life"],
        "R" if f["real_property"] == "Y" else "",
        "N",  # the schedule's half-year default; the 40 % test is the preparer's
        "N",  # new
        _cut(f"Line {f['line']} IRC {f['irc_section']} class {f['asset_class'] or '-'}", 45),
        _cut(memo_bonus, FACS_LIMIT),
    )


FIXED_ASSETS_CS: Final = Profile(
    name="fixed_assets_cs",
    vendor="Thomson Reuters Fixed Assets CS",
    file_types=("xlsx", "csv"),
    description_limit=FACS_LIMIT,
    date_format=US_DATE,
    money="Cost",
    columns=(
        Column("Description"),
        Column("Date In Service", "date"),
        Column("Date Acquired", "date"),
        Column("Cost", "number"),
        Column("Method"),
        Column("Life", "number"),
        Column("ADS Life", "number"),
        Column("Real property"),
        Column("Force Mid Qtr"),
        Column("New/Used"),
        Column("Memo one"),
        Column("Memo two"),
    ),
    values=_facs_values,
)

# ------------------------------------------------------------------ Sage Fixed Assets

SAGE_PERCENT: Final = {"200DB": "200", "150DB": "150", "SL": "100"}


def _sage_values(f: Fields) -> tuple[str, ...]:
    real = f["real_property"] == "Y"
    bonus = _bonus_applies(f)
    return (
        f["line"],
        _cut(f["description"], 80),
        _cut(f["location"], 25),
        "R" if real else "P",
        _date(f["date_placed_in_service"], US_DATE),
        _date(f["date_acquired"], US_DATE),
        f["basis"],
        "MA" if bonus else "MF",
        SAGE_PERCENT[f["method"]],
        _bonus_percent(f) if bonus else "",
        _yymm(f["gds_life"]),
        _yymm(f["ads_life"]),
        "" if real else "N",
    )


SAGE: Final = Profile(
    name="sage",
    vendor="Sage Fixed Assets Depreciation",
    file_types=("xlsx", "csv"),
    description_limit=80,
    date_format=US_DATE,
    money="Acquisition Value",
    columns=(
        Column("Asset ID"),
        Column("Description"),
        Column("Location"),
        Column("Property Type"),
        Column("Placed-In-Service Date", "date"),
        Column("Acquisition Date", "date"),
        Column("Acquisition Value", "number"),
        Column("Depreciation Method"),
        Column("Declining Balance %", "number"),
        Column("168 Allow %", "number"),
        Column("Estimated Life"),  # YYMM keeps its leading zero: text
        Column("ADS Life"),
        Column("Mid-Quarter Flag"),
    ),
    values=_sage_values,
)

# ------------------------------------------------------------------ Intuit ProConnect / Lacerte

PROCONNECT_CATEGORY: Final = {"5": "3", "7": "2", "15": "5", "27.5": "4", "39": "4"}
PROCONNECT_METHOD: Final = {"5": "53", "7": "57", "15": "66", "27.5": "85", "39": "87"}


def _proconnect_values(f: Fields) -> tuple[str, ...]:
    period = _period(f)
    return (
        _cut(f["description"], 25),
        "",  # Form Number: depends on the return filed
        "",  # Activity Number: likewise
        PROCONNECT_CATEGORY[period],
        _date(f["date_placed_in_service"], US_DATE),
        f["basis"],
        "",  # Current Sec 179
        PROCONNECT_METHOD[period],
        "",  # Prior Sec 179
        "",  # Prior Special Depreciation Allowance
        "",  # Prior Depreciation or Amortization
        "",  # Prior Bonus Depreciation
        "",  # AMT Prior Depreciation (MACRS only)
        "",  # Book Cost Basis
        "",  # Book Method
        "",  # Book Life
        "",  # Book Prior Depreciation
    )


PROCONNECT: Final = Profile(
    name="proconnect",
    vendor="Intuit ProConnect Tax (and Lacerte, as CSV)",
    file_types=("xlsx", "csv"),
    description_limit=25,
    date_format=US_DATE,
    money="Cost or Basis",
    columns=(
        Column("Description of Property"),
        Column("Form Number", "number"),
        Column("Activity Number", "number"),
        Column("Category Number (1-8)", "number"),
        Column("Date Placed in Service", "date"),
        Column("Cost or Basis", "number"),
        Column("Current Sec 179", "number"),
        Column("Method", "number"),
        Column("Prior Sec 179", "number"),
        Column("Prior Special Depreciation Allowance", "number"),
        Column("Prior Depreciation or Amortization", "number"),
        Column("Prior Bonus Depreciation", "number"),
        Column("AMT Prior Depreciation (MACRS only)", "number"),
        Column("Book Cost Basis", "number"),
        Column("Book Method", "number"),
        Column("Book Life", "number"),
        Column("Book Prior Depreciation", "number"),
    ),
    values=_proconnect_values,
)

# ------------------------------------------------------------------ mapped layouts (CCH, Bloomberg)

MAPPED_METHOD: Final = {"200DB": "MACRS 200DB", "150DB": "MACRS 150DB", "SL": "MACRS SL"}
MAPPED_CONVENTION: Final = {"HY": "Half-Year", "MM": "Mid-Month"}


def _mapped_values(f: Fields) -> tuple[str, ...]:
    return (
        f["line"],
        _cut(f["description"], 45),
        _cut(f["location"], 45),
        _date(f["date_placed_in_service"], US_DATE),
        _date(f["date_acquired"], US_DATE),
        f["basis"],
        MAPPED_METHOD[f["method"]],
        MAPPED_CONVENTION[f["convention"]],
        f["gds_life"],
        f["ads_life"],
        f["irc_section"],
        f["asset_class"],
        _bonus_percent(f),
        f["authority"],
    )


def _mapped_columns(*names: str) -> tuple[Column, ...]:
    kinds: tuple[Kind, ...] = (
        "number",  # asset number
        "text",
        "text",
        "date",
        "date",
        "number",
        "text",
        "text",
        "number",
        "number",
        "text",
        "text",
        "number",
        "text",
    )
    return tuple(Column(n, k) for n, k in zip(names, kinds, strict=True))


CCH: Final = Profile(
    name="cch",
    vendor="CCH ProSystem fx Fixed Assets (User Defined Import)",
    file_types=("xlsx", "csv"),
    description_limit=45,
    date_format=US_DATE,
    money="Cost",
    columns=_mapped_columns(
        "Asset Number",
        "Description",
        "Location",
        "Date In Service",
        "Date Acquired",
        "Cost",
        "Method",
        "Convention",
        "Life",
        "ADS Life",
        "IRC Section",
        "Asset Class",
        "Bonus Default %",
        "Authority",
    ),
    values=_mapped_values,
)

BLOOMBERG: Final = Profile(
    name="bloomberg",
    vendor="Bloomberg Tax Fixed Assets (Import wizard)",
    file_types=("csv",),
    description_limit=45,
    date_format=US_DATE,
    money="Cost",
    columns=_mapped_columns(
        "Asset ID",
        "Description",
        "Location",
        "Placed In Service Date",
        "Acquisition Date",
        "Cost",
        "Depreciation Method",
        "Convention",
        "Recovery Period",
        "ADS Life",
        "IRC Section",
        "Asset Class",
        "Bonus Default %",
        "Authority",
    ),
    values=_mapped_values,
)

# ------------------------------------------------------------------ generic


def _generic_values(f: Fields) -> tuple[str, ...]:
    return tuple(f[name] for name in COLUMNS)


GENERIC: Final = Profile(
    name="generic",
    vendor="Basis (Form 4562 columns)",
    file_types=("xlsx", "csv"),
    description_limit=generic.DESCRIPTION_LIMIT,
    date_format="%Y-%m-%d",
    money="basis",
    columns=tuple(Column(name) for name in COLUMNS),
    values=_generic_values,
    verified=True,  # it is the stored file itself
)

PROFILES: Final[dict[str, Profile]] = {
    p.name: p for p in (GENERIC, FIXED_ASSETS_CS, SAGE, PROCONNECT, CCH, BLOOMBERG)
}
assert set(PROFILES) == set(get_args(ProfileName))


# ------------------------------------------------------------------ rendering


def vendor_rows(profile: Profile, schedule: Iterable[Row]) -> tuple[Row, ...]:
    """The generic rows in the profile's layout. Each row's money column is the line's
    allocated cost, checked, so the rows still sum to the depreciable basis."""
    money = profile.headers.index(profile.money)
    out: list[Row] = []
    for row in schedule:
        fields = dict(zip(COLUMNS, row.values, strict=True))
        values = profile.values(fields)
        if len(values) != len(profile.columns):
            raise ReportError(
                E.REPORT_TOTALS_DO_NOT_TIE,
                f"profile {profile.name} gave {len(values)} values for "
                f"{len(profile.columns)} columns",
            )
        if Decimal(values[money]) != row.basis:
            raise ReportError(
                E.REPORT_TOTALS_DO_NOT_TIE,
                f"profile {profile.name} line {fields['line']}: {values[money]} is not the "
                f"line's basis {row.basis}",
            )
        out.append(Row(values=values, basis=row.basis))
    return tuple(out)


def to_csv(schedule: Iterable[Row], profile: Profile = GENERIC) -> bytes:
    if profile is GENERIC:
        return generic.to_csv(schedule)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(profile.headers)
    for row in vendor_rows(profile, schedule):
        writer.writerow(row.values)
    return buffer.getvalue().encode("utf-8")


def to_xlsx(
    schedule: Iterable[Row], profile: Profile = GENERIC, *, title: str = "Depreciation schedule"
) -> bytes:
    """The profile's rows as a workbook: numbers and dates typed by column, the money column
    to the cent, document properties and zip times pinned (:func:`schedule.workbook_bytes`)."""
    if profile is GENERIC:
        return generic.to_xlsx(schedule, title=title)
    typed: list[list[object]] = []
    for row in vendor_rows(profile, schedule):
        cells: list[object] = []
        for column, value in zip(profile.columns, row.values, strict=True):
            if value and column.kind == "number":
                cells.append(Decimal(value) if "." in value else int(value))
            elif value and column.kind == "date":
                cells.append(datetime.strptime(value, profile.date_format).date())
            else:
                cells.append(value)
        typed.append(cells)
    return generic.workbook_bytes(profile.headers, typed, money=(profile.money,), title=title)


__all__ = [
    "BLOOMBERG",
    "CCH",
    "FIXED_ASSETS_CS",
    "GENERIC",
    "PROCONNECT",
    "PROFILES",
    "SAGE",
    "Column",
    "Profile",
    "ProfileName",
    "to_csv",
    "to_xlsx",
    "vendor_rows",
]
