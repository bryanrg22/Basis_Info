"""The appraisal gate's ``gla_vs_sketch`` flag (``basis.extraction.validation``; EVAL_REPORT 96).

Raised when the sketch's living area is further from the GLA box than the tolerance: exactly
when the takeoff refuses the sketch, so the engineer hears about it where the GLA is
corrected. Fixture values are fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from basis.domain.measurements import measurements_from_fields
from basis.errors.catalogue import BY_CODE, codes_for_reasons
from basis.errors.codes import E
from basis.extraction.results import ExtractedField
from basis.extraction.validation import GLA, GLA_VS_SKETCH, SKETCH_LIVING_AREA, gla_vs_sketch

TOLERANCE = Decimal("0.01")


def field(key: str, value: str | None, page: int | None = None) -> ExtractedField:
    return ExtractedField(
        key=key,
        raw_text=value,
        value_text=value,
        value_num=None if value is None else Decimal(value),
        confidence=0.8,
        source="azure",
        page=page,
    )


def fields(gla: str | None, living: str | None) -> dict[str, ExtractedField]:
    out: dict[str, ExtractedField] = {}
    if gla is not None:
        out[GLA] = field(GLA, gla, 1)
    if living is not None:
        out[SKETCH_LIVING_AREA] = field(SKETCH_LIVING_AREA, living, 9)
    return out


def test_a_sketch_far_from_the_gla_box_is_one_warning_naming_both_fields() -> None:
    (issue,) = gla_vs_sketch(fields("2400", "2100"), TOLERANCE)
    assert issue.rule == GLA_VS_SKETCH == "gla_vs_sketch"
    assert issue.keys == ("improvements.gla_sqft", "sketch.living_area_sqft")
    assert issue.severity == "warning"
    assert issue.message == (
        "the sketch's living area is 2100 sq ft (page 9) and the gross living area box is "
        "2400 sq ft, 12.5 % apart (more than 1 %). One of them is wrong: correct the gross "
        "living area if the sketch is right, or the sketch's living area if the box is. Until "
        "they agree the takeoff does not use the sketch."
    )


@pytest.mark.parametrize(
    ("gla", "living", "flagged"),
    [
        ("2100", "2100", False),
        ("2000", "2020", False),  # exactly at the tolerance
        ("2000", "2020.01", True),
        ("2000", "1979.99", True),
        ("2000", "1980", False),
        ("1568", "1568.00", False),
        (None, "2100", False),
        ("2100", None, False),
        ("0", "2100", False),
    ],
)
def test_the_flag_follows_the_tolerance(gla: str | None, living: str | None, flagged: bool) -> None:
    assert bool(gla_vs_sketch(fields(gla, living), TOLERANCE)) is flagged


def test_nothing_is_raised_when_the_sketch_is_not_read() -> None:
    assert gla_vs_sketch(fields("2400", "2100"), None) == []
    assert (
        gla_vs_sketch(
            {GLA: field(GLA, None), SKETCH_LIVING_AREA: field(SKETCH_LIVING_AREA, "9")}, TOLERANCE
        )
        == []
    )


def test_a_wider_tolerance_admits_it_and_the_message_names_the_tolerance() -> None:
    assert gla_vs_sketch(fields("2400", "2100"), Decimal("0.15")) == []
    (issue,) = gla_vs_sketch(fields("2400", "2100"), Decimal("0.025"))
    assert "(more than 2.5 %)" in issue.message
    (no_page,) = gla_vs_sketch(
        {GLA: field(GLA, "2400"), SKETCH_LIVING_AREA: field(SKETCH_LIVING_AREA, "2100")}, TOLERANCE
    )
    assert "living area is 2100 sq ft and the gross" in no_page.message


@dataclass
class Row:
    section: str
    field: str
    value_num: Decimal | None = None
    value_text: str | None = None
    value_date: date | None = None
    source: str = "azure"


@pytest.mark.parametrize(
    ("gla", "living"),
    [("2100", "2100"), ("2000", "2020"), ("2000", "2020.01"), ("2400", "2100"), ("1900", "2100")],
)
def test_the_gate_flags_exactly_the_sketches_the_takeoff_refuses(gla: str, living: str) -> None:
    rows = [
        Row("improvements", "gla_sqft", Decimal(gla)),
        Row("improvements", "stories", Decimal(2)),
        Row("sketch", "living_area_sqft", Decimal(living)),
    ]
    measured = measurements_from_fields(rows, sketch_tolerance=TOLERANCE)
    assert measured.sketch is not None
    flagged = bool(gla_vs_sketch(fields(gla, living), TOLERANCE))
    assert flagged is (not measured.sketch.accepted)


def test_the_reason_has_a_catalogue_code_and_its_text_names_no_provider() -> None:
    reason = "gla_vs_sketch:improvements.gla_sqft,sketch.living_area_sqft"
    assert codes_for_reasons([reason]) == [E.CHECK_EXTRACTION_GLA_VS_SKETCH]
    entry = BY_CODE[E.CHECK_EXTRACTION_GLA_VS_SKETCH]
    assert entry.signal == "gla_vs_sketch" and entry.kind == "flag"
    text = f"{entry.what} {entry.fix}".lower()
    assert not any(name in text for name in ("azure", "openai", "anthropic", "claude"))
