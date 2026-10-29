"""Page geometry of an Azure DI analysis: words, selection marks, the page text with marks.

Built from the geometry :func:`basis.extraction.azure_di.from_sdk` keeps
(:class:`~basis.extraction.azure_di.AzureDiResult`, :func:`layout_of`):

* per page, the **text with marks**: ``content`` sliced by the page's spans, so a checked box is
  ``:selected:`` and an empty one ``:unselected:`` where Azure DI read them, in reading order;
* **words** and **selection marks** with their boxes as page fractions ``(x0, y0, x1, y1)``,
  origin top-left (polygon x / page width, y / page height);
* the share of the page covered by figures (photo and sketch pages).

:func:`locate` finds a text among a page's words (whitespace and case folded, selection-mark
tokens and their labels ignored) and returns the union box of the words it covers: the region
the review page highlights. The appraisal sweep (``evals/appraisal/systems``) uses the same
code over its recordings.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from basis.extraction.azure_di import AzureDiResult

Box = tuple[float, float, float, float]

MARK_TOKEN: Final = re.compile(r":(?:un)?selected:", re.IGNORECASE)
# A mark with the label :mod:`basis.extraction.marks` writes after it.
LABELLED_MARK_TOKEN: Final = re.compile(r":(?:un)?selected:(?:\[[^\]\n]*\])?", re.IGNORECASE)
_QUOTES: Final = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"'})
_SPACES: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    box: Box
    offset: int = -1  # into the result's ``content``; -1 when the recording has no span


@dataclass(frozen=True, slots=True)
class Mark:
    selected: bool
    box: Box
    offset: int = -1


@dataclass(frozen=True, slots=True)
class Page:
    number: int
    width: float
    height: float
    words: tuple[Word, ...] = ()
    marks: tuple[Mark, ...] = ()
    lines: tuple[str, ...] = ()
    text: str = ""  # content with selection marks, this page only
    figure_fraction: float = 0.0  # share of the page area inside figures (0..1)

    @property
    def word_count(self) -> int:
        return len(self.words)


@dataclass(frozen=True, slots=True)
class Layout:
    pages: tuple[Page, ...]

    def page(self, number: int | None) -> Page | None:
        for page in self.pages:
            if page.number == number:
                return page
        return None

    def texts(self) -> list[str]:
        """Page texts with marks, page 1 first (index = page number - 1; gaps are "")."""
        if not self.pages:
            return []
        out = [""] * max(p.number for p in self.pages)
        for page in self.pages:
            out[page.number - 1] = page.text
        return out


def layout_of(result: AzureDiResult) -> Layout:
    """The geometry of a stored analysis (pages without geometry give empty pages)."""
    pages = tuple(
        Page(
            number=page.page_number,
            width=page.width,
            height=page.height,
            words=tuple(Word(w.text, w.bbox, w.offset) for w in page.words),
            marks=tuple(Mark(m.selected, m.bbox, m.offset) for m in page.marks),
            lines=page.lines,
            text=result.page_text_with_marks(page),
            figure_fraction=page.figure_fraction,
        )
        for page in sorted(result.pages, key=lambda p: p.page_number)
    )
    return Layout(pages)


def has_geometry(result: AzureDiResult) -> bool:
    return any(page.words for page in result.pages)


def union(boxes: Sequence[Box]) -> Box:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


# --------------------------------------------------------------------------- locating text


def fold_chars(text: str) -> str:
    """NFKC, casefold, typographic quotes straight, selection-mark tokens (with their labels)
    and all whitespace removed: what :func:`locate` compares."""
    folded = unicodedata.normalize("NFKC", LABELLED_MARK_TOKEN.sub(" ", text)).translate(_QUOTES)
    return _SPACES.sub("", folded.casefold())


def _inside(box: Box, within: Box, slack: float = 0.005) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return (
        within[0] - slack <= cx <= within[2] + slack
        and within[1] - slack <= cy <= within[3] + slack
    )


def _distance(a: Box, b: Box) -> float:
    ax, ay = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return float((ax - bx) ** 2 + (ay - by) ** 2)


def find_all(
    page: Page, text: str, *, within: Box | None = None, whole_words: bool = False
) -> list[Box]:
    """Every run of consecutive words (reading order) whose folded characters contain the
    folded ``text``; the union box of each run. ``within``: only words centred in that box.
    ``whole_words``: the run must start at its first word's start and end at its last word's
    end (a caption "City" is not the end of "Electricity")."""
    target = fold_chars(text)
    if not target:
        return []
    words = [w for w in page.words if within is None or _inside(w.box, within)]
    joined: list[str] = []
    owner: list[int] = []  # the word index of each character of ``joined``
    starts: list[int] = []  # the haystack offset where each word starts
    for index, word in enumerate(words):
        folded = fold_chars(word.text)
        starts.append(len(owner))
        joined.append(folded)
        owner.extend([index] * len(folded))
    haystack = "".join(joined)
    found: list[Box] = []
    start = haystack.find(target)
    while start >= 0:
        end = start + len(target)
        first, last = owner[start], owner[end - 1]
        last_end = starts[last + 1] if last + 1 < len(starts) else len(haystack)
        if not whole_words or (start == starts[first] and end == last_end):
            found.append(union([w.box for w in words[first : last + 1]]))
        start = haystack.find(target, start + 1)
    return found


def locate(
    page: Page | None,
    text: str | None,
    *,
    near: Box | None = None,
    within: Box | None = None,
    whole_words: bool = False,
) -> Box | None:
    """The box of ``text`` on ``page`` (the occurrence nearest ``near`` when it is given, else
    the first in reading order), or ``None`` when the words do not contain it."""
    if page is None or not text:
        return None
    found = find_all(page, text, within=within, whole_words=whole_words)
    if not found:
        return None
    if near is None:
        return found[0]
    return min(found, key=lambda box: _distance(box, near))


def region(page: int, box: Box) -> dict[str, Any]:
    """The JSON form of a region: ``{"page": n, "bbox": [x0, y0, x1, y1]}`` (page fractions)."""
    return {"page": page, "bbox": [round(v, 4) for v in box]}
