"""Azure DI page geometry from a raw recording: words, selection marks, page text with marks.

The production :class:`~basis.extraction.azure_di.AzureDiResult` keeps page lines (which carry
no selection marks) and drops every polygon. The sweep systems read the raw analysis instead
(``recordings/<sha>/azure_di.json``, the SDK's ``as_dict()``; the ``prebuilt-mortgage.us.1004``
result has the same ``pages`` shape):

* per page, the **text with marks**: ``content`` sliced by the page's spans, so a checked box is
  ``:selected:`` and an empty one ``:unselected:`` where Azure DI read them, in reading order;
* **words** and **selection marks** with their boxes as page fractions ``(x0, y0, x1, y1)``
  (polygon x / page width, y / page height; Azure DI gives PDF coordinates in inches);
* the share of the page covered by figures (photo and sketch pages).

:func:`locate` finds a text among a page's words (whitespace and case folded, selection-mark
tokens ignored) and returns the union box of the words it covers: the region the review page
would highlight.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

Box = tuple[float, float, float, float]

MARK_TOKEN: Final = re.compile(r":(?:un)?selected:", re.IGNORECASE)
# A mark with the label :mod:`~evals.appraisal.systems.marks` writes after it.
LABELLED_MARK_TOKEN: Final = re.compile(r":(?:un)?selected:(?:\[[^\]\n]*\])?", re.IGNORECASE)
_QUOTES: Final = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})
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


def box_of(polygon: Sequence[float] | None, width: float, height: float) -> Box | None:
    """The bounding box of an Azure polygon ``[x1, y1, ..., x4, y4]`` as page fractions."""
    if not polygon or len(polygon) < 4 or width <= 0 or height <= 0:
        return None
    xs = [float(v) for v in polygon[0::2]]
    ys = [float(v) for v in polygon[1::2]]
    return (
        _clamp(min(xs) / width),
        _clamp(min(ys) / height),
        _clamp(max(xs) / width),
        _clamp(max(ys) / height),
    )


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def union(boxes: Sequence[Box]) -> Box:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _offset(item: Mapping[str, Any]) -> int:
    span = item.get("span") or {}
    return int(span.get("offset", -1)) if isinstance(span, Mapping) else -1


def from_raw(raw: Mapping[str, Any]) -> Layout:
    """The geometry of a raw ``AnalyzeResult.as_dict()`` (camelCase REST names)."""
    content = str(raw.get("content") or "")
    figure_boxes: dict[int, list[Box]] = {}
    sizes: dict[int, tuple[float, float]] = {}
    for page in raw.get("pages") or ():
        sizes[int(page["pageNumber"])] = (
            float(page.get("width") or 0.0),
            float(page.get("height") or 0.0),
        )
    for figure in raw.get("figures") or ():
        for region in figure.get("boundingRegions") or ():
            number = int(region.get("pageNumber", 0))
            width, height = sizes.get(number, (0.0, 0.0))
            box = box_of(region.get("polygon"), width, height)
            if box is not None:
                figure_boxes.setdefault(number, []).append(box)
    pages: list[Page] = []
    for page in raw.get("pages") or ():
        number = int(page["pageNumber"])
        width, height = sizes[number]
        words = tuple(
            Word(str(w.get("content", "")), box, _offset(w))
            for w in page.get("words") or ()
            if (box := box_of(w.get("polygon"), width, height)) is not None
        )
        marks = tuple(
            Mark(str(m.get("state", "")).lower() == "selected", box, _offset(m))
            for m in page.get("selectionMarks") or ()
            if (box := box_of(m.get("polygon"), width, height)) is not None
        )
        lines = tuple(str(line.get("content", "")) for line in page.get("lines") or ())
        spans = [s for s in page.get("spans") or () if isinstance(s, Mapping)]
        if content and spans:
            text = "".join(
                content[int(s["offset"]) : int(s["offset"]) + int(s["length"])] for s in spans
            )
        else:
            text = "\n".join(lines)
        covered = min(1.0, sum(_area(b) for b in figure_boxes.get(number, ())))
        pages.append(Page(number, width, height, words, marks, lines, text, covered))
    pages.sort(key=lambda p: p.number)
    return Layout(tuple(pages))


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


def find_all(page: Page, text: str, *, within: Box | None = None) -> list[Box]:
    """Every run of consecutive words (reading order) whose folded characters contain the
    folded ``text``; the union box of each run. ``within``: only words centred in that box."""
    target = fold_chars(text)
    if not target:
        return []
    words = [w for w in page.words if within is None or _inside(w.box, within)]
    joined: list[str] = []
    owner: list[int] = []  # the word index of each character of ``joined``
    for index, word in enumerate(words):
        folded = fold_chars(word.text)
        joined.append(folded)
        owner.extend([index] * len(folded))
    haystack = "".join(joined)
    found: list[Box] = []
    start = haystack.find(target)
    while start >= 0:
        first, last = owner[start], owner[start + len(target) - 1]
        found.append(union([w.box for w in words[first : last + 1]]))
        start = haystack.find(target, start + 1)
    return found


def locate(
    page: Page | None, text: str | None, *, near: Box | None = None, within: Box | None = None
) -> Box | None:
    """The box of ``text`` on ``page`` (the occurrence nearest ``near`` when it is given, else
    the first in reading order), or ``None`` when the words do not contain it."""
    if page is None or not text:
        return None
    found = find_all(page, text, within=within)
    if not found:
        return None
    if near is None:
        return found[0]
    return min(found, key=lambda box: _distance(box, near))


def region(page: int, box: Box) -> dict[str, Any]:
    """The JSON form of a region: ``{"page": n, "bbox": [x0, y0, x1, y1]}`` (page fractions)."""
    return {"page": page, "bbox": [round(v, 4) for v in box]}
