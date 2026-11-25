"""``extract_appraisal`` with ``ExtractionContext.sketch`` (``TAKEOFF_SKETCH``; EVAL_REPORT 96).

Off, the result is the one the pipeline produced before the sketch was read; on, the
building sketch's fields are added after the review gate and the whole reading is kept in
``raw["sketch"]``; a sketch whose living area is not the GLA box's flags the appraisal
(``gla_vs_sketch``) and nothing else about the gate changes. The sketch pages are fabricated
(``tests/fixtures/sketch``).
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from typing import Any

from fixtures import sketch_fixture
from llm_fakes import load_azure_fixture

from basis.extraction.azure_di import AzureDiPage, AzureDiResult
from basis.extraction.pipeline import extract_appraisal
from basis.extraction.sketch import FIELD_KEYS, GROUND_FLOOR_AREA, LIVING_AREA

from .test_pipeline import SAMPLE, StaticAzure, context

PAGES: dict[str, Any] = json.loads(sketch_fixture("pages.json").read_text("utf-8"))


def with_pages(pages: list[tuple[int, list[str]]]) -> AzureDiResult:
    """The sample analysis with sketch pages appended as extra pages."""
    base = load_azure_fixture()
    extra = tuple(AzureDiPage(page_number=number, lines=tuple(lines)) for number, lines in pages)
    return base.model_copy(update={"pages": (*base.pages, *extra)})


def with_sketch_pages(name: str = "two_level") -> AzureDiResult:
    """The sample analysis with a fixture's sketch pages (their living area is not the
    sample's GLA: the two disagree)."""
    return with_pages([(number, lines) for number, lines in PAGES[name]["pages"]])


def agreeing_sketch(gla: Decimal, *, patio: bool = True) -> AzureDiResult:
    """The sample analysis with a one-level sketch whose living area is the sample's GLA."""
    area = format(gla.normalize(), "f")
    lines = [
        "Area Calculations Summary",
        "Living Area",
        "First Floor",
        f"{area} Sq ft",
        f"{area} x 1 = {area}",
        "Total Living Area (Rounded):",
        f"{area} Sq ft",
        "Non-living Area",
        "2 Car Attached",
        "484 Sq ft",
        "22 x 22 = 484",
    ]
    if patio:
        lines += ["Patio", "120 Sq ft", "12 x 10 = 120"]
    return with_pages([(9, lines)])


async def test_off_the_extraction_is_unchanged_even_with_a_sketch_in_the_document() -> None:
    plain = await extract_appraisal(SAMPLE, "d", context(StaticAzure(load_azure_fixture())))
    off = await extract_appraisal(SAMPLE, "d", context(StaticAzure(with_sketch_pages())))
    assert "sketch" not in off.raw
    assert not any(key.startswith("sketch.") for key in off.fields)
    assert off.fields == plain.fields
    assert (off.needs_review, off.review_reasons, off.overall_confidence) == (
        plain.needs_review,
        plain.review_reasons,
        plain.overall_confidence,
    )


async def sample_gla() -> Decimal:
    plain = await extract_appraisal(SAMPLE, "d", context(StaticAzure(load_azure_fixture())))
    gla = plain.fields["improvements.gla_sqft"].value_num
    assert gla is not None
    return gla


async def test_on_an_agreeing_sketch_adds_its_fields_without_touching_the_gate() -> None:
    gla = await sample_gla()
    azure = StaticAzure(agreeing_sketch(gla))
    off = await extract_appraisal(SAMPLE, "d", context(azure))
    beats: list[str] = []
    ctx = replace(context(azure), sketch=True, heartbeat=beats.append)
    on = await extract_appraisal(SAMPLE, "d", ctx)

    assert set(on.fields) - set(off.fields) == set(FIELD_KEYS)
    assert {k: v for k, v in on.fields.items() if k in off.fields} == off.fields
    assert (on.needs_review, on.review_reasons, on.overall_confidence, on.issues) == (
        off.needs_review,
        off.review_reasons,
        off.overall_confidence,
        off.issues,
    )
    assert on.raw["issues"] == off.raw["issues"]
    assert on.raw["review_reasons"] == off.raw["review_reasons"]
    living, ground = on.fields[LIVING_AREA], on.fields[GROUND_FLOOR_AREA]
    assert (living.value_num, living.page, living.source) == (gla, 9, "azure")
    assert (ground.value_num, ground.page) == (gla, 9)
    trace = on.raw["sketch"]
    assert trace["status"] == "read" and trace["pages"] == [9]
    assert trace["fields"] == sorted(FIELD_KEYS) and trace["notes"] == []
    assert trace["gla_tolerance"] == "0.01"
    assert [area["label"] for area in trace["areas"]][:2] == ["First Floor", "2 Car Attached"]
    json.dumps(on.raw["sketch"])  # stored as JSON in extractions.raw
    assert beats[-1] == "sketch"


async def test_on_a_sketch_that_disagrees_with_the_gla_flags_the_appraisal() -> None:
    gla = await sample_gla()
    assert gla != Decimal(2100)  # the fixture sketch's living area
    azure = StaticAzure(with_sketch_pages())
    off = await extract_appraisal(SAMPLE, "d", context(azure))
    on = await extract_appraisal(SAMPLE, "d", replace(context(azure), sketch=True))

    reason = "gla_vs_sketch:improvements.gla_sqft,sketch.living_area_sqft"
    assert on.needs_review
    assert on.review_reasons == [*off.review_reasons, reason]
    assert on.overall_confidence == off.overall_confidence
    assert [i.rule for i in on.issues] == [*(i.rule for i in off.issues), "gla_vs_sketch"]
    issue = on.issues[-1]
    assert issue.severity == "warning"
    assert issue.keys == ("improvements.gla_sqft", "sketch.living_area_sqft")
    assert "the sketch's living area is 2100 sq ft (page 9)" in issue.message
    assert on.raw["review_reasons"] == [*off.raw["review_reasons"], reason]
    assert on.raw["issues"][-1]["rule"] == "gla_vs_sketch"
    assert len(on.raw["issues"]) == len(off.raw["issues"]) + 1
    # The fields are still written: the engineer sees both figures.
    assert on.fields[LIVING_AREA].value_num == Decimal(2100)
    assert {k: v for k, v in on.fields.items() if k in off.fields} == off.fields


async def test_the_flag_uses_the_contexts_tolerance() -> None:
    azure = StaticAzure(with_sketch_pages())
    off = await extract_appraisal(SAMPLE, "d", context(azure))
    wide = replace(context(azure), sketch=True, sketch_gla_tolerance=Decimal(1))
    on = await extract_appraisal(SAMPLE, "d", wide)
    assert on.review_reasons == off.review_reasons and on.issues == off.issues
    assert on.raw["sketch"]["gla_tolerance"] == "1"


async def test_on_a_document_with_no_table_says_so_and_adds_nothing() -> None:
    azure = StaticAzure(load_azure_fixture())
    off = await extract_appraisal(SAMPLE, "d", context(azure))
    on = await extract_appraisal(SAMPLE, "d", replace(context(azure), sketch=True))
    assert on.fields == off.fields
    assert on.raw["sketch"] == {"status": "no_table", "gla_tolerance": "0.01"}
    assert on.review_reasons == off.review_reasons


async def test_on_a_damaged_table_is_kept_in_raw_and_writes_no_field() -> None:
    azure = StaticAzure(with_sketch_pages("damaged"))
    on = await extract_appraisal(SAMPLE, "d", replace(context(azure), sketch=True))
    assert not any(key.startswith("sketch.") for key in on.fields)
    trace = on.raw["sketch"]
    assert trace["status"] == "read" and trace["fields"] == []
    assert trace["notes"][0].startswith("living area: the rectangles do not sum")
    assert [area["verified"] for area in trace["areas"]] == [False, False]


async def test_without_azure_the_text_layer_is_read() -> None:
    # The sample PDF has no sketch page: the text-layer path runs and finds no table.
    on = await extract_appraisal(SAMPLE, "d", replace(context(None), sketch=True))
    assert on.raw["sketch"] == {"status": "no_table", "gla_tolerance": "0.01"}
