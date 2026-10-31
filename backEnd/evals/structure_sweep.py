"""``python -m evals.structure_sweep``: every way of producing the structure's replacement
cost, scored the same way on stored exports (EVAL_REPORT section 44.3, STRUCTURE_MODEL.md
section 7).

The axes (``basis.domain.structure``): the building RCN's source (the appraisal's printed
total, the sum of its parts, the RSMeans square-foot model sized by the facing page's
matrix), the split into systems (the RSMeans model page, the NAHB 2024 stage shares, the
Marshall & Swift segregated-cost percentages, which have no source in the corpus and are
reported as such), the carve-out (the itemized rows subtracted from each component, or the
top-down figures left whole) and the depreciation (the appraiser's physical depreciation,
age-life, none). Every variant goes through :func:`evals.structure_replay.replay` and
:func:`evals.e2e_report.score` on each export, so the numbers are the scorer's: the kind C
rows produced and bucket right, the bucket shares against the engineer's and their errors,
the row-share distance, the reconciliation's status and gap, the implied uniform factor,
and the photo-visible rows' scores as the regression check (they must not move). Each
variant's wall time is measured; its cost is zero (deterministic, no model call).

The pick is by accuracy: the smallest mean absolute bucket-share error over the exports
among the variants that reconcile, ties by the row-share distance, then by the gap; the
runner-up is next. Usage (from ``backEnd/``)::

    BASIS_EVAL_DATA_DIR=... python -m evals.structure_sweep export_a.json export_b.json \\
        --cost-table costs/unit_costs_rsmeans_2020.json --out sweep.md --json sweep.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from basis.domain.cost_indices import YearIndex
from basis.domain.cost_table import load_cost_table
from basis.domain.structure import (
    DEPRECIATION_METHODS,
    MARSHALL_SWIFT_UNAVAILABLE,
    RCN_SOURCES,
    SPLIT_METHODS,
    StructureError,
)
from evals.e2e_report import (
    E2ERows,
    EngineerReport,
    default_labels_path,
    export_from_document,
    load_report,
    load_rows,
    score,
)
from evals.structure_replay import PRODUCTION, Variant, rebuild_intake, replay

BUCKETS = ("y5", "y7", "y15", "y39")


def grid() -> list[Variant]:
    return [
        Variant(rcn_source=r, split=s, carve=c, depreciation=d)
        for r, s, c, d in itertools.product(
            RCN_SOURCES, SPLIT_METHODS, (True, False), DEPRECIATION_METHODS
        )
    ]


@dataclass(frozen=True)
class Cell:
    """One variant on one export."""

    export: str
    variant: str
    sourced: bool
    note: str
    runtime_ms: float
    status: str | None = None
    gap_share_pct: float | None = None
    implied_uniform_factor: float | None = None
    allocation_factor: float | None = None
    kind_c_produced: int = 0
    kind_c_right: int = 0
    kind_c_wrong: tuple[str, ...] = ()
    bucket_share_pct: dict[str, float] | None = None
    bucket_error_pct: dict[str, float] | None = None
    mean_bucket_error_pct: float | None = None
    tvd_pct: float | None = None
    mean_row_error_pct: float | None = None
    visible_evidenced: int | None = None
    visible_right: int | None = None
    regression_ok: bool | None = None


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def run_cell(
    name: str,
    document: dict[str, Any],
    variant: Variant,
    *,
    cost_table: Any,
    rows: E2ERows,
    report: EngineerReport,
    baseline: tuple[int, int],
    supplement: Mapping[int, YearIndex] | None = None,
    label: str | None = None,
) -> Cell:
    """One variant on one export; ``supplement`` the stored index values (as the replay
    takes them), ``label`` the cell's name (default the variant's)."""
    started = time.perf_counter()
    try:
        intake = rebuild_intake(document, None)
        replayed, changes = replay(
            document, cost_table=cost_table, intake=intake, variant=variant, supplement=supplement
        )
    except StructureError as exc:
        return Cell(
            export=name,
            variant=label or variant.name,
            sourced=False,
            note=str(exc),
            runtime_ms=(time.perf_counter() - started) * 1000,
        )
    scored = score(export_from_document(replayed, name), rows, report)
    elapsed = (time.perf_counter() - started) * 1000
    c, sh = scored.summary["classification"], scored.summary["shares"]
    rec = changes["reconciliation"] or {}
    errors = {b: float(sh["bucket_share_error_pct"].get(b, 0.0)) for b in BUCKETS}
    visible = (int(c["evidenced"]), int(c["bucket_right_by_count"]))
    return Cell(
        export=name,
        variant=label or variant.name,
        sourced=True,
        note="",
        runtime_ms=elapsed,
        status=rec.get("status"),
        gap_share_pct=None if rec.get("gap_share") is None else float(rec["gap_share"]) * 100,
        implied_uniform_factor=_f(rec.get("implied_uniform_factor")),
        allocation_factor=None
        if changes["allocation"] is None
        else float(changes["allocation"]["factor"]),
        kind_c_produced=int(c["kind_c_produced"]),
        kind_c_right=int(c["kind_c_bucket_right"]),
        kind_c_wrong=tuple(c["kind_c_wrong_bucket_rows"]),
        bucket_share_pct={b: float(sh["pipeline_bucket_share_pct"].get(b, 0.0)) for b in BUCKETS},
        bucket_error_pct=errors,
        mean_bucket_error_pct=sum(errors.values()) / len(errors),
        tvd_pct=float(sh["total_variation_distance_pct"]),
        mean_row_error_pct=float(sh["mean_abs_row_share_error_pct"]),
        visible_evidenced=visible[0],
        visible_right=visible[1],
        regression_ok=visible == baseline,
    )


def baseline_scores(
    document: dict[str, Any], name: str, rows: E2ERows, report: EngineerReport
) -> tuple[int, int]:
    """The photo-visible rows' (evidenced, bucket right) on the export as stored."""
    c = score(export_from_document(document, name), rows, report).summary["classification"]
    return int(c["evidenced"]), int(c["bucket_right_by_count"])


def rank(cells: Sequence[Cell]) -> list[tuple[str, float, float, float]]:
    """Variants that are sourced and reconcile on every export, by mean absolute bucket-share
    error, then row-share distance, then absolute gap (all averaged over the exports)."""
    by_variant: dict[str, list[Cell]] = {}
    for cell in cells:
        by_variant.setdefault(cell.variant, []).append(cell)
    ranked = []
    for variant, group in by_variant.items():
        if not all(c.sourced and c.status == "reconciled" for c in group):
            continue
        n = len(group)
        ranked.append(
            (
                variant,
                sum(c.mean_bucket_error_pct or 0.0 for c in group) / n,
                sum(c.tvd_pct or 0.0 for c in group) / n,
                sum(abs(c.gap_share_pct or 0.0) for c in group) / n,
            )
        )
    ranked.sort(key=lambda r: (round(r[1], 4), round(r[2], 4), round(r[3], 4), r[0]))
    return ranked


def _num(value: float | None, places: int = 1) -> str:
    return "-" if value is None else f"{value:.{places}f}"


def render_markdown(
    cells: Sequence[Cell],
    ranked: Sequence[tuple[str, float, float, float]],
    engineer: dict[str, float],
) -> str:
    lines = [
        "# Structure model sweep",
        "",
        "Engineer's bucket shares (%): "
        + ", ".join(f"{b} {engineer.get(b, 0.0):.1f}" for b in BUCKETS)
        + ". Cost per variant: $0 (deterministic, no model call); runtime is the replay "
        "plus the scorer.",
        "",
    ]
    for export in dict.fromkeys(c.export for c in cells):
        lines += [
            f"## {export}",
            "",
            "| variant | status | gap % of B | implied uniform f | alloc f | "
            "kind C produced / right | "
            "shares y5 / y7 / y15 / y39 | errors y5 / y7 / y15 / y39 | mean bucket err | TVD | "
            "mean row err | visible evidenced / right | regression | ms |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for c in cells:
            if c.export != export:
                continue
            if not c.sourced:
                lines.append(
                    f"| {c.variant} | not sourced: {c.note} | | | | | | | | | | | | "
                    f"{c.runtime_ms:.0f} |"
                )
                continue
            shares = c.bucket_share_pct or {}
            errors = c.bucket_error_pct or {}
            wrong = f" ({'; '.join(c.kind_c_wrong)})" if c.kind_c_wrong else ""
            lines.append(
                f"| {c.variant} | {c.status} | {_num(c.gap_share_pct)} | "
                f"{_num(c.implied_uniform_factor, 3)} | {_num(c.allocation_factor, 3)} | "
                f"{c.kind_c_produced} / {c.kind_c_right}{wrong} | "
                f"{' / '.join(_num(shares.get(b)) for b in BUCKETS)} | "
                f"{' / '.join(_num(errors.get(b)) for b in BUCKETS)} | "
                f"{_num(c.mean_bucket_error_pct, 2)} | {_num(c.tvd_pct)} | "
                f"{_num(c.mean_row_error_pct, 2)} | {c.visible_evidenced} / {c.visible_right} | "
                f"{'ok' if c.regression_ok else 'MOVED'} | {c.runtime_ms:.0f} |"
            )
        lines.append("")
    lines += ["## Ranking (sourced and reconciled on every export)", ""]
    lines.append("| rank | variant | mean bucket err (points) | TVD (points) | abs gap (% of B) |")
    lines.append("|---|---|---|---|---|")
    for i, (variant, err, tvd, gap) in enumerate(ranked, 1):
        lines.append(f"| {i} | {variant} | {err:.2f} | {tvd:.1f} | {gap:.1f} |")
    if ranked:
        lines += [
            "",
            f"**Pick:** `{ranked[0][0]}`."
            + (f" **Runner-up:** `{ranked[1][0]}`." if len(ranked) > 1 else ""),
        ]
    production = PRODUCTION.name
    lines.append(f"Production default: `{production}`.")
    unsourced = sorted({c.note for c in cells if not c.sourced})
    if unsourced:
        lines += ["", "Not sourced (reported, never defaulted):"] + [f"- {n}" for n in unsourced]
    moved = [c for c in cells if c.sourced and c.regression_ok is False]
    lines += [
        "",
        "Regression check (photo-visible rows evidenced / bucket right against the stored export): "
        + (
            "every variant unchanged."
            if not moved
            else "MOVED on " + ", ".join(f"{c.export} {c.variant}" for c in moved)
        ),
    ]
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.structure_sweep", description=__doc__)
    parser.add_argument("exports", type=Path, nargs="+", help="rev-<n>.json files")
    parser.add_argument("--cost-table", type=Path, default=None)
    parser.add_argument("--labels-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True, help="the Markdown table")
    parser.add_argument("--json", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    rows = load_rows(default_labels_path("e2e_rows_v1.json", args.labels_dir))
    report = load_report(default_labels_path("engineer_report_v1.json", args.labels_dir))
    table = None if args.cost_table is None else load_cost_table(str(args.cost_table))
    cells: list[Cell] = []
    for path in args.exports:
        document = json.loads(path.read_text(encoding="utf-8"))
        name = path.parent.parent.name if path.parent.name == "export" else path.stem
        baseline = baseline_scores(document, name, rows, report)
        for variant in grid():
            cells.append(
                run_cell(
                    name,
                    document,
                    variant,
                    cost_table=table,
                    rows=rows,
                    report=report,
                    baseline=baseline,
                )
            )
    ranked = rank(cells)
    engineer = {b: float(v) for b, v in report.bucket_totals.items()}
    total = sum(engineer.values()) or 1.0
    engineer = {b: v / total * 100 for b, v in engineer.items()}
    text = render_markdown(cells, ranked, engineer)
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
                    "production": PRODUCTION.name,
                    "not_sourced": MARSHALL_SWIFT_UNAVAILABLE,
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
