"""Highlight regions of extracted fields (``basis.extraction.regions``) on fabricated geometry.

The layout copies the client 1004's first page where Bryan's review screenshots lit up several
occurrences: "Montrose" is the city, the county and part of the neighborhood name, and
"Purchase Transaction" is a checked option and a phrase of the contract sentence.
"""

from __future__ import annotations

from typing import Any

import pytest

from basis.extraction.azure_di import (
    AzureDiKeyValue,
    AzureDiMark,
    AzureDiPage,
    AzureDiRegion,
    AzureDiResult,
    AzureDiWord,
)
from basis.extraction.geometry import find_all, layout_of, locate
from basis.extraction.regions import field_regions, region_json, with_regions
from basis.extraction.results import ExtractedField, Region

ROW = 0.0095  # a word's height


def word(text: str, x0: float, y0: float, width: float | None = None) -> AzureDiWord:
    w = width if width is not None else 0.0065 * len(text)
    return AzureDiWord(text=text, bbox=(x0, y0, x0 + w, y0 + ROW))


def line(y: float, x: float, *tokens: str) -> list[AzureDiWord]:
    out = []
    for token in tokens:
        out.append(word(token, x, y))
        x += 0.0065 * len(token) + 0.0035
    return out


WORDS = [
    *line(0.0735, 0.516, "City"),
    *line(0.0735, 0.545, "Montrose"),
    *line(0.085, 0.749, "County"),
    *line(0.085, 0.792, "Montrose"),
    *line(0.119, 0.050, "Neighborhood", "Name"),
    *line(0.119, 0.163, "City", "of", "Montrose"),
    *line(0.152, 0.050, "Assignment", "Type"),
    *line(0.152, 0.171, "Purchase", "Transaction"),
    *line(0.152, 0.325, "Refinance", "Transaction"),
    *line(0.320, 0.050, "did", "analyze", "the", "subject", "purchase", "transaction"),
    *line(0.380, 0.300, "City", "of", "Montrose"),
    *line(0.500, 0.050, "Electricity"),
    *line(0.600, 0.050, "Fireplace(s)", "#", "2"),
    *line(0.700, 0.050, "Pool", "2244", "None"),
    *line(0.800, 0.0665, "Refrigerator"),
    *line(0.800, 0.1665, "Range/Oven"),
    *line(0.800, 0.2665, "Microwave"),
]
MARKS = [
    AzureDiMark(selected=True, bbox=(0.1512, 0.1525, 0.1665, 0.1615)),
    AzureDiMark(selected=False, bbox=(0.3050, 0.1525, 0.3203, 0.1615)),
    AzureDiMark(selected=True, bbox=(0.0500, 0.8005, 0.0630, 0.8090)),
    AzureDiMark(selected=True, bbox=(0.1500, 0.8005, 0.1630, 0.8090)),
    AzureDiMark(selected=False, bbox=(0.2500, 0.8005, 0.2630, 0.8090)),
]


def azure(**extra: Any) -> AzureDiResult:
    page = AzureDiPage(
        page_number=1, width=8.5, height=11.0, words=tuple(WORDS), marks=tuple(MARKS)
    )
    return AzureDiResult(pages=(page,), **extra)


def field(
    key: str, value: str, source: str = "llm", page: int | None = 1, **extra: Any
) -> ExtractedField:
    return ExtractedField(
        key=key, raw_text=value, value_text=value, confidence=0.6, source=source, page=page, **extra
    )  # type: ignore[arg-type]


def boxes(item: ExtractedField) -> list[tuple[float, ...]]:
    return [tuple(round(v, 3) for v in r.bbox) for r in field_regions(item, layout_of(azure()))]


def test_a_repeated_value_is_boxed_beside_its_own_caption() -> None:
    assert boxes(field("subject.city", "Montrose")) == [(0.545, 0.073, 0.597, 0.083)]
    assert boxes(field("subject.county", "Montrose")) == [(0.792, 0.085, 0.844, 0.095)]
    [name] = boxes(field("subject.neighborhood_name", "City of Montrose"))
    assert name[1] == 0.119  # the Neighborhood Name row, not the description's occurrence


def test_a_checkbox_field_is_its_checked_box_with_the_label() -> None:
    [box] = boxes(field("subject.assignment_type", "Purchase Transaction"))
    assert box[0] == 0.151 and box[1] == 0.152  # starts at the checked box, on its row
    assert box[2] < 0.305  # stops before the unchecked Refinance box


def test_several_checked_options_read_without_separators_are_one_row_box() -> None:
    # The model joined the checked appliances without commas; Microwave is not checked.
    [box] = boxes(field("improvements.appliances", "Refrigerator Range/Oven"))
    assert box[0] == 0.05 and box[2] == pytest.approx(0.2315, abs=1e-3)
    assert box[2] < 0.25  # stops before the unchecked Microwave box


def test_a_checkbox_without_a_checked_box_falls_back_to_the_text() -> None:
    # "Refinance Transaction" names an unchecked box: located as text beside the caption.
    [box] = boxes(field("subject.assignment_type", "Refinance Transaction"))
    assert box[0] == pytest.approx(0.325, abs=1e-3)


def test_whole_words_first_and_the_first_occurrence_without_a_caption() -> None:
    # "2" beside its caption, not inside "2244".
    [box] = boxes(field("improvements.fireplaces", "2"))
    assert box[1] == 0.6
    # The caption "City" is not the end of "Electricity" (whole-word captions).
    page = layout_of(azure()).page(1)
    assert len(find_all(page, "City")) == 4  # type: ignore[arg-type]
    assert len(find_all(page, "City", whole_words=True)) == 3  # type: ignore[arg-type]
    # No caption on the page: the first occurrence in reading order.
    [first] = boxes(field("site.zoning_description", "Montrose"))
    assert first[1] == 0.073


def test_an_azure_pair_keeps_its_own_value_box_on_the_cited_page() -> None:
    own = Region(page=1, bbox=(0.79, 0.08, 0.85, 0.095))
    item = field("subject.county", "Montrose", source="azure", regions=(own,))
    assert field_regions(item, layout_of(azure())) == (own,)
    elsewhere = field("subject.county", "Montrose", source="azure", regions=(own,), page=2)
    assert field_regions(elsewhere, layout_of(azure())) == ()  # never another page
    assert field_regions(field("subject.city", "Montrose", page=None), layout_of(azure())) == ()


def test_with_regions_needs_geometry_and_never_changes_a_value() -> None:
    fields = {"subject.city": field("subject.city", "Montrose")}
    assert with_regions(fields, None) == fields
    assert with_regions(fields, AzureDiResult(pages=(AzureDiPage(page_number=1),))) == fields
    out = with_regions(fields, azure())
    assert out["subject.city"].regions
    assert out["subject.city"].model_dump(exclude={"regions"}) == fields["subject.city"].model_dump(
        exclude={"regions"}
    )
    assert region_json(out["subject.city"].regions) == [
        {"page": 1, "bbox": [0.545, 0.0735, 0.597, 0.083]}
    ]


def test_old_stored_results_without_geometry_still_validate() -> None:
    stored = {
        "model_id": "prebuilt-layout",
        "content": "x",
        "pages": [{"page_number": 1, "lines": ["City Montrose"]}],
        "key_value_pairs": [{"key": "City", "value": "Montrose", "confidence": 0.9, "page": 1}],
    }
    result = AzureDiResult.model_validate(stored)
    assert result.pages[0].words == () and result.key_value_pairs[0].value_region is None
    assert result.page_texts() == ["City Montrose"]
    assert layout_of(result).page(1) is not None


def test_a_pair_region_round_trips_through_json() -> None:
    pair = AzureDiKeyValue(
        key="County",
        value="Montrose",
        confidence=0.9,
        page=1,
        value_region=AzureDiRegion(page=1, bbox=(0.79, 0.085, 0.85, 0.094)),
    )
    result = azure(key_value_pairs=(pair,))
    again = AzureDiResult.model_validate_json(result.model_dump_json())
    assert again == result
    assert locate(layout_of(again).page(1), "County") is not None
