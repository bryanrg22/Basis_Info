"""Field extraction from the PDF text layer by printed captions (the no-OCR path).

Migrated from ``evidence_layer/src/extract_fields.py`` (``extract_appraisal_fields``).
The legacy version searched the whole document for loose phrases ("market value",
"location", any ``\\d{5}`` as the ZIP code) and returned a commercial-appraisal shape that
matched none of the URAR keys the rest of the pipeline uses. This version reads the form
the way it is printed:

* Section headings (a line that is exactly "Subject", "Site", "Reconciliation", ...)
  set the current section, so a caption that occurs in two sections maps to the right
  field ("Indicated Value by Cost Approach" in Reconciliation vs. the Cost Approach table).
* ``Caption: value`` pairs: every catalogue caption followed by a colon starts a value
  that runs to the next caption on the same line (forms print two or three pairs per
  line). A caption is only recognised at the start of a line or after whitespace and only
  with its colon, and case-sensitively (as printed, or in capitals), so prose that merely
  mentions "final opinion of market value" is never read as that field
  (``tests/fixtures/synthetic/adversarial/urar_injected.pdf``).
* Cost approach rows: inside the Cost Approach section, a line that starts with a row
  caption takes the line's last dollar amount (the "Amount" column).
* Ruled tables (from ``pdfplumber`` or Azure DI) are mapped by :func:`table_candidates`:
  the cost approach table by row caption and the sales grid into comparables.

Values keep their printed text; cleaning and typing happen later
(:mod:`basis.extraction.cleaners`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from basis.extraction.cleaners import clean_comparable, clean_field
from basis.extraction.field_mappings import (
    COST_TABLE_ROWS,
    FIELD_BY_KEY,
    REGEX_CONFIDENCE,
    TABLE_CONFIDENCE,
    all_labels,
    key_for_label,
    normalize_label,
    section_for_heading,
)
from basis.extraction.results import Candidate, Source

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})
# Captions as printed or in capitals (some forms print them upper-case). Case matters on
# purpose: prose such as "support for the opinion of site value: ..." is not a caption.
_CAPTION_TEXTS: Final = sorted(
    {variant for label in all_labels() for variant in (label, label.upper())},
    key=lambda label: (-len(label), label),
)
_CAPTION = re.compile(
    r"(?:(?<=\s)|^)(" + "|".join(re.escape(label) for label in _CAPTION_TEXTS) + r")\s*:"
)
_AMOUNT = re.compile(r"[-+]?\$\s?[\d,]+(?:\.\d+)?|\(\$?[\d,]+(?:\.\d+)?\)")
_COST_ROW_CAPTIONS: Final = sorted(
    {(label, key) for key in COST_TABLE_ROWS for label in FIELD_BY_KEY[key].labels},
    key=lambda pair: (-len(pair[0]), pair[0]),
)


@dataclass(frozen=True, slots=True)
class Line:
    page: int
    section: str | None
    text: str


def section_lines(pages: Sequence[str]) -> list[Line]:
    """Every non-empty line with its page number and the section it is printed in."""
    lines: list[Line] = []
    section: str | None = None
    for number, page in enumerate(pages, start=1):
        for raw in page.splitlines():
            text = raw.translate(_QUOTES).strip()
            if not text:
                continue
            heading = section_for_heading(text)
            if heading is not None:
                section = heading
            lines.append(Line(number, section, text))
    return lines


def caption_pairs(text: str) -> list[tuple[str, str]]:
    """``(caption, value)`` for every ``Caption: value`` on one line."""
    matches = list(_CAPTION.finditer(text))
    pairs: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pairs.append((match.group(1), text[match.end() : end].strip()))
    return pairs


def _cost_row(text: str) -> tuple[str, str] | None:
    lowered = text.lower()
    for caption, key in _COST_ROW_CAPTIONS:
        if lowered.startswith(caption.lower()) and (
            len(text) == len(caption) or not text[len(caption)].isalnum()
        ):
            amounts = _AMOUNT.findall(text[len(caption) :])
            if amounts:
                return key, amounts[-1].replace(" ", "")
    return None


def extract_appraisal_fields(pages: Sequence[str]) -> list[Candidate]:
    """Candidates from the text layer (one per field: the first occurrence wins)."""
    found: dict[str, Candidate] = {}

    def add(key: str, value: str, page: int, source: Source, confidence: float) -> None:
        if not value:
            return
        existing = found.get(key)
        # The first occurrence wins, unless it does not parse as the field's type and this
        # one does (a caption reused in prose before the real value).
        if existing is not None and (
            clean_field(key, existing.raw_text).error is None
            or clean_field(key, value).error is not None
        ):
            return
        found[key] = Candidate(
            key=key, raw_text=value, page=page, confidence=confidence, source=source
        )

    for line in section_lines(pages):
        if section_for_heading(line.text) is not None:
            continue
        pairs = caption_pairs(line.text)
        for caption, value in pairs:
            key = key_for_label(caption, line.section)
            if key is not None:
                add(key, value, line.page, "regex", REGEX_CONFIDENCE)
        if not pairs and line.section == "cost_approach":
            row = _cost_row(line.text)
            if row is not None:
                add(row[0], row[1], line.page, "table", TABLE_CONFIDENCE)
    return list(found.values())


# --------------------------------------------------------------------------- tables

_COMPARABLE_ROWS: Final[dict[str, str]] = {
    "address": "address",
    "proximity to subject": "proximity",
    "sale price": "sale_price",
    "sale price/gross liv. area": "price_per_sqft",
    "date of sale": "sale_date",
    "site": "lot_size",
    "design (style)": "design",
    "condition": "condition",
    "gross living area": "gla_sqft",
    "adjusted sale price": "adjusted_sale_price",
}


def _is_cost_table(rows: Sequence[Sequence[str]]) -> bool:
    text = " ".join(" ".join(row) for row in rows).lower()
    return "cost-new" in text or "cost new" in text or "depreciation" in text


def _is_sales_grid(rows: Sequence[Sequence[str]]) -> bool:
    header = " ".join(rows[0]).lower() if rows else ""
    return "comparable" in header


def table_candidates(
    tables: Iterable[tuple[int | None, Sequence[Sequence[str]]]],
    *,
    source: Source,
    confidence: float = TABLE_CONFIDENCE,
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Cost approach fields and sales-grid comparables from ``(page, rows)`` tables."""
    candidates: dict[str, Candidate] = {}
    comparables: list[dict[str, Any]] = []
    for page, rows in tables:
        if not rows:
            continue
        if _is_cost_table(rows):
            for row in rows:
                if not row:
                    continue
                key = key_for_label(row[0], "cost_approach")
                if key not in COST_TABLE_ROWS or key in candidates:
                    continue
                values = [cell for cell in row[1:] if cell and cell.strip()]
                amounts = [cell for cell in values if _AMOUNT.search(cell)]
                if amounts:
                    candidates[key] = Candidate(
                        key=key,
                        raw_text=amounts[-1].strip(),
                        page=page,
                        confidence=confidence,
                        source=source,
                    )
        elif _is_sales_grid(rows) and not comparables:
            columns = len(rows[0])
            for column in range(2, columns):
                raw: dict[str, Any] = {"id": column - 1}
                for row in rows[1:]:
                    if len(row) <= column or not row:
                        continue
                    name = _COMPARABLE_ROWS.get(normalize_label(row[0]))
                    if name is not None and row[column] not in ("", "-"):
                        raw[name] = row[column]
                if len(raw) > 1:
                    comparables.append(_jsonable(clean_comparable(raw)))
    return list(candidates.values()), comparables


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    from decimal import Decimal

    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, Decimal):
            out[key] = int(value) if value == value.to_integral_value() else float(value)
        else:
            out[key] = value
    return out
