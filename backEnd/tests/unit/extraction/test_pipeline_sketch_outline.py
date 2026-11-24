"""``extract_appraisal`` with ``ExtractionContext.sketch_outline`` (``TAKEOFF_SKETCH_OUTLINE``;
EVAL_REPORT 100).

Off, the extraction is the one ``TAKEOFF_SKETCH`` alone produces and the PDF's line work is
never opened; on, the two perimeter fields are added after the review gate, read on a thread,
and what each page gave is kept in ``raw["sketch"]["outline"]``. The PDFs are fabricated
(``sketch_pdf``).
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from decimal import Decimal

import pytest

from basis.extraction import pipeline
from basis.extraction.pipeline import extract_appraisal, with_sketch
from basis.extraction.results import ExtractionResult
from basis.extraction.sketch import FIELD_KEYS as AREA_KEYS
from basis.extraction.sketch_outline import GROUND_PERIMETER, SOURCE, UPPER_PERIMETER

from .sketch_pdf import SketchPage, build, l_house_page, rectangle
from .test_pipeline import context


def two_level_pdf() -> bytes:
    upper = SketchPage(scale=9.0).walls(rectangle(0, 0, 30, 25))
    upper.section("Living Area").row("Second Floor", "750 Sq ft", "30 x 25 = 750")
    return build(l_house_page(), upper, before=1)


async def test_off_the_extraction_is_the_one_without_the_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = two_level_pdf()
    before = await extract_appraisal(pdf, "d", replace(context(None), sketch=True))

    def never(*_: object, **__: object) -> None:
        raise AssertionError("the PDF's line work was opened with the outline off")

    monkeypatch.setattr(pipeline, "page_drawings", never)
    monkeypatch.setattr(pipeline, "read_outline", never)
    off = await extract_appraisal(
        pdf, "d", replace(context(None), sketch=True, sketch_outline=False)
    )
    assert off.fields == before.fields
    assert json.dumps(off.raw["sketch"]) == json.dumps(before.raw["sketch"])
    assert "outline" not in off.raw["sketch"]
    assert set(off.raw["sketch"]) == {
        "status", "source", "pages", "areas", "living_totals", "fields", "notes", "gla_tolerance"
    }  # fmt: skip
    assert not {GROUND_PERIMETER, UPPER_PERIMETER} & set(off.fields)


async def test_the_outline_setting_alone_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*_: object, **__: object) -> None:
        raise AssertionError("read without TAKEOFF_SKETCH")

    monkeypatch.setattr(pipeline, "page_drawings", never)
    pdf = two_level_pdf()
    plain = await extract_appraisal(pdf, "d", context(None))
    alone = await extract_appraisal(pdf, "d", replace(context(None), sketch_outline=True))
    assert alone.fields == plain.fields and "sketch" not in alone.raw


async def test_on_the_two_perimeters_are_added_without_touching_the_gate() -> None:
    pdf = two_level_pdf()
    off = await extract_appraisal(pdf, "d", replace(context(None), sketch=True))
    beats: list[str] = []
    ctx = replace(context(None), sketch=True, sketch_outline=True, heartbeat=beats.append)
    on = await extract_appraisal(pdf, "d", ctx)

    assert set(on.fields) - set(off.fields) == {GROUND_PERIMETER, UPPER_PERIMETER}
    assert {k: v for k, v in on.fields.items() if k in off.fields} == off.fields
    assert (on.needs_review, on.review_reasons, on.overall_confidence, on.issues) == (
        off.needs_review,
        off.review_reasons,
        off.overall_confidence,
        off.issues,
    )
    ground, upper = on.fields[GROUND_PERIMETER], on.fields[UPPER_PERIMETER]
    assert (ground.value_num, ground.page, ground.source) == (Decimal(188), 2, SOURCE)
    assert (upper.value_num, upper.page, upper.source) == (Decimal(110), 3, SOURCE)
    assert (ground.section, ground.field) == ("sketch", "ground_perimeter_ft")
    trace = on.raw["sketch"]
    assert trace["fields"] == sorted([*AREA_KEYS[:3], GROUND_PERIMETER, UPPER_PERIMETER])
    assert {k: v for k, v in trace.items() if k not in ("outline", "fields")} == {
        k: v for k, v in off.raw["sketch"].items() if k != "fields"
    }
    outline = trace["outline"]
    assert outline["status"] == "read" and outline["notes"] == []
    assert [page["status"] for page in outline["pages"]] == ["read", "read"]
    json.dumps(on.raw["sketch"])  # stored as JSON in extractions.raw
    assert beats[-1] == "sketch"


async def test_the_line_work_is_read_on_a_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    real = pipeline.page_drawings

    def watched(*args: object, **kwargs: object) -> object:
        seen.append(threading.current_thread() is threading.main_thread())
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "page_drawings", watched)
    ctx = replace(context(None), sketch=True, sketch_outline=True)
    await extract_appraisal(two_level_pdf(), "d", ctx)
    assert seen == [False]


async def test_a_sketch_that_cannot_be_read_adds_no_field_and_says_why() -> None:
    page = SketchPage().walls(rectangle(0, 0, 40, 24))  # drawn 40 x 24, printed 40 x 25
    page.section("Living Area").row("First Floor", "1000 Sq ft", "40 x 25 = 1000")
    ctx = replace(context(None), sketch=True, sketch_outline=True)
    on = await extract_appraisal(build(page), "d", ctx)
    assert not {GROUND_PERIMETER, UPPER_PERIMETER} & set(on.fields)
    outline = on.raw["sketch"]["outline"]
    assert outline["status"] == "not_read"
    assert outline["notes"] == [
        "ground perimeter: no outline on page 1 reproduces 'First Floor' (1000 Sq ft); "
        "the page's line work: no_match"
    ]


async def test_line_work_the_reader_chokes_on_does_not_fail_the_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("an operator the parser does not know")

    monkeypatch.setattr(pipeline, "page_drawings", broken)
    pdf = two_level_pdf()
    off = await extract_appraisal(pdf, "d", replace(context(None), sketch=True))
    ctx = replace(context(None), sketch=True, sketch_outline=True)
    on = await extract_appraisal(pdf, "d", ctx)
    assert on.fields == off.fields
    assert on.raw["sketch"]["outline"] == {
        "status": "not_read",
        "notes": ["the line work could not be read (RuntimeError)"],
    }


async def test_a_document_with_no_table_is_unchanged() -> None:
    ctx = replace(context(None), sketch=True, sketch_outline=True)
    on = await extract_appraisal(build(SketchPage(heading="")), "d", ctx)
    assert on.raw["sketch"] == {"status": "no_table", "gla_tolerance": "0.01"}


async def test_without_a_pdf_there_is_no_line_work_to_read() -> None:
    # The UAD 3.6 XML path with no report PDF: the table can still come from a stored
    # analysis, the drawing cannot.
    from basis.extraction.azure_di import AzureDiPage, AzureDiResult

    lines = ("Area Calculations Summary", "Living Area", "First Floor", "1008 Sq ft")
    lines += ("40 x 18 = 720", "24 x 12 = 288")
    azure = AzureDiResult(pages=(AzureDiPage(page_number=1, lines=lines),))
    ctx = replace(context(None), sketch=True, sketch_outline=True)
    result = ExtractionResult(doc_id="d", method="mismo_xml", page_count=0)
    out = await with_sketch(result, ctx, azure, [], None)
    assert "sketch.ground_floor_area_sqft" in out.fields and GROUND_PERIMETER not in out.fields
    assert out.raw["sketch"]["outline"] == {
        "status": "not_read",
        "notes": ["no PDF to read line work from"],
    }
