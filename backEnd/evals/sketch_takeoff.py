"""``python -m evals.sketch_takeoff``: what reading the appraisal's sketch changes in the
takeoff, replayed on stored exports (EVAL_REPORT section 96).

The takeoff derives the building's footprint as GLA / stories plus the cost approach's
garage line and its perimeter as a 1.5:1 rectangle (``basis.domain.measurements``). The
appraisal's sketch prints the areas themselves: each floor of living area, the garage, the
porch, each with the rectangles it was summed from (``basis.extraction.sketch``). This
harness measures, with no model call, what using them moves.

For each export it reads the sketch from the stored Azure Document Intelligence analysis of
the appraisal the export was extracted from (``--azure``, the SDK's ``as_dict`` JSON or the
stored ``AzureDiResult``; with ``--pdf`` the PDF's text layer is read too and the two
readings compared), writes the ``sketch`` fields into the export as the extraction would
have, and replays the takeoff under each setup (:func:`setups`):

* **today**: ``TAKEOFF_SKETCH=off``, the baseline every other setup is compared to. Its
  quantities are also compared to the export's stored ones (``stored_drift``: rows whose
  formula changed after the export was written move here, with or without this harness).
* **sketch on**: the setting on at its default tolerance. A sketch whose living area is not
  within the tolerance of the printed GLA is refused, and the takeoff must equal today's.
* **sketch on, check widened**: the tolerance set to admit the sketch whatever it says
  (what the sketch's areas alone would change, the GLA as printed).
* **GLA corrected, sketch off** and **GLA corrected, sketch on**: the printed GLA replaced
  by the sketch's living area, as an engineer who agreed with the sketch would correct it at
  the appraisal gate; the first isolates what the corrected GLA moves by itself (room areas,
  the square-foot model), the second adds the sketch.
* **... and the outline's perimeters** (with ``--outline`` or ``--read-outline``): the same,
  with the ground and upper-floor perimeters of the drawing. ``--outline`` takes them from a
  private file (section 96, before a reader existed); ``--read-outline`` reads them from the
  ``--pdf``'s line work with ``basis.extraction.sketch_outline`` as the extraction does with
  ``TAKEOFF_SKETCH_OUTLINE=on`` (section 100), and with both the two are compared. With the
  reader, two more setups show the choice it makes for a floor drawn on separate pages:
  **ground outline only** (the upper floors left a rectangle, as if their outline were
  withheld) and **upper blocks sharing a wall** (the summed outlines less twice the wall the
  ground story shares, as if the blocks adjoined there).

Each setup's takeoff is priced, carved and scored as section 87 did
(``evals.takeoff_constants.retakeoff``, ``evals.reprice_export``, ``evals.structure_replay``
at the production variant, ``evals.e2e_report``). ``--sample NAME=FILE:GLA`` adds a public
sample report to the cross-check table (its sketch's living area against its printed GLA),
which is what the tolerance rests on.

A site item the sketch could answer (a dimensioned patio) is counted, not repriced: the
export stores it as a count with no unit cost. Deterministic; cost $0. Usage (from
``backEnd/``)::

    BASIS_EVAL_DATA_DIR=... python -m evals.sketch_takeoff export_a.json export_b.json \\
        --azure recordings/<sha>/azure_di.json --pdf inputs/appraisal.pdf \\
        --cost-table costs/unit_costs_rsmeans_2020.json --supplement hci.json \\
        --read-outline --out work/e2e_artifacts/sketch_takeoff_<date>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from basis.config import Settings
from basis.domain.cost_indices import YearIndex, supplement_for
from basis.domain.cost_table import CostTable, load_cost_table
from basis.domain.measurements import (
    NEEDS_MEASUREMENT,
    SKETCH_KEYS,
    BuildingMeasurements,
    footprint,
    ground_footprint,
    measurements_from_fields,
    perimeter_ft,
    property_quantities,
)
from basis.domain.takeoffs import takeoff_key
from basis.extraction.sketch import (
    LIVING_AREA,
    SketchReading,
    read_document_sketch,
    read_sketch,
    sketch_fields,
)
from basis.extraction.sketch_outline import (
    GROUND_PERIMETER,
    UPPER_PERIMETER,
    page_drawings,
    read_outline,
)
from evals import record
from evals.e2e_report import (
    E2ERows,
    EngineerReport,
    default_labels_path,
    load_report,
    load_rows,
)
from evals.reprice_export import reprice
from evals.structure_replay import PRODUCTION, ReplayError, field_rows
from evals.structure_sweep import BUCKETS, Cell, baseline_scores, run_cell
from evals.takeoff_constants import engineer_shares, priced_summary, retakeoff, share_error

COMPONENT: Final = "sketch_takeoff"
VERSION: Final = "v2"  # v2: --read-outline (the outline reader) and its variants
GLA_KEY: Final = ("improvements", "gla_sqft")
WIDE: Final = Decimal(1)  # a tolerance that admits any sketch
PLACES: Final = Decimal("0.01")
DEFAULT_TOLERANCE: Final = Decimal(
    str(Settings.model_fields["takeoff_sketch_gla_tolerance"].default)
)


# --------------------------------------------------------------------------- reading the sketch


def azure_pages(path: Path) -> list[tuple[int, list[str]]]:
    """``(page number, lines)`` of a stored Azure DI analysis: the SDK's ``as_dict`` JSON
    (``pageNumber``, lines as objects with ``content``) or the stored ``AzureDiResult``
    (``page_number``, lines as strings)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[tuple[int, list[str]]] = []
    for page in raw.get("pages") or []:
        number = page.get("pageNumber", page.get("page_number"))
        lines = [
            str(line.get("content", "")) if isinstance(line, dict) else str(line)
            for line in page.get("lines") or []
        ]
        out.append((int(number), lines))
    return sorted(out)


def pdf_pages(path: Path) -> list[str]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def field_dicts(reading: SketchReading) -> list[dict[str, Any]]:
    """The reading's ``sketch`` fields as an export stores extraction fields."""
    fields, _ = sketch_fields(reading)
    return [
        {
            "section": item.section,
            "field": item.field,
            "value_num": str(item.value_num),
            "value_text": item.value_text,
            "value_date": None,
            "source": item.source,
            "page": item.page,
        }
        for item in fields.values()
    ]


def outline_fields(outline: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The private outline file's perimeters as ``sketch`` fields (source ``manual``)."""
    out = []
    for name in ("ground_perimeter_ft", "upper_perimeter_ft"):
        if outline.get(name) is None:
            continue
        section, field = SKETCH_KEYS[name].split(".", 1)
        out.append(
            {
                "section": section,
                "field": field,
                "value_num": str(outline[name]),
                "value_text": str(outline[name]),
                "value_date": None,
                "source": "manual",
                "page": outline.get("page"),
            }
        )
    return out


def read_outline_fields(
    reading: SketchReading, pdf: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """``(the perimeter fields the outline reader writes for this PDF, its trace)``: what
    the extraction adds with ``TAKEOFF_SKETCH_OUTLINE=on``, as an export stores fields."""
    table, _ = sketch_fields(reading)
    read = read_outline(reading, table, page_drawings(pdf.read_bytes(), reading.pages))
    fields = [
        {
            "section": item.section,
            "field": item.field,
            "value_num": str(item.value_num),
            "value_text": item.value_text,
            "value_date": None,
            "source": item.source,
            "page": item.page,
        }
        for item in read.fields.values()
    ]
    return fields, read.trace


GROUND_ONLY: Final = "ground outline only"
UPPER_SHARED: Final = "upper blocks sharing a wall"


def outline_variants(
    fields: Sequence[Mapping[str, Any]], trace: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """The other ways a floor drawn on separate pages could have been combined, for the
    side-by-side: the upper outline withheld, and the blocks taken to adjoin along the wall
    the ground story shares. Empty unless the reader summed blocks from separate pages."""
    upper = trace.get("upper") or {}
    shared = (trace.get("ground") or {}).get("shared_wall_ft")
    if not upper.get("separate_pages"):
        return {}
    ground_key, upper_key = GROUND_PERIMETER.split(".", 1)[1], UPPER_PERIMETER.split(".", 1)[1]
    ground = [dict(f) for f in fields if f["field"] == ground_key]
    out = {GROUND_ONLY: ground}
    if shared:
        less = Decimal(str(upper["perimeter_ft"])) - 2 * Decimal(str(shared))
        adjoined = [
            {**f, "value_num": str(less), "value_text": str(less)}
            for f in fields
            if f["field"] == upper_key
        ]
        out[UPPER_SHARED] = [*ground, *adjoined]
    return out


def with_fields(
    document: Mapping[str, Any],
    added: Sequence[Mapping[str, Any]],
    *,
    gla: Decimal | None = None,
) -> dict[str, Any]:
    """A copy of the export with ``added`` fields on its first extraction and, when ``gla``
    is given, its printed GLA replaced (the engineer's correction at the appraisal gate)."""
    out: dict[str, Any] = json.loads(json.dumps(document))
    extractions = out.get("extraction") or []
    if not extractions:
        raise ReplayError("the export has no extraction: nowhere to write the sketch fields")
    if gla is not None:
        for extraction in extractions:
            for f in extraction.get("fields") or []:
                if (f.get("section"), f.get("field")) == GLA_KEY:
                    f["value_num"], f["value_text"] = str(gla), str(gla)
    extractions[0].setdefault("fields", []).extend(dict(f) for f in added)
    return out


# --------------------------------------------------------------------------- the setups


@dataclass(frozen=True)
class Setup:
    name: str
    what: str
    tolerance: Decimal | None  # None: TAKEOFF_SKETCH=off
    gla_corrected: bool = False
    outline: bool = False
    variant: str | None = None  # a key of the run's outline variants, in place of the outline


OUTLINE_SETUP: Final = "GLA corrected, sketch on, outline perimeters"


def setups(
    tolerance: Decimal, *, outline: bool, read: bool = False, variants: Sequence[str] = ()
) -> list[Setup]:
    limit = f"{float(tolerance * 100):g} %"
    out = [
        Setup("today", "TAKEOFF_SKETCH=off: GLA / stories, the cost approach's garage", None),
        Setup("sketch on", f"TAKEOFF_SKETCH=on, tolerance {limit}, the GLA as printed", tolerance),
        Setup(
            "sketch on, check widened",
            "TAKEOFF_SKETCH=on with the cross-check admitting any sketch, the GLA as printed",
            WIDE,
        ),
        Setup(
            "GLA corrected, sketch off",
            "the printed GLA replaced by the sketch's living area, TAKEOFF_SKETCH=off",
            None,
            gla_corrected=True,
        ),
        Setup(
            "GLA corrected, sketch on",
            f"the same correction, TAKEOFF_SKETCH=on, tolerance {limit}",
            tolerance,
            gla_corrected=True,
        ),
    ]
    if outline:
        out.append(
            Setup(
                OUTLINE_SETUP,
                "the same, with the ground and upper-floor perimeters "
                + (
                    "read from the PDF's line work by the outline reader"
                    if read
                    else "measured from the drawing (not produced by the reader)"
                ),
                tolerance,
                gla_corrected=True,
                outline=True,
            )
        )
    for variant in variants:
        out.append(
            Setup(
                f"GLA corrected, sketch on, {variant}",
                f"the same as the outline setup, with {variant}",
                tolerance,
                gla_corrected=True,
                variant=variant,
            )
        )
    return out


def _q(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(PLACES))


def geometry(m: BuildingMeasurements) -> dict[str, str | None]:
    """The property-level geometry of a set of measurements, as the takeoff derives it."""
    quantities = property_quantities(m)
    base, ground = footprint(m), ground_footprint(m)
    roof = quantities.get("roof")
    perimeter = None if roof is None else Decimal(roof.inputs["perimeter_ft"])
    return {
        "dwelling_footprint_sf": None if base is None else _q(base.quantity),
        "ground_footprint_sf": None if ground is None else _q(ground.quantity),
        "ground_perimeter_ft": _q(perimeter),
        "roof_sf": None if roof is None else _q(roof.quantity),
        "siding_sf": _q(quantities["siding"].quantity) if "siding" in quantities else None,
        "gutter_lf": _q(quantities["gutter"].quantity) if "gutter" in quantities else None,
        "landscaping_sf": _q(quantities["landscaping"].quantity)
        if "landscaping" in quantities
        else None,
        "patio_sf": _q(quantities["patio"].quantity) if "patio" in quantities else None,
    }


def needs_measurement(
    assets: Sequence[Mapping[str, Any]], answered: Sequence[str]
) -> dict[str, int]:
    """How many site items still wait for a reviewer's measurement, and how many of them the
    takeoff's property quantities (``answered``: their labels) now size."""
    waiting = answered_now = 0
    for asset in assets:
        inputs = asset.get("takeoff_inputs") or {}
        if not inputs.get("needs_measurement"):
            continue
        key = takeoff_key(str(inputs.get("component") or asset.get("label") or ""))
        if key in NEEDS_MEASUREMENT and key in answered:
            answered_now += 1
        else:
            waiting += 1
    return {"waiting": waiting, "answered_by_sketch": answered_now}


@dataclass(frozen=True)
class Scored:
    export: str
    setup: str
    what: str
    runtime_ms: float
    sketch_check: str | None
    sketch_accepted: bool | None
    geometry: dict[str, str | None]
    stored_drift: list[dict[str, Any]]  # rows that differ from the export's stored quantity
    moved: int  # rows whose quantity differs from the today cell's on the same export
    quantities: dict[str, str]
    needs_measurement: dict[str, int]
    priced: int
    priced_total: str
    priced_total_ratio: float  # against the today cell on the same export
    itemized_shares: dict[str, float]
    itemized_error: float
    cell: Cell | None


def run_setup(
    name: str,
    document: Mapping[str, Any],
    setup: Setup,
    *,
    sketch: Sequence[Mapping[str, Any]],
    outline: Sequence[Mapping[str, Any]],
    sketch_living: Decimal | None,
    cost_table: CostTable | None,
    rows: E2ERows,
    report: EngineerReport,
    baseline: tuple[int, int],
    supplement: Mapping[int, YearIndex] | None,
    engineer: Mapping[str, float],
    today: Scored | None,
    variants: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> Scored:
    """One setup on one export: the export as the extraction would have written it (the
    sketch fields always present: the setting decides whether they are read), the takeoff,
    the repricing, the structure replay and the scorer."""
    started = time.perf_counter()
    drawn = (variants or {})[setup.variant] if setup.variant else outline if setup.outline else []
    added = [*sketch, *drawn]
    gla = sketch_living if setup.gla_corrected else None
    if setup.gla_corrected and gla is None:
        raise ReplayError("the sketch has no living area to correct the GLA to")
    prepared = with_fields(document, added, gla=gla)
    read = measurements_from_fields(field_rows(prepared), sketch_tolerance=setup.tolerance)
    taken, changes = retakeoff(prepared, sketch_tolerance=setup.tolerance)
    repriced, _ = reprice(
        taken,
        cost_table=cost_table,
        state=None,
        zip_code=None,
        supplement=supplement,
        cost_basis="installed",
        overhead="pro_rata",
    )
    cell = run_cell(
        name,
        repriced,
        PRODUCTION,
        cost_table=cost_table,
        rows=rows,
        report=report,
        baseline=baseline,
        supplement=supplement,
        label=setup.name,
    )
    priced = priced_summary(repriced["assets"])
    total = Decimal(priced["total"])
    mean, _ = share_error(priced["shares"], engineer)
    against = total if today is None else Decimal(today.priced_total)
    quantities = {str(a.get("id")): str(a.get("quantity")) for a in taken["assets"]}
    moved = (
        0
        if today is None
        else sum(1 for key, value in quantities.items() if today.quantities.get(key) != value)
    )
    return Scored(
        export=name,
        setup=setup.name,
        what=setup.what,
        runtime_ms=(time.perf_counter() - started) * 1000,
        sketch_check=None if read.sketch is None else read.sketch.check,
        sketch_accepted=None if read.sketch is None else read.sketch.accepted,
        geometry=geometry(read),
        stored_drift=list(changes["changed"]),
        moved=moved,
        quantities=quantities,
        needs_measurement=needs_measurement(repriced["assets"], list(property_quantities(read))),
        priced=priced["priced"],
        priced_total=priced["total"],
        priced_total_ratio=float((total / against).quantize(Decimal("0.0001"))) if against else 0.0,
        itemized_shares=priced["shares"],
        itemized_error=round(mean, 4),
        cell=cell,
    )


# --------------------------------------------------------------------------- the report


def pct_change(new: str | None, old: str | None) -> str:
    if new is None or old is None or Decimal(old) == 0:
        return "-"
    change = (Decimal(new) - Decimal(old)) / Decimal(old) * 100
    return f"{change:+.1f} %"


def _shares(shares: Mapping[str, float] | None) -> str:
    shares = shares or {}
    return " / ".join(f"{shares.get(b, 0.0):.1f}" for b in BUCKETS)


def _num(value: float | None, places: int = 2) -> str:
    return "-" if value is None else f"{value:.{places}f}"


GEOMETRY_ROWS: Final = (
    ("dwelling_footprint_sf", "dwelling footprint (SF)"),
    ("ground_footprint_sf", "ground footprint with the garage (SF)"),
    ("ground_perimeter_ft", "ground perimeter (ft)"),
    ("roof_sf", "roof (SF)"),
    ("siding_sf", "siding (SF)"),
    ("gutter_lf", "gutters (LF)"),
    ("landscaping_sf", "landscaping, upper bound (SF)"),
)


def render_markdown(
    cells: Sequence[Scored],
    reading: Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
    engineer: Mapping[str, float],
    *,
    private: bool,
) -> str:
    """The run's tables. ``private`` prints the quantities themselves (the run directory,
    outside the repository); without it only changes against today's takeoff are printed."""
    lines = [
        "# Sketch takeoff replay",
        "",
        "Engineer's class shares (%): "
        + ", ".join(f"{b} {engineer.get(b, 0.0):.1f}" for b in BUCKETS)
        + ". Cost per cell: $0 (no model call).",
        "",
        "## The sketch as read",
        "",
        f"Source: {reading.get('source')}; pages with a table: {reading.get('pages')}; "
        f"rows: {reading.get('rows')}, verified: {reading.get('verified')}; fields written: "
        f"{', '.join(reading.get('fields') or []) or 'none'}; text layer reads the same: "
        f"{reading.get('text_layer_equal')}.",
        "",
    ]
    if samples:
        lines += [
            "## Cross-check: the sketch's living area against the printed GLA",
            "",
            "| report | sketch pages | rows (verified) | sketch living area vs printed GLA | "
            "accepted at the tolerance |",
            "|---|---|---|---|---|",
        ]
        for s in samples:
            lines.append(
                f"| {s['name']} | {s['pages']} | {s['rows']} ({s['verified']}) | "
                f"{s['apart_pct']} | {s['accepted']} |"
            )
        lines.append("")
    for export in dict.fromkeys(c.export for c in cells):
        mine = [c for c in cells if c.export == export]
        today = next(c for c in mine if c.setup == "today")
        lines += [f"## {export}", "", "### Geometry", ""]
        header = "| quantity | " + " | ".join(c.setup for c in mine) + " |"
        lines += [header, "|---|" + "---|" * len(mine)]
        for key, title in GEOMETRY_ROWS:
            row = []
            for c in mine:
                value = c.geometry.get(key)
                change = pct_change(value, today.geometry.get(key))
                if c is today:
                    row.append(f"{value}" if private else "baseline")
                else:
                    row.append(f"{value} ({change})" if private else change)
            lines.append(f"| {title} | " + " | ".join(row) + " |")
        reference = next((c for c in mine if c.setup == OUTLINE_SETUP), None)
        if reference is not None:
            others = [c for c in mine if c is not reference]
            lines += [
                "",
                "### Against the drawing (the outline setup: the sketch's areas and its outline)",
                "",
                "| quantity | " + " | ".join(c.setup for c in others) + " |",
                "|---|" + "---|" * len(others),
            ]
            for key, title in GEOMETRY_ROWS:
                against = reference.geometry.get(key)
                row = [pct_change(c.geometry.get(key), against) for c in others]
                lines.append(f"| {title} | " + " | ".join(row) + " |")
        lines += [
            "",
            "### Schedule",
            "",
            "| setup | sketch | rows whose quantity moved | site items waiting for a measurement | "
            "priced | priced total (today = 1.00) | itemized shares y5 / y7 / y15 / y39 | "
            "itemized class error | scored shares y5 / y7 / y15 / y39 | scored mean class "
            "error | gap % | status | ms |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for c in mine:
            used = {None: "not read", True: "used", False: "refused"}[c.sketch_accepted]
            cell = c.cell
            lines.append(
                f"| {c.setup} | {used} | {c.moved} | "
                f"{c.needs_measurement['waiting']} | {c.priced} | {c.priced_total_ratio:.4f} | "
                f"{_shares(c.itemized_shares)} | {c.itemized_error:.2f} | "
                f"{_shares(None if cell is None else cell.bucket_share_pct)} | "
                f"{_num(None if cell is None else cell.mean_bucket_error_pct)} | "
                f"{_num(None if cell is None else cell.gap_share_pct, 1)} | "
                f"{None if cell is None else cell.status} | {c.runtime_ms:.0f} |"
            )
        lines.append("")
        for c in mine:
            if c.sketch_check and private:
                lines.append(f"- {c.setup}: {c.sketch_check}")
        lines.append("")
    refused = [c for c in cells if c.sketch_accepted is False]
    lines.append(
        "A refused sketch leaves the takeoff as today's: "
        + (
            "no setup refused a sketch"
            if not refused
            else "; ".join(f"{c.export} / {c.setup}: {c.moved} rows moved" for c in refused)
        )
        + "."
    )
    for c in cells:
        if c.setup == "today" and c.stored_drift:
            labels = sorted({str(row.get("label")) for row in c.stored_drift})
            lines.append(
                f"Stored drift on {c.export}: {len(c.stored_drift)} rows ({', '.join(labels)}) "
                "differ from the export's stored quantities under today's code, sketch or not."
            )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- main


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.sketch_takeoff", description=__doc__)
    parser.add_argument("exports", type=Path, nargs="+", help="rev-<n>.json files")
    parser.add_argument("--azure", type=Path, required=True, help="the appraisal's Azure DI JSON")
    parser.add_argument("--pdf", type=Path, default=None, help="the appraisal PDF (text layer)")
    parser.add_argument("--outline", type=Path, default=None, help="measured perimeters (JSON)")
    parser.add_argument(
        "--read-outline",
        action="store_true",
        help="read the perimeters from --pdf's line work (basis.extraction.sketch_outline)",
    )
    parser.add_argument("--cost-table", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, default=None, help="stored index values")
    parser.add_argument("--labels-dir", type=Path, default=None)
    parser.add_argument("--tolerance", type=Decimal, default=DEFAULT_TOLERANCE)
    parser.add_argument(
        "--sample",
        action="append",
        default=[],
        metavar="NAME=FILE:GLA",
        help="a report for the cross-check table: its Azure DI JSON and its printed GLA",
    )
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    return parser.parse_args(argv)


def sample_row(
    name: str, pages: Sequence[tuple[int, Sequence[str]]], gla: Decimal, tolerance: Decimal
) -> dict[str, Any]:
    """One line of the cross-check table: a report's sketch against its printed GLA."""
    reading = read_sketch(pages, "azure")
    if reading is None:
        return {
            "name": name,
            "pages": "none",
            "rows": 0,
            "verified": 0,
            "apart_pct": "no table",
            "accepted": "-",
        }
    fields, _ = sketch_fields(reading)
    living = fields.get(LIVING_AREA)
    base = {
        "name": name,
        "pages": len(reading.pages),
        "rows": len(reading.areas),
        "verified": sum(1 for a in reading.areas if a.verified),
    }
    if living is None or living.value_num is None:
        return {**base, "apart_pct": "living area not read", "accepted": "no"}
    apart = (living.value_num - gla) / gla
    return {
        **base,
        "apart_pct": f"{float(apart * 100):+.1f} %",
        "accepted": "yes" if abs(apart) <= tolerance else "no",
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component=COMPONENT, version=VERSION
    )
    try:
        rows_path = default_labels_path("e2e_rows_v1.json", args.labels_dir)
        report_path = default_labels_path("engineer_report_v1.json", args.labels_dir)
        rows, report = load_rows(rows_path), load_report(report_path)
        run.dataset(rows_path)
        run.dataset(report_path)
        engineer = engineer_shares(report)
        table = load_cost_table(str(args.cost_table))
        run.input("cost_table", args.cost_table)
        supplement = supplement_for(None if args.supplement is None else str(args.supplement))
        if args.supplement is not None:
            run.input("supplement", args.supplement)

        run.input("azure_di", args.azure)
        pages = azure_pages(args.azure)
        reading = read_sketch(pages, "azure")
        if reading is None or not reading.areas:
            raise ReplayError(f"{args.azure} has no area calculation table: nothing to replay")
        sketch = field_dicts(reading)
        fields, notes = sketch_fields(reading)
        living = fields.get(LIVING_AREA)
        text_equal: bool | None = None
        if args.pdf is not None:
            run.input("pdf", args.pdf)
            text = read_document_sketch(None, pdf_pages(args.pdf))
            text_equal = text is not None and [
                (a.label, a.kind, a.level, a.area_sf, a.page, a.verified) for a in text.areas
            ] == [(a.label, a.kind, a.level, a.area_sf, a.page, a.verified) for a in reading.areas]
            run.case("sketch:text_layer", "passed" if text_equal else "failed", kind="reading")
        read_summary = {
            "source": reading.source,
            "pages": list(reading.pages),
            "rows": len(reading.areas),
            "verified": sum(1 for a in reading.areas if a.verified),
            "fields": sorted(fields),
            "notes": notes,
            "text_layer_equal": text_equal,
            "reading": reading.as_json(),
        }
        outline: list[dict[str, Any]] = []
        variants: dict[str, list[dict[str, Any]]] = {}
        outline_trace: dict[str, Any] | None = None
        if args.outline is not None:
            run.input("outline", args.outline)
            outline = outline_fields(json.loads(args.outline.read_text(encoding="utf-8")))
        if args.read_outline:
            if args.pdf is None:
                raise ReplayError("--read-outline needs --pdf: the line work is in the PDF")
            measured = {f["field"]: Decimal(f["value_num"]) for f in outline}
            outline, outline_trace = read_outline_fields(reading, args.pdf)
            if not outline:
                raise ReplayError(
                    "the outline reader wrote no perimeter: "
                    + "; ".join(outline_trace.get("notes") or ["no reason recorded"])
                )
            variants = outline_variants(outline, outline_trace)
            if args.outline is not None:
                same = measured == {f["field"]: Decimal(f["value_num"]) for f in outline}
                run.case(
                    "outline:reader_equals_file", "passed" if same else "failed", kind="reading"
                )

        samples: list[dict[str, Any]] = []
        for spec in args.sample:
            name, _, rest = spec.partition("=")
            path, _, gla = rest.rpartition(":")
            run.input(f"sample:{name}", Path(path))
            samples.append(sample_row(name, azure_pages(Path(path)), Decimal(gla), args.tolerance))

        cells: list[Scored] = []
        for path in args.exports:
            document = json.loads(path.read_text(encoding="utf-8"))
            name = path.parent.parent.name if path.parent.name == "export" else path.stem
            run.input(name, path)
            baseline = baseline_scores(document, name, rows, report)
            printed = measurements_from_fields(field_rows(document)).gla_sf
            if printed is not None and living is not None and living.value_num is not None:
                samples.append(
                    sample_row(
                        f"{name} (the study's appraisal)", pages, printed.value, args.tolerance
                    )
                )
            today: Scored | None = None
            for setup in setups(
                args.tolerance,
                outline=bool(outline),
                read=args.read_outline,
                variants=list(variants),
            ):
                scored = run_setup(
                    name,
                    document,
                    setup,
                    sketch=sketch,
                    outline=outline,
                    sketch_living=None if living is None else living.value_num,
                    cost_table=table,
                    rows=rows,
                    report=report,
                    baseline=baseline,
                    supplement=supplement,
                    engineer=engineer,
                    today=today,
                    variants=variants,
                )
                if setup.name == "today":
                    today = scored
                cells.append(scored)
                # A refused sketch must leave every quantity as today's.
                ok = (scored.cell is None or scored.cell.sourced) and not (
                    scored.sketch_accepted is False and scored.moved
                )
                run.case(
                    f"{name}:{setup.name}",
                    "passed" if ok else "failed",
                    kind="setup",
                    detail={
                        "sketch_accepted": scored.sketch_accepted,
                        "rows_moved": scored.moved,
                        "stored_drift": len(scored.stored_drift),
                        "status": None if scored.cell is None else scored.cell.status,
                    },
                    seconds=scored.runtime_ms / 1000,
                )
        private = render_markdown(cells, read_summary, samples, engineer, private=True)
        public = render_markdown(cells, read_summary, samples, engineer, private=False)
        (args.out / "sketch.md").write_text(private, encoding="utf-8")
        (args.out / "sketch_public.md").write_text(public, encoding="utf-8")
        payload = {
            "tolerance": str(args.tolerance),
            "sketch": read_summary,
            "outline": outline,
            "outline_source": "reader" if args.read_outline else "file" if outline else None,
            "outline_trace": outline_trace,
            "samples": samples,
            "cells": [
                {
                    **{k: v for k, v in c.__dict__.items() if k not in ("cell", "quantities")},
                    "cell": None if c.cell is None else c.cell.__dict__,
                }
                for c in cells
            ],
            "engineer_shares_pct": engineer,
            "rectangle_perimeter_of_ground_footprint": {
                c.export: _q(perimeter_ft(Decimal(c.geometry["ground_footprint_sf"])))
                for c in cells
                if c.setup == "today" and c.geometry["ground_footprint_sf"] is not None
            },
        }
        (args.out / "sketch.json").write_text(
            json.dumps(payload, indent=1, default=str) + "\n", encoding="utf-8"
        )
        print(private)
        run.finish(
            {
                "exports": [c.export for c in cells if c.setup == "today"],
                "setups": len({c.setup for c in cells}),
                "cells": len(cells),
                "refused_equals_today": all(
                    c.moved == 0 for c in cells if c.sketch_accepted is False
                ),
                "stored_drift_rows": {
                    c.export: len(c.stored_drift) for c in cells if c.setup == "today"
                },
                "sketch_accepted_at_default": [
                    c.sketch_accepted for c in cells if c.setup == "sketch on"
                ],
                "cost_usd": 0,
            }
        )
    except (ReplayError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        run.finish(None, status=record.STATUS_FAILED, exit_code=2, error=str(exc))
        return 2
    return 0


__all__ = [
    "Scored",
    "Setup",
    "azure_pages",
    "field_dicts",
    "geometry",
    "needs_measurement",
    "outline_fields",
    "outline_variants",
    "pct_change",
    "read_outline_fields",
    "render_markdown",
    "run_setup",
    "sample_row",
    "setups",
    "with_fields",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
