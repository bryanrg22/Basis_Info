"""``python -m evals.gap_sweep``: the reconciliation-gap fixes, alone and together, scored the
same way on stored exports (EVAL_REPORT section 49, STRUCTURE_MODEL.md sections 8-9).

Since the year factor became the published index (EVAL_REPORT 47) the two live exports of
the client property replay to RCNLD 13-14 % below the purchase price less land, so the
allocation is withheld. Section 8 of the design doc decomposed the gap by the guide's causes;
each change it supports is an axis here:

* pricing (``evals.reprice_export``): the itemized rows at RSMeans bare cost (the exports'
  basis), at the line's Total Incl O&P (``installed``), and installed with the square-foot
  model's general contractor's O&P on the rows inside the model (``pro_rata``);
* the building RCN's source: the RSMeans square-foot model (the production source) or the
  appraisal's printed total cost new (section 44's runner-up);
* the square-foot model's modifications and alternatives for the house's baths, heating and
  cooling (off: the base model as priced before; the appraisal's figure has none);
* the Specialties residual reported (before) or written as a building row;
* the small systems added (before) or carved from their component.

The split, the carve-out and the depreciation are section 44's pick (RSMeans split, carved,
the appraiser's physical depreciation; age-life is the same rate on this appraisal). Every
cell goes through :func:`evals.structure_replay.replay` and :func:`evals.e2e_report.score`
with the stored index values (``--supplement``), exactly as section 47 replayed the exports.
The pick is :func:`evals.structure_sweep.rank`'s: among the cells that reconcile on every
export, the smallest mean absolute bucket-share error, then the row-share distance, then the
gap. The photo-visible rows' scores against the stored export are the regression line.
Deterministic: no model call, cost $0. Usage (from ``backEnd/``)::

    BASIS_EVAL_DATA_DIR=... python -m evals.gap_sweep export_a.json export_b.json \\
        --cost-table costs/unit_costs_rsmeans_2020.json --supplement hci.json \\
        --out gap_sweep.md --json gap_sweep.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from basis.domain.cost_indices import supplement_for
from basis.domain.cost_table import load_cost_table
from basis.domain.costs import CostBasis
from basis.domain.structure import OverheadAllocation, RcnSource, SpecialtiesResidual
from evals.e2e_report import default_labels_path, load_report, load_rows
from evals.reprice_export import reprice
from evals.structure_replay import Variant
from evals.structure_sweep import Cell, baseline_scores, rank, render_markdown, run_cell


@dataclass(frozen=True)
class Pricing:
    """How the itemized rows are priced before the replay."""

    cost_basis: CostBasis
    overhead: OverheadAllocation

    @property
    def name(self) -> str:
        return {
            ("bare", "none"): "bare",
            ("installed", "none"): "installed",
            ("installed", "pro_rata"): "installed+gc",
        }.get((self.cost_basis, self.overhead), f"{self.cost_basis}+{self.overhead}")


PRICINGS: Final[tuple[Pricing, ...]] = (
    Pricing("bare", "none"),
    Pricing("installed", "none"),
    Pricing("installed", "pro_rata"),
)
RCN_SOURCES: Final[tuple[RcnSource, ...]] = ("square_foot", "appraisal_total")
SPECIALTIES: Final[tuple[SpecialtiesResidual, ...]] = ("report", "building")
# Section 47's production cell: what the exports replayed to before this section; and the
# production defaults since (the settings' defaults, basis.config).
BEFORE: Final = "bare|square_foot|mods=off|spec=report|small=added"
PRODUCTION: Final = "installed+gc|square_foot|mods=on|spec=building|small=carved"


def cell_name(pricing: Pricing, variant: Variant) -> str:
    return (
        f"{pricing.name}|{variant.rcn_source}|mods={'on' if variant.modifications else 'off'}"
        f"|spec={variant.specialties}|small={'carved' if variant.carve_small else 'added'}"
    )


def grid() -> list[tuple[Pricing, Variant]]:
    """Every pricing x structure variant; the modifications only where the square-foot model
    is the source (the appraisal's cost new is the whole house)."""
    cells: list[tuple[Pricing, Variant]] = []
    for pricing, source, mods, spec, small in itertools.product(
        PRICINGS, RCN_SOURCES, (False, True), SPECIALTIES, (False, True)
    ):
        if source != "square_foot" and mods:
            continue
        cells.append(
            (
                pricing,
                Variant(
                    rcn_source=source,
                    modifications=mods,
                    specialties=spec,
                    carve_small=small,
                    cost_basis=pricing.cost_basis,
                    overhead=pricing.overhead,
                ),
            )
        )
    return cells


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.gap_sweep", description=__doc__)
    parser.add_argument("exports", type=Path, nargs="+", help="rev-<n>.json files")
    parser.add_argument("--cost-table", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, default=None, help="stored index values")
    parser.add_argument("--labels-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True, help="the Markdown table")
    parser.add_argument("--json", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    rows = load_rows(default_labels_path("e2e_rows_v1.json", args.labels_dir))
    report = load_report(default_labels_path("engineer_report_v1.json", args.labels_dir))
    table = load_cost_table(str(args.cost_table))
    supplement = supplement_for(None if args.supplement is None else str(args.supplement))
    cells: list[Cell] = []
    for path in args.exports:
        document = json.loads(path.read_text(encoding="utf-8"))
        name = path.parent.parent.name if path.parent.name == "export" else path.stem
        baseline = baseline_scores(document, name, rows, report)
        repriced: dict[Pricing, dict[str, Any]] = {}
        for pricing in PRICINGS:
            repriced[pricing], _ = reprice(
                document,
                cost_table=table,
                state=None,
                zip_code=None,
                supplement=supplement,
                cost_basis=pricing.cost_basis,
                overhead=pricing.overhead,
            )
        for pricing, variant in grid():
            cells.append(
                run_cell(
                    name,
                    repriced[pricing],
                    variant,
                    cost_table=table,
                    rows=rows,
                    report=report,
                    baseline=baseline,
                    supplement=supplement,
                    label=cell_name(pricing, variant),
                )
            )
    ranked = rank(cells)
    engineer = {b: float(v) for b, v in report.bucket_totals.items()}
    total = sum(engineer.values()) or 1.0
    engineer = {b: v / total * 100 for b, v in engineer.items()}
    text = render_markdown(cells, ranked, engineer).replace(
        "# Structure model sweep", "# Reconciliation-gap sweep", 1
    )
    text = "\n".join(
        f"Production default: `{PRODUCTION}`." if line.startswith("Production default") else line
        for line in text.split("\n")
    )
    text += f"\nBefore this section (EVAL_REPORT 47's replay): `{BEFORE}`.\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    if args.json is not None:
        args.json.write_text(
            json.dumps(
                {
                    "cells": [c.__dict__ for c in cells],
                    "ranking": ranked,
                    "pick": ranked[0][0] if ranked else None,
                    "runner_up": ranked[1][0] if len(ranked) > 1 else None,
                    "before": BEFORE,
                    "production": PRODUCTION,
                },
                indent=1,
                default=str,
            ),
            encoding="utf-8",
        )
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
