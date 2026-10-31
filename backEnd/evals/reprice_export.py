"""``python -m evals.reprice_export``: reprice a stored study export with the cost indices.

What it measures (EVAL_REPORT section 45): what replacing the placeholder year and location
factors with RSMeans' published indices (``basis.domain.cost_indices``,
docs/review_2026-10-15/COST_INDICES.md) does to a study that already ran, without a paid
run. Every itemized asset that has a cost is priced again by ``estimate_cost`` from the same
cost table row, with the export's cost year and the study's location resolved through the
new tables (the city factor of ``--zip``, else the mean of ``--state``'s cities; the state
is read from the export's ``location_source`` when not given). The concealed rows are
dropped (``evals.structure_replay`` rebuilds them on the repriced export, so the square-foot
model takes the same factors) and ``summary`` is recomputed. The output is an export in the
export's own format.

Usage (from ``backEnd/``)::

    python -m evals.reprice_export export/rev-5.json --out out/rev-5.repriced.json \\
        [--cost-table costs/unit_costs_rsmeans_2020.json] [--state CA] [--zip 90001] \\
        [--supplement supplement.json] [--changes out/rev-5.reprice.json] \\
        [--cost-basis installed|bare] [--overhead pro_rata|none]
    python -m evals.structure_replay out/rev-5.repriced.json --out out/rev-5.structure.json ...
    python -m evals.e2e_report --export out/rev-5.structure.json ...

The repricing is checked against the stored dollars: the old unit cost divided by the old
factors must equal the row's base unit cost within a cent, else the asset is reported as
``base_mismatch`` (a cost table row that changed since the run). A summary goes to stdout
(and ``--changes`` as JSON).

``--cost-basis`` and ``--overhead`` (STRUCTURE_MODEL.md section 9.3, EVAL_REPORT 49): the
basis every row is priced on (installed: the line's Total Incl O&P; bare: material + labor +
equipment, the basis the exports were priced on) and whether a row inside the square-foot
model carries the model's general contractor's O&P pro rata (the model chosen from the
export's extraction fields, as the structure model chooses it). The base check is made on
the bare basis either way.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from basis.domain.cost_indices import (
    YearIndex,
    location_factor_for_state_code,
    location_factor_for_zip,
    supplement_for,
)
from basis.domain.cost_table import CostTable, load_cost_table
from basis.domain.costs import (
    COST_BASES,
    DEFAULT_COST_BASIS,
    CostBasis,
    CostError,
    Markup,
    estimate_cost,
    year_factor_index,
)
from basis.domain.measurements import measurements_from_fields
from basis.domain.structure import (
    DEFAULT_OVERHEAD_ALLOCATION,
    OVERHEAD_ALLOCATIONS,
    OverheadAllocation,
    cost_approach_from_fields,
    inside_model,
    overhead_markup,
    select_model,
)
from basis.domain.takeoffs import Takeoff
from evals.structure_replay import EXPORT_FORMATS, ReplayError, field_rows, takeoff_block

CENT = Decimal("0.01")
EXTRAPOLATED = "cost:YEAR_INDEX_MISSING"
BUCKET_KEYS = {"y5": "y5", "y7": "y7", "y15": "y15", "y27_5": "y27_5", "y39": "y39"}


def _concealed(asset: Mapping[str, Any]) -> bool:
    inputs = asset.get("takeoff_inputs")
    return isinstance(inputs, dict) and bool(inputs.get("concealed"))


def _origin(source: str | None) -> str:
    """``intake`` from ``state CA (intake)``; the whole parenthesis otherwise."""
    if not source:
        return "export"
    match = re.search(r"\(([^()]*(?:\([^()]*\))?[^()]*)\)\s*$", source)
    return match.group(1) if match else "export"


def _state_from(source: str | None) -> str | None:
    match = re.search(r"\bstate ([A-Za-z]{2})\b", source or "")
    return match.group(1).upper() if match else None


def reprice(
    document: Mapping[str, Any],
    *,
    cost_table: CostTable | None,
    state: str | None,
    zip_code: str | None,
    supplement: Mapping[int, YearIndex] | None,
    cost_basis: CostBasis = "bare",
    overhead: OverheadAllocation = "none",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(the repriced export, a summary of the run)``."""
    if document.get("format") not in EXPORT_FORMATS:
        raise ReplayError(f"not a study export (format {document.get('format')!r})")
    block = takeoff_block(document)
    cost_year = int(block.get("cost_year") or 0)
    if not cost_year:
        raise ReplayError("the export's summary.takeoff has no cost_year")
    old_location = Decimal(str(block.get("location_factor") or "1"))
    year_source = block.get("cost_year_source")
    old_location_source = block.get("location_source")
    origin = _origin(old_location_source)
    state = state or _state_from(old_location_source)

    year = year_factor_index(cost_year, supplement)
    location = None
    if zip_code:
        location = location_factor_for_zip(zip_code)
        kind, value = "zip", zip_code
    if location is None and state:
        location = location_factor_for_state_code(state)
        kind, value = "state", state
    if location is None:
        raise ReplayError("no location: pass --state or --zip (the export names neither)")
    location_source = f"{kind} {value} ({origin}): {location.citation()}"
    new_location = location.factor
    markup: Markup | None = None
    if overhead == "pro_rata":
        fields = field_rows(document)
        read = measurements_from_fields(fields)
        choice = select_model(None if read.is_empty else read, cost_approach_from_fields(fields))
        markup = overhead_markup(choice.model)

    out = json.loads(json.dumps(document))
    kept: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    old_total = new_total = Decimal(0)
    old_year_factor: Decimal | None = None
    for asset in out["assets"]:
        if _concealed(asset):
            counts["concealed_dropped"] += 1
            continue
        kept.append(asset)
        if asset.get("total") is None or asset.get("bucket") == "land":
            counts["unpriced"] += 1
            continue
        inputs = asset.get("takeoff_inputs") or {}
        component = str(inputs.get("component") or asset["label"])
        takeoff = Takeoff(
            component=component,
            quantity=Decimal(str(asset["quantity"])),
            unit=str(asset["unit"]),
            formula=str(asset.get("formula") or ""),
        )
        try:
            bare = estimate_cost(
                takeoff,
                cost_year,
                new_location,
                table=cost_table,
                label=str(asset["label"]),
                year_source=year_source,
                location_source=location_source,
                year=year,
                basis="bare",
            )
            estimate = estimate_cost(
                takeoff,
                cost_year,
                new_location,
                table=cost_table,
                label=str(asset["label"]),
                year_source=year_source,
                location_source=location_source,
                year=year,
                basis=cost_basis,
                markup=markup if inside_model(str(asset["label"])) else None,
            )
        except CostError as exc:
            counts["not_repriced"] += 1
            asset.setdefault("takeoff_inputs", {})["reprice_error"] = str(exc)
            continue
        # The stored dollars against the row: old unit cost / old factors = the base.
        if old_year_factor is None:
            match = re.search(r"x([0-9.]+) for \d{4}", str(asset.get("cost_source") or ""))
            old_year_factor = Decimal(match.group(1)) if match else None
        if old_year_factor is not None:
            implied = Decimal(str(asset["unit_cost"])) / (old_year_factor * old_location)
            if abs(implied - bare.base_unit_cost) > CENT:
                counts["base_mismatch"] += 1
        old_total += Decimal(str(asset["total"]))
        new_total += estimate.total
        asset["unit_cost"] = str(estimate.unit_cost)
        asset["total"] = str(estimate.total)
        asset["cost_source"] = estimate.source_row
        asset["cost_year"] = cost_year
        asset["location_factor"] = f"{new_location:.3f}"
        inputs = dict(asset.get("takeoff_inputs") or {})
        inputs["location_source"] = location_source
        if year.assumption is not None:
            inputs["cost_year_assumption"] = year.assumption
            reasons = [r for r in str(asset.get("review_reason") or "").split("; ") if r]
            if EXTRAPOLATED not in reasons:
                reasons.append(EXTRAPOLATED)
            asset["review_reason"] = "; ".join(reasons)
            asset["needs_review"] = True
        asset["takeoff_inputs"] = inputs
        counts["repriced"] += 1
    out["assets"] = kept

    summary = out["summary"]
    total = sum((Decimal(str(a["total"])) for a in kept if a.get("total") is not None), Decimal(0))
    by_bucket: dict[str, dict[str, Any]] = {}
    for asset in kept:
        if asset.get("total") is None:
            continue
        entry = by_bucket.setdefault(asset["bucket"], {"assets": 0, "total": Decimal(0)})
        entry["assets"] += 1
        entry["total"] += Decimal(str(asset["total"]))
    summary["total_cost"] = str(total.quantize(CENT))
    summary["by_bucket"] = {
        bucket: {
            "assets": entry["assets"],
            "total": str(entry["total"].quantize(CENT)),
            "percent_of_total": str(
                (entry["total"] / total * 100).quantize(CENT, rounding=ROUND_HALF_UP)
                if total
                else Decimal(0)
            ),
        }
        for bucket, entry in sorted(by_bucket.items())
    }
    summary["assets"] = len(kept)
    summary["needs_review"] = sum(1 for a in kept if a.get("needs_review"))
    block = dict(summary["takeoff"])
    block["location_factor"] = f"{new_location:.3f}"
    block["location_source"] = location_source
    if year.assumption is not None:
        block["cost_year_assumption"] = year.assumption
    block["area_sources"] = {
        k: v for k, v in dict(block.get("area_sources") or {}).items() if k != "structure_model"
    }
    summary["takeoff"] = block
    changes = {
        "cost_year": cost_year,
        "year_factor": {"old": str(old_year_factor), "new": str(year.factor)},
        "year_extrapolated": year.extrapolated,
        "year_citation": year.citation(),
        "location_factor": {"old": str(old_location), "new": str(new_location)},
        "location_source": {"old": old_location_source, "new": location_source},
        "cost_basis": cost_basis,
        "overhead": overhead,
        "overhead_rate": None if markup is None else str(markup.rate),
        "counts": dict(counts),
        "priced_total": {
            "old": str(old_total.quantize(CENT)),
            "new": str(new_total.quantize(CENT)),
        },
        "priced_total_ratio": str(
            (new_total / old_total).quantize(Decimal("0.0001")) if old_total else Decimal(0)
        ),
    }
    return out, changes


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.reprice_export", description=__doc__)
    parser.add_argument("export", type=Path, help="rev-<n>.json of a study")
    parser.add_argument("--out", type=Path, required=True, help="the repriced export")
    parser.add_argument("--cost-table", type=Path, default=None)
    parser.add_argument("--state", default=None, help="two-letter code; default: the export's")
    parser.add_argument("--zip", dest="zip_code", default=None, help="the study's ZIP")
    parser.add_argument("--supplement", type=Path, default=None, help="later index values")
    parser.add_argument("--changes", type=Path, default=None, help="the run summary as JSON")
    parser.add_argument("--cost-basis", choices=COST_BASES, default=DEFAULT_COST_BASIS)
    parser.add_argument(
        "--overhead", choices=OVERHEAD_ALLOCATIONS, default=DEFAULT_OVERHEAD_ALLOCATION
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    document = json.loads(args.export.read_text(encoding="utf-8"))
    table = None if args.cost_table is None else load_cost_table(str(args.cost_table))
    supplement = supplement_for(None if args.supplement is None else str(args.supplement))
    repriced, changes = reprice(
        document,
        cost_table=table,
        state=args.state,
        zip_code=args.zip_code,
        supplement=supplement,
        cost_basis=args.cost_basis,
        overhead=args.overhead,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(repriced, indent=1) + "\n", encoding="utf-8")
    if args.changes is not None:
        args.changes.write_text(json.dumps(changes, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(changes, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
