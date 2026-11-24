"""``python -m evals.sketch_outline``: what the outline reader does with stored appraisal PDFs
(EVAL_REPORT section 100).

``basis.extraction.sketch_outline`` reads a sketch's outline from the PDF's own line work and
believes a loop only when the page's table agrees with it. This harness runs it, with no
model call, over PDFs on disk and answers three things:

* **The survey.** For each ``--document NAME=PDF[:AZURE_JSON]``: where the area table was
  read from (the stored Azure Document Intelligence analysis when one is given, else the
  PDF's text layer), and for each sketch page whether it carries line work at all (strokes,
  loops, images), what the reader made of it (its status, the rows reproduced, the scale)
  and which perimeter fields were written, with the time it took.
* **The checks, side by side.** On every page with loops and verified rows, each
  ``(row, loop)`` pair fixes a scale by area. Three ways of believing a pair are counted on
  the same pairs: *area at one scale* (how many different scales reproduce two or more rows
  by area alone), *walls on the printed precision* (the loop's every wall lands on the whole,
  half or tenth foot the calculation prints) and *printed dimensions* (the rule that
  shipped: the row's rectangles span the loop's corners). The pairs the reader accepted are
  the reference; a check that passes other pairs would have believed the wrong loop.
* **The reference.** With ``--reference`` (a private JSON of perimeters measured from the
  first document's drawing by hand-checked tracing), whether the reader reproduces it.

Writes ``outline.json`` (everything, including dimensions: private), ``outline.md`` (the
same as tables) and ``outline_public.md`` (statuses and counts only, no dimension of any
property). Deterministic; cost $0. Usage (from ``backEnd/``)::

    BASIS_EVAL_DATA_DIR=... python -m evals.sketch_outline \\
        --document "client appraisal=inputs/appraisal.pdf:recordings/<sha>/azure_di.json" \\
        --document "public sample SF3=gse_d1/SF3/appraisal.pdf:recordings/<sha>/azure_di.json" \\
        --reference work/sketch_reader/outline.json --out work/e2e_artifacts/sketch_outline_<date>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from basis.extraction.pdf_text import read_pdf
from basis.extraction.sketch import SketchArea, SketchReading, read_document_sketch, read_sketch
from basis.extraction.sketch import sketch_fields as table_fields
from basis.extraction.sketch_outline import (
    GROUND_PERIMETER,
    UPPER_PERIMETER,
    Loop,
    PageDrawing,
    on_printed_precision,
    page_drawings,
    page_loops,
    printed_grid,
    read_outline,
    shows_dimensions,
)
from evals import record
from evals.sketch_takeoff import azure_pages

COMPONENT: Final = "sketch_outline"
VERSION: Final = "v1"


def _rounding(row: SketchArea) -> float:
    exponent = row.area_sf.as_tuple().exponent
    return 0.5 * 10 ** min(int(exponent), 0) if isinstance(exponent, int) else 0.5


def compare_checks(rows: Sequence[SketchArea], loops: Sequence[Loop]) -> dict[str, int]:
    """Module doc, "The checks, side by side": over every ``(verified row, loop)`` pair of a
    page, at the scale the pair's areas imply, how many pass each single-pair check."""
    grid = printed_grid(rows)
    on_precision = dimensions = 0
    for row in rows:
        area = float(row.area_sf)
        slack = _rounding(row) / area / 2
        for loop in loops:
            scale = math.sqrt(loop.area / area)
            on_precision += on_printed_precision(loop, scale, grid, slack)
            dimensions += shows_dimensions(loop, row, scale, slack)
    return {
        "pairs": len(rows) * len(loops),
        "walls_on_printed_precision": on_precision,
        "printed_dimensions": dimensions,
    }


def survey(name: str, pdf: Path, azure: Path | None) -> dict[str, Any]:
    """One document: the table's reading, each sketch page's line work, the reader's result."""
    data = pdf.read_bytes()
    started = time.perf_counter()
    text = read_pdf(data)
    reading: SketchReading | None
    if azure is not None:
        reading = read_sketch(azure_pages(azure), "azure")
        if reading is not None and not reading.areas:
            reading = None
    else:
        reading = read_document_sketch(None, text.pages)
    in_text_layer = read_document_sketch(None, text.pages) is not None
    out: dict[str, Any] = {
        "name": name,
        "pages": text.page_count,
        "table_source": None if reading is None else reading.source,
        "table_in_text_layer": in_text_layer,
        "sketch_pages": [] if reading is None else list(reading.pages),
    }
    if reading is None:
        return {**out, "status": "no_table", "fields": [], "page_detail": [], "seconds": 0.0}
    fields, _ = table_fields(reading)
    started = time.perf_counter()
    drawings: Mapping[int, PageDrawing] = page_drawings(data, reading.pages)
    result = read_outline(reading, fields, drawings)
    seconds = time.perf_counter() - started
    detail = []
    for page in result.trace["pages"]:
        entry = dict(page)
        drawing = drawings.get(page["page"])
        rows = [r for r in reading.areas if r.page == page["page"] and r.verified]
        if drawing is not None and drawing.heading_top is not None and rows:
            loops, _ = page_loops(drawing)
            if loops:
                entry["checks"] = compare_checks(rows, loops)
        detail.append(entry)
    return {
        **out,
        "status": result.trace["status"],
        "fields": sorted(result.fields),
        "values": {key: str(item.value_num) for key, item in result.fields.items()},
        "notes": result.trace["notes"],
        "ground": result.trace.get("ground"),
        "upper": result.trace.get("upper"),
        "page_detail": detail,
        "seconds": round(seconds, 3),
    }


def against_reference(document: Mapping[str, Any], reference: Mapping[str, Any]) -> dict[str, Any]:
    """Whether the reader's result equals the hand-checked measurement: the two fields, the
    shared wall, and the perimeter and area of every loop."""
    values = document.get("values") or {}
    matched = [m for page in document["page_detail"] for m in page.get("matched") or []]
    checks = reference.get("checks") or {}
    measured = sorted(
        (float(c["printed_area_sf"]), float(c["perimeter_ft"]), len(c["wall_lengths_ft"]))
        for c in checks.values()
        if "perimeter_ft" in c
    )
    # Each reference loop is paired with one loop the reader matched: same printed area, same
    # perimeter (two floors can share both, so a matched loop is used once).
    unused = list(matched)
    paired: list[Mapping[str, Any]] = []
    for area, perimeter, _ in measured:
        for m in unused:
            if (float(m["printed_area_sf"]), float(m["perimeter_ft"])) == (area, perimeter):
                paired.append(m)
                unused.remove(m)
                break
    comparisons = {
        "ground_perimeter": (values.get(GROUND_PERIMETER), reference.get("ground_perimeter_ft")),
        "upper_perimeter": (values.get(UPPER_PERIMETER), reference.get("upper_perimeter_ft")),
        "shared_wall": (
            (document.get("ground") or {}).get("shared_wall_ft"),
            reference.get("shared_wall_ft"),
        ),
    }
    equal = {
        key: mine is not None and theirs is not None and float(mine) == float(theirs)
        for key, (mine, theirs) in comparisons.items()
    }
    equal["every_loop_area_and_perimeter"] = bool(measured) and len(paired) == len(measured)
    worst = max(
        (abs(float(m["area_sf"]) - float(m["printed_area_sf"])) for m in matched), default=None
    )
    return {
        "equal": equal,
        "all_equal": all(equal.values()),
        "loops_in_reference": len(measured),
        "walls_in_reference": sum(walls for _, _, walls in measured),
        "walls_read": sum(int(m["walls"]) for m in paired),
        "loops_on_printed_precision": sum(bool(m["walls_on_printed_precision"]) for m in matched),
        "loops_matched": len(matched),
        "worst_area_difference_sf": worst,
    }


# --------------------------------------------------------------------------- the report


def _page_line(page: Mapping[str, Any]) -> str:
    checks = page.get("checks") or {}
    return (
        f"| {page['page']} | {page.get('status')} | {page.get('images', '-')} | "
        f"{page.get('strokes', '-')} | {page.get('dashed_strokes', '-')} | "
        f"{page.get('loops', '-')} | {page.get('rows', '-')} | "
        f"{len(page.get('matched') or [])} | "
        f"{page.get('scales_reproducing_two_rows_by_area', '-')} | "
        f"{checks.get('pairs', '-')} | {checks.get('walls_on_printed_precision', '-')} | "
        f"{checks.get('printed_dimensions', '-')} |"
    )


def render_markdown(
    documents: Sequence[Mapping[str, Any]], reference: Mapping[str, Any] | None, *, private: bool
) -> str:
    """The run's tables. ``private`` adds the perimeters and scales themselves."""
    lines = [
        "# Sketch outline reader",
        "",
        "No model call; cost $0.",
        "",
        "## The survey",
        "",
        "| document | pages | table read from | table in the PDF's text layer | sketch pages | "
        "pages with line work read | pages that are images | perimeter fields written | "
        "seconds |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for d in documents:
        detail = d["page_detail"]
        read_pages = sum(1 for p in detail if p.get("status") == "read")
        raster = sum(1 for p in detail if p.get("status") == "raster")
        names = ", ".join(f.split(".", 1)[1] for f in d["fields"]) or "none"
        lines.append(
            f"| {d['name']} | {d['pages']} | {d['table_source'] or 'no table'} | "
            f"{'yes' if d['table_in_text_layer'] else 'no'} | {len(d['sketch_pages'])} | "
            f"{read_pages} | {raster} | {names} | {d['seconds']:.2f} |"
        )
    lines += ["", "## Each sketch page", ""]
    for d in documents:
        if not d["page_detail"]:
            continue
        lines += [
            f"### {d['name']}",
            "",
            "| page | status | images | strokes above the table | dashed | loops | verified rows | "
            "rows reproduced | scales reproducing 2+ rows by area alone | (row, loop) pairs | "
            "pairs passing walls-on-printed-precision | pairs passing printed dimensions |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        lines += [_page_line(page) for page in d["page_detail"]]
        for note in d.get("notes") or []:
            lines.append(f"- {note}" if private else f"- {note.split(' (')[0]}")
        if private:
            lines.append(f"- values: {d.get('values')}; ground: {d.get('ground')}")
            lines.append(f"- upper: {d.get('upper')}")
            for page in d["page_detail"]:
                for m in page.get("matched") or []:
                    lines.append(
                        f"- page {page['page']} at {page.get('scale_pt_per_ft')} pt/ft: "
                        f"{m['label']} {m['printed_area_sf']} SF printed, {m['area_sf']} drawn, "
                        f"{m['walls']} walls, perimeter {m['perimeter_ft']} ft, on the printed "
                        f"precision: {m['walls_on_printed_precision']}"
                    )
        lines.append("")
    if reference is not None:
        lines += ["## Against the hand-checked measurement", ""]
        lines += [
            f"- {key}: {'equal' if ok else 'DIFFERENT'}" for key, ok in reference["equal"].items()
        ]
        lines.append(
            f"- loops: {reference['loops_matched']} read, {reference['loops_in_reference']} in "
            f"the reference; walls of the reference's loops: {reference['walls_read']} as read "
            f"(a wall is straight from corner to corner), {reference['walls_in_reference']} in "
            "the reference (which counts a wall another outline's corner sits on as two); "
            "loops with every wall on the printed precision: "
            f"{reference['loops_on_printed_precision']} of {reference['loops_matched']}"
        )
        if private:
            lines.append(f"- worst area difference: {reference['worst_area_difference_sf']} SF")
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- main


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.sketch_outline", description=__doc__)
    parser.add_argument(
        "--document",
        action="append",
        required=True,
        metavar="NAME=PDF[:AZURE_JSON]",
        help="a PDF to read, with its stored Azure DI analysis when the table is not text",
    )
    parser.add_argument("--reference", type=Path, default=None, help="the first document's outline")
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component=COMPONENT, version=VERSION
    )
    try:
        documents: list[dict[str, Any]] = []
        for spec in args.document:
            name, _, rest = spec.partition("=")
            pdf, _, azure = rest.partition(":")
            run.input(name, Path(pdf))
            if azure:
                run.input(f"{name}:azure_di", Path(azure))
            found = survey(name, Path(pdf), Path(azure) if azure else None)
            documents.append(found)
            # Nothing may be written from a page that is not line work that was read.
            unread = [p for p in found["page_detail"] if p.get("status") != "read"]
            ok = not found["fields"] or len(unread) < len(found["page_detail"])
            run.case(
                f"survey:{name}",
                "passed" if ok else "failed",
                kind="document",
                detail={
                    "status": found["status"],
                    "fields": found["fields"],
                    "page_statuses": [p.get("status") for p in found["page_detail"]],
                },
                seconds=found["seconds"],
            )
        compared: dict[str, Any] | None = None
        if args.reference is not None:
            run.input("reference", args.reference)
            compared = against_reference(
                documents[0], json.loads(args.reference.read_text(encoding="utf-8"))
            )
            run.case(
                "reference:reproduced",
                "passed" if compared["all_equal"] else "failed",
                kind="reference",
                detail=compared,
            )
        (args.out / "outline.json").write_text(
            json.dumps({"documents": documents, "reference": compared}, indent=1) + "\n",
            encoding="utf-8",
        )
        private = render_markdown(documents, compared, private=True)
        (args.out / "outline.md").write_text(private, encoding="utf-8")
        (args.out / "outline_public.md").write_text(
            render_markdown(documents, compared, private=False), encoding="utf-8"
        )
        print(private)
        run.finish(
            {
                "documents": len(documents),
                "with_a_table": sum(1 for d in documents if d["status"] != "no_table"),
                "perimeters_written": sum(1 for d in documents if d["fields"]),
                "reference_reproduced": None if compared is None else compared["all_equal"],
                "cost_usd": 0,
            }
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        run.finish(None, status=record.STATUS_FAILED, exit_code=2, error=str(exc))
        return 2
    return 0


__all__ = [
    "against_reference",
    "compare_checks",
    "render_markdown",
    "survey",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
