"""Derived site-area fields (EVAL_REPORT section 27): one reading of the Area box, three views."""

from __future__ import annotations

from decimal import Decimal

from basis.extraction.derived import derive_fields
from basis.extraction.results import Candidate, ExtractedField


def read(key: str, raw: str, source: str = "azure", page: int | None = 2) -> ExtractedField:
    return ExtractedField.from_candidate(
        Candidate(key=key, raw_text=raw, page=page, confidence=0.88, source=source)  # type: ignore[arg-type]
    )


def test_an_area_printed_in_acres_gives_the_text_and_the_acres() -> None:
    derived = derive_fields({"site.area_sqft": read("site.area_sqft", "1.39 ac")})
    assert set(derived) == {"site.area", "site.area_acres"}
    text, acres = derived["site.area"], derived["site.area_acres"]
    assert (text.value_text, text.source, text.page, text.confidence) == (
        "1.39 ac",
        "azure",
        2,
        0.88,
    )
    assert acres.value_num == Decimal("1.39")


def test_an_area_in_square_feet_has_no_acres() -> None:
    derived = derive_fields({"site.area_sqft": read("site.area_sqft", "60,548 sf")})
    assert set(derived) == {"site.area"}
    assert derived["site.area"].value_text == "60,548 sf"


def test_a_printed_area_read_by_the_model_gives_the_square_feet() -> None:
    derived = derive_fields({"site.area": read("site.area", "0.25 acre", source="llm")})
    assert derived["site.area_sqft"].value_num == Decimal("10890")
    assert derived["site.area_sqft"].source == "llm"
    assert derived["site.area_acres"].value_num == Decimal("0.25")


def test_existing_values_are_kept_and_nothing_is_derived_from_nothing() -> None:
    fields = {
        "site.area_sqft": read("site.area_sqft", "1.39 ac"),
        "site.area": read("site.area", "1.39 acres", source="manual"),
        "site.area_acres": read("site.area_acres", "1.39 acres", source="manual"),
    }
    assert derive_fields(fields) == {}
    assert derive_fields({}) == {}
    empty = ExtractedField(
        key="site.area_sqft", raw_text=None, value_text=None, confidence=0.5, source="azure"
    )
    assert derive_fields({"site.area_sqft": empty}) == {}


def test_an_unparseable_reading_derives_only_what_parses() -> None:
    derived = derive_fields({"site.area": read("site.area", "see addendum", source="llm")})
    # Square feet cannot be read from it (a parse error), the text is kept.
    assert set(derived) == set()  # site.area exists already; site.area_sqft would not parse
    derived = derive_fields({"site.area_sqft": read("site.area_sqft", "irregular ac")})
    assert set(derived) == {"site.area"}  # "ac" but no number: no acres
