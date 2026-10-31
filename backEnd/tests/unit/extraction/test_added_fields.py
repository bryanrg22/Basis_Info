"""An appraisal field the engineer adds (``extraction_field_add``; basis.extraction.added_fields).

The rules the decision route, ``apply_corrections`` and ``extract_fields`` share: a catalogue
key, exactly one value in the member the key's type stores, a page from 1 (within the PDF when
its page count is known), non-blank evidence of at most 1000 characters, nothing else; a key
the extraction already has is refused (correct it instead).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from basis.errors.codes import E
from basis.extraction import added_fields
from basis.extraction.added_fields import MAX_EVIDENCE, page_count, parse, problems, target_problems

GOOD: dict[str, Any] = {
    "key": "improvements.year_built",
    "value_num": 1987,
    "page": 3,
    "evidence": "Year Built 1987",
}


def codes(after: dict[str, Any]) -> list[str]:
    return [p.code for p in problems(after)]


@pytest.mark.parametrize(
    "after",
    [
        GOOD,
        {**GOOD, "value_num": "1987"},  # a numeric string parses like parse_number
        {"key": "subject.city", "value_text": "Oxnard", "page": 1, "evidence": "City Oxnard"},
        {"key": "site.fema_map_date", "value_date": "2019-06-04", "page": 2, "evidence": "x"},
        {"key": "improvements.bathrooms", "value_num": 2.5, "page": 2, "evidence": "2.1"},
        {"key": "site.special_flood_hazard", "value_text": "No", "page": 2, "evidence": "[X] No"},
        {
            "key": "sales_comparison.indicated_value",
            "value_num": 650000,
            "page": 2,
            "evidence": "$",
        },
        {
            "key": "sales_comparison.indicated_value",
            "value_text": "N/A",
            "page": 2,
            "evidence": "-",
        },
        {**GOOD, "value_text": None, "value_date": None},  # explicit nulls are not values
        {**GOOD, "evidence": "e" * MAX_EVIDENCE},
    ],
)
def test_a_valid_addition_has_no_problems(after: dict[str, Any]) -> None:
    assert problems(after) == []


@pytest.mark.parametrize(
    ("after", "code", "fragment"),
    [
        ({**GOOD, "source": "appraisal"}, E.CORRECT_FIELD_ADD_SHAPE, "not source"),
        ({**GOOD, "key": "improvements.year"}, E.CORRECT_FIELD_ADD_UNKNOWN_KEY, "catalogue"),
        ({**GOOD, "key": 7}, E.CORRECT_FIELD_ADD_SHAPE, "key must be"),
        ({k: v for k, v in GOOD.items() if k != "value_num"}, E.CORRECT_FIELD_ADD_SHAPE, "got 0"),
        ({**GOOD, "value_text": "1987"}, E.CORRECT_FIELD_ADD_SHAPE, "got 2"),
        ({**GOOD, "value_num": "nineteen"}, E.CORRECT_NOT_A_NUMBER, "not a finite number"),
        ({**GOOD, "value_num": True}, E.CORRECT_NOT_A_NUMBER, "not a finite number"),
        ({**GOOD, "value_num": "NaN"}, E.CORRECT_NOT_A_NUMBER, "not a finite number"),
        ({**GOOD, "value_num": 1987.5}, E.CORRECT_FIELD_ADD_TYPE, "whole number"),
        (
            {"key": "improvements.year_built", "value_text": "1987", "page": 3, "evidence": "x"},
            E.CORRECT_FIELD_ADD_TYPE,
            "takes value_num",
        ),
        (
            {"key": "site.fema_map_date", "value_text": "2019-06-04", "page": 2, "evidence": "x"},
            E.CORRECT_FIELD_ADD_TYPE,
            "takes value_date",
        ),
        (
            {"key": "site.fema_map_date", "value_date": "06/04/2019", "page": 2, "evidence": "x"},
            E.CORRECT_NOT_A_DATE,
            "ISO date",
        ),
        (
            {"key": "site.special_flood_hazard", "value_text": "maybe", "page": 2, "evidence": "x"},
            E.CORRECT_FIELD_ADD_TYPE,
            "Yes or No",
        ),
        (
            {"key": "subject.city", "value_text": "  ", "page": 1, "evidence": "x"},
            E.CORRECT_FIELD_ADD_SHAPE,
            "non-blank",
        ),
        ({**GOOD, "page": 0}, E.CORRECT_FIELD_ADD_SHAPE, "from 1"),
        ({**GOOD, "page": True}, E.CORRECT_FIELD_ADD_SHAPE, "from 1"),
        ({**GOOD, "page": "3"}, E.CORRECT_FIELD_ADD_SHAPE, "from 1"),
        ({k: v for k, v in GOOD.items() if k != "page"}, E.CORRECT_FIELD_ADD_SHAPE, "from 1"),
        ({**GOOD, "evidence": " \t"}, E.CORRECT_FIELD_ADD_SHAPE, "non-blank"),
        ({k: v for k, v in GOOD.items() if k != "evidence"}, E.CORRECT_FIELD_ADD_SHAPE, "evidence"),
        ({**GOOD, "evidence": "e" * (MAX_EVIDENCE + 1)}, E.CORRECT_FIELD_ADD_SHAPE, "at most"),
    ],
)
def test_each_problem_is_named_with_its_code(
    after: dict[str, Any], code: str, fragment: str
) -> None:
    found = problems(after)
    assert code in [p.code for p in found], found
    assert any(fragment in p.message for p in found), found


def test_an_unknown_key_is_reported_once_not_as_a_type_mismatch() -> None:
    assert codes({**GOOD, "key": "subject.nope"}) == [E.CORRECT_FIELD_ADD_UNKNOWN_KEY]


def test_every_catalogue_type_names_its_member() -> None:
    from basis.extraction.field_mappings import FIELDS

    assert {spec.type for spec in FIELDS} <= set(added_fields.TYPE_MEMBERS)


def test_the_target_refuses_an_existing_key_and_a_page_past_the_pdf() -> None:
    assert target_problems("a.b", 3, existing_keys={"a.c"}, page_count=3) == []
    assert target_problems("a.b", 99, existing_keys=set(), page_count=None) == []  # unknown
    [exists] = target_problems("a.b", 1, existing_keys={"a.b"}, page_count=None)
    assert exists.code == E.CORRECT_FIELD_ADD_EXISTS and "extraction_field" in exists.message
    [past] = target_problems("a.b", 4, existing_keys=set(), page_count=3)
    assert past.code == E.CORRECT_FIELD_ADD_PAGE and "3 pages" in past.message


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"page_count": 12}, 12),
        ({"page_count": 0}, None),
        ({}, None),
        (None, None),
        ({"page_count": True}, None),
        ({"page_count": "12"}, None),
    ],
)
def test_the_page_count_is_known_only_when_recorded(raw: object, expected: int | None) -> None:
    assert page_count(raw) == expected


def test_parse_returns_the_typed_value() -> None:
    added = parse(GOOD)
    assert (added.key, added.section, added.field) == (
        "improvements.year_built",
        "improvements",
        "year_built",
    )
    assert (added.value_num, added.value_text, added.value_date) == (Decimal("1987"), None, None)
    assert (added.page, added.evidence) == (3, "Year Built 1987")
    day = parse(
        {"key": "site.fema_map_date", "value_date": "2019-06-04", "page": 2, "evidence": "x"}
    )
    assert day.value_date == date(2019, 6, 4)
    text = parse({"key": "subject.city", "value_text": " Oxnard ", "page": 1, "evidence": "x"})
    assert text.value_text == "Oxnard"
    with pytest.raises(ValueError, match="catalogue"):
        parse({**GOOD, "key": "subject.nope"})
