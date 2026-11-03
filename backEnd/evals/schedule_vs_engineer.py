"""``python -m evals.schedule_vs_engineer``: the preparer's schedule Basis writes against the
engineer's schedule for the same study, row by row and in total, under the IRS rules and
their alternatives. No model call; cost $0.

What is compared. Basis's schedule is produced from a stored study export through the
production report code (:func:`basis.report.model.build_report`, then
:func:`basis.report.schedule.rows`: the rows the export step writes as ``rev-<n>.schedule.csv``),
so the comparison is of what ships. The engineer's schedule is the label file
``engineer_report_v1.json`` (one row per line of the engineer's report: its system, its
recovery bucket and its cost). The engineer's report prints a recovery period and a cost per
row and nothing else of a depreciation schedule (no method, convention, bonus rate, dates or
first-year amount); :func:`engineer_pdf_terms` reads the report's text layer and records
which of those terms appear, so the gap is stated from the file rather than assumed. For
the columns the engineer does not print, the engineer's side is the Pub 946 default for the
engineer's recovery period ("implied"), and the comparison says so.

Row matching reuses :func:`evals.e2e_report.assign_assets` (``e2e_rows_v1.json``: every
pipeline asset charged to at most one engineer row by label, room type and material); a
Basis schedule row is charged to the row of its priced asset (a folded copy follows its
host). Per engineer row: the recovery period (the engineer's bucket against the bucket
carrying most of Basis's allocated dollars on that row, with the count tally), the MACRS
method, the convention and the bonus rate (Basis's row terms against the Pub 946 default of
the engineer's period), and the first-year depreciation as a share of cost on each side.
In total: the share of basis in each class, the first-year total as a share of basis, and
the mid-quarter 40 % test on each side.

The first-year share of cost follows Pub 946 (2025): the special depreciation allowance is
taken first and the remaining basis takes the year-1 rate of Appendix A (Table A-1 half-year,
Tables A-2 to A-5 mid-quarter by the quarter placed in service, Table A-6 and A-7a mid-month
by the month). The quarter and month assume a calendar tax year (the study does not record
the taxpayer's). The 40 % test is Pub 946 chapter 4, "Which Convention Applies?": the
depreciable bases of MACRS property other than nonresidential real and residential rental
property placed in service in the last three months of the year, over the year's; the basis
for the test is before the special depreciation allowance and after any section 179
expense (none here). Basis knows this study's rows only, so the test is reported as "on
this study's rows" on both sides, with the engineer's rows dated as the export's (the report
prints no dates).

Rule sets (:data:`RULE_SETS`), each applied to both sides so an election shows up as the
same change on both and a class difference as a different one:

* ``statutory``: what Basis prints: the statutory bonus default for the dates and the
  half-year convention;
* ``no_bonus``: the election out of the allowance by class (IRC 168(k)(7)), half-year;
* ``mid_quarter``: the statutory default with the mid-quarter convention (the 40 % test met);
* ``no_bonus_mid_quarter``: both.

Usage (from ``backEnd/``)::

    python -m evals.schedule_vs_engineer --export .../export/rev-5.json \\
        --out $BASIS_EVAL_DATA_DIR/predictions/schedule_vs_engineer_<date> \\
        [--rows LABELS/e2e_rows_v1.json] [--report LABELS/engineer_report_v1.json] \\
        [--engineer-pdf INPUTS/engineer_report.pdf] [--stored-schedule .../rev-5.schedule.csv] \\
        [--stored-report .../rev-5.report.pdf]

Regression: the schedule built now is compared byte for byte with the stored
``rev-<n>.schedule.csv`` and ``.xlsx``, and each class row of the stored report's section 8
("Depreciation schedule": basis, lives, method, convention, in service, bonus default) is
looked for on a text line of the stored ``rev-<n>.report.pdf``
(:func:`report_pdf_regression`). :func:`engineer_choices` states whether the engineer's
report prints anything (a method, a convention, a bonus rate, a first-year figure) from
which its elections could be identified; the rule sets are computed on both sides either way.

The run directory gets ``comparison.json`` (every row, with dollars: it lives in the private
data directory), ``comparison.md`` (shares and percentages only, for the public report) and
the run record of :mod:`evals.record` (one case per engineer row, per class share, per
rule set's first-year total, the 40 % test and the regression against the stored schedule),
filed under ``preparer_schedule`` in the verification tree.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Literal

from basis.db.enums import Bucket
from basis.report.model import CLASS_ORDER, TEMPLATE_VERSION, StudyReport, build_report
from basis.report.schedule import COLUMNS, rows, to_csv, to_xlsx
from basis.report.tax import bonus_default, terms
from evals import record
from evals.e2e_report import (
    E2ERows,
    EngineerReport,
    assign_assets,
    default_labels_path,
    export_from_document,
    load_report,
    load_rows,
)

COMPONENT: Final = "preparer_schedule"
ZERO: Final = Decimal(0)
SHARE: Final = Decimal("0.0001")
CENT: Final = Decimal("0.01")
# A class share or a first-year total within this many percentage points of the engineer's
# counts as agreeing (the engineer prints class percentages to tenths).
POINTS_TOLERANCE: Final = Decimal("1.0")
MID_QUARTER_THRESHOLD: Final = Decimal("0.40")

Convention = Literal["HY", "MQ", "MM"]

# Pub 946 (2025), Appendix A, year-1 rates (percent) by recovery period.
HALF_YEAR_YEAR1: Final[dict[str, Decimal]] = {  # Table A-1
    "5": Decimal("20.00"),
    "7": Decimal("14.29"),
    "15": Decimal("5.00"),
}
MID_QUARTER_YEAR1: Final[dict[int, dict[str, Decimal]]] = {  # Tables A-2, A-3, A-4, A-5
    1: {"5": Decimal("35.00"), "7": Decimal("25.00"), "15": Decimal("8.75")},
    2: {"5": Decimal("25.00"), "7": Decimal("17.85"), "15": Decimal("6.25")},
    3: {"5": Decimal("15.00"), "7": Decimal("10.71"), "15": Decimal("3.75")},
    4: {"5": Decimal("5.00"), "7": Decimal("3.57"), "15": Decimal("1.25")},
}
RESIDENTIAL_YEAR1: Final[tuple[Decimal, ...]] = tuple(  # Table A-6, months 1-12
    Decimal(v)
    for v in (
        "3.485", "3.182", "2.879", "2.576", "2.273", "1.970",
        "1.667", "1.364", "1.061", "0.758", "0.455", "0.152",
    )
)  # fmt: skip
NONRESIDENTIAL_YEAR1: Final[tuple[Decimal, ...]] = tuple(  # Table A-7a, months 1-12
    Decimal(v)
    for v in (
        "2.461", "2.247", "2.033", "1.819", "1.605", "1.391",
        "1.177", "0.963", "0.749", "0.535", "0.321", "0.107",
    )
)  # fmt: skip
REAL_PROPERTY: Final = frozenset({"27.5", "39"})


@dataclass(frozen=True, slots=True)
class RuleSet:
    name: str
    title: str
    bonus: Literal["default", "none"]
    convention: Literal["default", "mid_quarter"]


RULE_SETS: Final[tuple[RuleSet, ...]] = (
    RuleSet(
        "statutory", "statutory bonus default, half-year (what Basis prints)", "default", "default"
    ),
    RuleSet("no_bonus", "elected out of bonus (168(k)(7)), half-year", "none", "default"),
    RuleSet(
        "mid_quarter",
        "statutory bonus default, mid-quarter (40 % test met)",
        "default",
        "mid_quarter",
    ),
    RuleSet("no_bonus_mid_quarter", "elected out of bonus, mid-quarter", "none", "mid_quarter"),
)


# ------------------------------------------------------------------ the IRS arithmetic


def quarter(day: date) -> int:
    """The calendar quarter of a date, 1 to 4."""
    return (day.month - 1) // 3 + 1


def year1_rate(period: str, placed: date, convention: Convention) -> Decimal:
    """The Appendix A year-1 rate (a fraction) for a recovery period placed in service on
    ``placed`` under ``convention``: half-year (A-1), mid-quarter by quarter (A-2 to A-5),
    mid-month by month (A-6 for 27.5 years, A-7a for 39)."""
    if period in REAL_PROPERTY:
        table = RESIDENTIAL_YEAR1 if period == "27.5" else NONRESIDENTIAL_YEAR1
        return table[placed.month - 1] / 100
    if convention == "MM":
        raise ValueError(f"the mid-month convention does not apply to {period}-year property")
    if convention == "MQ":
        return MID_QUARTER_YEAR1[quarter(placed)][period] / 100
    return HALF_YEAR_YEAR1[period] / 100


def first_year_share(
    period: str, placed: date, bonus: Decimal | None, convention: Convention
) -> Decimal:
    """First-year depreciation as a fraction of cost: the special depreciation allowance
    (``bonus``, a fraction, ``None`` or 0 when not claimed or not eligible) plus the year-1
    rate on the remaining basis (Pub 946 chapter 3, "How Much Can You Deduct?")."""
    rate = year1_rate(period, placed, convention)
    b = bonus if bonus is not None and period not in REAL_PROPERTY else ZERO
    return b + (1 - b) * rate


def convention_for(period: str, rule: RuleSet) -> Convention:
    if period in REAL_PROPERTY:
        return "MM"
    return "MQ" if rule.convention == "mid_quarter" else "HY"


# ------------------------------------------------------------------ Basis's schedule


@dataclass(frozen=True, slots=True)
class ScheduleRow:
    """One row of the schedule Basis ships, as the CSV prints it plus the asset ids of the
    report line it came from."""

    line: int
    description: str
    asset_ids: tuple[str, ...]
    period: str
    method: str
    convention: str
    bonus_rate: Decimal | None
    basis: Decimal
    acquired: date | None
    placed: date


def basis_schedule(document: Mapping[str, Any]) -> tuple[StudyReport, tuple[ScheduleRow, ...]]:
    """The report and its schedule rows from an export, through the production code."""
    report = build_report(document)
    by_name = {name: i for i, name in enumerate(COLUMNS)}
    out: list[ScheduleRow] = []
    for item, row in zip(report.items, rows(report), strict=True):
        v = row.values
        if int(v[by_name["line"]]) != item.line:
            raise ValueError("the schedule rows and the report lines are out of step")
        bonus = v[by_name["statutory_bonus_rate"]]
        acquired = v[by_name["date_acquired"]]
        out.append(
            ScheduleRow(
                line=item.line,
                description=v[by_name["description"]],
                asset_ids=item.asset_ids,
                period=v[by_name["recovery_period"]],
                method=v[by_name["method"]],
                convention=v[by_name["convention"]],
                bonus_rate=Decimal(bonus) if bonus else None,
                basis=row.basis,
                acquired=date.fromisoformat(acquired) if acquired else None,
                placed=date.fromisoformat(v[by_name["date_placed_in_service"]]),
            )
        )
    return report, tuple(out)


def row_first_year(row: ScheduleRow, rule: RuleSet) -> Decimal:
    bonus = row.bonus_rate if rule.bonus == "default" else None
    return row.basis * first_year_share(
        row.period, row.placed, bonus, convention_for(row.period, rule)
    )


@dataclass(frozen=True, slots=True)
class MidQuarterTest:
    """The 40 % test on a set of rows: the depreciable basis of property other than real
    property placed in service in the last three months of the (calendar) year, over the
    year's; ``applies`` when the share exceeds 40 %."""

    last_quarter_basis: Decimal
    year_basis: Decimal
    share: Decimal | None
    applies: bool


def mid_quarter_test(items: Sequence[tuple[str, date, Decimal]]) -> MidQuarterTest:
    """``items``: ``(period, placed, basis)`` per row, before any bonus; real property is
    left out (Pub 946 chapter 4, "Which Convention Applies?")."""
    year = sum((b for p, _, b in items if p not in REAL_PROPERTY), ZERO)
    last = sum((b for p, d, b in items if p not in REAL_PROPERTY and quarter(d) == 4), ZERO)
    share = (last / year).quantize(SHARE) if year else None
    return MidQuarterTest(last, year, share, share is not None and share > MID_QUARTER_THRESHOLD)


# ------------------------------------------------------------------ the engineer's PDF

SCHEDULE_TERMS: Final[tuple[str, ...]] = (
    "recovery period",
    "method",
    "convention",
    "half-year",
    "mid-quarter",
    "mid-month",
    "bonus",
    "168(k)",
    "special depreciation",
    "placed in service",
    "acquired",
    "acquisition date",
    "first year",
    "macrs",
    "200",
    "150",
    "straight line",
    "depreciation",
    "depreciable",
    "5 year",
    "7 year",
    "15 year",
    "39 year",
    "27.5 year",
)


def engineer_pdf_terms(path: Path) -> dict[str, Any]:
    """Which schedule terms the engineer's report prints, from its text layer: pages with
    text, pages without (images), and the pages each term appears on. No figure is kept."""
    import pdfplumber

    found: dict[str, list[int]] = {t: [] for t in SCHEDULE_TERMS}
    text_pages: list[int] = []
    image_pages: list[int] = []
    with pdfplumber.open(str(path)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").lower()
            (text_pages if text.strip() else image_pages).append(number)
            for term in SCHEDULE_TERMS:
                if term in text:
                    found[term].append(number)
    return {
        "pages": len(text_pages) + len(image_pages),
        "text_pages": text_pages,
        "image_pages": image_pages,
        "terms_found": {t: p for t, p in found.items() if p},
        "terms_missing": [t for t, p in found.items() if not p],
    }


# ------------------------------------------------------------------ the comparison


@dataclass
class RowComparison:
    row_id: str
    system: str
    kind: str
    engineer_period: str
    engineer_cost: Decimal
    engineer_share: Decimal  # of the engineer's total
    lines: list[int] = field(default_factory=list)
    basis_total: Decimal = ZERO
    basis_share: Decimal = ZERO  # of Basis's depreciable basis
    tally_dollars: dict[str, Decimal] = field(default_factory=dict)
    tally_count: dict[str, int] = field(default_factory=dict)
    basis_period: str | None = None  # by allocated dollars, ties by count then shorter period
    period_match: bool | None = None
    # Basis's row terms (as printed) against the Pub 946 default of the engineer's period.
    basis_method: str | None = None
    expected_method: str = ""
    basis_convention: str | None = None
    expected_convention: str = ""
    basis_bonus: Decimal | None = None
    expected_bonus: Decimal | None = None
    first_year_basis: dict[str, Decimal] = field(default_factory=dict)  # rule -> share of cost
    first_year_engineer: dict[str, Decimal] = field(default_factory=dict)
    reason: str = ""

    @property
    def matched(self) -> bool:
        return bool(self.lines)


@dataclass
class Comparison:
    study_id: str
    revision: int
    acquired: date | None
    placed: date
    depreciable_basis: Decimal
    engineer_total: Decimal
    rows: list[RowComparison]
    unmatched_lines: list[int]
    unmatched_basis: Decimal
    class_share_basis: dict[str, Decimal]
    class_share_engineer: dict[str, Decimal]
    first_year_total_basis: dict[str, Decimal]  # rule -> share of basis
    first_year_total_engineer: dict[str, Decimal]
    mid_quarter_basis: MidQuarterTest
    mid_quarter_engineer: MidQuarterTest
    printed_convention: str  # what Basis prints on its personal-property rows
    pdf_terms: dict[str, Any] | None = None


def _period_of(bucket: str) -> str:
    return f"{terms(bucket).gds_life.normalize():f}"


def _share(part: Decimal, whole: Decimal) -> Decimal:
    return (part / whole).quantize(SHARE) if whole else ZERO


def _bucket_by_dollars(
    lines: Sequence[ScheduleRow],
) -> tuple[str, dict[str, Decimal], dict[str, int]]:
    dollars: dict[str, Decimal] = {}
    counts: Counter[str] = Counter()
    for line in lines:
        dollars[line.period] = dollars.get(line.period, ZERO) + line.basis
        counts[line.period] += 1
    order = [_period_of(b) for b in CLASS_ORDER]
    best = max(dollars, key=lambda p: (dollars[p], counts[p], -order.index(p)))
    return best, dollars, dict(counts)


def compare(
    document: Mapping[str, Any],
    e2e_rows: E2ERows,
    engineer: EngineerReport,
    *,
    pdf_terms: dict[str, Any] | None = None,
) -> Comparison:
    report, schedule = basis_schedule(document)
    export = export_from_document(document)
    assignment = assign_assets(e2e_rows, export.assets)
    row_of_asset = {a.id: row_id for row_id, assets in assignment.by_row.items() for a in assets}
    by_row: dict[str, list[ScheduleRow]] = {r.id: [] for r in engineer.rows}
    unmatched: list[ScheduleRow] = []
    for line in schedule:
        row_id = row_of_asset.get(line.asset_ids[0])
        (by_row[row_id] if row_id is not None else unmatched).append(line)

    out: list[RowComparison] = []
    for e_row in engineer.rows:
        kind = e2e_rows.row(e_row.id).kind
        period = _period_of(e_row.bucket)
        placed = report.schedule[0].placed_in_service
        c = RowComparison(
            row_id=e_row.id,
            system=e_row.system,
            kind=kind,
            engineer_period=period,
            engineer_cost=e_row.cost,
            engineer_share=_share(e_row.cost, engineer.total),
            expected_method=terms(e_row.bucket).method,
            expected_convention=terms(e_row.bucket).convention,
        )
        if terms(Bucket(e_row.bucket)).bonus_eligible:
            # The statutory default for the export's dates, whatever class Basis gave the row.
            c.expected_bonus = bonus_default(report.schedule[0].acquired, placed).rate
        for rule in RULE_SETS:
            bonus = c.expected_bonus if rule.bonus == "default" else None
            c.first_year_engineer[rule.name] = first_year_share(
                period, placed, bonus, convention_for(period, rule)
            ).quantize(SHARE)
        lines = by_row[e_row.id]
        if not lines:
            c.reason = (
                "kind C (concealed): no structure-model line charged"
                if kind == "C"
                else "vocabulary gap: no pipeline label is assigned to this row"
                if e2e_rows.row(e_row.id).vocabulary_gap
                else "no Basis line charged (the row was missed)"
            )
            out.append(c)
            continue
        c.lines = [line.line for line in lines]
        c.basis_total = sum((line.basis for line in lines), ZERO)
        c.basis_share = _share(c.basis_total, report.depreciable_basis)
        c.basis_period, c.tally_dollars, c.tally_count = _bucket_by_dollars(lines)
        c.period_match = c.basis_period == period
        majority = [line for line in lines if line.period == c.basis_period]
        c.basis_method = majority[0].method
        c.basis_convention = majority[0].convention
        c.basis_bonus = majority[0].bonus_rate
        for rule in RULE_SETS:
            fy = sum((row_first_year(line, rule) for line in lines), ZERO)
            c.first_year_basis[rule.name] = _share(fy, c.basis_total)
        others = sorted(p for p in c.tally_count if p != c.basis_period)
        if c.period_match and others:
            minority = sum((c.tally_dollars[p] for p in others), ZERO) / c.basis_total
            c.reason = (
                "same recovery period for the row; "
                + ", ".join(f"{c.tally_count[p]} line(s) in {p}-year" for p in others)
                + f" ({_pct(minority)} of the row's dollars): a classification difference on "
                "those lines, not a schedule-table one"
            )
        elif c.period_match:
            c.reason = "same recovery period; method, convention and bonus follow from it"
        else:
            c.reason = (
                f"Basis puts {_pct(c.tally_dollars.get(c.basis_period, ZERO) / c.basis_total)} of "
                f"the row's dollars in {c.basis_period}-year, the engineer the whole row in "
                f"{period}-year: a classification difference (bucket), not a schedule-table one"
            )
        out.append(c)

    class_basis = {c.name: c.share for c in report.classes}
    class_engineer = {
        terms(b).name: _share(t, engineer.total) for b, t in engineer.bucket_totals.items()
    }
    fy_basis = {
        rule.name: _share(
            sum((row_first_year(line, rule) for line in schedule), ZERO), report.depreciable_basis
        )
        for rule in RULE_SETS
    }
    fy_engineer: dict[str, Decimal] = {}
    for rule in RULE_SETS:
        total = ZERO
        for c in out:
            total += c.engineer_cost * c.first_year_engineer[rule.name]
        fy_engineer[rule.name] = _share(total, engineer.total)
    personal = [line for line in schedule if line.period not in REAL_PROPERTY]
    printed = sorted({line.convention for line in personal}) or ["none"]
    return Comparison(
        study_id=report.study_id,
        revision=report.revision,
        acquired=report.schedule[0].acquired,
        placed=report.schedule[0].placed_in_service,
        depreciable_basis=report.depreciable_basis,
        engineer_total=engineer.total,
        rows=out,
        unmatched_lines=[line.line for line in unmatched],
        unmatched_basis=sum((line.basis for line in unmatched), ZERO),
        class_share_basis=class_basis,
        class_share_engineer=class_engineer,
        first_year_total_basis=fy_basis,
        first_year_total_engineer=fy_engineer,
        mid_quarter_basis=mid_quarter_test([(s.period, s.placed, s.basis) for s in schedule]),
        mid_quarter_engineer=mid_quarter_test(
            [
                (r.engineer_period, report.schedule[0].placed_in_service, r.engineer_cost)
                for r in out
            ]
        ),
        printed_convention="/".join(printed),
        pdf_terms=pdf_terms,
    )


# ------------------------------------------------------------------ rendering


def _pct(value: Decimal | None, places: int = 2) -> str:
    return "n/a" if value is None else f"{(value * 100).quantize(Decimal(10) ** -places)} %"


def _points(a: Decimal, b: Decimal) -> Decimal:
    return ((a - b) * 100).quantize(Decimal("0.01"))


def render_markdown(c: Comparison) -> str:
    """The comparison as tables with shares and percentages only (no dollar figure)."""
    lines = [
        "# Basis's preparer schedule against the engineer's",
        "",
        f"Study `{c.study_id[:8]}` revision {c.revision}; acquired {c.acquired}, placed in "
        f"service {c.placed} (the export's dates; the engineer's report prints none).",
        f"Basis lines charged to an engineer row: "
        f"{sum(len(r.lines) for r in c.rows)}; not charged: {len(c.unmatched_lines)} "
        f"({_pct(_share(c.unmatched_basis, c.depreciable_basis))} of the basis).",
        "",
        "## Per class",
        "",
        "| Class | Basis share of basis | Engineer share of total | Difference (points) |",
        "|---|---|---|---|",
    ]
    for name in dict.fromkeys([*c.class_share_basis, *c.class_share_engineer]):
        a = c.class_share_basis.get(name, ZERO)
        b = c.class_share_engineer.get(name, ZERO)
        lines.append(f"| {name} | {_pct(a)} | {_pct(b)} | {_points(a, b):+} |")
    lines += [
        "",
        "## First-year depreciation as a share of basis, by rule set",
        "",
        "| Rule set | Basis | Engineer (implied) | Difference (points) |",
        "|---|---|---|---|",
    ]
    for rule in RULE_SETS:
        a = c.first_year_total_basis[rule.name]
        b = c.first_year_total_engineer[rule.name]
        lines.append(f"| {rule.name} · {rule.title} | {_pct(a)} | {_pct(b)} | {_points(a, b):+} |")
    mq_b, mq_e = c.mid_quarter_basis, c.mid_quarter_engineer
    lines += [
        "",
        "## The mid-quarter 40 % test, on this study's rows",
        "",
        "| Side | Basis placed in the last quarter, of the year's (non-real property) "
        "| Mid-quarter applies | Convention printed |",
        "|---|---|---|---|",
        f"| Basis | {_pct(mq_b.share)} | {'yes' if mq_b.applies else 'no'} "
        f"| {c.printed_convention} |",
        f"| Engineer | {_pct(mq_e.share)} | {'yes' if mq_e.applies else 'no'} | not printed |",
        "",
        "## Per engineer row",
        "",
        "| Row | System | Kind | Engineer period | Basis period (by dollars) | Lines "
        "| Engineer share | Basis share | Method (Basis / expected) | Convention | Bonus "
        "| First year, statutory (Basis / engineer) | First year, mid-quarter (Basis / engineer) "
        "| Note |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in c.rows:
        fy_e_s, fy_e_m = r.first_year_engineer["statutory"], r.first_year_engineer["mid_quarter"]
        if r.matched:
            fy_s = f"{_pct(r.first_year_basis['statutory'])} / {_pct(fy_e_s)}"
            fy_m = f"{_pct(r.first_year_basis['mid_quarter'])} / {_pct(fy_e_m)}"
            tally = ", ".join(f"{p}-yr {n}" for p, n in sorted(r.tally_count.items()))
            period = f"{r.basis_period} ({tally})"
            method = f"{r.basis_method} / {r.expected_method}"
            convention = f"{r.basis_convention} / {r.expected_convention}"
            bonus = f"{_pct(r.basis_bonus, 0) if r.basis_bonus is not None else 'none'} / " + (
                _pct(r.expected_bonus, 0) if r.expected_bonus is not None else "none"
            )
        else:
            fy_s, fy_m = f"- / {_pct(fy_e_s)}", f"- / {_pct(fy_e_m)}"
            period, method, convention, bonus = "-", "-", "-", "-"
        basis_share = _pct(r.basis_share) if r.matched else "-"
        lines.append(
            f"| {r.row_id} | {r.system} | {r.kind} | {r.engineer_period} | {period} | "
            f"{len(r.lines)} | {_pct(r.engineer_share)} | {basis_share} | "
            f"{method} | {convention} | {bonus} | {fy_s} | {fy_m} | {r.reason} |"
        )
    if c.pdf_terms:
        t = c.pdf_terms
        found = ", ".join(
            f"`{k}` (p. {', '.join(map(str, v))})" for k, v in t["terms_found"].items()
        )
        lines += [
            "",
            "## What the engineer's PDF prints (text layer)",
            "",
            f"{t['pages']} pages: text on {t['text_pages']}, images only on {t['image_pages']}.",
            f"Terms found: {found or 'none'}.",
            "Terms missing: " + ", ".join(f"`{k}`" for k in t["terms_missing"]) + ".",
            "",
            f"The engineer's elections: {engineer_choices(t)['reason']}.",
        ]
    return "\n".join(lines) + "\n"


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def to_json(c: Comparison) -> dict[str, Any]:
    out = asdict(c)
    return {str(k): _plain(v) for k, v in out.items()}


# ------------------------------------------------------------------ the run


def _cases(run: record.EvalRun, c: Comparison) -> None:
    for r in c.rows:
        if not r.matched:
            run.case(r.row_id, "failed", kind="row", detail=f"{r.system}: {r.reason}")
        else:
            run.case(
                r.row_id,
                "passed" if r.period_match else "failed",
                kind="row",
                detail=f"{r.system}: engineer {r.engineer_period}-year, Basis "
                f"{r.basis_period}-year; {r.reason}",
            )
    for name in dict.fromkeys([*c.class_share_basis, *c.class_share_engineer]):
        diff = _points(c.class_share_basis.get(name, ZERO), c.class_share_engineer.get(name, ZERO))
        run.case(
            f"class_share:{name}",
            "passed" if abs(diff) <= POINTS_TOLERANCE else "failed",
            kind="class_share",
            detail=f"Basis {_pct(c.class_share_basis.get(name, ZERO))}, engineer "
            f"{_pct(c.class_share_engineer.get(name, ZERO))}, {diff:+} points "
            f"(tolerance {POINTS_TOLERANCE})",
        )
    for rule in RULE_SETS:
        diff = _points(c.first_year_total_basis[rule.name], c.first_year_total_engineer[rule.name])
        run.case(
            f"first_year_total:{rule.name}",
            "passed" if abs(diff) <= POINTS_TOLERANCE else "failed",
            kind="first_year_total",
            detail=f"Basis {_pct(c.first_year_total_basis[rule.name])}, engineer (implied) "
            f"{_pct(c.first_year_total_engineer[rule.name])}, {diff:+} points",
        )
    expected = "MQ" if c.mid_quarter_basis.applies else "HY"
    run.case(
        "mid_quarter_test",
        "passed" if c.printed_convention == expected else "failed",
        kind="convention",
        detail=f"on this study's rows the last-quarter share is {_pct(c.mid_quarter_basis.share)} "
        f"(engineer's rows, same date: {_pct(c.mid_quarter_engineer.share)}), so the test says "
        f"{expected} if these are the taxpayer's only additions that year; Basis prints "
        f"{c.printed_convention} on every 5, 7 and 15-year row (basis/report/tax.py TERMS, "
        "printed by basis/report/schedule.py rows) with the generic preparer's note",
    )


def regression(
    document: Mapping[str, Any], stored_csv: Path, stored_xlsx: Path | None
) -> dict[str, Any]:
    """The schedule produced now against the one the export step stored, byte for byte."""
    report = build_report(document)
    schedule = rows(report)
    csv_now = to_csv(schedule)
    result: dict[str, Any] = {
        "csv_equal": csv_now == stored_csv.read_bytes(),
        "rows": len(schedule),
    }
    if stored_xlsx is not None and stored_xlsx.is_file():
        result["xlsx_equal"] = to_xlsx(schedule) == stored_xlsx.read_bytes()
    return result


def _squash(text: str) -> str:
    return " ".join(text.split())


def report_pdf_regression(document: Mapping[str, Any], stored_pdf: Path) -> dict[str, Any]:
    """The class table of section 8 ("Depreciation schedule") of the stored report PDF against
    the report built now: for each class, the printed run "basis, GDS / ADS life, method,
    convention, in service, bonus default" (as :func:`basis.report.render.view` formats it)
    must appear on one text line of the PDF. Only class names and booleans are returned."""
    import pdfplumber

    from basis.report.render import view

    entries = view(build_report(document))["schedule"]
    with pdfplumber.open(str(stored_pdf)) as pdf:
        lines = [
            _squash(line) for page in pdf.pages for line in (page.extract_text() or "").splitlines()
        ]
    missing: list[str] = []
    for e in entries:
        run = _squash(
            f"{e['basis']} {e['life']} / {e['ads']} {e['method']} {e['convention']} "
            f"{e['placed']} {e['bonus']}"
        )
        if not any(run in line for line in lines):
            missing.append(e["name"])
    return {"classes": len(entries), "found": len(entries) - len(missing), "missing": missing}


# Terms that would let the engineer's elections be read from the report: a method, a
# convention, a bonus rate or a first-year figure. Recovery-period headers are not enough.
ELECTION_TERMS: Final = (
    "method",
    "convention",
    "half-year",
    "mid-quarter",
    "bonus",
    "168(k)",
    "special depreciation",
    "first year",
    "depreciation",
    "placed in service",
)


def engineer_choices(pdf_terms: Mapping[str, Any] | None) -> dict[str, Any]:
    """Whether the engineer's report prints anything from which its bonus election and its
    convention can be identified (a term of :data:`ELECTION_TERMS` in the text layer)."""
    if pdf_terms is None:
        return {"identifiable": None, "printed": [], "reason": "the engineer's PDF was not read"}
    printed = [t for t in ELECTION_TERMS if t in pdf_terms["terms_found"]]
    return {
        "identifiable": bool(printed),
        "printed": printed,
        "reason": (
            "the report prints " + ", ".join(printed)
            if printed
            else "the report prints recovery-period columns and costs only: no method, "
            "convention, bonus, date or first-year figure, so no rule set can be told from "
            "another on the engineer's side"
        ),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals.schedule_vs_engineer", description=__doc__
    )
    parser.add_argument(
        "--export", type=Path, required=True, help="the study export (rev-<n>.json)"
    )
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    parser.add_argument("--rows", type=Path, default=None, help="e2e_rows_v1.json")
    parser.add_argument("--report", type=Path, default=None, help="engineer_report_v1.json")
    parser.add_argument("--engineer-pdf", type=Path, default=None, help="the engineer's report PDF")
    parser.add_argument(
        "--stored-schedule",
        type=Path,
        default=None,
        help="the rev-<n>.schedule.csv the export step wrote (default: next to the export)",
    )
    parser.add_argument(
        "--stored-report",
        type=Path,
        default=None,
        help="the rev-<n>.report.pdf the export step wrote (default: next to the export)",
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--tree", type=Path, default=None, help="the verification tree root")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    t0 = time.monotonic()
    run = record.EvalRun.begin(
        args.out,
        harness=__name__,
        args=args,
        component=COMPONENT,
        version=f"template-{TEMPLATE_VERSION}",
        tree=args.tree,
    )
    try:
        rows_path = args.rows or default_labels_path("e2e_rows_v1.json", args.data_dir)
        report_path = args.report or default_labels_path("engineer_report_v1.json", args.data_dir)
        run.input("export", args.export)
        run.dataset(rows_path)
        run.dataset(report_path)
        document = json.loads(args.export.read_text(encoding="utf-8"))
        pdf_terms = None
        if args.engineer_pdf is not None:
            run.input("engineer_pdf", args.engineer_pdf)
            pdf_terms = engineer_pdf_terms(args.engineer_pdf)
        comparison = compare(
            document, load_rows(rows_path), load_report(report_path), pdf_terms=pdf_terms
        )
        _cases(run, comparison)
        stored = args.stored_schedule
        if stored is None:
            stem = args.export.name.removesuffix(".json")
            stored = args.export.with_name(f"{stem}.schedule.csv")
        reg: dict[str, Any] | None = None
        if stored.is_file():
            run.input("stored_schedule", stored)
            reg = regression(document, stored, stored.with_suffix(".xlsx"))
            run.case(
                "regression:stored_schedule",
                "passed" if reg["csv_equal"] and reg.get("xlsx_equal", True) else "failed",
                kind="regression",
                detail=f"{reg['rows']} rows; csv equal {reg['csv_equal']}, "
                f"xlsx equal {reg.get('xlsx_equal', 'not stored')}",
            )
        stored_pdf = args.stored_report
        if stored_pdf is None:
            stem = args.export.name.removesuffix(".json")
            stored_pdf = args.export.with_name(f"{stem}.report.pdf")
        pdf_reg: dict[str, Any] | None = None
        if stored_pdf.is_file():
            run.input("stored_report", stored_pdf)
            pdf_reg = report_pdf_regression(document, stored_pdf)
            run.case(
                "regression:stored_report_schedule",
                "passed" if not pdf_reg["missing"] else "failed",
                kind="regression",
                detail=f"section 8 of the stored report: {pdf_reg['found']} of "
                f"{pdf_reg['classes']} class rows printed as built now"
                + (f"; missing {', '.join(pdf_reg['missing'])}" if pdf_reg["missing"] else ""),
            )
        choices = engineer_choices(pdf_terms)
        (args.out / "comparison.json").write_text(
            json.dumps(to_json(comparison), indent=1) + "\n", encoding="utf-8"
        )
        (args.out / "comparison.md").write_text(render_markdown(comparison), encoding="utf-8")
        matched = [r for r in comparison.rows if r.matched]
        summary = {
            "study_id": comparison.study_id,
            "revision": comparison.revision,
            "rows": len(comparison.rows),
            "matched": len(matched),
            "period_right": sum(1 for r in matched if r.period_match),
            "class_share_basis": _plain(comparison.class_share_basis),
            "class_share_engineer": _plain(comparison.class_share_engineer),
            "first_year_total_basis": _plain(comparison.first_year_total_basis),
            "first_year_total_engineer": _plain(comparison.first_year_total_engineer),
            "mid_quarter_share_basis": _plain(comparison.mid_quarter_basis.share),
            "mid_quarter_applies": comparison.mid_quarter_basis.applies,
            "printed_convention": comparison.printed_convention,
            "regression": reg,
            "report_regression": pdf_reg,
            "engineer_choices": choices,
            "pdf_terms": pdf_terms,
            "wall_seconds": round(time.monotonic() - t0, 3),
            "spent_usd": 0.0,
        }
    except BaseException as exc:
        record.finish_pending(args, exit_code=1, error=exc)
        raise
    run.finish(summary)
    print(
        f"wrote {args.out}: {summary['period_right']} of {summary['matched']} matched rows in the "
        f"engineer's period ({summary['rows']} rows); {summary['wall_seconds']} s",
        file=sys.stderr,
    )
    return 0


__all__ = [
    "COMPONENT",
    "ELECTION_TERMS",
    "HALF_YEAR_YEAR1",
    "MID_QUARTER_YEAR1",
    "NONRESIDENTIAL_YEAR1",
    "RESIDENTIAL_YEAR1",
    "RULE_SETS",
    "Comparison",
    "MidQuarterTest",
    "RowComparison",
    "RuleSet",
    "ScheduleRow",
    "basis_schedule",
    "compare",
    "convention_for",
    "engineer_choices",
    "engineer_pdf_terms",
    "first_year_share",
    "main",
    "mid_quarter_test",
    "quarter",
    "regression",
    "render_markdown",
    "report_pdf_regression",
    "to_json",
    "year1_rate",
]


if __name__ == "__main__":
    raise SystemExit(main())
