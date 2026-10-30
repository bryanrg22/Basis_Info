"""``python -m evals.structure_replay``: run the structure model over a stored study export.

What it measures (EVAL_REPORT section 44): what the structure model (``basis.domain.structure``,
docs/review_2026-10-15/STRUCTURE_MODEL.md) adds to a study that already ran, without a paid
run. From the export's extraction fields it reads the appraisal's cost approach and the
building's measurements; from the export's assets the itemized rows; it then does what
``process_assets`` does after the component loop (the building RCN, the split, the carve-out,
the concealed rows, each classified by the rules table and priced from the residual or from
the cost table's row for its label) and what the result does (depreciation, reconciliation,
the allocation on RCNLD within tolerance). The output is the export with the concealed
assets appended and ``summary.basis`` replaced, in the export's own format, so
:mod:`evals.e2e_report` scores it like any export and the kind C rows become scored rows.

Usage (from ``backEnd/``)::

    python -m evals.structure_replay export/rev-5.json --out export/rev-5.structure.json \\
        [--cost-table costs/unit_costs_rsmeans_2020.json] [--intake intake.json] \\
        [--rcn-source appraisal_total|appraisal_parts|square_foot] \\
        [--split rsmeans|nahb|marshall_swift] [--no-carve] \\
        [--depreciation appraisal_physical|age_life|none]
    python -m evals.e2e_report --export export/rev-5.structure.json ...

The variant options are the sweep's axes (``evals.structure_sweep``, STRUCTURE_MODEL.md
section 7); the defaults are the production choice.

The intake is rebuilt from the export's ``summary.basis.worksheet`` (price, land as a flat
share with the worksheet's source text, the capitalized costs, the use and dates); pass
``--intake`` (the intake document as ``PUT /v1/studies/{id}/intake`` takes it) to use the
study's own. The cost year, location factor and property class are the export's
``summary.takeoff``. A summary of the run goes to stdout (and ``--changes`` as JSON).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from basis.domain.cost_indices import YearIndex, supplement_for
from basis.domain.cost_table import CostTable, load_cost_table
from basis.domain.costs import CostError, NeedsQuantity, estimate_cost, year_factor_index
from basis.domain.intake import StudyIntake, StudyIntakeIn, with_location
from basis.domain.measurements import measurements_from_fields
from basis.domain.rules import RECOVERY_YEARS, lookup
from basis.domain.structure import (
    DEFAULT_DEPRECIATION,
    DEFAULT_RCN_SOURCE,
    DEFAULT_SPLIT,
    DEPRECIATION_METHODS,
    RCN_SOURCES,
    SPLIT_METHODS,
    DepreciationMethod,
    ItemizedRow,
    RcnSource,
    SplitMethod,
    StructureError,
    StructureInputs,
    building_rcn,
    carve_out,
    concealed_rows,
    cost_approach_from_fields,
    small_system_inputs,
    split_components,
)
from basis.domain.takeoffs import Takeoff
from basis.evidence.result import ScheduleRow, compute_result, concealed_label

EXPORT_FORMATS = frozenset({"basis-export/2"})
# The replayed assets' ids: one per label, stable across runs.
REPLAY_NAMESPACE = uuid.UUID("c4c1e3a2-4d2b-4f0e-9a8b-7d6c5b4a3f21")


class ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class Variant:
    """One point of the sweep (STRUCTURE_MODEL.md section 7)."""

    rcn_source: RcnSource = DEFAULT_RCN_SOURCE
    split: SplitMethod = DEFAULT_SPLIT
    carve: bool = True
    depreciation: DepreciationMethod = DEFAULT_DEPRECIATION

    @property
    def name(self) -> str:
        carve = "carve" if self.carve else "top_down"
        return f"{self.rcn_source}+{self.split}+{carve}+{self.depreciation}"


PRODUCTION: Final = Variant()


@dataclass(frozen=True)
class FieldRowView:
    """An export's extraction field as ``measurements_from_fields`` reads one."""

    section: str
    field: str
    value_num: Decimal | None
    value_text: str | None
    value_date: date | None
    source: str


def field_rows(document: Mapping[str, Any]) -> list[FieldRowView]:
    rows: list[FieldRowView] = []
    for extraction in document.get("extraction") or []:
        for f in extraction.get("fields") or []:
            num = f.get("value_num")
            when = f.get("value_date")
            rows.append(
                FieldRowView(
                    section=str(f.get("section")),
                    field=str(f.get("field")),
                    value_num=None if num is None else Decimal(str(num)),
                    value_text=f.get("value_text"),
                    value_date=None if not when else date.fromisoformat(str(when)),
                    source=str(f.get("source") or "llm"),
                )
            )
    return rows


def takeoff_block(document: Mapping[str, Any]) -> dict[str, Any]:
    summary = document.get("summary")
    block = summary.get("takeoff") if isinstance(summary, dict) else None
    if not isinstance(block, dict):
        raise ReplayError("the export has no summary.takeoff (written since EVAL_REPORT 37)")
    return block


def rebuild_intake(document: Mapping[str, Any], override: Path | None) -> StudyIntake | None:
    """The study's intake: the file given, else the worksheet's figures (land as a flat
    share of the price with the worksheet's own source text), else None."""
    if override is not None:
        body = json.loads(override.read_text(encoding="utf-8"))
        return with_location(
            StudyIntakeIn.model_validate(body), appraisal_state=None, appraisal_zip=None
        )
    summary = document.get("summary") or {}
    basis = summary.get("basis") or {}
    ws = basis.get("worksheet")
    if not isinstance(ws, dict):
        return None
    price = Decimal(str(ws["purchase_price"]))
    land = Decimal(str(ws["land"]))
    pct = (land / price * 100).quantize(Decimal("0.000001"))
    body = {
        "purchase_price": str(price),
        "acquisition_costs": [
            {
                "category": c["category"],
                "amount": str(c["amount"]),
                "description": c.get("description"),
            }
            for c in ws.get("acquisition_costs") or []
        ],
        "land_method": "flat_percentage",
        "flat_land_percentage": str(pct),
        "flat_land_source": f"replayed from the export's worksheet: {ws.get('land_source')}",
        "property_use": ws["property_use"],
        "transient_lodging": ws["property_use"] == "nonresidential",
        "placed_in_service_date": ws["placed_in_service_date"],
        "acquisition_date": ws.get("acquisition_date"),
        "state": ws.get("state"),
        "zip": ws.get("zip"),
    }
    return with_location(
        StudyIntakeIn.model_validate(body),
        appraisal_state=ws.get("state"),
        appraisal_zip=ws.get("zip"),
    )


def itemized_rows(assets: Sequence[Mapping[str, Any]]) -> list[ItemizedRow]:
    rows = []
    for a in assets:
        if a.get("bucket") == "land":
            continue
        raw = a.get("takeoff_inputs")
        inputs: dict[str, Any] = raw if isinstance(raw, dict) else {}
        count = inputs.get("count")
        total = a.get("total")
        rows.append(
            ItemizedRow(
                a.get("label"),
                None if total is None else Decimal(str(total)),
                int(count) if isinstance(count, int) and count > 0 else 1,
            )
        )
    return rows


def replay(
    document: Mapping[str, Any],
    *,
    cost_table: CostTable | None,
    intake: StudyIntake | None,
    variant: Variant = PRODUCTION,
    supplement: Mapping[int, YearIndex] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(the replayed export, a summary of the run)``; ``StructureError`` for a variant
    that has no source (the Marshall & Swift split)."""
    if document.get("format") not in EXPORT_FORMATS:
        raise ReplayError(f"not a study export (format {document.get('format')!r})")
    existing = [a for a in document["assets"] if not concealed_label(a.get("takeoff_inputs"))[1]]
    fields = field_rows(document)
    takeoff = takeoff_block(document)
    property_class = str(takeoff.get("property_class") or "residential_rental")
    cost_year = int(takeoff.get("cost_year") or 2026)
    location_factor = Decimal(str(takeoff.get("location_factor") or "1"))
    read = measurements_from_fields(fields)
    measurements = None if read.is_empty else read
    cost_approach = cost_approach_from_fields(fields)
    # The study's resolved time factor: the stored index values (``--supplement``, what
    # ``python -m basis.costs.refresh export`` writes) over the released book, as the
    # assets step resolves it; the square-foot RCN and the small systems price on it.
    year = year_factor_index(cost_year, supplement)
    inputs = StructureInputs(
        measurements=measurements,
        cost_approach=cost_approach,
        cost_year=cost_year,
        year_source=takeoff.get("cost_year_source"),
        location_factor=location_factor,
        location_source=takeoff.get("location_source"),
        year=year,
        rcn_source=variant.rcn_source,
        split=variant.split,
        carve=variant.carve,
        depreciation_method=variant.depreciation,
    )
    building = building_rcn(
        measurements,
        cost_approach,
        cost_year=cost_year,
        year_source=inputs.year_source,
        location_factor=location_factor,
        location_source=inputs.location_source,
        rcn_source=variant.rcn_source,
        year=year,
        supplement=supplement,
    )
    itemized = itemized_rows(existing)
    carve = None
    if building is not None:
        carve = carve_out(
            split_components(building.amount, building.model, variant.split),
            itemized,
            carve=variant.carve,
        )
    small = small_system_inputs(measurements, cost_approach, itemized)
    rows = concealed_rows(building, carve, inputs.living_area, small, variant.split)

    appended: list[dict[str, Any]] = []
    for row in rows:
        hit = lookup(row.label, None, "permanent", property_class)
        if hit is None:
            raise ReplayError(f"no rules row for {row.label!r}")
        reasons = list(row.review_reasons)
        if hit.contested:
            reasons.append("contested")
        takeoff_obj = Takeoff(
            component=row.label,
            quantity=row.quantity,
            unit=row.unit,
            formula=row.formula,
            method=row.method,
        )
        unit_cost: Decimal | None
        total: Decimal | None
        if row.rcn is not None:
            total = row.rcn
            unit_cost = (
                (row.rcn / row.quantity).quantize(Decimal("0.01")) if row.quantity else row.rcn
            )
            source = row.source
            factor: Decimal | None = None
        else:
            factor = location_factor
            try:
                estimate = estimate_cost(
                    takeoff_obj,
                    cost_year,
                    location_factor,
                    table=cost_table,
                    label=row.label,
                    year_source=inputs.year_source,
                    location_source=inputs.location_source,
                    year=year,
                )
            except NeedsQuantity:
                estimate = None
                reasons.append("cross:COST_NEEDS_QUANTITY")
            except CostError:
                estimate = None
                reasons.append("cross:COST_ROW_MISSING")
            unit_cost = None if estimate is None else estimate.unit_cost
            total = None if estimate is None else estimate.total
            source = (
                estimate.source_row
                if estimate is not None
                else f"missing: no cost row for {row.label!r} per {row.unit}"
            )
        appended.append(
            {
                "id": str(uuid.uuid5(REPLAY_NAMESPACE, f"{document['study_id']}:{row.label}")),
                "component_id": None,
                "label": row.label,
                "material": None,
                "attachment": "permanent",
                "room_id": None,
                "room_name": None,
                "room_type": None,
                "category": row.category,
                "bucket": hit.bucket.value,
                "recovery_period": str(RECOVERY_YEARS[hit.bucket]),
                "irs_section": hit.section,
                "method": "rule",
                "confidence": hit.confidence,
                "needs_review": bool(reasons) or row.needs_review,
                "review_reason": "; ".join(dict.fromkeys(reasons)) or None,
                "quantity": str(row.quantity),
                "unit": row.unit,
                "formula": row.formula,
                "takeoff_inputs": {
                    **row.inputs,
                    "concealed": True,
                    "label": row.label,
                    "system": row.system,
                    "component": row.component,
                    "method": row.method,
                    "assumptions": list(row.assumptions),
                    "quantity_source": "structure_model",
                    "structure_source": row.source,
                },
                "unit_cost": None if unit_cost is None else str(unit_cost),
                "total": None if total is None else str(total),
                "cost_year": cost_year,
                "location_factor": None if factor is None else str(factor),
                "cost_source": source,
                "citations": [],
                "convention": None,
                "provenance": {"origin": "model", "replayed_by": "evals.structure_replay"},
            }
        )

    all_assets = [*existing, *appended]
    schedule = [
        ScheduleRow(
            asset_id=str(a["id"]),
            category=str(a["category"]),
            bucket=str(a["bucket"]),
            rcn=None if a.get("total") is None else Decimal(str(a["total"])),
            label=a.get("label"),
            concealed=concealed_label(a.get("takeoff_inputs"))[1],
        )
        for a in all_assets
    ]
    result = compute_result(schedule, intake, int(document["revision"]), inputs)
    out = dict(document)
    out["assets"] = all_assets
    summary = dict(document.get("summary") or {})
    summary["basis"] = result.model_dump(mode="json")
    summary["structure_replay"] = {
        "appended": len(appended),
        "cost_table": None if cost_table is None else cost_table.name,
        "intake": "export worksheet" if intake is not None else None,
        "variant": variant.name,
    }
    out["summary"] = summary
    changes = {
        "building_rcn": None
        if building is None
        else {
            "amount": str(building.amount),
            "source": building.source,
            "model": building.model.name,
        },
        "appended": [
            {
                "label": a["label"],
                "bucket": a["bucket"],
                "quantity": a["quantity"],
                "unit": a["unit"],
                "total": a["total"],
                "needs_review": a["needs_review"],
                "review_reason": a["review_reason"],
            }
            for a in appended
        ],
        "depreciation": None
        if result.depreciation is None
        else result.depreciation.model_dump(mode="json"),
        "reconciliation": None
        if result.reconciliation is None
        else {
            "status": result.reconciliation.status,
            "building_basis": str(result.reconciliation.building_basis),
            "rcn_total": str(result.reconciliation.rcn_total),
            "rcnld_total": None
            if result.reconciliation.rcnld_total is None
            else str(result.reconciliation.rcnld_total),
            "gap": None if result.reconciliation.gap is None else str(result.reconciliation.gap),
            "gap_share": None
            if result.reconciliation.gap_share is None
            else str(result.reconciliation.gap_share),
            "implied_uniform_factor": None
            if result.reconciliation.implied_uniform_factor is None
            else str(result.reconciliation.implied_uniform_factor),
            "unassigned_remainders": [
                {"component": c.component, "top_down": str(c.top_down), "itemized": str(c.itemized)}
                for c in result.reconciliation.unassigned_remainders
            ],
        },
        "allocation": None
        if result.allocation is None
        else {
            "factor": str(result.allocation.factor),
            "by_bucket": [
                {"bucket": b.bucket.value, "percent": str(b.percent), "allocated": str(b.allocated)}
                for b in result.allocation.by_bucket
            ],
        },
        "missing": result.missing,
    }
    return out, changes


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.structure_replay", description=__doc__)
    parser.add_argument("export", type=Path, help="rev-<n>.json of a study")
    parser.add_argument("--out", type=Path, required=True, help="the replayed export")
    parser.add_argument(
        "--cost-table", type=Path, default=None, help="the unit-cost table (private)"
    )
    parser.add_argument("--intake", type=Path, default=None, help="the intake document (JSON)")
    parser.add_argument("--changes", type=Path, default=None, help="the run summary as JSON")
    parser.add_argument(
        "--supplement",
        type=Path,
        default=None,
        help="later index values (python -m basis.costs.refresh export), as reprice_export",
    )
    parser.add_argument("--rcn-source", choices=RCN_SOURCES, default=DEFAULT_RCN_SOURCE)
    parser.add_argument("--split", choices=SPLIT_METHODS, default=DEFAULT_SPLIT)
    parser.add_argument("--no-carve", action="store_true", help="top-down only: no carve-out")
    parser.add_argument(
        "--depreciation", choices=DEPRECIATION_METHODS, default=DEFAULT_DEPRECIATION
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    document = json.loads(args.export.read_text(encoding="utf-8"))
    table = None if args.cost_table is None else load_cost_table(str(args.cost_table))
    variant = Variant(
        rcn_source=args.rcn_source,
        split=args.split,
        carve=not args.no_carve,
        depreciation=args.depreciation,
    )
    try:
        intake = rebuild_intake(document, args.intake)
        supplement = supplement_for(None if args.supplement is None else str(args.supplement))
        out, changes = replay(
            document, cost_table=table, intake=intake, variant=variant, supplement=supplement
        )
    except (ReplayError, StructureError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, sort_keys=True, indent=1), encoding="utf-8")
    if args.changes is not None:
        args.changes.write_text(json.dumps(changes, indent=1), encoding="utf-8")
    print(json.dumps(changes, indent=1))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
