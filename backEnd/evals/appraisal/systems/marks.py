"""Which option each Azure DI selection mark checks, from the page geometry.

Azure DI reads a checkbox as a bare ``:selected:`` / ``:unselected:`` token in reading order and
leaves the option it belongs to implicit. The legacy 1004 prints options two ways:

* **inline**, the label right after the box: ``☐ Yes ☒ No``, ``☒ Urban ☐ Suburban``;
* **in a column** under a header, the row caption at the left: the utilities grid
  (``Utilities  Public  Other (describe)`` over ``Electricity ☒ ☐``) and off-site improvements
  (``Public  Private`` over ``Street Asphalt ☒ ☐``).

Azure DI's own key-value pairs get the inline case right and the column case wrong (they pair
the box with the row caption: ``Water -> :selected:`` where the box is under ``Public``), so the
label is read from the boxes instead (:func:`label_marks`):

1. the words right after the mark on its line (a gap of at most :data:`RIGHT_GAP`, words joined
   while their gaps stay under :data:`WORD_GAP`, stopping at the next mark);
2. else, when the mark is in a **column** (another mark centred within :data:`COLUMN_X` of it, a
   row or two above or below), the header over the column's top mark: the nearest word above
   whose box spans the column's centre, within :data:`HEADER_REACH`, with the words that follow
   it on its line. (Words right after the box win: a vertical list of inline options, such as
   the neighborhood grid, also stacks its boxes in columns.)
3. otherwise the words right before the mark on its line (a box printed after its label).

:func:`labelled_texts` writes the label into the page text after the token
(``:selected:[Public]``), so a model reads which option is checked instead of guessing which
neighbour a bare token belongs to; :func:`checkbox_problem` checks a checkbox answer against
the labelled marks.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from basis.extraction.fallback import appears_in
from evals.appraisal.systems.layout import MARK_TOKEN, Box, Layout, Mark, Page, Word, locate

RIGHT_GAP: Final = 0.010  # page widths between a box and the first word of its label
WORD_GAP: Final = 0.005  # page widths between two words of one label (measured: words of one
# label 0.0029-0.0045 apart; the next caption or an Other write-in 0.0057 or more)
LEFT_GAP: Final = 0.010
COLUMN_X: Final = 0.006  # page widths between the centres of two boxes of one column
COLUMN_Y: Final = 0.030  # page heights between two boxes of one column
HEADER_REACH: Final = 0.030  # page heights from a column's top box up to its header
MAX_WORDS: Final = 8
LINE_OVERLAP: Final = 0.5  # share of the shorter box's height two boxes overlap on one line

_FOLD: Final = re.compile(r"[^0-9a-z%#/]+")
_LABELLED: Final = re.compile(r":(un)?selected:\[([^\]\n]*)\]", re.IGNORECASE)
_LABEL_AFTER_MARK: Final = re.compile(r"(?<=selected:)\[[^\]\n]*\]", re.IGNORECASE)
_SPLIT: Final = re.compile(r"\s*[,;]\s*")


@dataclass(frozen=True, slots=True)
class LabelledMark:
    mark: Mark
    label: str  # "" when nothing near the box reads as its label
    how: str  # "right" | "column" | "left" | "none"


def _cy(box: Box) -> float:
    return (box[1] + box[3]) / 2


def _cx(box: Box) -> float:
    return (box[0] + box[2]) / 2


def same_line(a: Box, b: Box) -> bool:
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    shorter = min(a[3] - a[1], b[3] - b[1])
    return shorter > 0 and overlap >= LINE_OVERLAP * shorter


def _phrase_right(start: Word, words: Sequence[Word], stops: Sequence[float]) -> list[Word]:
    """``start`` and the words after it on its line, while the gaps stay word gaps and no box
    (``stops``: the left edges of the line's other boxes) comes between."""
    line = sorted((w for w in words if same_line(w.box, start.box)), key=lambda w: w.box[0])
    out = [start]
    for word in line:
        if word.box[0] <= out[-1].box[0]:
            continue
        gap = word.box[0] - out[-1].box[2]
        if gap > WORD_GAP or any(out[-1].box[2] <= s <= word.box[0] for s in stops):
            break
        out.append(word)
        if len(out) >= MAX_WORDS:
            break
    return out


def _right_words(mark: Mark, page: Page) -> list[Word]:
    on_line = [
        w for w in page.words if same_line(w.box, mark.box) and w.box[0] >= mark.box[2] - 0.002
    ]
    if not on_line:
        return []
    first = min(on_line, key=lambda w: w.box[0])
    if first.box[0] - mark.box[2] > RIGHT_GAP:
        return []
    stops = [m.box[0] for m in page.marks if m is not mark and same_line(m.box, mark.box)]
    if any(mark.box[2] <= s <= first.box[0] for s in stops):
        return []
    phrase = _phrase_right(first, page.words, stops)
    return phrase + _wrapped(phrase, page)


def _wrapped(phrase: Sequence[Word], page: Page) -> list[Word]:
    """The next line's opening words when a sentence option wraps (``☐ subject to the`` /
    ``following required inspection``): the phrase ends its line with a lower-case word and the
    next line opens with one at the left margin."""
    last = phrase[-1]
    if len(phrase) >= MAX_WORDS or not last.text[:1].islower():
        return []
    if any(same_line(w.box, last.box) and w.box[0] > last.box[2] for w in page.words):
        return []  # the line goes on: the phrase stopped at a gap or a box, not the line end
    height = last.box[3] - last.box[1]
    # The next line down; single letters are the form's vertical side labels ("S U B J E C T").
    below = [
        w
        for w in page.words
        if len(w.text) > 1
        and 0 < w.box[1] - last.box[1] <= 2 * height
        and not same_line(w.box, last.box)
    ]
    if not below:
        return []
    top = min(below, key=lambda w: w.box[1])
    margin = min(w.box[0] for w in page.words if len(w.text) > 1)
    opening = min((w for w in below if same_line(w.box, top.box)), key=lambda w: w.box[0])
    if opening.box[0] - margin > RIGHT_GAP * 3 or not opening.text[:1].islower():
        return []
    stops = [m.box[0] for m in page.marks if same_line(m.box, opening.box)]
    return _phrase_right(opening, page.words, stops)[: MAX_WORDS - len(phrase)]


def _left_words(mark: Mark, page: Page) -> list[Word]:
    line = sorted(
        (w for w in page.words if same_line(w.box, mark.box) and w.box[2] <= mark.box[0] + 0.002),
        key=lambda w: -w.box[2],
    )
    stops = [m.box[2] for m in page.marks if m is not mark and same_line(m.box, mark.box)]
    out: list[Word] = []
    edge = mark.box[0]
    for word in line:
        gap = edge - word.box[2]
        if gap > (LEFT_GAP if not out else WORD_GAP) or any(
            word.box[2] <= s <= edge for s in stops
        ):
            break
        out.append(word)
        edge = word.box[0]
        if len(out) >= MAX_WORDS:
            break
    return list(reversed(out))


def column_of(mark: Mark, page: Page) -> list[Mark]:
    """The boxes stacked with ``mark`` (itself included), top first: boxes centred within
    :data:`COLUMN_X` of each other, each within :data:`COLUMN_Y` of the next."""
    column = [mark]
    others = [
        m for m in page.marks if m is not mark and abs(_cx(m.box) - _cx(mark.box)) <= COLUMN_X
    ]
    grew = True
    while grew:
        grew = False
        for other in others:
            if other in column:
                continue
            if any(abs(_cy(other.box) - _cy(c.box)) <= COLUMN_Y for c in column):
                column.append(other)
                grew = True
    return sorted(column, key=lambda m: m.box[1])


def _header(column: Sequence[Mark], page: Page) -> list[Word]:
    top = column[0].box
    centre = sum(_cx(m.box) for m in column) / len(column)
    above = [
        w
        for w in page.words
        if w.box[3] <= top[1] + 0.002
        and top[1] - w.box[3] <= HEADER_REACH
        and w.box[0] - 0.002 <= centre <= w.box[2] + 0.002
    ]
    if not above:
        return []
    nearest = max(above, key=lambda w: w.box[3])
    return _phrase_right(nearest, page.words, [])


def _text(words: Sequence[Word]) -> str:
    return " ".join(w.text for w in words).strip()


def label_mark(mark: Mark, page: Page) -> LabelledMark:
    right = _text(_right_words(mark, page))
    if right:
        return LabelledMark(mark, right, "right")
    column = column_of(mark, page)
    header = _text(_header(column, page)) if len(column) > 1 else ""
    if header:
        return LabelledMark(mark, header, "column")
    left = _text(_left_words(mark, page))
    if left:
        return LabelledMark(mark, left, "left")
    return LabelledMark(mark, "", "none")


def label_marks(page: Page) -> list[LabelledMark]:
    """Every mark on ``page`` with its label, in reading order (content offset)."""
    marks = sorted(page.marks, key=lambda m: m.offset)
    return [label_mark(m, page) for m in marks]


def labelled_text(page: Page) -> str:
    """The page text with each mark token followed by ``[label]``; the text unchanged when its
    tokens and the page's marks do not pair one to one (a recording without spans)."""
    labelled = label_marks(page)
    tokens = list(MARK_TOKEN.finditer(page.text))
    if len(tokens) != len(labelled) or any(m.mark.offset < 0 for m in labelled):
        return page.text
    out: list[str] = []
    last = 0
    for token, item in zip(tokens, labelled, strict=True):
        out.append(page.text[last : token.end()])
        if item.label:
            out.append(f"[{item.label.replace('[', '(').replace(']', ')')}]")
        last = token.end()
    out.append(page.text[last:])
    return "".join(out)


def labelled_texts(layout: Layout) -> list[str]:
    """:meth:`Layout.texts` with every mark labelled."""
    if not layout.pages:
        return []
    out = [""] * max(p.number for p in layout.pages)
    for page in layout.pages:
        out[page.number - 1] = labelled_text(page)
    return out


def fold_label(text: str) -> str:
    return _FOLD.sub(" ", text.casefold()).strip()


def quote_labels(quote: str) -> tuple[list[str], list[str]]:
    """The folded labels of every labelled mark in ``quote``, and of the checked ones."""
    every: list[str] = []
    checked: list[str] = []
    for match in _LABELLED.finditer(quote):
        label = fold_label(match.group(2))
        every.append(label)
        if match.group(1) is None:
            checked.append(label)
    return every, checked


def checkbox_problem(value: str, quote: str, page: Page | None) -> str | None:
    """Why a checkbox answer is refused, or ``None``. Each option (``, `` / ``;`` separated):

    * the label of a box in the quote: that box must be checked (``checkbox_not_selected``);
    * else the label of a box elsewhere on the page (a quote without the box's mark): a box
      with that label must be checked somewhere on the page;
    * else not a box on this page (the UAD 3.6 form prints the option as text): the option
      must occur in the quote (``value_not_in_quote``), and the quote must not be on a line
      with boxes (text written beside a box, "Municipal" by an empty Other box, is not the
      checked option: ``checkbox_not_selected``)."""
    options = [o for o in _SPLIT.split(value.strip()) if o]
    if not options:
        return "not_found"
    in_quote, checked_in_quote = quote_labels(quote)
    on_page = [fold_label(m.label) for m in label_marks(page)] if page is not None else []
    checked_on_page = selected_labels(page) if page is not None else []
    for option in options:
        if option_checked(option, in_quote):
            if not option_checked(option, checked_in_quote):
                return "checkbox_not_selected"
        elif option_checked(option, on_page):
            if not option_checked(option, checked_on_page):
                return "checkbox_not_selected"
        elif not appears_in(option, quote):
            return "value_not_in_quote"
        elif page is not None and beside_a_box(quote, page):
            return "checkbox_not_selected"
    return None


def beside_a_box(quote: str, page: Page) -> bool:
    """A selection mark sits on a line the quote's words cover."""
    box = locate(page, quote)
    if box is None:
        return False
    return any(_cy(m.box) >= box[1] and _cy(m.box) <= box[3] for m in page.marks)


def without_labels(text: str) -> str:
    """``text`` with the ``[label]`` after each mark removed (a quote the model copied without
    the labels still matches the page)."""
    return _LABEL_AFTER_MARK.sub("", text)


def selected_labels(page: Page) -> list[str]:
    """The folded labels of the checked boxes on ``page``."""
    return [fold_label(i.label) for i in label_marks(page) if i.mark.selected and i.label]


def option_checked(option: str, labels: Sequence[str]) -> bool:
    """``option`` names a checked box: its folded words equal a label's, or are a prefix of it
    (``Other`` for ``Other (describe)``), or a label is a prefix of the option (``Other`` box
    with the write-in ``Other Infloor``)."""
    want = fold_label(option)
    if not want:
        return False
    for label in labels:
        if not label:
            continue
        if want == label or label.startswith(want + " ") or want.startswith(label + " "):
            return True
    return False
