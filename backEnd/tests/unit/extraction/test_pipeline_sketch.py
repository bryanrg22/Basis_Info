"""``extract_appraisal`` with ``ExtractionContext.sketch`` (``TAKEOFF_SKETCH``; EVAL_REPORT 96).

Off, the result is the one the pipeline produced before the sketch was read; on, the
building sketch's fields are added after the review gate and the whole reading is kept in
``raw["sketch"]``. The sketch pages are fabricated (``tests/fixtures/sketch``).
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


def with_sketch_pages(name: str = "two_level") -> AzureDiResult:
    """The sample analysis with a fixture's sketch pages appended as extra pages."""
    base = load_azure_fixture()
    extra = tuple(
        AzureDiPage(page_number=number, lines=tuple(lines))
        for number, lines in PAGES[name]["pages"]
    )
    return base.model_copy(update={"pages": (*base.pages, *extra)})


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


async def test_on_the_sketch_fields_are_added_without_touching_the_gate() -> None:
    azure = StaticAzure(with_sketch_pages())
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
    living, ground = on.fields[LIVING_AREA], on.fields[GROUND_FLOOR_AREA]
    assert (living.value_num, living.page, living.source) == (Decimal(2100), 9, "azure")
    assert (ground.value_num, ground.page) == (Decimal(1200), 9)
    trace = on.raw["sketch"]
    assert trace["status"] == "read" and trace["pages"] == [9, 10]
    assert trace["fields"] == sorted(FIELD_KEYS) and trace["notes"] == []
    assert [area["label"] for area in trace["areas"]][:2] == ["First Floor", "2 Car Attached"]
    json.dumps(on.raw["sketch"])  # stored as JSON in extractions.raw
    assert beats[-1] == "sketch"


async def test_on_a_document_with_no_table_says_so_and_adds_nothing() -> None:
    azure = StaticAzure(load_azure_fixture())
    off = await extract_appraisal(SAMPLE, "d", context(azure))
    on = await extract_appraisal(SAMPLE, "d", replace(context(azure), sketch=True))
    assert on.fields == off.fields
    assert on.raw["sketch"] == {"status": "no_table"}


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
    assert on.raw["sketch"] == {"status": "no_table"}
