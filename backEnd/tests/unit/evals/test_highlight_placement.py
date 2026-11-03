"""``evals.highlight_placement``: the placement methods, the words-in-the-box test (lenient
and exact), the comparison, its summary, the pick and the run record, on fabricated page
geometry (no eval data, no PDF, no model)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from basis.extraction.azure_di import AzureDiResult, from_sdk
from basis.extraction.geometry import layout_of
from evals import highlight_placement as hp
from evals import record
from evals.appraisal.recordings import rebuild_sdk_result
from evals.appraisal.score import PageWords, highlight_status
from evals.appraisal.truth import DocumentTruth
from tests.unit.evals.appraisal_support import (
    field,
    prediction_doc,
    predictions,
    repeat,
    truth_doc,
    truth_set,
)
from tests.unit.evals.appraisal_systems_support import (
    HEIGHT,
    WIDTH,
    raw_layout,
    region_of,
    token_box,
)

# One form page. "Springfield" is printed three times (the city, the county, the neighborhood
# name); each field's own occurrence is the one to light up. Water's checked box is under
# the Public header. The site value is printed as "$" and "85,000" (two words).
PAGE = [
    "City Springfield County Springfield",  # line 0
    "Neighborhood Name City of Springfield",  # line 1
    "Utilities Public Other",  # line 2
    "Water :selected: :unselected:",  # line 3
    "Opinion of Site Value = $ 85,000",  # line 4
    "Year Built 1998",  # line 5
]
LINES = [line.split(" ") for line in PAGE]


def _bbox(line: int, first: int, last: int | None = None) -> tuple[float, float, float, float]:
    x0, y0 = token_box(line, LINES[line], first)[:2]
    x1, y1 = token_box(line, LINES[line], first if last is None else last)[2:]
    return (x0, y0, x1, y1)


def _box(line: int, first: int, last: int | None = None) -> dict[str, Any]:
    return {"page": 1, "bbox": [round(v, 4) for v in _bbox(line, first, last)]}


def _pair(key: tuple[int, int], value: tuple[int, int], caption: str, text: str) -> dict:
    return {
        "key": {"content": caption, "boundingRegions": [region_of(1, _bbox(*key))]},
        "value": {"content": text, "boundingRegions": [region_of(1, _bbox(*value))]},
        "confidence": 0.95,
    }


def _result(pairs: list[dict[str, Any]], documents: list[dict] | None = None) -> AzureDiResult:
    return from_sdk(rebuild_sdk_result(_raw(pairs, documents)))


def _raw(pairs: list[dict[str, Any]], documents: list[dict] | None = None) -> dict[str, Any]:
    if documents is None:
        return raw_layout([PAGE], key_value_pairs=pairs)
    return raw_layout([PAGE], model_id="prebuilt-mortgage.us.1004", documents=documents)


def _words() -> tuple[PageWords, ...]:
    """The PDF text layer of the fabricated page: the same words at the same places, in
    inches (``PageWords`` takes any unit as long as width and height match)."""
    words = []
    for number, tokens in enumerate(LINES):
        for index, token in enumerate(tokens):
            if token.startswith(":"):
                continue
            x0, y0, x1, y1 = token_box(number, tokens, index)
            words.append((token, x0 * WIDTH, y0 * HEIGHT, x1 * WIDTH, y1 * HEIGHT))
    return (PageWords(WIDTH, HEIGHT, tuple(words)),)


CITY = _box(0, 1)
COUNTY = _box(0, 3)
NEIGHBORHOOD = _box(1, 4)
SITE_VALUE = _box(4, 6)
YEAR = _box(5, 2)
PUBLIC_HEADER = _box(2, 1)
WATER_BOX = _box(3, 1)


def _present(type_: str, value: Any, printed: str, quote: str | None = None) -> dict[str, Any]:
    return {
        "status": "present",
        "type": type_,
        "value": value,
        "printed": printed,
        "page": 1,
        "quote": quote,
    }


def _truth() -> DocumentTruth:
    return truth_doc(
        {
            "subject.county": _present("text", "springfield", "Springfield", "County Springfield"),
            "subject.city": _present("text", "springfield", "Springfield", "City Springfield"),
            "site.utilities_water": _present("enum", "public", "Public", "Public"),
            "cost_approach.site_value": _present(
                "currency", 85000, "85,000", "Opinion of Site Value = $ 85,000"
            ),
            "improvements.year_built": _present("int", 1998, "1998", "Year Built 1998"),
        },
        pages=1,
    )


def _predictions(**overrides: dict[str, Any]) -> dict[str, Any]:
    fields = {
        # Azure read the county from its pair: production kept the pair's box.
        "subject.county": {**field("Springfield", source="azure"), "regions": [COUNTY]},
        # The model read the city; production found the word nearest the "City" caption.
        "subject.city": {**field("Springfield", source="llm"), "regions": [CITY]},
        # The checkbox: the fabricated mark's label is its row caption, so the value is
        # located as text, the "Public" header.
        "site.utilities_water": {**field("Public", source="llm"), "regions": [PUBLIC_HEADER]},
        # The site value from the model: the number's word.
        "cost_approach.site_value": {
            **field("85,000", value_num="85000", source="llm"),
            "regions": [SITE_VALUE],
        },
        # Year built, read right but stored with no box.
        "improvements.year_built": {**field("1998", value_num="1998", source="llm"), "regions": []},
    }
    fields.update(overrides)
    return predictions(prediction_doc("doc_a", repeat(fields)))


# Azure's own pairs: the county (right) and the city paired with the neighborhood's
# "Springfield" (a wrong box); no pair for the water, the site value or the year.
PAIRS = [
    _pair((0, 2), (0, 3), "County", "Springfield"),
    _pair((0, 0), (1, 4), "City", "Springfield"),
]


def _node(kind: str, box: tuple[float, float, float, float], **values: Any) -> dict[str, Any]:
    return {"type": kind, "confidence": 0.9, "boundingRegions": [region_of(1, box)], **values}


# Azure's 1004 model: the address field over the whole first line (its city part has no box
# of its own), the water selection group over its two rows, the year over the year's word.
FORM_1004 = [
    {
        "docType": "mortgage.us.1004",
        "confidence": 0.9,
        "fields": {
            "Subject": {
                "type": "object",
                "valueObject": {
                    "PropertyAddress": _node(
                        "address",
                        _bbox(0, 0, 3),
                        content="Springfield",
                        valueAddress={"city": "Springfield"},
                    )
                },
            },
            "Site": {
                "type": "object",
                "valueObject": {
                    "Utilities": {
                        "type": "object",
                        "valueObject": {
                            "WaterType": _node(
                                "selectionGroup",
                                (_bbox(2, 0)[0], _bbox(2, 0)[1], _bbox(2, 2)[2], _bbox(3, 0)[3]),
                                valueSelectionGroup=["Public"],
                            )
                        },
                    }
                },
            },
            "Improvements": {
                "type": "object",
                "valueObject": {
                    "YearBuilt": _node("integer", _bbox(5, 2), content="1998", valueInteger=1998)
                },
            },
        },
    }
]


def _compare(
    result: AzureDiResult | None = None,
    preds: dict[str, Any] | None = None,
    **options: Any,
) -> hp.Comparison:
    result = result if result is not None else _result(PAIRS)
    truths = {"synthetic_1004": truth_set(_truth())}
    return hp.compare(
        truths,
        preds if preds is not None else _predictions(),
        layouts=lambda doc: result,
        words_for=lambda doc: _words(),
        texts_for=lambda doc: (" ".join(t for t in " ".join(PAGE).split() if t[0] != ":"),),
        forms_1004=lambda doc: _raw([], FORM_1004),
        **options,
    )


def _item(key: str, value: str, **kw: Any) -> hp.Placement:
    return hp.Placement(key, value, kw.pop("num", None), None, kw.pop("source", "llm"), 1, **kw)


# --------------------------------------------------------------------------- the methods


def test_azure_pairs_map_pairs_to_keys_by_caption() -> None:
    pairs = hp.azure_pairs(_result(PAIRS), uad36=False)
    assert pairs["subject.county"] == hp.AzurePair("Springfield", [COUNTY])
    assert pairs["subject.city"].regions == [NEIGHBORHOOD]
    assert "cost_approach.site_value" not in pairs
    assert hp.azure_pairs(_result([]), uad36=False) == {}


def test_text_search_takes_the_quote_else_the_value_first_occurrence() -> None:
    layout = layout_of(_result([]))
    county = _item("subject.county", "Springfield", stored=[COUNTY])
    assert hp.place(hp.TEXT_SEARCH, county, layout, []) == [CITY]  # first "Springfield": the city
    quoted = _item("subject.county", "Springfield", quote="County Springfield")
    assert hp.place(hp.TEXT_SEARCH, quoted, layout, []) == [_box(0, 2, 3)]
    assert hp.place(hp.TEXT_SEARCH, _item("k", "Nowhere"), layout, []) == []
    elsewhere = hp.Placement("k", "Springfield", None, None, "llm", 9)
    assert hp.place(hp.TEXT_SEARCH, elsewhere, layout, []) == []


def test_the_combinations_fall_back_in_their_order() -> None:
    layout = layout_of(_result([]))
    pair = hp.AzurePair("Springfield", [COUNTY])
    both = _item("k", "Springfield", pair=pair)
    assert hp.place(hp.AZURE_ELSE_TODAY, both, layout, [CITY]) == [COUNTY]
    assert hp.place(hp.TODAY_ELSE_AZURE, both, layout, [CITY]) == [CITY]
    assert hp.place(hp.TODAY_ELSE_AZURE, both, layout, []) == [COUNTY]
    only_today = _item("k", "Springfield")
    assert hp.place(hp.AZURE_ELSE_TODAY, only_today, layout, [CITY]) == [CITY]
    assert hp.place(hp.AZURE_PAIR, only_today, layout, [CITY]) == []
    assert hp.place(hp.TODAY, only_today, layout, [CITY]) == [CITY]
    with pytest.raises(ValueError):
        hp.place("nothing", both, layout, [])


def test_quote_value_checks_basis_box_against_the_quote() -> None:
    layout = layout_of(_result([]))
    # Basis put the county on the city's word: outside the quote, so the value's words
    # inside the quote replace it.
    county = _item("subject.county", "Springfield", quote="County Springfield")
    assert hp.place(hp.QUOTE_VALUE, county, layout, [CITY]) == [COUNTY]
    # Basis's box inside the quote is kept as it is (a wider box over the value too).
    wide = [_box(0, 2, 3)]
    assert hp.place(hp.QUOTE_VALUE, county, layout, wide) == wide
    unquoted = _item("subject.county", "Springfield")
    assert hp.place(hp.QUOTE_VALUE, unquoted, layout, [CITY]) == [CITY]
    missing = _item("subject.county", "Springfield", quote="Not on the page")
    assert hp.place(hp.QUOTE_VALUE, missing, layout, [CITY]) == [CITY]
    water = _item("site.utilities_water", "Public", quote="Utilities Public")
    assert hp.place(hp.QUOTE_VALUE, water, layout, [WATER_BOX]) == [WATER_BOX]  # a checkbox
    value = _item("cost_approach.site_value", "85,000", quote="$ 85,000")
    assert hp.place(hp.QUOTE_VALUE, value, layout, []) == [SITE_VALUE]
    # Whole words first; a part of a word only when no whole word matches.
    year = _item("improvements.year_built", "1998", quote="Year Built 1998")
    assert hp.place(hp.QUOTE_VALUE, year, layout, []) == [YEAR]
    part = _item("k", "000", quote="Value = $ 85,000")
    assert hp.place(hp.QUOTE_VALUE, part, layout, []) == [SITE_VALUE]
    # The value not inside the quote: Basis's box stays.
    absent = _item("k", "Nowhere", quote="Year Built 1998")
    assert hp.place(hp.QUOTE_VALUE, absent, layout, [CITY]) == [CITY]


def test_checked_pair_takes_azures_box_only_when_azures_words_in_it_hold_the_value() -> None:
    layout = layout_of(_result([]))
    holds = _item("subject.county", "Springfield", pair=hp.AzurePair("Springfield", [COUNTY]))
    assert hp.pair_holds_value(holds, layout)
    assert hp.place(hp.CHECKED_PAIR, holds, layout, [CITY]) == [COUNTY]
    # The pair's box is over the year: its words do not hold the value, Basis's rule stays.
    off = _item("subject.county", "Springfield", pair=hp.AzurePair("Springfield", [YEAR]))
    assert not hp.pair_holds_value(off, layout)
    assert hp.place(hp.CHECKED_PAIR, off, layout, [COUNTY]) == [COUNTY]
    assert not hp.pair_holds_value(_item("k", "Springfield"), layout)  # no pair
    # A pair on another page than the field's cited page is not taken.
    other_page = hp.Placement(
        "subject.county", "Springfield", None, None, "llm", 2, pair=hp.AzurePair("x", [COUNTY])
    )
    assert not hp.pair_holds_value(other_page, layout)
    assert hp.place(hp.CHECKED_PAIR, other_page, layout, [CITY]) == [CITY]
    # The value's words inside the pair box: here the pair box is the word itself.
    assert hp.place(hp.CHECKED_PAIR_WORDS, holds, layout, [CITY]) == [COUNTY]
    assert hp.place(hp.CHECKED_PAIR_WORDS, off, layout, [CITY]) == [CITY]
    wide = _item("subject.county", "Springfield", pair=hp.AzurePair("x", [_box(0, 2, 3)]))
    assert hp.place(hp.CHECKED_PAIR, wide, layout, [CITY]) == [_box(0, 2, 3)]
    assert hp.place(hp.CHECKED_PAIR_WORDS, wide, layout, [CITY]) == [COUNTY]  # tight
    # A duplicate value cannot be told apart by its words: the neighborhood's "Springfield".
    duplicate = _item(
        "subject.city", "Springfield", pair=hp.AzurePair("Springfield", [NEIGHBORHOOD])
    )
    assert hp.place(hp.CHECKED_PAIR, duplicate, layout, [CITY]) == [NEIGHBORHOOD]


def test_recomputing_todays_rule_reproduces_production() -> None:
    layout = layout_of(_result(PAIRS))
    pairs = hp.azure_pairs(_result(PAIRS), uad36=False)
    county = _item("subject.county", "Springfield", source="azure", pair=pairs["subject.county"])
    assert hp.recompute_today(county, layout, "pipeline") == [COUNTY]  # the pair's own box
    # A model-read city: nearest the "City" caption, not Azure's (wrong) pair.
    city = _item("subject.city", "Springfield", pair=pairs["subject.city"])
    assert hp.recompute_today(city, layout, "pipeline") == [CITY]
    water = _item("site.utilities_water", "Public")
    assert hp.recompute_today(water, layout, "pipeline") == [PUBLIC_HEADER]
    value = _item("cost_approach.site_value", "85,000", num="85000")
    assert hp.recompute_today(value, layout, "pipeline") == [SITE_VALUE]


def test_the_grounded_reading_falls_back_to_the_quote() -> None:
    layout = layout_of(_result([]))
    # A cleaned value not printed as such ("85000"): no box from the value's words.
    quoted = _item("cost_approach.site_value", "85000", num="85000", quote="$ 85,000")
    assert hp.recompute_today(quoted, layout, "pipeline") == []
    assert hp.recompute_today(quoted, layout, "grounded") == [_box(4, 5, 6)]
    unquoted = _item("cost_approach.site_value", "85000", num="85000")
    assert hp.recompute_today(unquoted, layout, "grounded") == []


def test_azure_1004_boxes_raw_and_narrowed() -> None:
    boxes = hp.azure_1004_regions(_raw([], FORM_1004))
    raw_city, narrowed_city = boxes["subject.city"]
    assert raw_city == [_box(0, 0, 3)]  # the address field's box: the whole line
    assert narrowed_city == [CITY]  # narrowed to the city's words in it
    raw_water, narrowed_water = boxes["site.utilities_water"]
    assert narrowed_water == [PUBLIC_HEADER]
    assert raw_water != narrowed_water
    assert boxes["improvements.year_built"] == ([YEAR], [YEAR])
    assert "subject.county" not in boxes  # not a field of the 1004 model's map
    assert hp.azure_1004_regions(_raw(PAIRS)) == {}  # a layout analysis has no documents


# --------------------------------------------------------------------------- the test


def test_exact_means_the_box_holds_the_value_and_nothing_else() -> None:
    truth = _truth()
    words = _words()
    county = truth.field("subject.county")
    assert hp.exact(county, [COUNTY], words)
    whole_line = {"page": 1, "bbox": [0.0, COUNTY["bbox"][1], 1.0, COUNTY["bbox"][3]]}
    assert highlight_status(county, [whole_line], words) == "correct"  # lenient: contains
    assert not hp.exact(county, [whole_line], words)  # strict: other words inside
    assert not hp.exact(county, [], words)
    assert not hp.exact(county, [{"page": 7, "bbox": [0, 0, 1, 1]}], words)
    value = truth.field("cost_approach.site_value")
    assert hp.exact(value, [SITE_VALUE], words)
    assert hp.exact(value, [_box(4, 5, 6)], words)  # "$" is the form's, not another word
    assert hp.exact(value, [_box(4, 4, 6)], words)  # "=" "$" "85,000": the form's signs
    assert not hp.exact(value, [_box(4, 3, 6)], words)  # "Value" is another word
    assert hp.box_words([whole_line], words) == 4
    assert hp.box_words([], words) is None
    assert hp.box_words([{"page": 3, "bbox": [0, 0, 1, 1]}], words) is None
    assert hp.box_area([COUNTY]) == pytest.approx(
        (COUNTY["bbox"][2] - COUNTY["bbox"][0]) * (COUNTY["bbox"][3] - COUNTY["bbox"][1])
    )
    assert hp.box_area([]) is None


def test_checkable_needs_a_page_with_a_text_layer_and_a_printed_value() -> None:
    truth = _truth()
    assert hp.checkable(truth.field("subject.county"), _words())
    assert not hp.checkable(truth.field("subject.county"), None)
    assert not hp.checkable(truth.field("subject.county"), (PageWords(WIDTH, HEIGHT, ()),))
    assert not hp.checkable(truth.field("appraiser.name"), _words())  # absent


# --------------------------------------------------------------------------- the comparison


def _statuses(comparison: hp.Comparison, method: str) -> dict[str, str]:
    return {r.key: r.by_method[method]["status"] for r in comparison.rows}


def test_compare_scores_every_method_on_the_same_population() -> None:
    comparison = _compare()
    assert comparison.reader == "pipeline" and comparison.today_from == "stored"
    assert sorted(_statuses(comparison, hp.TODAY)) == sorted(
        [
            "cost_approach.site_value",
            "improvements.year_built",
            "site.utilities_water",
            "subject.city",
            "subject.county",
        ]
    )
    # Today's rule as stored: four right, the year has no box.
    assert _statuses(comparison, hp.TODAY) == {
        "subject.county": "correct",
        "subject.city": "correct",
        "site.utilities_water": "correct",
        "cost_approach.site_value": "correct",
        "improvements.year_built": "no_region",
    }
    # Azure's pair: the county right; the city on the neighborhood's "Springfield", which the
    # words test cannot tell from the city's (the occurrence test below can).
    assert _statuses(comparison, hp.AZURE_PAIR) == {
        "subject.county": "correct",
        "subject.city": "correct",
        "site.utilities_water": "no_region",
        "cost_approach.site_value": "no_region",
        "improvements.year_built": "no_region",
    }
    by = {r.key: r.by_method for r in comparison.rows}
    assert by["subject.city"][hp.AZURE_PAIR]["occurrence"] is False
    assert by["subject.city"][hp.TODAY]["occurrence"] is True
    assert by["subject.city"][hp.AZURE_PAIR]["iou"] == 0.0
    assert by["subject.city"][hp.TODAY]["iou"] == pytest.approx(1.0, abs=0.01)
    # Plain search: the first "Springfield" is the city's, so the county's box is on the
    # city's word: right by the words, not on the county's own occurrence.
    assert by["subject.county"][hp.TEXT_SEARCH]["status"] == "correct"
    assert by["subject.county"][hp.TEXT_SEARCH]["occurrence"] is False
    assert by["improvements.year_built"][hp.TEXT_SEARCH]["occurrence"] is True
    assert by["subject.city"][hp.AZURE_ELSE_TODAY]["occurrence"] is False
    assert by["subject.city"][hp.TODAY_ELSE_AZURE]["occurrence"] is True
    assert (
        by["subject.city"][hp.CHECKED_PAIR]["occurrence"] is False
    )  # the pair holds "Springfield"
    assert by["improvements.year_built"][hp.TODAY]["occurrence"] is False  # no box
    # Azure's 1004 model: the city, water and year boxes (the county is not in its map).
    form = _statuses(comparison, hp.AZURE_1004)
    assert form["subject.county"] == "no_region"
    assert form["subject.city"] == form["improvements.year_built"] == "correct"
    assert not by["subject.city"][hp.AZURE_1004]["strict"]  # the whole address line
    assert by["subject.city"][hp.AZURE_1004_NARROWED]["strict"]
    assert by["subject.county"][hp.TODAY]["strict"] and by["subject.county"][hp.TODAY]["words"] == 1
    # The year: stored with no box, recomputed found it.
    assert comparison.agreement == {"rows": 5, "equal": 4, "differ": 1}
    assert comparison.pair_coverage == {"printed": 5, "with_pair_box": 2, "with_1004_box": 3}
    assert comparison.scorer_highlight["checked"] == 5
    assert comparison.scorer_highlight["correct"] == 4
    assert comparison.pages_1004 == [1]
    assert all(comparison.seconds[name] >= 0 for name, _ in hp.METHODS)


def test_today_recomputed_is_scored_when_asked() -> None:
    comparison = _compare(today_from="recomputed")
    assert _statuses(comparison, hp.TODAY)["improvements.year_built"] == "correct"
    summary = hp.summarize(comparison)
    assert summary["methods"][hp.TODAY]["correct"] == 5
    assert summary["regression"]["stored"]["correct"] == 4
    assert summary["regression"]["recomputed"]["correct"] == 5


def test_the_reader_is_grounded_when_the_run_kept_quotes() -> None:
    quoted = {**field("1998", value_num="1998", source="llm"), "regions": [], "quote": "1998"}
    preds = _predictions(**{"improvements.year_built": quoted})
    assert hp.reader_of(preds) == "grounded"
    assert hp.reader_of(_predictions()) == "pipeline"
    assert _compare(preds=preds).reader == "grounded"


def test_only_correctly_read_verified_fields_are_scored() -> None:
    wrong_city = {**field("Elsewhere", source="llm"), "regions": [CITY]}
    comparison = _compare(preds=_predictions(**{"subject.city": wrong_city}))
    assert "subject.city" not in {r.key for r in comparison.rows}
    assert len(comparison.rows) == 4
    unverified = truth_doc(
        {
            "subject.county": {**_present("text", "springfield", "Springfield"), "verified": False},
            "subject.city": _present("text", "springfield", "Springfield"),
        },
        pages=1,
    )
    result = _result(PAIRS)
    rows = hp.compare(
        {"synthetic_1004": truth_set(unverified)},
        _predictions(),
        layouts=lambda doc: result,
        words_for=lambda doc: _words(),
        texts_for=lambda doc: None,
    ).rows
    assert [r.key for r in rows] == ["subject.city"]


def test_a_document_without_geometry_or_pdf_is_skipped() -> None:
    truths = {"synthetic_1004": truth_set(_truth())}

    def run(**kw: Any) -> hp.Comparison:
        options: dict[str, Any] = {
            "layouts": lambda doc: _result([]),
            "words_for": lambda doc: _words(),
            "texts_for": lambda doc: None,
            **kw,
        }
        return hp.compare(truths, _predictions(), **options)

    no_geometry = run(layouts=lambda doc: None)
    assert no_geometry.rows == [] and no_geometry.skipped[0]["reason"] == "no Azure DI geometry"
    no_pdf = run(words_for=lambda doc: None)
    assert no_pdf.rows == [] and no_pdf.skipped[0]["reason"] == "no PDF"
    unknown = run(documents=["synthetic_1004/other"])
    assert unknown.rows == [] and unknown.documents == []
    failed = hp.compare(
        truths,
        predictions(prediction_doc("doc_a", {"repeat": 1, "status": "error"})),
        layouts=lambda doc: _result([]),
        words_for=lambda doc: _words(),
        texts_for=lambda doc: None,
    )
    assert failed.skipped == [{"document": "synthetic_1004/doc_a", "reason": "failed"}]


def test_summary_counts_pick_and_table() -> None:
    summary = hp.summarize(_compare())
    today = summary["methods"][hp.TODAY]
    assert today["fields"] == 5 and today["placed"] == 4 and today["correct"] == 4
    assert today["right_among_placed"] == 1.0 and today["right_among_all"] == 0.8
    assert today["no_region"] == 1 and today["words_median"] == 1.0
    assert today["occurrence_fields"] == 5 and today["occurrence_right"] == 4
    assert today["iou_fields"] == 5 and today["iou50"] == 4
    azure = summary["methods"][hp.AZURE_PAIR]
    assert azure["placed"] == 2 and azure["correct"] == 2 and azure["wrong_box"] == 0
    assert azure["occurrence_right"] == 1 and azure["iou50"] == 1
    assert azure["coverage"] == 0.4 and azure["right_among_placed"] == 1.0
    assert summary["regression"]["scorer_agrees_with_stored"] is True
    assert summary["pair_coverage"]["rate"] == 0.4
    assert summary["pair_coverage"]["rate_1004"] == 0.6
    assert summary["form_1004"] == {"documents": 1, "pages": 1, "usd_per_appraisal": 0.01}
    assert summary["by"]["source"]["azure"][hp.TODAY]["fields"] == 1
    assert summary["by"]["kind"]["checkbox"][hp.TODAY]["fields"] == 1  # the water
    assert summary["by"]["kind"]["value"][hp.TODAY]["fields"] == 4
    # Basis first: today's four, plus Azure's box where today has none (none for the year).
    assert summary["methods"][hp.TODAY_ELSE_AZURE]["correct"] == 4
    # By the strictest test each field allows, today, Basis-first and plain search tie at 4
    # (search finds the year today stored no box for, but puts the county on the city's
    # word); search has one more by the words test alone, which cannot see that.
    assert today["right_combined"] == 4
    assert summary["methods"][hp.TEXT_SEARCH]["right_combined"] == 4
    assert summary["methods"][hp.TEXT_SEARCH]["correct"] == 5
    assert hp.pick(summary) == hp.TEXT_SEARCH
    table = hp.render_comparison(summary)
    assert "How to read this table" in table
    assert "No AI model" in table and "| None |" in table
    assert "prebuilt-mortgage.us.1004" in table and "$0.010 more" in table
    assert table.count("\n| ") == len(hp.METHODS) + 1  # the header and one row per method
    assert "Springfield" not in table and "85,000" not in table  # counts only, never a value


def test_pick_prefers_the_strictest_right_then_words_then_exact_then_time() -> None:
    methods = {
        "a": {"right_combined": 8, "correct": 10, "strict": 9, "seconds": 1.0},
        "b": {"right_combined": 8, "correct": 10, "strict": 9, "seconds": 0.5},
        "c": {"right_combined": 7, "correct": 11, "strict": 11, "seconds": 0.1},
    }
    assert hp.pick({"methods": methods}) == "b"
    methods["a"]["strict"] = 10
    assert hp.pick({"methods": methods}) == "a"
    methods["b"]["right_combined"] = 9
    assert hp.pick({"methods": methods}) == "b"


def test_box_truth_needs_the_quote_once_and_the_value_once_inside_it() -> None:
    truth = _truth()
    words = _words()
    county = hp.box_truth(truth.field("subject.county"), words)
    assert county.quote_box is not None and county.value_box is not None
    assert [round(v, 4) for v in county.value_box] == COUNTY["bbox"]
    # "Springfield" alone is printed three times: no box truth.
    ambiguous = truth.field("subject.city").model_copy(update={"quote": "Springfield"})
    assert hp.box_truth(ambiguous, words) == hp.BoxTruth(None, None)
    missing = truth.field("subject.city").model_copy(update={"quote": "Not on the page"})
    assert hp.box_truth(missing, words) == hp.BoxTruth(None, None)
    unquoted = truth.field("subject.city").model_copy(update={"quote": None})
    assert hp.box_truth(unquoted, words) == hp.BoxTruth(None, None)
    # A quote spanning words; the value found inside it once.
    value = hp.box_truth(truth.field("cost_approach.site_value"), words)
    assert value.value_box is not None
    assert [round(v, 4) for v in value.value_box] == SITE_VALUE["bbox"]
    assert hp.find_runs(words[0], "Springfield", within=county.quote_box) == [county.value_box]
    assert len(hp.find_runs(words[0], "Springfield")) == 3
    assert hp.find_runs(words[0], "") == []


def test_iou() -> None:
    assert hp.iou((0, 0, 1, 1), (0, 0, 1, 1)) == 1.0
    assert hp.iou((0, 0, 1, 1), (2, 2, 3, 3)) == 0.0
    assert hp.iou((0, 0, 2, 1), (1, 0, 3, 1)) == pytest.approx(1 / 3)
    assert hp.iou((0, 0, 0, 0), (0, 0, 0, 0)) == 0.0


def test_truth_overrides_parse_and_refuse(tmp_path: Path) -> None:
    path = tmp_path / "gse.json"
    path.write_text("{}")
    assert hp.truth_overrides([f"gse_d1={path}"]) == {"gse_d1": path}
    for bad in ("gse_d1", f"nope={path}", f"gse_d1={tmp_path / 'missing.json'}"):
        with pytest.raises(SystemExit):
            hp.truth_overrides([bad])


# --------------------------------------------------------------------------- the run record


def test_the_run_record_files_one_case_per_field_and_method(tmp_path: Path) -> None:
    out = tmp_path / "run"
    run = record.EvalRun.begin(
        out,
        harness="evals.highlight_placement",
        log=False,
        component=hp.COMPONENT,
        tree=tmp_path / "tree",
    )
    comparison = _compare()
    for row in comparison.rows:
        for method, score in row.by_method.items():
            run.case(
                f"{row.set}/{row.document}/{row.key}#{method}",
                record.PASSED if score["status"] == "correct" else record.FAILED,
                kind="highlight",
                detail={"method": method, **score},
            )
    summary = hp.summarize(comparison)
    manifest = run.finish(summary)
    assert manifest["tree"]["component"] == hp.COMPONENT
    results = json.loads((out / record.RESULTS_FILE).read_text())
    passed = sum(m["correct"] for m in summary["methods"].values())
    assert results["summary"][record.PASSED] == passed
    assert results["summary"][record.PASSED] + results["summary"][record.FAILED] == 5 * len(
        hp.METHODS
    )
    assert (tmp_path / "tree" / hp.COMPONENT).is_dir()
    assert json.loads((out / record.SUMMARY_FILE).read_text())["fields"] == 5
