"""The appraisal's building sketch: its area calculation table, read from the page text.

An appraisal carries an exterior building sketch with "calculations to show how the
estimate for gross living area was derived" (SKETCH_READER.md section 1). The sketching
tools print those calculations under the drawing as a table headed "Area Calculations
Summary": one row per area (a floor of living area, the garage, the basement, a porch),
each with its area and the rectangles it was summed from (``12 x 5 = 60``). This module
reads that table from text the extraction already has: Azure Document Intelligence's page
lines, else the PDF's text layer. No model is called and nothing is read from the drawing.

What is read (SKETCH_READER.md section 3):

* every row after the heading: its label, its printed area, its rectangles, the page;
* a row is **verified** when its rectangles' printed results sum to its printed area (two
  prints of the same number agree; half a square foot of rounding per rectangle is allowed).
  A row that is not verified (no rectangles, a result the text lost) is kept in the reading
  and never becomes a field;
* the row's kind from its label and the heading it sits under: a level of living area
  (with the level when the label names one), a garage, a basement, a porch, a deck, a
  patio, or other.

What is not read: the outline. The edge labels of the drawing are in the text (``24'``),
but not which wall each belongs to, and some walls carry none, so the perimeter cannot be
recovered from them (SKETCH_READER.md section 2; measured in EVAL_REPORT 96).

:func:`sketch_fields` turns a reading into extraction fields of the ``sketch`` section
(:data:`FIELD_KEYS`), each with its page and source, which
:func:`basis.domain.measurements.measurements_from_fields` cross-checks against the printed
gross living area before the takeoff uses them. Pure functions: no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from basis.extraction.field_mappings import TABLE_CONFIDENCE
from basis.extraction.results import ExtractedField, Source

SECTION: Final = "sketch"
LIVING_AREA: Final = "sketch.living_area_sqft"
GROUND_FLOOR_AREA: Final = "sketch.ground_floor_area_sqft"
GARAGE_AREA: Final = "sketch.garage_area_sqft"
PATIO_AREA: Final = "sketch.patio_area_sqft"
FIELD_KEYS: Final = (LIVING_AREA, GROUND_FLOOR_AREA, GARAGE_AREA, PATIO_AREA)

Kind = Literal["living", "garage", "garage_detached", "basement", "porch", "deck", "patio", "other"]
Heading = Literal["living", "non_living", "below_grade"]

# Rounding allowed between a row's printed area and the sum of its rectangles' printed
# results: each is printed to the whole square foot (ANSI Z765 reports whole square feet).
ROUNDING_SF: Final = Decimal("0.5")

_ANCHOR: Final = re.compile(r"area\s+calculations?\s+summary", re.IGNORECASE)
_AREA: Final = r"\d[\d,]*(?:\.\d+)?\s*sq\.?\s*f(?:ee)?t\.?"
_DETAIL: Final = r"[\d\s.,xX×=\-–−]"
_ROW: Final = re.compile(
    rf"^(?P<label>.*?)\s*(?P<area>{_AREA})\s*(?P<details>{_DETAIL}*)$", re.IGNORECASE
)
_DETAIL_ONLY: Final = re.compile(rf"^{_DETAIL}+$")
_NUMBER: Final = re.compile(r"\d[\d,]*(?:\.\d+)?|\.\d+")
_TOKEN: Final = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)|(?P<x>[xX×])|(?P<eq>=)|(?P<dash>[-–−])"
)
_HEADINGS: Final[dict[str, Heading]] = {
    "living area": "living",
    "total above grade": "living",
    "non-living area": "non_living",
    "non living area": "non_living",
    "total below grade": "below_grade",
}
_ORDINALS: Final = (
    (re.compile(r"\b(first|1st|main|ground)\b"), 1),
    (re.compile(r"\b(second|2nd|upper)\b"), 2),
    (re.compile(r"\b(third|3rd)\b"), 3),
    (re.compile(r"\b(fourth|4th)\b"), 4),
)
_FLOOR_NUMBER: Final = re.compile(r"\bfloor\s*(\d)\b")
_LEVEL_NUMBER: Final = re.compile(r"\b(?:level|story|storey|lvl|lv)\s*(\d)\b")
_BELOW_GRADE: Final = re.compile(r"basement|below\s+grade|\bb\d\b")
_GARAGE: Final = re.compile(r"garage|\bcar\b")


@dataclass(frozen=True, slots=True)
class Rectangle:
    """One line of a row's calculation: ``a x b = result`` (a triangle prints three factors,
    ``0.5 x a x b``). ``factors`` are the operands the text kept; ``product_ok`` says they
    multiply to the result."""

    factors: tuple[Decimal, ...]
    result: Decimal
    product_ok: bool


@dataclass(frozen=True, slots=True)
class SketchArea:
    """One row of the area calculation table."""

    label: str
    heading: Heading | None
    kind: Kind
    level: int | None  # the floor a living row names (1 = the ground floor)
    attached: bool | None  # a garage row: what its label says; None: it does not say
    area_sf: Decimal
    page: int
    rectangles: tuple[Rectangle, ...]
    verified: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "heading": self.heading,
            "kind": self.kind,
            "level": self.level,
            "attached": self.attached,
            "area_sf": str(self.area_sf),
            "page": self.page,
            "verified": self.verified,
            "rectangles": [
                {
                    "factors": [str(f) for f in r.factors],
                    "result": str(r.result),
                    "product_ok": r.product_ok,
                }
                for r in self.rectangles
            ],
        }


@dataclass(frozen=True, slots=True)
class SketchReading:
    """Every area calculation table of a document, in page order."""

    pages: tuple[int, ...]  # the pages that carry the heading
    areas: tuple[SketchArea, ...]
    # Each page's printed "Total Living Area" (TOTAL prints one per page).
    living_totals: tuple[tuple[int, Decimal], ...]
    source: Source

    def of(self, *kinds: Kind) -> tuple[SketchArea, ...]:
        return tuple(area for area in self.areas if area.kind in kinds)

    def as_json(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "pages": list(self.pages),
            "areas": [area.as_json() for area in self.areas],
            "living_totals": [[page, str(total)] for page, total in self.living_totals],
        }


def _number(text: str) -> Decimal | None:
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _norm(text: str) -> str:
    return " ".join(text.lower().replace(":", " ").split())


def level_of(label: str) -> int | None:
    """The floor a living row's label names: ``Floor 2`` and ``Second Floor`` are 2. A
    floor number outranks a level number (``Floor 2 Level 1`` is the second floor)."""
    text = _norm(label)
    found = _FLOOR_NUMBER.search(text)
    if found:
        return int(found.group(1)) or None
    for pattern, level in _ORDINALS:
        if pattern.search(text):
            return level
    found = _LEVEL_NUMBER.search(text)
    if found:
        return int(found.group(1)) or None
    return None


def classify(label: str, heading: Heading | None) -> tuple[Kind, int | None, bool | None]:
    """``(kind, level, attached)`` of a row from its label and the heading it sits under.
    The label decides first (a garage listed under "Living Area" is still a garage). A row is
    living area when its label names a floor and it sits under a living heading or under none:
    the footprint needs to know which rows are the ground floor, and a row that names no floor
    (an accessory unit, a room in an outbuilding) is not part of the dwelling's stack. The
    cross-check against the printed living area catches a row that should have counted."""
    text = _norm(label)
    if _GARAGE.search(text):
        if "detach" in text:
            return "garage_detached", None, False
        return "garage", None, True if ("attach" in text or "built" in text) else None
    if _BELOW_GRADE.search(text) or heading == "below_grade":
        return "basement", None, None
    if "patio" in text:
        return "patio", None, None
    if "porch" in text:
        return "porch", None, None
    if "deck" in text:
        return "deck", None, None
    if "balc" in text or "gross building area" in text or "common area" in text:
        return "other", None, None
    level = level_of(text)
    if level is not None and heading in ("living", None):
        return "living", level, None
    return "other", None, None


def rectangles(details: str) -> tuple[Rectangle, ...]:
    """The ``a x b = result`` lines in a row's calculation text. Read by position, so a
    multiplication or equals sign the text lost, or an equals sign read as a dash, does not
    lose the line; a leading minus is a deduction (stairs open to the floor below)."""
    tokens = [(m.lastgroup or "", m.group()) for m in _TOKEN.finditer(details)]
    out: list[Rectangle] = []
    i, n = 0, len(tokens)

    def kind(at: int) -> str:
        return tokens[at][0] if at < n else ""

    while i < n:
        negative = False
        while kind(i) == "dash":
            negative, i = True, i + 1
        if kind(i) != "num":
            i += 1
            continue
        numbers = [tokens[i][1]]
        i += 1
        while kind(i) == "x" and kind(i + 1) == "num":
            numbers.append(tokens[i + 1][1])
            i += 2
        if kind(i) == "x":
            i += 1  # a multiplication sign whose operand the text lost
        if len(numbers) == 1 and kind(i) == "num" and kind(i + 1) in ("eq", "dash", "num"):
            numbers.append(tokens[i][1])  # the multiplication sign was lost, not the operand
            i += 1
        while kind(i) in ("eq", "dash"):
            i += 1
        if kind(i) != "num":
            break  # no result: the line is incomplete, and the row will not verify
        result = _number(tokens[i][1])
        i += 1
        factors = tuple(f for f in (_number(text) for text in numbers) if f is not None)
        if result is None:
            continue
        product = Decimal(1)
        for factor in factors:
            product *= factor
        product_ok = (
            len(factors) == len(numbers)
            and len(factors) >= 2
            and abs(product - result) <= ROUNDING_SF
        )
        out.append(Rectangle(factors, -result if negative else result, product_ok))
    return tuple(out)


def _verified(area: Decimal, parts: Sequence[Rectangle]) -> bool:
    if not parts:
        return False
    total = sum((r.result for r in parts), Decimal(0))
    return abs(total - area) <= ROUNDING_SF * (len(parts) + 1)


@dataclass
class _Open:
    label: str
    heading: Heading | None
    area: Decimal
    page: int
    details: list[str]

    def close(self) -> SketchArea:
        parts = rectangles(" ".join(self.details))
        kind, level, attached = classify(self.label, self.heading)
        return SketchArea(
            label=self.label,
            heading=self.heading,
            kind=kind,
            level=level,
            attached=attached,
            area_sf=self.area,
            page=self.page,
            rectangles=parts,
            verified=_verified(self.area, parts),
        )


def _page_rows(
    page: int, lines: Sequence[str]
) -> tuple[list[SketchArea], list[tuple[int, Decimal]]]:
    """The rows and printed living totals of one page's text after the heading."""
    areas: list[SketchArea] = []
    totals: list[tuple[int, Decimal]] = []
    heading: Heading | None = None
    pending: str | None = None
    current: _Open | None = None

    def close() -> None:
        nonlocal current
        if current is not None:
            areas.append(current.close())
            current = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if _DETAIL_ONLY.match(line):
            if current is not None:
                current.details.append(line)
            continue
        row = _ROW.match(line)
        if row is not None:
            close()
            label = row.group("label").strip() or (pending or "")
            area = _number(_NUMBER.search(row.group("area")).group())  # type: ignore[union-attr]
            pending = None
            if area is None:
                continue
            if _norm(label).startswith("total"):
                # "Total Living Area (Rounded): 1200 Sq ft": a page total, not a row.
                if "living" in _norm(label):
                    totals.append((page, area))
                continue
            current = _Open(label, heading, area, page, [row.group("details")])
            continue
        close()
        name = _norm(line).replace("calculation details", "").strip()
        if not name:
            continue
        if name in _HEADINGS:
            heading, pending = _HEADINGS[name], None
        else:
            pending = line
    close()
    return areas, totals


def read_sketch(pages: Iterable[tuple[int, Sequence[str]]], source: Source) -> SketchReading | None:
    """The area calculation tables in ``pages`` (``(page number, lines)``); None when no
    page carries the heading. Only the text after the heading is read: on these pages the
    drawing, with its room names and edge labels, comes first."""
    found: list[int] = []
    areas: list[SketchArea] = []
    totals: list[tuple[int, Decimal]] = []
    for number, lines in pages:
        after: list[str] | None = None
        for line in lines:
            if after is not None:
                after.append(line)
                continue
            anchor = _ANCHOR.search(line)
            if anchor is not None:
                after = [line[anchor.end() :]]
        if after is None:
            continue
        found.append(number)
        page_areas, page_totals = _page_rows(number, after)
        areas.extend(page_areas)
        totals.extend(page_totals)
    if not found:
        return None
    return SketchReading(tuple(found), tuple(areas), tuple(totals), source)


def read_document_sketch(
    azure_pages: Iterable[tuple[int, Sequence[str]]] | None, pdf_pages: Sequence[str]
) -> SketchReading | None:
    """The document's sketch tables: from Azure Document Intelligence's page lines when it
    analyzed the document and they carry a table, else from the PDF's text layer."""
    if azure_pages is not None:
        reading = read_sketch(azure_pages, "azure")
        if reading is not None and reading.areas:
            return reading
    text = read_sketch(
        ((number, page.splitlines()) for number, page in enumerate(pdf_pages, start=1)), "regex"
    )
    if text is not None and text.areas:
        return text
    return None


def _total(rows: Sequence[SketchArea]) -> Decimal:
    return sum((row.area_sf for row in rows), Decimal(0))


def _printed(rows: Sequence[SketchArea]) -> str:
    return "; ".join(f"{row.label} {row.area_sf} Sq ft (page {row.page})" for row in rows)


def sketch_fields(reading: SketchReading) -> tuple[dict[str, ExtractedField], list[str]]:
    """``(the fields a reading supports, why each of the others was withheld)``.

    * ``sketch.living_area_sqft``: the sum of the living rows, when every one is verified
      and each page's rows sum to the page's printed "Total Living Area";
    * ``sketch.ground_floor_area_sqft``: the living rows of level 1, when the living area
      stands;
    * ``sketch.garage_area_sqft``: the garage rows a label does not call detached, when
      every one is verified (a detached garage is not part of the dwelling's footprint);
    * ``sketch.patio_area_sqft``: the rows labelled as a patio, when every one is verified.
      A porch or a deck is not a patio, and an unlabelled non-living row ("Non-Calculated")
      names nothing, so neither is used.
    """
    fields: dict[str, ExtractedField] = {}
    notes: list[str] = []

    def add(key: str, rows: Sequence[SketchArea]) -> None:
        value = _total(rows)
        text = format(value.normalize(), "f") if value == value.to_integral_value() else str(value)
        fields[key] = ExtractedField(
            key=key,
            raw_text=_printed(rows),
            value_text=text,
            value_num=value,
            confidence=TABLE_CONFIDENCE,
            source=reading.source,
            page=rows[0].page,
        )

    def unverified(rows: Sequence[SketchArea]) -> list[str]:
        return [f"{row.label!r} on page {row.page}" for row in rows if not row.verified]

    living = reading.of("living")
    living_ok = False
    if not living:
        notes.append("living area: the table has no living-area row")
    elif bad := unverified(living):
        notes.append(
            "living area: the rectangles do not sum to the printed area of " + ", ".join(bad)
        )
    else:
        mismatched = [
            f"page {page}: rows {_total([r for r in living if r.page == page])} against "
            f"the printed total {total}"
            for page, total in reading.living_totals
            if abs(_total([r for r in living if r.page == page]) - total) > ROUNDING_SF * 2
        ]
        if mismatched:
            notes.append(
                "living area: the rows do not sum to the page total (" + "; ".join(mismatched) + ")"
            )
        else:
            living_ok = True
            add(LIVING_AREA, living)
    if living_ok:
        ground = [row for row in living if row.level == 1]
        if not ground:
            notes.append("ground floor: no living row is the first floor")
        else:
            add(GROUND_FLOOR_AREA, ground)
    for row in reading.areas:
        if row.heading == "living" and row.kind == "other":
            notes.append(
                f"living area: {row.label!r} on page {row.page} names no floor, so it is not "
                "counted"
            )

    garage = reading.of("garage")
    if garage:
        if bad := unverified(garage):
            notes.append(
                "garage: the rectangles do not sum to the printed area of " + ", ".join(bad)
            )
        else:
            add(GARAGE_AREA, garage)
    for row in reading.of("garage_detached"):
        notes.append(f"garage: {row.label!r} on page {row.page} is detached, so it is not used")

    patio = reading.of("patio")
    if patio:
        if bad := unverified(patio):
            notes.append(
                "patio: the rectangles do not sum to the printed area of " + ", ".join(bad)
            )
        else:
            add(PATIO_AREA, patio)
    return fields, notes


__all__ = [
    "FIELD_KEYS",
    "GARAGE_AREA",
    "GROUND_FLOOR_AREA",
    "LIVING_AREA",
    "PATIO_AREA",
    "ROUNDING_SF",
    "SECTION",
    "Rectangle",
    "SketchArea",
    "SketchReading",
    "classify",
    "level_of",
    "rectangles",
    "read_document_sketch",
    "read_sketch",
    "sketch_fields",
]
