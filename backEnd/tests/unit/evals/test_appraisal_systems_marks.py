"""Checkbox labels from the page geometry (``evals.appraisal.systems.marks``).

Positions are page fractions copied from the Azure DI analyses of the client 1004 and a
synthetic 1004 (the utilities grid, the neighborhood grid, the appraisal-made sentence options).
"""

from __future__ import annotations

from evals.appraisal.systems.layout import Mark, Page, Word, fold_chars
from evals.appraisal.systems.marks import (
    beside_a_box,
    checkbox_problem,
    label_marks,
    labelled_text,
    option_checked,
    selected_labels,
    without_labels,
)


def w(text: str, x0: float, x1: float, y0: float, y1: float, offset: int = -1) -> Word:
    return Word(text, (x0, y0, x1, y1), offset)


def m(selected: bool, x0: float, x1: float, y0: float, y1: float, offset: int = -1) -> Mark:
    return Mark(selected, (x0, y0, x1, y1), offset)


def page(words: list[Word], marks: list[Mark], text: str = "") -> Page:
    return Page(1, 8.5, 11.0, tuple(words), tuple(marks), (), text)


def labels(p: Page) -> list[tuple[bool, str, str]]:
    return [(i.mark.selected, i.label, i.how) for i in label_marks(p)]


# The client's utilities grid: the boxes sit under the column headers, nothing after them
# except an Other write-in ("Natural Gas") or the next column's caption ("Water").
UTILITIES = page(
    [
        w("Utilities", 0.050, 0.087, 0.5042, 0.5132),
        w("Public", 0.120, 0.151, 0.5042, 0.5136),
        w("Other", 0.166, 0.191, 0.5044, 0.5138),
        w("(describe)", 0.194, 0.243, 0.5044, 0.5139),
        w("Electricity", 0.050, 0.095, 0.5157, 0.5254),
        w("Water", 0.337, 0.365, 0.5160, 0.5248),
        w("Gas", 0.051, 0.069, 0.5274, 0.5356),
        w("Natural", 0.194, 0.240, 0.5280, 0.5371),
        w("Gas", 0.244, 0.270, 0.5281, 0.5371),
    ],
    [
        m(True, 0.126, 0.142, 0.5161, 0.5253, 1),
        m(False, 0.175, 0.190, 0.5159, 0.5254, 2),
        m(True, 0.126, 0.142, 0.5275, 0.5367, 3),
        m(False, 0.175, 0.190, 0.5271, 0.5365, 4),
    ],
)


def test_a_box_in_a_column_takes_its_header() -> None:
    assert labels(UTILITIES) == [
        (True, "Public", "column"),
        (False, "Other (describe)", "column"),
        (True, "Public", "column"),
        # An Other box with a write-in after it: the write-in is the right-hand label.
        (False, "Natural Gas", "right"),
    ]
    assert selected_labels(UTILITIES) == ["public", "public"]


def test_words_right_after_a_box_win_over_a_stacked_column() -> None:
    # The neighborhood grid stacks inline options in columns: Urban over Over 75% over Rapid.
    grid = page(
        [
            w("Neighborhood", 0.050, 0.110, 0.300, 0.310),
            w("Urban", 0.115, 0.145, 0.318, 0.328),
            w("Over", 0.116, 0.140, 0.330, 0.340),
            w("75%", 0.143, 0.163, 0.330, 0.340),
            w("Rapid", 0.115, 0.142, 0.342, 0.352),
        ],
        [
            m(False, 0.098, 0.112, 0.318, 0.328, 1),
            m(True, 0.099, 0.113, 0.330, 0.340, 2),
            m(False, 0.098, 0.112, 0.342, 0.352, 3),
        ],
    )
    assert labels(grid) == [
        (False, "Urban", "right"),
        (True, "Over 75%", "right"),
        (False, "Rapid", "right"),
    ]


def test_a_label_stops_at_the_next_caption_and_at_the_next_box() -> None:
    # Measured: words of one label 0.0029-0.0045 apart; the next caption 0.0058-0.0077.
    row = page(
        [
            w("Under", 0.270, 0.300, 0.330, 0.340),
            w("25%", 0.3032, 0.323, 0.330, 0.340),
            w("Demand/Supply", 0.3304, 0.400, 0.330, 0.340),
            w("Yes", 0.450, 0.470, 0.330, 0.340),
            w("No", 0.500, 0.515, 0.330, 0.340),
        ],
        [
            m(True, 0.253, 0.267, 0.330, 0.340, 1),
            m(False, 0.433, 0.447, 0.330, 0.340, 2),
            m(True, 0.4735, 0.4965, 0.330, 0.340, 3),
        ],
    )
    assert labels(row) == [
        (True, "Under 25%", "right"),
        (False, "Yes", "right"),
        (True, "No", "right"),
    ]


def test_a_sentence_option_wraps_to_the_next_line() -> None:
    # "or [x] subject to the" ends its line; "following required inspection" opens the next,
    # with the form's vertical side letters ("L", "I") further left.
    made = page(
        [
            w("or", 0.850, 0.865, 0.8965, 0.9065),
            w("subject", 0.890, 0.929, 0.897, 0.907),
            w("to", 0.932, 0.943, 0.897, 0.907),
            w("the", 0.9457, 0.9638, 0.8965, 0.9065),
            w("L", 0.033, 0.037, 0.902, 0.907),
            w("following", 0.045, 0.089, 0.907, 0.917),
            w("required", 0.092, 0.136, 0.907, 0.917),
            w("inspection", 0.139, 0.197, 0.907, 0.917),
            w("I", 0.034, 0.036, 0.910, 0.915),
        ],
        [m(True, 0.871, 0.886, 0.896, 0.906, 1)],
    )
    [(selected, label, how)] = labels(made)
    assert (selected, how) == (True, "right")
    assert label == "subject to the following required inspection"


def test_no_wrap_after_a_capitalized_label_or_a_line_that_goes_on() -> None:
    # "Private" ends the line, but the next line opens a new caption: not a wrapped sentence.
    p = page(
        [
            w("Private", 0.944, 0.978, 0.504, 0.513),
            w("Electricity", 0.050, 0.095, 0.516, 0.525),
        ],
        [m(True, 0.925, 0.940, 0.504, 0.513, 1)],
    )
    assert labels(p) == [(True, "Private", "right")]


def test_a_box_after_its_label_and_a_box_alone() -> None:
    p = page(
        [w("Fireplace(s)", 0.300, 0.360, 0.500, 0.510), w("#", 0.3635, 0.370, 0.500, 0.510)],
        [m(True, 0.373, 0.388, 0.500, 0.510, 1), m(False, 0.700, 0.715, 0.800, 0.810, 2)],
    )
    assert labels(p) == [(True, "Fireplace(s) #", "left"), (False, "", "none")]


def test_labelled_text_writes_each_label_after_its_token() -> None:
    text = "Occupant :selected: Owner :unselected: Tenant"
    p = page(
        [
            w("Occupant", 0.050, 0.100, 0.100, 0.110, 0),
            w("Owner", 0.122, 0.150, 0.100, 0.110, 20),
            w("Tenant", 0.172, 0.200, 0.100, 0.110, 39),
        ],
        [m(True, 0.104, 0.118, 0.100, 0.110, 9), m(False, 0.154, 0.168, 0.100, 0.110, 26)],
        text,
    )
    out = labelled_text(p)
    assert out == "Occupant :selected:[Owner] Owner :unselected:[Tenant] Tenant"
    assert without_labels(out) == text
    assert fold_chars(out) == fold_chars(text)  # locating a quote ignores the labels
    # Tokens and marks that do not pair one to one (no spans): the text is left alone.
    no_spans = page(list(p.words), [m(True, 0.104, 0.118, 0.100, 0.110)], text)
    assert labelled_text(no_spans) == text


def test_option_matching() -> None:
    assert option_checked("Det", ["det"])  # the model dropped the dot
    assert option_checked("Other", ["other describe"])
    assert option_checked("Other Infloor", ["other"])  # a write-in after the Other box
    assert option_checked('"as is"', ["as is"])
    assert not option_checked("Public", ["private"])
    assert not option_checked("Pub", ["public"])  # whole words only
    assert not option_checked("", ["public"])


def test_checkbox_problem_on_the_utilities_grid() -> None:
    # A quote with labels: the box named in the quote must be checked.
    labelled = "Electricity :selected:[Public] :unselected:[Other (describe)]"
    assert checkbox_problem("Public", labelled, UTILITIES) is None
    assert checkbox_problem("Other", labelled, UTILITIES) == "checkbox_not_selected"
    # A quote without labels: a box with that label must be checked on the page.
    bare = "Electricity :selected: :unselected:"
    assert checkbox_problem("Public", bare, UTILITIES) is None
    assert checkbox_problem("Other (describe)", bare, UTILITIES) == "checkbox_not_selected"
    # The write-in beside an empty Other box is not the checked option.
    assert checkbox_problem("Natural Gas", "Natural Gas", UTILITIES) == "checkbox_not_selected"
    # Several options: each must hold; an empty value is no answer.
    assert checkbox_problem("Public, Other", labelled, UTILITIES) == "checkbox_not_selected"
    assert checkbox_problem("", labelled, UTILITIES) == "not_found"


def test_checkbox_problem_when_the_form_prints_the_answer_as_text() -> None:
    # UAD 3.6: "Car Storage Garage", no box on the line.
    p = page(
        [
            w("Car", 0.050, 0.070, 0.300, 0.310),
            w("Storage", 0.073, 0.110, 0.300, 0.310),
            w("Garage", 0.300, 0.340, 0.300, 0.310),
        ],
        [m(True, 0.500, 0.515, 0.700, 0.710, 1)],  # a box elsewhere on the page
    )
    assert checkbox_problem("Garage", "Car Storage Garage", p) is None
    assert checkbox_problem("Carport", "Car Storage Garage", p) == "value_not_in_quote"
    assert not beside_a_box("Car Storage Garage", p)
    assert checkbox_problem("Garage", "Car Storage Garage", None) is None  # no geometry
