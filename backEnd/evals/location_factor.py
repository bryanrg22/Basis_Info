"""``python -m evals.location_factor``: the RSMeans location factor resolved several ways on
stored exports, each replayed and scored the same way (the developer page's gap: "the
ZIP-level factor never ran on a study"; EVAL_REPORT sections 45 and 47 priced both live
studies on the state mean).

Every priced dollar is the national-average unit cost times a year factor and a location
factor (``basis.domain.cost_indices``, docs/review_2026-10-15/COST_INDICES.md). The product
resolves the location factor as the city row of the ZIP's first three digits when a ZIP is
known, else the mean of the state's listed cities. This runner reprices an export under
each resolution and replays the structure model on it (``evals.reprice_export`` with a
caller-chosen factor, then ``evals.structure_replay`` through ``evals.structure_sweep.run_cell``
and ``evals.e2e_report``), with the production pricing (installed cost, the general
contractor's O&P pro rata; EVAL_REPORT 49.3):

* ``state_mean``: the mean of the state's listed cities, today's result and the regression
  check (``--regression`` compares it with a stored ``gap_sweep.json``'s production cell);
* ``zip_city``: the city row of the subject's three-digit ZIP prefix (the intake's ZIP, else
  the appraisal's ``subject.zip`` field); when the prefix is not in the table the code's
  own fallback, the state mean, is what runs, and the setup says so;
* ``nearest_prefix``: the book's rule for a city not listed ("use the factor for a nearby
  city"), read as the nearest listed prefix of the same state by prefix number; identical to
  ``zip_city`` when the prefix is listed, which the setup says;
* ``state_median``: the median of the state's listed cities instead of the mean;
* ``national``: 1.000, no location adjustment (the control);
* ``zip_city:<label>``: one more city row per ``--also-zip LABEL=ZIP``.

Per setup and export: the factor applied, the itemized priced total relative to the state
mean, the gap to the price and whether it balances at 10 %, the tax-class shares and their
error against the engineer's, the row-share distance, the photo-visible regression line and
the wall time. The state's spread (cities above and below the mean, the median, the range)
is reported with it, and the fallback check: each listed city's row hidden in turn, how far
the state mean and the nearest other prefix land from it (the state and every state).
Deterministic, no model call, cost $0. Usage (from ``backEnd/``)::

    BASIS_EVAL_DATA_DIR=... python -m evals.location_factor export_a.json export_b.json \\
        --cost-table costs/unit_costs_rsmeans_2020.json --supplement hci.json \\
        [--zip 90001] [--state CA] [--also-zip city=90210] \\
        [--regression work/.../gap_sweep.json] --out <run directory>

The run directory holds ``run.json`` (the setups, every cell, the spread, the regression
result), ``location_factor.md`` (the table), ``cells.json`` and the run record
(``evals.record``: manifest, cases, console log), filed under ``location_factor`` in the
verification tree. The table never prints a ZIP: a setup names the table row it resolved
to (city and factor), which is published reference data.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from statistics import median
from typing import Any, Final

from basis.domain.cost_indices import (
    LOCATION_QUANTUM,
    CityFactor,
    LocationTable,
    YearIndex,
    book_location_table,
    location_factor_for_state_code,
    location_factor_for_zip,
    supplement_for,
)
from basis.domain.cost_table import CostTable, load_cost_table
from basis.domain.costs import CostBasis
from basis.domain.measurements import measurements_from_fields
from basis.domain.structure import OverheadAllocation
from evals import record
from evals.e2e_report import E2ERows, EngineerReport, default_labels_path, load_report, load_rows
from evals.reprice_export import LocationChoice, _state_from, reprice
from evals.structure_replay import Variant, field_rows, takeoff_block
from evals.structure_sweep import Cell, baseline_scores, run_cell

COMPONENT: Final = "location_factor"
NATIONAL: Final = Decimal("1.000")
STATE_MEAN: Final = "state_mean"
ZIP_CITY: Final = "zip_city"
NEAREST_PREFIX: Final = "nearest_prefix"
STATE_MEDIAN: Final = "state_median"
NATIONAL_KEY: Final = "national"
# The production pricing (EVAL_REPORT 49.3): the itemized rows at the line's Total Incl O&P
# with the square-foot model's general contractor's O&P pro rata on the rows inside it.
PRODUCTION_COST_BASIS: Final[CostBasis] = "installed"
PRODUCTION_OVERHEAD: Final[OverheadAllocation] = "pro_rata"
# Section 49's production cell, as ``evals.gap_sweep`` names it.
PRODUCTION_CELL: Final = "installed+gc|square_foot|mods=on|spec=building|small=carved"
# The cell fields the regression check compares (what the table prints and what decides it).
REGRESSION_FIELDS: Final = (
    "status",
    "gap_share_pct",
    "implied_uniform_factor",
    "allocation_factor",
    "kind_c_produced",
    "kind_c_right",
    "bucket_share_pct",
    "bucket_error_pct",
    "mean_bucket_error_pct",
    "tvd_pct",
    "mean_row_error_pct",
    "visible_evidenced",
    "visible_right",
)


class LocationFactorError(ValueError):
    pass


@dataclass(frozen=True)
class Setup:
    """One way of resolving the location factor."""

    key: str
    title: str  # plain words for the table
    factor: Decimal
    source: str  # the location text written on every repriced row
    detail: str  # what the resolution found (prefix listed or absent, the row's state)
    row: str | None  # the table row applied, e.g. "Los Angeles 0.99", or None for a statistic


@dataclass(frozen=True)
class StateSpread:
    """The listed cities of a state against their mean: the "six points" check."""

    state: str
    cities: int
    mean: Decimal
    median: Decimal
    low: Decimal
    high: Decimal
    above_mean: int
    below_mean: int
    at_mean: int

    @property
    def spread_points(self) -> Decimal:
        """High less low, in points of the factor (0.94-1.07 is 13 points)."""
        return (self.high - self.low) * 100


@dataclass(frozen=True)
class Result:
    """One setup replayed on one export."""

    export: str
    setup: Setup
    cell: Cell
    priced_total: Decimal  # the itemized rows after repricing
    priced_total_ratio: Decimal | None  # against the export's state-mean setup
    reprice_counts: dict[str, int]
    wall_ms: float


# ------------------------------------------------------------------ resolution


def prefix_of(zip_code: str | None) -> str | None:
    """The first three digits of a ZIP, or None when it has fewer."""
    if not zip_code:
        return None
    digits = "".join(ch for ch in zip_code.strip() if ch.isdigit())
    return digits[:3] if len(digits) >= 3 else None


def state_spread(state: str, table: LocationTable | None = None) -> StateSpread:
    """The state's listed cities around the mean the product applies (three decimals)."""
    table = table or book_location_table()
    code = state.strip().upper()
    rows = table.by_state.get(code)
    if not rows:
        raise LocationFactorError(f"no location factors for state {code!r}")
    factors = [row.factor for row in rows]
    mean = (sum(factors, Decimal(0)) / len(factors)).quantize(
        LOCATION_QUANTUM, rounding=ROUND_HALF_UP
    )
    mid = Decimal(median(factors)).quantize(LOCATION_QUANTUM, rounding=ROUND_HALF_UP)
    return StateSpread(
        state=code,
        cities=len(rows),
        mean=mean,
        median=mid,
        low=min(factors),
        high=max(factors),
        above_mean=sum(1 for f in factors if f > mean),
        below_mean=sum(1 for f in factors if f < mean),
        at_mean=sum(1 for f in factors if f == mean),
    )


def nearest_listed_prefix(
    prefix: str, state: str, table: LocationTable | None = None
) -> tuple[str, CityFactor, bool] | None:
    """``(prefix, its row, absent)``: the listed prefix of ``state`` nearest to ``prefix`` by
    number (the prefix itself when listed, ``absent`` False; a tie goes to the lower
    prefix), or None when the state has no rows. The book's rule for a city not listed is
    "a nearby city with similar economic characteristics" (Residential 2020 p.718); the
    prefix number is the nearest proxy for "nearby" the table itself carries, since the
    Postal Service numbers sectional centers by region within a state."""
    table = table or book_location_table()
    code = state.strip().upper()
    rows = table.by_state.get(code)
    if not rows:
        return None
    listed = sorted((p, row) for row in rows for p in row.prefixes())
    for p, row in listed:
        if p == prefix:
            return p, row, False
    best = min(listed, key=lambda item: (abs(int(item[0]) - int(prefix)), item[0]))
    return best[0], best[1], True


def fallback_check(state: str, table: LocationTable | None = None) -> dict[str, Any]:
    """Which stand-in is closer to a listed city's own factor when its row is hidden, one
    row at a time: the mean of the state's other rows (today's fallback) or the nearest
    other listed prefix of the state (the nearby-city reading). Mean and worst absolute
    error in points of the factor over the state's rows; a state with one row has none."""
    table = table or book_location_table()
    code = state.strip().upper()
    rows = table.by_state.get(code)
    if not rows:
        raise LocationFactorError(f"no location factors for state {code!r}")
    listed = sorted((p, row) for row in rows for p in row.prefixes())
    mean_err: list[Decimal] = []
    near_err: list[Decimal] = []
    for row in rows:
        others = [(p, r) for p, r in listed if r is not row]
        if not others:
            continue
        other_rows = [r for r in rows if r is not row]
        loo_mean = (sum((r.factor for r in other_rows), Decimal(0)) / len(other_rows)).quantize(
            LOCATION_QUANTUM, rounding=ROUND_HALF_UP
        )
        own = row.prefixes()
        best = min(others, key=lambda item: (min(abs(int(item[0]) - int(p)) for p in own), item[0]))
        mean_err.append(abs(loo_mean - row.factor))
        near_err.append(abs(best[1].factor - row.factor))

    def points(values: list[Decimal], worst: bool = False) -> Decimal:
        if not values:
            return Decimal("0.00")
        value = max(values) if worst else sum(values, Decimal(0)) / len(values)
        return (value * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "state": code,
        "rows": len(near_err),
        "state_mean_mae_points": points(mean_err),
        "state_mean_worst_points": points(mean_err, worst=True),
        "nearest_prefix_mae_points": points(near_err),
        "nearest_prefix_worst_points": points(near_err, worst=True),
    }


def fallback_check_all(table: LocationTable | None = None) -> dict[str, Any]:
    """:func:`fallback_check` over every state with more than one row: the rows checked,
    each stand-in's mean error over all of them (points, weighted by rows), and in how many
    states the nearest listed prefix is the closer stand-in."""
    table = table or book_location_table()
    checks = [fallback_check(s, table) for s in sorted(table.by_state)]
    checks = [c for c in checks if c["rows"]]
    rows: int = sum(int(c["rows"]) for c in checks)

    def weighted(key: str) -> Decimal:
        if not rows:
            return Decimal("0.00")
        total: Decimal = sum((Decimal(c[key]) * int(c["rows"]) for c in checks), Decimal(0))
        return (total / rows).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "states": len(checks),
        "rows": rows,
        "state_mean_mae_points": weighted("state_mean_mae_points"),
        "nearest_prefix_mae_points": weighted("nearest_prefix_mae_points"),
        "states_nearest_closer": sum(
            1 for c in checks if c["nearest_prefix_mae_points"] < c["state_mean_mae_points"]
        ),
        "states_mean_closer": sum(
            1 for c in checks if c["state_mean_mae_points"] < c["nearest_prefix_mae_points"]
        ),
    }


def _row_text(city: CityFactor) -> str:
    return f"{city.city} {city.factor}"


def export_location(document: Mapping[str, Any]) -> tuple[str | None, str | None, str, str]:
    """``(state, zip, state origin, zip origin)`` read from an export: the worksheet (the
    intake), else the takeoff's location text for the state and the appraisal's
    ``subject.zip`` field for the ZIP, as the assets step orders its candidates."""
    summary = document.get("summary") or {}
    ws = (summary.get("basis") or {}).get("worksheet") or {}
    state = ws.get("state") or None
    state_origin = "intake"
    if not state:
        state = _state_from(takeoff_block(document).get("location_source"))
        state_origin = "export takeoff"
    zip_code = ws.get("zip") or None
    zip_origin = "intake"
    if not zip_code:
        read = measurements_from_fields(field_rows(document))
        if read.zip is not None:
            zip_code, zip_origin = read.zip.value, f"appraisal {read.zip.describe()}"
    return state, zip_code, state_origin, zip_origin


def setups(
    zip_code: str | None,
    state: str,
    *,
    zip_origin: str = "intake",
    extra_zips: Mapping[str, str] | None = None,
    table: LocationTable | None = None,
) -> list[Setup]:
    """The five resolutions (plus one city row per ``extra_zips`` label) for a subject with
    ``zip_code`` (or none) in ``state``."""
    table = table or book_location_table()
    code = state.strip().upper()
    spread = state_spread(code, table)
    mean = location_factor_for_state_code(code, table)
    if mean is None:  # pragma: no cover - state_spread raised first
        raise LocationFactorError(f"no location factors for state {code!r}")
    out: list[Setup] = [
        Setup(
            key=STATE_MEAN,
            title="the mean of the state's listed cities (today's result)",
            factor=mean.factor,
            source=f"state {code} (intake): {mean.citation()}",
            detail=f"{spread.cities} cities, {spread.low}-{spread.high}",
            row=None,
        )
    ]
    prefix = prefix_of(zip_code)
    city = location_factor_for_zip(zip_code, table) if zip_code else None
    if zip_code is None:
        detail = "no ZIP on the intake or the appraisal: the state mean is what runs"
        out.append(
            Setup(
                ZIP_CITY,
                "the city row of the subject's ZIP prefix",
                mean.factor,
                out[0].source,
                detail,
                None,
            )
        )
    elif city is None or city.city is None:
        detail = (
            "prefix absent from the table: the code falls back to the state mean"
            if prefix
            else "the ZIP has fewer than three digits: the code falls back to the state mean"
        )
        out.append(
            Setup(
                ZIP_CITY,
                "the city row of the subject's ZIP prefix",
                mean.factor,
                out[0].source,
                detail,
                None,
            )
        )
    else:
        where = (
            "prefix listed"
            if city.state == code
            else f"prefix listed under {city.state}, not {code}: no cross-check in the code"
        )
        out.append(
            Setup(
                ZIP_CITY,
                "the city row of the subject's ZIP prefix",
                city.factor,
                f"zip ({zip_origin}): {city.citation()}",
                where,
                _row_text(city.city),
            )
        )
    nearest = nearest_listed_prefix(prefix, code, table) if prefix else None
    if nearest is None:
        out.append(
            Setup(
                NEAREST_PREFIX,
                "the nearest listed prefix of the state (the book's nearby-city rule)",
                mean.factor,
                out[0].source,
                "no usable prefix: the state mean is what runs",
                None,
            )
        )
    else:
        near_prefix, near_row, absent = nearest
        out.append(
            Setup(
                NEAREST_PREFIX,
                "the nearest listed prefix of the state (the book's nearby-city rule)",
                near_row.factor,
                f"nearest listed prefix {near_prefix} of {code} ({zip_origin}): "
                f"{near_row.zip_prefixes} {near_row.city} {near_row.factor}, "
                f"{table.source} p.{near_row.printed_page}",
                (
                    f"prefix not among {code}'s listed prefixes: the nearest one applies"
                    if absent
                    else "prefix listed: the rule is not needed, identical to the city row"
                )
                + (
                    ""
                    if city is None or city.state == code
                    else f" (the ZIP's own row is in {city.state})"
                ),
                _row_text(near_row),
            )
        )
    out.append(
        Setup(
            STATE_MEDIAN,
            "the median of the state's listed cities",
            spread.median,
            f"state {code} (intake): median of {spread.cities} {code} cities "
            f"({spread.low}-{spread.high}), {table.source} pp.{table.pages}",
            f"{spread.above_mean} cities above the mean, {spread.below_mean} below",
            None,
        )
    )
    out.append(
        Setup(
            NATIONAL_KEY,
            "the national average, no location adjustment (control)",
            NATIONAL,
            f"national average 1.000 (no location factor applied), {table.source}",
            "control",
            None,
        )
    )
    for label, extra in (extra_zips or {}).items():
        found = location_factor_for_zip(extra, table)
        if found is None or found.city is None:
            raise LocationFactorError(f"--also-zip {label}: its prefix is not in the table")
        out.append(
            Setup(
                f"{ZIP_CITY}:{label}",
                f"the city row of the {label} ZIP prefix",
                found.factor,
                f"zip ({label}): {found.citation()}",
                "prefix listed" if found.state == code else f"prefix listed under {found.state}",
                _row_text(found.city),
            )
        )
    return out


# ------------------------------------------------------------------ the replay


def replay_setups(
    document: dict[str, Any],
    name: str,
    chosen: Sequence[Setup],
    *,
    cost_table: CostTable | None,
    supplement: Mapping[int, YearIndex] | None,
    rows: E2ERows,
    report: EngineerReport,
) -> list[Result]:
    """Every setup repriced (production pricing), replayed (the production variant) and
    scored on one export; the priced-total ratio is against the ``state_mean`` setup."""
    baseline = baseline_scores(document, name, rows, report)
    results: list[Result] = []
    for setup in chosen:
        started = time.perf_counter()
        repriced, changes = reprice(
            document,
            cost_table=cost_table,
            state=None,
            zip_code=None,
            supplement=supplement,
            cost_basis=PRODUCTION_COST_BASIS,
            overhead=PRODUCTION_OVERHEAD,
            location_choice=LocationChoice(setup.factor, setup.source),
        )
        cell = run_cell(
            name,
            repriced,
            Variant(),
            cost_table=cost_table,
            rows=rows,
            report=report,
            baseline=baseline,
            supplement=supplement,
            label=setup.key,
        )
        results.append(
            Result(
                export=name,
                setup=setup,
                cell=cell,
                priced_total=Decimal(changes["priced_total"]["new"]),
                priced_total_ratio=None,
                reprice_counts=dict(changes["counts"]),
                wall_ms=(time.perf_counter() - started) * 1000,
            )
        )
    base = next((r for r in results if r.setup.key == STATE_MEAN), None)
    if base is None or not base.priced_total:
        return results
    return [
        Result(
            r.export,
            r.setup,
            r.cell,
            r.priced_total,
            (r.priced_total / base.priced_total).quantize(Decimal("0.0001")),
            r.reprice_counts,
            r.wall_ms,
        )
        for r in results
    ]


def compare_cell(cell: Cell, expected: Mapping[str, Any]) -> list[str]:
    """The fields of ``cell`` that differ from a stored cell (``gap_sweep.json``), as
    ``field: ours != theirs``; empty when the replay reproduces the stored cell exactly."""
    ours = asdict(cell)
    diffs = []
    for name in REGRESSION_FIELDS:
        if ours.get(name) != expected.get(name):
            diffs.append(f"{name}: {ours.get(name)!r} != {expected.get(name)!r}")
    return diffs


def stored_production_cells(path: Path) -> dict[str, dict[str, Any]]:
    """The production cell per export name of a stored ``gap_sweep.json``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        c["export"]: c
        for c in data.get("cells") or []
        if c.get("variant") == (data.get("production") or PRODUCTION_CELL)
    }


# ------------------------------------------------------------------ the table


def _num(value: float | None, places: int = 1) -> str:
    """Half-up to ``places`` (82.25 prints 82.3, as the report's tables round), not the
    float format's half-to-even."""
    if value is None:
        return "-"
    quantum = Decimal(1).scaleb(-places)
    return str(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _shares(cell: Cell) -> str:
    if not cell.bucket_share_pct:
        return "-"
    s = cell.bucket_share_pct
    return " / ".join(_num(s.get(b), 1) for b in ("y5", "y7", "y15", "y39"))


def _errors(cell: Cell) -> str:
    if not cell.bucket_error_pct:
        return "-"
    worst = max(cell.bucket_error_pct.values())
    return f"{_num(cell.mean_bucket_error_pct, 2)} / {_num(worst, 1)}"


def _gap(cell: Cell) -> str:
    if cell.gap_share_pct is None:
        return cell.note or "-"
    sign = "+" if cell.gap_share_pct >= 0 else ""
    return f"{sign}{cell.gap_share_pct:.1f} {cell.status}"


_READ_INTRO: Final = (
    "**How to read this table.** What changes when the location factor is resolved "
    "differently on a study that already ran? One table per study; every row is the same "
    "export, the same cost table, the same July 2024 index and the same production pricing "
    "and structure model, with only the location factor changed."
)
_READ_COLUMNS: Final = (
    (
        "Location factor resolved as",
        'the rule that picked the factor; "today\'s result" is what the product did on this study.',
    ),
    (
        "Models",
        "the exact model id, its provider, the reasoning effort and the temperature as sent. "
        '"No AI model" means the row replays stored results with no model call.',
    ),
    (
        "Location price factor (table row)",
        "the multiplier applied to every priced dollar, and the published table row it came "
        "from (city and factor) when a row was used.",
    ),
    (
        "Priced total vs. state mean",
        "the itemized rows' total as a share of the state-mean row's (1.0000 = the same dollars).",
    ),
    (
        "Gap to the price (%) / balances?",
        "whether the depreciated cost lands within 10 % of the purchase price less land "
        "('reconciled'), and how far below it, in percent (negative = above).",
    ),
    (
        "Share of dollars by class: 5 / 7 / 15 / 39 years (%)",
        "how the dollars split across the four tax classes.",
    ),
    (
        "Class share error, average / worst (points)** (lower is better)",
        "average and worst gap, in percentage points, between our dollar share and the "
        "engineer's across the 5, 7, 15 and 39-year classes.",
    ),
    (
        "Row-share distance (points)** (lower is better)",
        "how far our dollar shares per engineer report line are from the engineer's, in "
        "points (total variation distance; 0 = identical).",
    ),
    (
        "Report lines found / in the right class (visible in photos)** (higher is better)",
        "engineer report lines that photos can show: found by our assets, and of those in "
        "the engineer's tax class; the regression line, unchanged by pricing.",
    ),
    ("Wall time (ms)", "repricing, replay and scoring of the row, on this machine."),
    ("Tokens (input / output)", 'tokens the run used; "None" means no model was called.'),
)
HOW_TO_READ: Final = (
    _READ_INTRO
    + "\n\n"
    + "\n".join(
        f"- **{name}: {text}" if name.endswith(")") and "**" in name else f"- **{name}**: {text}"
        for name, text in _READ_COLUMNS
    )
    + "\n"
)


def render_markdown(
    results: Sequence[Result],
    spreads: Mapping[str, StateSpread],
    engineer: Mapping[str, float],
    fallbacks: Mapping[str, Mapping[str, Any]] | None = None,
    national_fallback: Mapping[str, Any] | None = None,
) -> str:
    lines = ["# Location factor: five resolutions on stored exports", "", HOW_TO_READ]
    header = (
        "| Location factor resolved as | Models (provider, effort, temperature) | "
        "Location price factor (table row) | Priced total vs. state mean | "
        "Gap to the price (%) / balances? | Share of dollars by class: 5 / 7 / 15 / 39 years (%) | "
        "Class share error, average / worst (points) ↓ | Row-share distance (points) ↓ | "
        "Report lines found / in the right class (visible in photos) ↑ | Wall time (ms) | "
        "Tokens (input / output) |"
    )
    rule = "|" + "---|" * 11
    by_export: dict[str, list[Result]] = {}
    for r in results:
        by_export.setdefault(r.export, []).append(r)
    for export, group in by_export.items():
        lines += [f"## {export}", "", header, rule]
        for r in group:
            c = r.cell
            factor = f"{r.setup.factor}" + (f" ({r.setup.row})" if r.setup.row else "")
            visible = (
                f"{c.visible_evidenced} / {c.visible_right}"
                if c.visible_evidenced is not None
                else "-"
            )
            ratio = "-" if r.priced_total_ratio is None else f"{r.priced_total_ratio}"
            lines.append(
                f"| {r.setup.key} · {r.setup.title}; {r.setup.detail} | No AI model | {factor} | "
                f"{ratio} | {_gap(c)} | {_shares(c)} | {_errors(c)} | {_num(c.tvd_pct)} | "
                f"{visible} | {r.wall_ms:.0f} | None |"
            )
        lines.append("")
    eng = " / ".join(_num(engineer.get(b, 0.0)) for b in ("y5", "y7", "y15", "y39"))
    lines.append(f"Engineer: {eng}. Gap is B less RCNLD over B.")
    for state, s in spreads.items():
        lines.append(
            f"{state}: {s.cities} listed cities, mean {s.mean}, median {s.median}, range "
            f"{s.low}-{s.high} ({s.spread_points:.0f} points); {s.above_mean} above the mean, "
            f"{s.below_mean} below, {s.at_mean} at it."
        )
    for state, f in (fallbacks or {}).items():
        lines.append(
            f"{state}, each listed city's row hidden in turn: the mean of the others is off by "
            f"{f['state_mean_mae_points']} points on average (worst "
            f"{f['state_mean_worst_points']}), the nearest other listed prefix by "
            f"{f['nearest_prefix_mae_points']} (worst {f['nearest_prefix_worst_points']}), "
            f"over {f['rows']} rows."
        )
    if national_fallback:
        n = national_fallback
        lines.append(
            f"Every state with more than one row ({n['states']} states, {n['rows']} rows), the "
            f"same check: state mean {n['state_mean_mae_points']} points on average, nearest "
            f"prefix {n['nearest_prefix_mae_points']}; the nearest prefix is closer in "
            f"{n['states_nearest_closer']} states, the mean in {n['states_mean_closer']}."
        )
    lines.append(
        "The product's candidate order is the intake's ZIP, the intake's state, the "
        "appraisal's ZIP, the appraisal's state: a ZIP read from the appraisal only applies "
        "when the intake has neither a ZIP nor a state."
    )
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ the command


def _parse_extra(values: Sequence[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values or []:
        label, sep, zip_code = item.partition("=")
        if not sep or not label.strip() or not zip_code.strip():
            raise LocationFactorError(f"--also-zip takes LABEL=ZIP, got {item!r}")
        out[label.strip()] = zip_code.strip()
    return out


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.location_factor", description=__doc__)
    parser.add_argument("exports", type=Path, nargs="+", help="rev-<n>.json files")
    parser.add_argument("--cost-table", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, default=None, help="stored index values")
    parser.add_argument("--labels-dir", type=Path, default=None)
    parser.add_argument("--zip", dest="zip_code", default=None, help="default: the export's")
    parser.add_argument("--state", default=None, help="two-letter code; default: the export's")
    parser.add_argument(
        "--also-zip", action="append", default=None, metavar="LABEL=ZIP", help="one more city row"
    )
    parser.add_argument(
        "--regression", type=Path, default=None, help="a stored gap_sweep.json to reproduce"
    )
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    table = book_location_table()
    run = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component=COMPONENT, version=table.origin
    )
    try:
        rows = load_rows(default_labels_path("e2e_rows_v1.json", args.labels_dir))
        report = load_report(default_labels_path("engineer_report_v1.json", args.labels_dir))
        run.dataset(default_labels_path("e2e_rows_v1.json", args.labels_dir))
        run.dataset(default_labels_path("engineer_report_v1.json", args.labels_dir))
        cost_table = load_cost_table(str(args.cost_table))
        run.input("cost_table", args.cost_table)
        supplement = supplement_for(None if args.supplement is None else str(args.supplement))
        if args.supplement is not None:
            run.input("supplement", args.supplement)
        stored = {} if args.regression is None else stored_production_cells(args.regression)
        if args.regression is not None:
            run.input("regression", args.regression)
        extra = _parse_extra(args.also_zip)
        engineer = {b: float(v) for b, v in report.bucket_totals.items()}
        total = sum(engineer.values()) or 1.0
        engineer = {b: v / total * 100 for b, v in engineer.items()}

        results: list[Result] = []
        spreads: dict[str, StateSpread] = {}
        per_export: dict[str, dict[str, Any]] = {}
        setups_by_export: dict[str, list[dict[str, Any]]] = {}
        for path in args.exports:
            document = json.loads(path.read_text(encoding="utf-8"))
            name = path.parent.parent.name if path.parent.name == "export" else path.stem
            run.input(name, path)
            state, zip_code, state_origin, zip_origin = export_location(document)
            state = args.state or state
            zip_code = args.zip_code or zip_code
            if args.state:
                state_origin = "--state"
            if args.zip_code:
                zip_origin = "--zip"
            if not state:
                raise LocationFactorError(f"{name}: no state on the export; pass --state")
            spreads[state.upper()] = state_spread(state, table)
            chosen = setups(zip_code, state, zip_origin=zip_origin, extra_zips=extra, table=table)
            setups_by_export[name] = [asdict(s) for s in chosen]
            group = replay_setups(
                document,
                name,
                chosen,
                cost_table=cost_table,
                supplement=supplement,
                rows=rows,
                report=report,
            )
            results += group
            for r in group:
                outcome = (
                    record.PASSED if r.cell.sourced and r.cell.regression_ok else record.FAILED
                )
                run.case(
                    f"{name}:{r.setup.key}",
                    outcome,
                    kind="setup",
                    detail={
                        "factor": str(r.setup.factor),
                        "row": r.setup.row,
                        "detail": r.setup.detail,
                        "status": r.cell.status,
                        "gap_share_pct": r.cell.gap_share_pct,
                        "priced_total_ratio": None
                        if r.priced_total_ratio is None
                        else str(r.priced_total_ratio),
                        "reprice_counts": r.reprice_counts,
                    },
                    seconds=r.wall_ms / 1000,
                )
            regression: dict[str, Any] = {"compared": False}
            if name in stored:
                base = next(r for r in group if r.setup.key == STATE_MEAN)
                diffs = compare_cell(base.cell, stored[name])
                regression = {"compared": True, "identical": not diffs, "differences": diffs}
                run.case(
                    f"{name}:regression",
                    record.PASSED if not diffs else record.FAILED,
                    kind="regression",
                    detail=regression,
                )
            per_export[name] = {
                "state": state.upper(),
                "state_origin": state_origin,
                "zip_origin": zip_origin if zip_code else None,
                "zip_prefix_row": next(
                    (r.setup.row for r in group if r.setup.key == ZIP_CITY), None
                ),
                "zip_city_detail": next(
                    (r.setup.detail for r in group if r.setup.key == ZIP_CITY), None
                ),
                "regression": regression,
            }

        fallbacks = {state: fallback_check(state, table) for state in spreads}
        national_fallback = fallback_check_all(table)
        text = render_markdown(results, spreads, engineer, fallbacks, national_fallback)
        (args.out / "location_factor.md").write_text(text, encoding="utf-8")
        cells = [
            {
                "export": r.export,
                "setup": asdict(r.setup),
                "priced_total_ratio": r.priced_total_ratio,
                "reprice_counts": r.reprice_counts,
                "wall_ms": r.wall_ms,
                "cell": asdict(r.cell),
            }
            for r in results
        ]
        (args.out / "cells.json").write_text(
            json.dumps(cells, indent=1, default=str) + "\n", encoding="utf-8"
        )
        summary = {
            "setups": setups_by_export,
            "pricing": {"cost_basis": PRODUCTION_COST_BASIS, "overhead": PRODUCTION_OVERHEAD},
            "variant": Variant().gap_name,
            "location_table": {"year": table.year, "origin": table.origin, "source": table.source},
            "spread": {
                k: {**asdict(v), "spread_points": str(v.spread_points)} for k, v in spreads.items()
            },
            "fallback_check": {"by_state": fallbacks, "all_states": national_fallback},
            "exports": per_export,
            "cells": cells,
            "cost_usd": 0,
        }
        print(text)
    except Exception as exc:
        run.finish(None, status="error", exit_code=1, error=repr(exc))
        raise
    run.finish(json.loads(json.dumps(summary, default=str)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
