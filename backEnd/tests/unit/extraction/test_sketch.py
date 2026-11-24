"""The building sketch's area calculation table (``basis.extraction.sketch``; EVAL_REPORT 96).

Every page is fabricated (``tests/fixtures/sketch``); no client figure appears.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from fixtures import sketch_fixture

from basis.extraction.field_mappings import TABLE_CONFIDENCE
from basis.extraction.sketch import (
    FIELD_KEYS,
    GARAGE_AREA,
    GROUND_FLOOR_AREA,
    LIVING_AREA,
    PATIO_AREA,
    SketchReading,
    classify,
    level_of,
    read_document_sketch,
    read_sketch,
    rectangles,
    sketch_fields,
)

PAGES: dict[str, Any] = json.loads(sketch_fixture("pages.json").read_text("utf-8"))


def pages(name: str) -> list[tuple[int, list[str]]]:
    return [(number, lines) for number, lines in PAGES[name]["pages"]]


def reading(name: str) -> SketchReading:
    read = read_sketch(pages(name), "azure")
    assert read is not None
    return read


def rows(read: SketchReading) -> list[tuple[str, str, int | None, str, int, bool]]:
    return [(a.label, a.kind, a.level, str(a.area_sf), a.page, a.verified) for a in read.areas]


def test_a_two_level_sketch_is_read_row_by_row_with_its_pages() -> None:
    read = reading("two_level")
    assert read.pages == (9, 10)
    assert rows(read) == [
        ("First Floor", "living", 1, "1200", 9, True),
        ("2 Car Attached", "garage", None, "484", 9, True),
        ("Patio", "patio", None, "120", 9, True),
        ("Non-Calculated", "other", None, "60", 9, True),
        ("Second Floor", "living", 2, "900", 10, True),
    ]
    assert read.living_totals == ((9, Decimal(1200)), (10, Decimal(900)))
    # The calculation split over lines, with an equals sign read as a dash, is still two
    # rectangles whose operands multiply to their results.
    first = read.areas[0].rectangles
    assert [(r.factors, r.result, r.product_ok) for r in first] == [
        ((Decimal(30), Decimal(30)), Decimal(900), True),
        ((Decimal(20), Decimal(15)), Decimal(300), True),
    ]
    assert read.areas[1].attached is True


def test_the_fields_of_a_two_level_sketch_carry_value_page_and_source() -> None:
    fields, notes = sketch_fields(reading("two_level"))
    assert notes == []
    assert set(fields) == set(FIELD_KEYS)
    assert {k: (f.value_num, f.page, f.source) for k, f in fields.items()} == {
        LIVING_AREA: (Decimal(2100), 9, "azure"),
        GROUND_FLOOR_AREA: (Decimal(1200), 9, "azure"),
        GARAGE_AREA: (Decimal(484), 9, "azure"),
        PATIO_AREA: (Decimal(120), 9, "azure"),
    }
    living = fields[LIVING_AREA]
    assert living.section == "sketch" and living.field == "living_area_sqft"
    assert living.confidence == TABLE_CONFIDENCE
    assert living.raw_text == "First Floor 1200 Sq ft (page 9); Second Floor 900 Sq ft (page 10)"
    assert living.value_text == "2100"


def test_a_one_level_sketch_in_the_redesigned_form_style() -> None:
    read = reading("one_level")
    assert rows(read) == [
        ("Floor 1", "living", 1, "1650.50", 6, True),
        ("Accessory Dwelling Unit", "other", None, "400.00", 6, True),
        ("B1 Finished Below Grade", "basement", None, "800.00", 6, True),
        ("Detached Garage", "garage_detached", None, "576.00", 6, True),
    ]
    # A triangle prints three factors; a lost multiplication sign and a lost operand do not
    # lose the result, and the operand that was lost is reported.
    floor, unit, basement, _ = read.areas
    assert [len(r.factors) for r in floor.rectangles] == [2, 3]
    assert all(r.product_ok for r in floor.rectangles)
    assert unit.rectangles[0].factors == (Decimal("20.00"), Decimal("20.00"))
    assert basement.rectangles[0].product_ok is False and basement.verified is True
    # The trailing form text ("1", "2019") is not a calculation of the last row.
    assert len(read.areas[-1].rectangles) == 1

    fields, notes = sketch_fields(read)
    assert {k: f.value_num for k, f in fields.items()} == {
        LIVING_AREA: Decimal("1650.50"),
        GROUND_FLOOR_AREA: Decimal("1650.50"),
    }
    assert fields[LIVING_AREA].value_text == "1650.50"
    assert notes == [
        "living area: 'Accessory Dwelling Unit' on page 6 names no floor, so it is not counted",
        "garage: 'Detached Garage' on page 6 is detached, so it is not used",
    ]


def test_a_text_layer_line_holding_label_area_and_calculation_reads_the_same() -> None:
    read = read_document_sketch(None, ["", "", "\n".join(PAGES["text_layer"]["pages"][0][1])])
    assert read is not None and read.source == "regex" and read.pages == (3,)
    assert rows(read) == [
        ("First Floor", "living", 1, "1200", 3, True),
        ("2 Car Attached", "garage", None, "484", 3, True),
    ]
    assert read.living_totals == ((3, Decimal(1200)),)
    fields, _ = sketch_fields(read)
    assert {f.source for f in fields.values()} == {"regex"}


def test_azure_lines_are_preferred_and_the_text_layer_is_the_fallback() -> None:
    text = ["\n".join(PAGES["text_layer"]["pages"][0][1])]
    both = read_document_sketch(pages("two_level"), text)
    assert both is not None and both.source == "azure" and both.pages == (9, 10)
    # Azure analyzed the document but its lines carry no table: the text layer is read.
    fallback = read_document_sketch(pages("no_table"), text)
    assert fallback is not None and fallback.source == "regex"


def test_a_row_whose_calculation_does_not_sum_to_its_area_is_never_a_field() -> None:
    read = reading("damaged")
    assert [(a.label, a.verified, len(a.rectangles)) for a in read.areas] == [
        ("First Floor", False, 1),
        ("2 Car Attached", False, 0),
    ]
    fields, notes = sketch_fields(read)
    assert fields == {}
    assert notes == [
        "living area: the rectangles do not sum to the printed area of 'First Floor' on page 4",
        "garage: the rectangles do not sum to the printed area of '2 Car Attached' on page 4",
    ]


def test_rows_that_do_not_sum_to_the_page_total_are_refused() -> None:
    fields, notes = sketch_fields(reading("page_total_disagrees"))
    assert fields == {}
    assert notes == [
        "living area: the rows do not sum to the page total "
        "(page 4: rows 1200 against the printed total 1500)"
    ]


def test_a_document_with_no_table_has_no_sketch() -> None:
    assert read_sketch(pages("no_table"), "azure") is None
    assert read_document_sketch(pages("no_table"), ["Gross Living Area 2,100 Sq ft"]) is None
    assert read_document_sketch(None, []) is None


def test_a_table_in_another_layout_is_found_and_yields_nothing() -> None:
    read = read_sketch(pages("apex_style"), "azure")
    assert read is not None and read.pages == (7,) and read.areas == ()
    assert sketch_fields(read) == ({}, ["living area: the table has no living-area row"])
    assert read_document_sketch(pages("apex_style"), []) is None


def test_the_reading_is_json_serialisable() -> None:
    data = json.loads(json.dumps(reading("two_level").as_json()))
    assert data["pages"] == [9, 10] and data["source"] == "azure"
    assert data["areas"][0] == {
        "label": "First Floor",
        "heading": "living",
        "kind": "living",
        "level": 1,
        "attached": None,
        "area_sf": "1200",
        "page": 9,
        "verified": True,
        "rectangles": [
            {"factors": ["30", "30"], "result": "900", "product_ok": True},
            {"factors": ["20", "15"], "result": "300", "product_ok": True},
        ],
    }
    assert data["living_totals"] == [[9, "1200"], [10, "900"]]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12 × 5 = 60 11 × 7 = 77", [("12", "5", "60"), ("11", "7", "77")]),
        ("12 x 5 - 60", [("12", "5", "60")]),
        ("9x 9 = 81", [("9", "9", "81")]),
        ("21.5 × 30 =645", [("21.5", "30", "645")]),
        ("31.00 17.00 = 527.00", [("31.00", "17.00", "527.00")]),
        ("11 x 20 220.00", [("11", "20", "220.00")]),
        ("-5.00 x 12.00 = - 60.00", [("5.00", "12.00", "-60.00")]),
        ("1,200 x 2 = 2,400", [("1200", "2", "2400")]),
        ("12 × 5 =", []),
        ("", []),
    ],
)
def test_rectangles(text: str, expected: list[tuple[str, str, str]]) -> None:
    got = [(str(r.factors[0]), str(r.factors[1]), str(r.result)) for r in rectangles(text)]
    assert got == expected
    assert all(r.product_ok for r in rectangles(text))


def test_a_rectangle_whose_operand_was_lost_keeps_its_result_and_says_so() -> None:
    (lost,) = rectangles("x 25.00 = 550.00")
    assert lost.factors == (Decimal("25.00"),) and lost.result == Decimal("550.00")
    assert lost.product_ok is False
    (wrong,) = rectangles("20 x 25 = 550")
    assert wrong.product_ok is False


@pytest.mark.parametrize(
    ("label", "level"),
    [
        ("First Floor", 1),
        ("Second Floor", 2),
        ("2nd Floor", 2),
        ("Floor 1", 1),
        ("Level 2", 2),
        ("Unit C Floor 2 Level 1", 2),
        ("Main Level", 1),
        ("Upper Level", 2),
        ("Third Floor", 3),
        ("Bonus Room", None),
        ("Accessory Dwelling Unit", None),
    ],
)
def test_level_of(label: str, level: int | None) -> None:
    assert level_of(label) == level


@pytest.mark.parametrize(
    ("label", "heading", "kind"),
    [
        ("First Floor", "living", "living"),
        ("Level 1", None, "living"),
        ("First Floor", "non_living", "other"),
        ("Rec Room", "living", "other"),
        ("2 Car Attached", "non_living", "garage"),
        ("2 Car Garage", None, "garage"),
        ("Garage", "living", "garage"),
        ("Detached Garage", "non_living", "garage_detached"),
        ("Basement", "non_living", "basement"),
        ("Finished B1", None, "basement"),
        ("Storage", "below_grade", "basement"),
        ("Covered Patio", "non_living", "patio"),
        ("Unit A Cv Patio Floor 1 Lv1", "living", "patio"),
        ("Covered Porch", "non_living", "porch"),
        ("Deck", "non_living", "deck"),
        ("Non-Calculated", "non_living", "other"),
        ("Floor 1 Common Area", "living", "other"),
        ("Gross Building Area", "living", "other"),
    ],
)
def test_classify(label: str, heading: Any, kind: str) -> None:
    assert classify(label, heading)[0] == kind


def test_a_garage_says_attached_only_when_its_label_does() -> None:
    assert classify("2 Car Attached", "non_living") == ("garage", None, True)
    assert classify("2 Car Built-in", "non_living") == ("garage", None, True)
    assert classify("Garage", "non_living") == ("garage", None, None)
