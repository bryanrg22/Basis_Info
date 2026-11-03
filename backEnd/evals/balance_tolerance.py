"""``python -m evals.balance_tolerance``: which reconciliation tolerance admits the accurate
setups and refuses the inaccurate ones, measured on stored exports (EVAL_REPORT section 81).

The reconciliation (``basis.domain.structure.reconcile``, STRUCTURE_MODEL.md section 3.8)
allocates the purchase price over the assets only when the schedule's replacement cost new
less depreciation lands within ``RECONCILIATION_TOLERANCE`` (10 %) of the price less land.
The 10 % is the RSMeans square-foot method's own statement of its accuracy ("a variation of
+/- 10% should be expected", RSMeans Residential Costs 2020, printed p.15) and was never
varied. This harness varies it, offline and for $0:

* every cell of the reconciliation-gap sweep (``evals.gap_sweep``: 3 pricings x 12 structure
  variants, EVAL_REPORT 49.3; the production cell, its runner-up and every single-change
  variant are in it) and every variant of the structure sweep (``evals.structure_sweep``: 3
  RCN sources x 3 splits x carve on/off x 3 depreciations, EVAL_REPORT 44.3) on the export
  repriced as production prices it, replayed once per export through
  :func:`evals.structure_replay.replay` and scored by :func:`evals.e2e_report.score`;
* the gap each cell leaves (``gap_share``: the price less land minus RCNLD, over the price
  less land; positive = below the price), its share of the dollars that came from the
  square-foot model rather than from priced items, and its tax-class share error against
  the engineer's report;
* for every tolerance in ``--tolerances`` (2.5, 5, 7.5, 10, 12.5, 15 and 20 %), whether the
  cell balances (``|gap_share| <= tolerance``, the reconciliation's own comparison on the
  same six-place figure), and, over the variants that balance on every export, the share
  error they bring in: so "does a looser tolerance let a worse setup through?" is answered
  with numbers. A cell that keeps a known omission (the book's step 2 skipped, the residual
  not added back, the small systems counted twice, the itemized rows on a different cost
  basis from the model, the itemized rows counted twice, no depreciation) is tagged with it;
* two rules besides a fixed percentage, stated exactly: a tolerance that scales with the
  share of the schedule that came from the square-foot model (:func:`scaled_tolerance_pct`)
  and a two-band rule (:func:`two_band_verdict`: allocate within the inner band, allocate
  and flag within the outer, withhold beyond).

Regression: at the production tolerance the fixed rule must give the replay's own verdict on
every cell (a case per cell); with ``--reference`` (section 49.3's ``gap_sweep.json``) every
cell of the gap grid must reproduce its recorded status and gap exactly (a case per cell);
the photo-visible rows' scores must not move (a case per cell). Every run leaves the record
of :mod:`evals.record` (manifest, cases, ``run.json``, ``cells.json``, ``tolerance.md``) and
files under ``verification/balance_tolerance/<production tolerance>/``. Usage (from
``backEnd/``)::

    BASIS_EVAL_DATA_DIR=... python -m evals.balance_tolerance export_a.json export_b.json \\
        --cost-table costs/unit_costs_rsmeans_2020.json --supplement hci.json \\
        --reference gap_sweep.json --out work/e2e_artifacts/balance_tolerance_<date>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final, Literal

from basis.domain.basis import RCNLD_FACTOR_TOLERANCE
from basis.domain.cost_indices import YearIndex, supplement_for
from basis.domain.cost_table import CostTable, load_cost_table
from basis.domain.structure import RECONCILIATION_TOLERANCE, StructureError
from evals import gap_sweep as gs
from evals import structure_sweep as sw
from evals.e2e_report import (
    E2ERows,
    EngineerReport,
    default_labels_path,
    export_from_document,
    load_report,
    load_rows,
    score,
)
from evals.record import EvalRun
from evals.reprice_export import reprice
from evals.structure_replay import PRODUCTION, Variant, rebuild_intake, replay

COMPONENT: Final = "balance_tolerance"
BUCKETS: Final = sw.BUCKETS
#: The tolerances compared, in percent of the price less land.
TOLERANCES_PCT: Final[tuple[float, ...]] = (2.5, 5.0, 7.5, 10.0, 12.5, 15.0, 20.0)
#: The production tolerance (``RECONCILIATION_TOLERANCE``), in percent.
PRODUCTION_TOLERANCE_PCT: Final = float(RECONCILIATION_TOLERANCE * 100)
#: The square-foot method's stated accuracy (RSMeans Residential 2020 p.15), in percent.
MODEL_ACCURACY_PCT: Final = 10.0
#: The accuracy the scaled rule assumes for the priced items by default, in percent. Not a
#: published figure: AACE 18R-97's Class 2 ("detailed unit cost with forced detailed
#: take-off") low range starts at -5 %; EVAL_REPORT 81 says why it is reported, not picked.
ITEM_ACCURACY_PCT: Final = 5.0
#: The two-band rule's default inner band, in percent (EVAL_REPORT 81's pick).
INNER_BAND_PCT: Final = 5.0

GRID_GAP: Final = "49.3"
GRID_STRUCTURE: Final = "44.3"
Grid = Literal["49.3", "44.3"]
Verdict = Literal["balances", "withheld", "not_checked"]
Band = Literal["allocate", "allocate_flagged", "withhold", "not_checked"]

#: The gap grid's production cell and its runner-up (the appraisal's cost new, every other
#: change), EVAL_REPORT 49.3.
GAP_PRODUCTION: Final = gs.PRODUCTION
GAP_RUNNER_UP: Final = "installed+gc|appraisal_total|mods=off|spec=building|small=carved"
#: The structure grid's production variant and its runner-up (EVAL_REPORT 44.3).
STRUCTURE_PRODUCTION: Final = PRODUCTION.name
STRUCTURE_RUNNER_UP: Final = "appraisal_total+rsmeans+carve+appraisal_physical"

# The known omissions a cell keeps, by the axis value that keeps it: each moves the
# schedule's total (so a tolerance could, in principle, see it). The overhead allocation
# moves no total and is not listed.
DEFECTS: Final[dict[str, str]] = {
    "mods=off": "the book's step 2 skipped (baths, heating, cooling not priced as modifications)",
    "spec=report": "the Specialties residual not added back (ATG ch. 3 C.4(2))",
    "small=added": "fire alarm, data cabling and locks counted twice",
    "bare": "itemized rows at bare cost, the model with the installing contractor's O&P",
    "top_down": "itemized rows counted twice (no carve-out)",
    "none": "no depreciation (RCN against the basis: never checked)",
}


@dataclass(frozen=True)
class Cell:
    """One variant on one export, with what the tolerance question needs."""

    export: str
    grid: str
    name: str
    sourced: bool
    note: str
    runtime_ms: float
    defects: tuple[str, ...] = ()
    status: str | None = None  # the replay's verdict at the production tolerance
    gap_share: str | None = None  # (B - RCNLD) / B, six places, as the reconciliation wrote it
    gap_pct: float | None = None
    model_share_pct: float | None = None  # concealed (square-foot model) dollars / all priced
    bucket_share_pct: dict[str, float] | None = None
    bucket_error_pct: dict[str, float] | None = None
    mean_bucket_error_pct: float | None = None
    max_bucket_error_pct: float | None = None
    tvd_pct: float | None = None
    kind_c_produced: int = 0
    kind_c_right: int = 0
    visible_evidenced: int | None = None
    visible_right: int | None = None
    regression_ok: bool | None = None
    # The allocation's factor ``T / RCNLD`` (``T = B + CC``) and whether the second 10 %
    # (``RCNLD_FACTOR_TOLERANCE``, ``|factor - 1|``) calls it within: a different quantity
    # from the reconciliation's ``|(B - RCNLD) / B|``. The allocation's own factor when one
    # was produced (``factor_source`` "allocation"); otherwise the same ``(B + CC) / RCNLD``
    # from the reconciliation's figures and the worksheet's closing costs ("implied": the
    # allocation was withheld by a blocker); ``None`` when there is no RCNLD.
    allocation_factor: str | None = None
    factor_within: bool | None = None
    factor_source: str | None = None


# ------------------------------------------------------------------ the rules


def balances(gap_share: Decimal | str | None, tolerance_pct: float) -> bool | None:
    """The reconciliation's own test at another tolerance: ``|gap_share| <= tolerance`` on
    the six-place share it wrote (``None``: nothing to check)."""
    if gap_share is None:
        return None
    tolerance = (Decimal(str(tolerance_pct)) / 100).quantize(Decimal("0.000001"))
    return abs(Decimal(str(gap_share))) <= tolerance


def factor_check_within(factor: Decimal | str) -> bool:
    """The allocation's ``rcnld_factor`` check (``basis.domain.basis``): ``|factor - 1| <=
    RCNLD_FACTOR_TOLERANCE`` on ``T / RCNLD``. With no closing costs ``factor = 1 / (1 -
    gap_share)``, so a gap above ``1 - 1 / 1.1`` (9.09 %) reconciles at 10 % and still reads
    "outside" here."""
    return abs(Decimal(str(factor)) - 1) <= RCNLD_FACTOR_TOLERANCE


def fixed_verdict(cell: Cell, tolerance_pct: float) -> Verdict:
    if cell.status in (None, "not_checked") or cell.gap_share is None:
        return "not_checked"
    return "balances" if balances(cell.gap_share, tolerance_pct) else "withheld"


def scaled_tolerance_pct(
    model_share_pct: float,
    *,
    model_pct: float = MODEL_ACCURACY_PCT,
    item_pct: float = ITEM_ACCURACY_PCT,
) -> float:
    """A tolerance that follows where the dollars came from: ``model_pct`` on the share of
    the schedule the square-foot model produced (its residual rows), ``item_pct`` on the
    rest (the priced items), added linearly (each part at its own worst case). With
    ``item_pct == model_pct`` it is the fixed rule."""
    m = max(0.0, min(1.0, model_share_pct / 100))
    return round(model_pct * m + item_pct * (1 - m), 4)


def scaled_verdict(
    cell: Cell, *, model_pct: float = MODEL_ACCURACY_PCT, item_pct: float = ITEM_ACCURACY_PCT
) -> tuple[Verdict, float | None]:
    """``(verdict, the tolerance the cell got)``."""
    if cell.model_share_pct is None:
        return fixed_verdict(cell, model_pct), None
    tolerance = scaled_tolerance_pct(cell.model_share_pct, model_pct=model_pct, item_pct=item_pct)
    return fixed_verdict(cell, tolerance), tolerance


def two_band_verdict(
    cell: Cell, *, inner_pct: float = INNER_BAND_PCT, outer_pct: float = PRODUCTION_TOLERANCE_PCT
) -> Band:
    """Allocate within ``inner_pct`` (the gap is inside what the correct method leaves);
    allocate and flag the gap for the engineer within ``outer_pct`` (inside the method's
    stated accuracy, outside what the correct method leaves); withhold beyond."""
    if inner_pct > outer_pct:
        raise ValueError("the inner band must not exceed the outer band")
    first = fixed_verdict(cell, inner_pct)
    if first == "not_checked":
        return "not_checked"
    if first == "balances":
        return "allocate"
    return "allocate_flagged" if fixed_verdict(cell, outer_pct) == "balances" else "withhold"


# ------------------------------------------------------------------ the cells


def defects_of(name: str, grid: str) -> tuple[str, ...]:
    """The known omissions a cell's name says it keeps (``DEFECTS``' keys). The book's
    modifications apply to the square-foot model only (the appraisal's cost new prices the
    whole house), so ``mods=off`` counts only with that source."""
    parts = name.split("|") if grid == GRID_GAP else name.split("+")
    found = [key for key in DEFECTS if key in parts]
    if "square_foot" not in parts:
        found = [key for key in found if key != "mods=off"]
    return tuple(found)


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def measure(
    export_name: str,
    document: Mapping[str, Any],
    variant: Variant,
    *,
    grid: str,
    name: str,
    cost_table: CostTable | None,
    rows: E2ERows,
    report: EngineerReport,
    baseline: tuple[int, int],
    supplement: Mapping[int, YearIndex] | None = None,
) -> Cell:
    """One variant on one export: the replay's reconciliation and the scorer's shares."""
    started = time.perf_counter()
    defects = defects_of(name, grid)
    try:
        intake = rebuild_intake(document, None)
        replayed, changes = replay(
            document, cost_table=cost_table, intake=intake, variant=variant, supplement=supplement
        )
    except StructureError as exc:
        return Cell(
            export=export_name,
            grid=grid,
            name=name,
            sourced=False,
            note=str(exc),
            runtime_ms=(time.perf_counter() - started) * 1000,
            defects=defects,
        )
    scored = score(export_from_document(replayed, export_name), rows, report)
    elapsed = (time.perf_counter() - started) * 1000
    c, sh, d = (
        scored.summary["classification"],
        scored.summary["shares"],
        scored.summary["dollars"],
    )
    rec = changes["reconciliation"] or {}
    errors = {b: float(sh["bucket_share_error_pct"].get(b, 0.0)) for b in BUCKETS}
    visible = (int(c["evidenced"]), int(c["bucket_right_by_count"]))
    total = Decimal(str(d["pipeline_total_priced"]))
    concealed = Decimal(str(c["concealed_priced_dollars"]))
    model_share = (
        None
        if total <= 0
        else float((concealed / total * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    )
    gap_share = rec.get("gap_share")
    factor, factor_source = allocation_factor_of(replayed, changes)
    return Cell(
        export=export_name,
        grid=grid,
        name=name,
        sourced=True,
        note="",
        runtime_ms=elapsed,
        defects=defects,
        status=rec.get("status"),
        gap_share=None if gap_share is None else str(gap_share),
        gap_pct=None if gap_share is None else float(Decimal(str(gap_share)) * 100),
        model_share_pct=model_share,
        bucket_share_pct={b: float(sh["pipeline_bucket_share_pct"].get(b, 0.0)) for b in BUCKETS},
        bucket_error_pct=errors,
        mean_bucket_error_pct=sum(errors.values()) / len(errors),
        max_bucket_error_pct=max(errors.values()),
        tvd_pct=float(sh["total_variation_distance_pct"]),
        kind_c_produced=int(c["kind_c_produced"]),
        kind_c_right=int(c["kind_c_bucket_right"]),
        visible_evidenced=visible[0],
        visible_right=visible[1],
        regression_ok=visible == baseline,
        allocation_factor=None if factor is None else str(factor),
        factor_within=None if factor is None else factor_check_within(factor),
        factor_source=factor_source,
    )


def allocation_factor_of(
    replayed: Mapping[str, Any], changes: Mapping[str, Any]
) -> tuple[Decimal | None, str | None]:
    """``(T / RCNLD, where it came from)``: the allocation's own factor when one was
    produced, else ``(B + CC) / RCNLD`` from the reconciliation and the worksheet's
    ``building_basis`` (price less land) and ``capitalized_costs`` (what
    ``basis.evidence.result`` passes to ``allocate``), to the allocation's nine places."""
    allocation = changes.get("allocation") or {}
    if allocation.get("factor") is not None:
        return Decimal(str(allocation["factor"])), "allocation"
    rec = changes.get("reconciliation") or {}
    if rec.get("rcnld_total") is None:
        return None, None
    rcnld = Decimal(str(rec["rcnld_total"]))
    if rcnld <= 0:
        return None, None
    worksheet = ((replayed.get("summary") or {}).get("basis") or {}).get("worksheet") or {}
    building = Decimal(str(worksheet.get("building_basis", rec["building_basis"])))
    closing = Decimal(str(worksheet.get("capitalized_costs") or 0))
    factor = ((building + closing) / rcnld).quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    return factor, "implied"


def export_cells(
    export_name: str,
    document: Mapping[str, Any],
    *,
    cost_table: CostTable | None,
    rows: E2ERows,
    report: EngineerReport,
    supplement: Mapping[int, YearIndex] | None,
    grids: Sequence[str] = (GRID_GAP, GRID_STRUCTURE),
) -> list[Cell]:
    """Every cell of the gap grid (each pricing's repriced export, EVAL_REPORT 49.3) and of
    the structure grid on the production pricing (EVAL_REPORT 44.3's axes) for one export."""
    baseline = sw.baseline_scores(dict(document), export_name, rows, report)
    repriced: dict[gs.Pricing, dict[str, Any]] = {}
    for pricing in gs.PRICINGS:
        repriced[pricing], _ = reprice(
            document,
            cost_table=cost_table,
            state=None,
            zip_code=None,
            supplement=supplement,
            cost_basis=pricing.cost_basis,
            overhead=pricing.overhead,
        )
    common: dict[str, Any] = {
        "cost_table": cost_table,
        "rows": rows,
        "report": report,
        "baseline": baseline,
        "supplement": supplement,
    }
    cells: list[Cell] = []
    if GRID_GAP in grids:
        for pricing, variant in gs.grid():
            cells.append(
                measure(
                    export_name,
                    repriced[pricing],
                    variant,
                    grid=GRID_GAP,
                    name=gs.cell_name(pricing, variant),
                    **common,
                )
            )
    if GRID_STRUCTURE in grids:
        production_pricing = gs.PRICINGS[2]
        for variant in sw.grid():
            cells.append(
                measure(
                    export_name,
                    repriced[production_pricing],
                    variant,
                    grid=GRID_STRUCTURE,
                    name=variant.name,
                    **common,
                )
            )
    return cells


# ------------------------------------------------------------------ the summaries


def by_variant(cells: Sequence[Cell], grid: str) -> dict[str, list[Cell]]:
    out: dict[str, list[Cell]] = {}
    for cell in cells:
        if cell.grid == grid:
            out.setdefault(cell.name, []).append(cell)
    return out


def _mean(values: Sequence[float]) -> float | None:
    return None if not values else sum(values) / len(values)


@dataclass(frozen=True)
class VariantVerdict:
    """One variant under one rule, over every export."""

    name: str
    verdicts: tuple[str, ...]
    admitted: bool  # balances (or allocates) on every export
    defects: tuple[str, ...]
    mean_bucket_error_pct: float | None  # averaged over the exports
    max_bucket_error_pct: float | None  # the worst export's worst class
    mean_abs_gap_pct: float | None


def variant_verdicts(
    groups: Mapping[str, Sequence[Cell]], verdict_of: Any, admit: frozenset[str]
) -> list[VariantVerdict]:
    out = []
    for name, group in groups.items():
        if not all(c.sourced for c in group):
            continue
        verdicts = tuple(str(verdict_of(c)) for c in group)
        out.append(
            VariantVerdict(
                name=name,
                verdicts=verdicts,
                admitted=all(v in admit for v in verdicts),
                defects=group[0].defects,
                mean_bucket_error_pct=_mean([c.mean_bucket_error_pct or 0.0 for c in group]),
                max_bucket_error_pct=max(c.max_bucket_error_pct or 0.0 for c in group),
                mean_abs_gap_pct=_mean([abs(c.gap_pct or 0.0) for c in group]),
            )
        )
    return out


@dataclass(frozen=True)
class ToleranceRow:
    """What one fixed tolerance admits on one grid."""

    grid: str
    tolerance_pct: float
    variants: int  # sourced and checked (a depreciation rate exists)
    admitted: int
    admitted_with_defects: int
    admitted_defect_names: tuple[str, ...]
    clean_refused: int  # variants with no known omission that the tolerance withholds
    defects_refused: int  # variants with a known omission that it withholds
    admitted_mean_bucket_error_pct: float | None
    admitted_worst_bucket_error_pct: float | None
    worst_admitted: tuple[str, float] | None  # by mean bucket error
    best_refused: tuple[str, float] | None
    production_admitted: bool
    runner_up_admitted: bool
    production_gaps_pct: tuple[float, ...]
    runner_up_gaps_pct: tuple[float, ...]


def tolerance_rows(
    cells: Sequence[Cell], tolerances: Sequence[float] = TOLERANCES_PCT
) -> list[ToleranceRow]:
    out: list[ToleranceRow] = []
    for grid, production, runner_up in (
        (GRID_GAP, GAP_PRODUCTION, GAP_RUNNER_UP),
        (GRID_STRUCTURE, STRUCTURE_PRODUCTION, STRUCTURE_RUNNER_UP),
    ):
        groups = by_variant(cells, grid)
        if not groups:
            continue
        for t in tolerances:
            verdicts = variant_verdicts(
                groups, lambda c, t=t: fixed_verdict(c, t), frozenset({"balances"})
            )
            checked = [v for v in verdicts if "not_checked" not in v.verdicts]
            admitted = [v for v in checked if v.admitted]
            refused = [v for v in checked if not v.admitted]
            with_defects = [v for v in admitted if v.defects]
            worst = max(admitted, key=lambda v: v.mean_bucket_error_pct or 0.0, default=None)
            best = min(refused, key=lambda v: v.mean_bucket_error_pct or 0.0, default=None)
            out.append(
                ToleranceRow(
                    grid=grid,
                    tolerance_pct=t,
                    variants=len(checked),
                    admitted=len(admitted),
                    admitted_with_defects=len(with_defects),
                    admitted_defect_names=tuple(
                        sorted({d for v in with_defects for d in v.defects})
                    ),
                    clean_refused=sum(not v.defects for v in refused),
                    defects_refused=sum(bool(v.defects) for v in refused),
                    admitted_mean_bucket_error_pct=_mean(
                        [v.mean_bucket_error_pct or 0.0 for v in admitted]
                    ),
                    admitted_worst_bucket_error_pct=max(
                        (v.max_bucket_error_pct or 0.0 for v in admitted), default=None
                    ),
                    worst_admitted=None
                    if worst is None
                    else (worst.name, worst.mean_bucket_error_pct or 0.0),
                    best_refused=None
                    if best is None
                    else (best.name, best.mean_bucket_error_pct or 0.0),
                    production_admitted=any(v.name == production and v.admitted for v in admitted),
                    runner_up_admitted=any(v.name == runner_up and v.admitted for v in admitted),
                    production_gaps_pct=tuple(c.gap_pct or 0.0 for c in groups.get(production, [])),
                    runner_up_gaps_pct=tuple(c.gap_pct or 0.0 for c in groups.get(runner_up, [])),
                )
            )
    return out


def separation(cells: Sequence[Cell], grid: str) -> dict[str, float | None]:
    """What a fixed tolerance can tell apart on one grid: ``"clean"`` is the largest |gap| of
    any checked cell with no known omission (a tolerance below it withholds a correct
    setup); each omission's value is the smallest |gap| of any checked cell that keeps it
    (a tolerance at or above it lets that omission through somewhere)."""
    checked = [c for c in cells if c.grid == grid and c.sourced and c.gap_pct is not None]
    out: dict[str, float | None] = {
        "clean": max((abs(c.gap_pct or 0.0) for c in checked if not c.defects), default=None)
    }
    for key in DEFECTS:
        gaps = [abs(c.gap_pct or 0.0) for c in checked if key in c.defects]
        out[key] = min(gaps) if gaps else None
    return out


def tightest_tolerance(
    group: Sequence[Cell], tolerances: Sequence[float] = TOLERANCES_PCT
) -> float | None:
    """The smallest listed tolerance at which the variant balances on every export."""
    for t in tolerances:
        if group and all(fixed_verdict(c, t) == "balances" for c in group):
            return t
    return None


def rule_rows(
    cells: Sequence[Cell],
    *,
    item_pct: float = ITEM_ACCURACY_PCT,
    inner_pct: float = INNER_BAND_PCT,
    outer_pct: float = PRODUCTION_TOLERANCE_PCT,
) -> list[dict[str, Any]]:
    """Every sourced variant under the fixed, scaled and two-band rules."""
    out: list[dict[str, Any]] = []
    for grid in (GRID_GAP, GRID_STRUCTURE):
        for name, group in by_variant(cells, grid).items():
            if not all(c.sourced for c in group):
                continue
            scaled = [scaled_verdict(c, item_pct=item_pct) for c in group]
            out.append(
                {
                    "grid": grid,
                    "name": name,
                    "defects": list(group[0].defects),
                    "exports": [c.export for c in group],
                    "gap_pct": [c.gap_pct for c in group],
                    "model_share_pct": [c.model_share_pct for c in group],
                    "mean_bucket_error_pct": [c.mean_bucket_error_pct for c in group],
                    "tightest_fixed_pct": tightest_tolerance(group),
                    "scaled_tolerance_pct": [t for _, t in scaled],
                    "scaled_verdict": [v for v, _ in scaled],
                    "two_band": [
                        two_band_verdict(c, inner_pct=inner_pct, outer_pct=outer_pct) for c in group
                    ],
                }
            )
    return out


# ------------------------------------------------------------------ the record


def _num(value: float | None, places: int = 1) -> str:
    return "-" if value is None else f"{value:.{places}f}"


def _factor(cell: Cell) -> str:
    if cell.allocation_factor is None:
        return "-"
    within = "within" if cell.factor_within else "outside"
    return f"{cell.allocation_factor} ({within}, {cell.factor_source})"


def factor_disagreements(cells: Sequence[Cell]) -> list[Cell]:
    """Cells the reconciliation reconciles whose ``rcnld_factor`` check still says outside."""
    return [c for c in cells if c.status == "reconciled" and c.factor_within is False]


def _pair(value: tuple[str, float] | None) -> str:
    return "-" if value is None else f"{value[0]} ({value[1]:.2f})"


def render_markdown(
    cells: Sequence[Cell],
    rows: Sequence[ToleranceRow],
    rules: Sequence[dict[str, Any]],
    *,
    item_pct: float,
    inner_pct: float,
    outer_pct: float,
) -> str:
    lines = [
        "# Balance tolerance sweep",
        "",
        f"Production tolerance {PRODUCTION_TOLERANCE_PCT:g} % (`RECONCILIATION_TOLERANCE`). "
        "Gap is (price less land - RCNLD) / (price less land), in percent; positive = below "
        "the price. Model share is the square-foot model's concealed dollars over every "
        "priced dollar. Cost per cell: $0 (deterministic, no model call).",
        "",
        "## Fixed tolerances",
        "",
        "| grid | tolerance % | variants checked | admitted on every export | admitted with a "
        "known omission (which) | clean refused | with an omission refused | admitted: mean "
        "bucket err (points) | admitted: worst bucket "
        "err | worst admitted (mean err) | best refused (mean err) | production admitted "
        "(gaps) | runner-up admitted (gaps) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.grid} | {r.tolerance_pct:g} | {r.variants} | {r.admitted} | "
            f"{r.admitted_with_defects} ({', '.join(r.admitted_defect_names) or '-'}) | "
            f"{r.clean_refused} | {r.defects_refused} | "
            f"{_num(r.admitted_mean_bucket_error_pct, 2)} | "
            f"{_num(r.admitted_worst_bucket_error_pct, 1)} | {_pair(r.worst_admitted)} | "
            f"{_pair(r.best_refused)} | {'yes' if r.production_admitted else 'no'} "
            f"({', '.join(f'{g:+.1f}' for g in r.production_gaps_pct)}) | "
            f"{'yes' if r.runner_up_admitted else 'no'} "
            f"({', '.join(f'{g:+.1f}' for g in r.runner_up_gaps_pct)}) |"
        )
    lines += [
        "",
        "## What a fixed tolerance can separate (largest clean |gap|, smallest |gap| per "
        "omission, over both exports)",
        "",
        "| grid | clean (max) | " + " | ".join(f"{k} (min)" for k in DEFECTS) + " |",
        "|---|---|" + "---|" * len(DEFECTS),
    ]
    for grid in (GRID_GAP, GRID_STRUCTURE):
        sep = separation(cells, grid)
        if sep["clean"] is None and all(sep[k] is None for k in DEFECTS):
            continue
        lines.append(
            f"| {grid} | {_num(sep['clean'], 2)} | "
            + " | ".join(_num(sep[k], 2) for k in DEFECTS)
            + " |"
        )
    lines += [
        "",
        "## Every variant: tightest fixed tolerance, the scaled rule (model "
        f"{MODEL_ACCURACY_PCT:g} %, items {item_pct:g} %) and the two-band rule "
        f"({inner_pct:g} / {outer_pct:g} %)",
        "",
        "| grid | variant | known omissions | gaps % | model share % | mean bucket err | "
        "tightest fixed % | scaled tolerance % | scaled verdict | two-band |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in rules:
        lines.append(
            f"| {v['grid']} | {v['name']} | {', '.join(v['defects']) or '-'} | "
            f"{' / '.join(_num(g) for g in v['gap_pct'])} | "
            f"{' / '.join(_num(m) for m in v['model_share_pct'])} | "
            f"{' / '.join(_num(e, 2) for e in v['mean_bucket_error_pct'])} | "
            f"{_num(v['tightest_fixed_pct'])} | "
            f"{' / '.join(_num(t, 2) for t in v['scaled_tolerance_pct'])} | "
            f"{' / '.join(v['scaled_verdict'])} | {' / '.join(v['two_band'])} |"
        )
    lines += ["", "## Cells", ""]
    lines.append(
        "| export | grid | variant | status at production | gap % | model share % | shares "
        "y5 / y7 / y15 / y39 | mean / max bucket err | TVD | kind C produced / right | visible "
        "evidenced / right | allocation factor (rcnld_factor check) | regression | ms |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in cells:
        if not c.sourced:
            lines.append(
                f"| {c.export} | {c.grid} | {c.name} | not sourced: {c.note} | | | | | | | | | | "
                f"{c.runtime_ms:.0f} |"
            )
            continue
        shares = c.bucket_share_pct or {}
        lines.append(
            f"| {c.export} | {c.grid} | {c.name} | {c.status} | {_num(c.gap_pct)} | "
            f"{_num(c.model_share_pct)} | {' / '.join(_num(shares.get(b)) for b in BUCKETS)} | "
            f"{_num(c.mean_bucket_error_pct, 2)} / {_num(c.max_bucket_error_pct)} | "
            f"{_num(c.tvd_pct)} | {c.kind_c_produced} / {c.kind_c_right} | "
            f"{c.visible_evidenced} / {c.visible_right} | {_factor(c)} | "
            f"{'ok' if c.regression_ok else 'MOVED'} | {c.runtime_ms:.0f} |"
        )
    moved = [c for c in cells if c.sourced and c.regression_ok is False]
    split = factor_disagreements(cells)
    lines += [
        "",
        "The two 10 % checks (reconciled by `|gap_share| <= 10 %` but `|T / RCNLD - 1|` over "
        "`RCNLD_FACTOR_TOLERANCE`; the factor is implied where a blocker withheld the "
        "allocation): "
        + ("none." if not split else ", ".join(f"{c.export} {c.name}" for c in split)),
    ]
    lines += [
        "",
        "Regression check (photo-visible rows evidenced / bucket right against the stored "
        "export): "
        + (
            "every cell unchanged."
            if not moved
            else "MOVED on " + ", ".join(f"{c.export} {c.name}" for c in moved)
        ),
    ]
    return "\n".join(lines) + "\n"


def reference_cells(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Section 49.3's ``gap_sweep.json`` cells by (export, variant)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(str(c["export"]), str(c["variant"])): c for c in data["cells"]}


def record_cases(
    run: EvalRun, cells: Sequence[Cell], reference: Mapping[tuple[str, str], Mapping[str, Any]]
) -> None:
    """One case per cell for each regression: the fixed rule at the production tolerance is
    the replay's verdict; the photo-visible rows did not move; with a reference, the gap
    grid's status and gap are the recorded ones."""
    for c in cells:
        if not c.sourced:
            run.case(f"{c.export}:{c.grid}:{c.name}:sourced", "passed", detail=c.note)
            continue
        expected = (
            "not_checked"
            if c.status in (None, "not_checked")
            else ("balances" if c.status == "reconciled" else "withheld")
        )
        got = fixed_verdict(c, PRODUCTION_TOLERANCE_PCT)
        run.case(
            f"{c.export}:{c.grid}:{c.name}:verdict_at_production",
            "passed" if got == expected else "failed",
            detail={"replay": c.status, "rule": got, "gap_share": c.gap_share},
            seconds=c.runtime_ms / 1000,
        )
        run.case(
            f"{c.export}:{c.grid}:{c.name}:visible_rows_unchanged",
            "passed" if c.regression_ok else "failed",
            detail={"evidenced": c.visible_evidenced, "right": c.visible_right},
        )
        ref = reference.get((c.export, c.name)) if c.grid == GRID_GAP else None
        if ref is not None:
            same = ref.get("status") == c.status and (
                ref.get("gap_share_pct") is None
                if c.gap_pct is None
                else ref.get("gap_share_pct") is not None
                and round(float(ref["gap_share_pct"]), 4) == round(c.gap_pct, 4)
            )
            run.case(
                f"{c.export}:{c.grid}:{c.name}:matches_49.3",
                "passed" if same else "failed",
                detail={
                    "reference": {"status": ref.get("status"), "gap_pct": ref.get("gap_share_pct")},
                    "now": {"status": c.status, "gap_pct": c.gap_pct},
                },
            )


def summary_of(
    cells: Sequence[Cell],
    rows: Sequence[ToleranceRow],
    rules: Sequence[dict[str, Any]],
    *,
    wall_seconds: float,
    tolerances: Sequence[float],
    item_pct: float,
    inner_pct: float,
    outer_pct: float,
) -> dict[str, Any]:
    return {
        "component": COMPONENT,
        "production_tolerance_pct": PRODUCTION_TOLERANCE_PCT,
        "tolerances_pct": list(tolerances),
        "scaled_rule": {"model_pct": MODEL_ACCURACY_PCT, "item_pct": item_pct},
        "two_band_rule": {"inner_pct": inner_pct, "outer_pct": outer_pct},
        "exports": sorted({c.export for c in cells}),
        "cells": len(cells),
        "sourced": sum(c.sourced for c in cells),
        "wall_seconds": round(wall_seconds, 2),
        "cell_ms": {
            "mean": round(_mean([c.runtime_ms for c in cells]) or 0.0, 1),
            "max": round(max((c.runtime_ms for c in cells), default=0.0), 1),
        },
        "cost_usd": 0,
        "tolerances": [asdict(r) for r in rows],
        "separation": {g: separation(cells, g) for g in (GRID_GAP, GRID_STRUCTURE)},
        "variants": list(rules),
        "allocated": sum(c.factor_source == "allocation" for c in cells),
        "factor_check_disagrees": [
            f"{c.export} {c.grid} {c.name}" for c in factor_disagreements(cells)
        ],
        "regression_moved": [
            f"{c.export} {c.grid} {c.name}" for c in cells if c.sourced and c.regression_ok is False
        ],
    }


@dataclass
class _Timer:
    started: float = field(default_factory=time.perf_counter)

    def seconds(self) -> float:
        return time.perf_counter() - self.started


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.balance_tolerance", description=__doc__)
    parser.add_argument("exports", type=Path, nargs="+", help="rev-<n>.json files")
    parser.add_argument("--cost-table", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, default=None, help="stored index values")
    parser.add_argument("--labels-dir", type=Path, default=None)
    parser.add_argument(
        "--reference", type=Path, default=None, help="section 49.3's gap_sweep.json"
    )
    parser.add_argument(
        "--tolerances",
        type=str,
        default=",".join(f"{t:g}" for t in TOLERANCES_PCT),
        help="percent values, comma-separated",
    )
    parser.add_argument("--item-pct", type=float, default=ITEM_ACCURACY_PCT)
    parser.add_argument("--inner-pct", type=float, default=INNER_BAND_PCT)
    parser.add_argument("--outer-pct", type=float, default=PRODUCTION_TOLERANCE_PCT)
    parser.add_argument(
        "--grid",
        choices=(GRID_GAP, GRID_STRUCTURE, "both"),
        default="both",
        help="the gap sweep's cells (49.3), the structure sweep's variants (44.3), or both",
    )
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    tolerances = tuple(float(t) for t in args.tolerances.split(",") if t.strip())
    grids = (GRID_GAP, GRID_STRUCTURE) if args.grid == "both" else (args.grid,)
    run = EvalRun.begin(
        args.out,
        harness=__name__,
        args=args,
        component=COMPONENT,
        version=f"tolerance_{RECONCILIATION_TOLERANCE}",
    )
    timer = _Timer()
    try:
        rows_path = default_labels_path("e2e_rows_v1.json", args.labels_dir)
        report_path = default_labels_path("engineer_report_v1.json", args.labels_dir)
        rows = load_rows(rows_path)
        report = load_report(report_path)
        run.dataset(rows_path)
        run.dataset(report_path)
        run.input("cost_table", args.cost_table)
        if args.supplement is not None:
            run.input("supplement", args.supplement)
        if args.reference is not None:
            run.input("reference", args.reference)
        table = load_cost_table(str(args.cost_table))
        supplement = supplement_for(None if args.supplement is None else str(args.supplement))
        reference = {} if args.reference is None else reference_cells(args.reference)
        cells: list[Cell] = []
        for path in args.exports:
            run.input("export", path)
            document = json.loads(path.read_text(encoding="utf-8"))
            name = path.parent.parent.name if path.parent.name == "export" else path.stem
            cells += export_cells(
                name,
                document,
                cost_table=table,
                rows=rows,
                report=report,
                supplement=supplement,
                grids=grids,
            )
        tol_rows = tolerance_rows(cells, tolerances)
        rules = rule_rows(
            cells, item_pct=args.item_pct, inner_pct=args.inner_pct, outer_pct=args.outer_pct
        )
        record_cases(run, cells, reference)
        text = render_markdown(
            cells,
            tol_rows,
            rules,
            item_pct=args.item_pct,
            inner_pct=args.inner_pct,
            outer_pct=args.outer_pct,
        )
        (args.out / "tolerance.md").write_text(text, encoding="utf-8")
        (args.out / "cells.json").write_text(
            json.dumps([asdict(c) for c in cells], indent=1), encoding="utf-8"
        )
        summary = summary_of(
            cells,
            tol_rows,
            rules,
            wall_seconds=timer.seconds(),
            tolerances=tolerances,
            item_pct=args.item_pct,
            inner_pct=args.inner_pct,
            outer_pct=args.outer_pct,
        )
        print(text)
    except Exception as exc:
        run.finish(status="error", exit_code=1, error=f"{type(exc).__name__}: {exc}")
        raise
    manifest = run.finish(summary)
    failed = int(manifest["results"]["failed"]) + int(manifest["results"]["test_error"])
    print(f"wrote {args.out} (tree: {manifest['tree']})")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
