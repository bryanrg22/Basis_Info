"""Value cleaners for extracted appraisal fields (Azure DI, regex, or LLM text).

Migrated from ``agentic/utils/azure_di_cleaner.py``. Changes, each covered by a test in
``tests/unit/extraction/test_cleaners.py``:

* Unparseable numbers are ``None``, not ``0``. The legacy ``clean_currency("n/a")``
  returned ``0.0``, so a missing contract price became a valid-looking ``$0`` that passed
  every presence check; ``None`` keeps "missing" distinguishable from "zero".
* Money is :class:`~decimal.Decimal` (it is stored in a ``Numeric`` column and compared
  for equality); ``(5,000)`` and ``-$5,000`` are negative.
* ``clean_checkbox`` matches whole words: the legacy substring test read "Unknown" and
  "None" as *no* ("no" in the text) and "eyes" as *yes*.
* ``clean_text`` keeps the OCR word-split repair ("P urchase" -> "Purchase",
  "Le gal" -> "Legal") but never joins real words: a single "A"/"I" or a two-letter word
  such as "As", "In", "Up" before the next word stays separate (the legacy rule turned
  "Up to date" into "Upto date").
* The legacy per-section cleaners (``clean_subject_section`` etc.) returned the old
  Firestore document shape with ``0``/``""`` defaults; the pipeline now cleans each field
  by its catalogue type with :func:`clean_value` instead.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from basis.extraction.field_mappings import FIELD_BY_KEY, FieldType

_SELECTION_MARK = re.compile(r":(?:un)?selected:", re.IGNORECASE)
_SINGLE_LETTER_SPLIT = re.compile(r"\b([B-HJ-Z])\s+([a-z])")
_TWO_LETTER_SPLIT = re.compile(r"\b([A-Z][a-z])\s+([a-z]{2,})")
_TRAILING_LETTER = re.compile(r"\s+[a-z]\s*$")
_SPACES = re.compile(r"\s+")
# Two-letter words that are words, not OCR fragments: never glued to the next word.
_TWO_LETTER_WORDS: Final = frozenset(
    "am an as at be by do go he if in is it me my no of on or so to up us we".split()
)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_TRUE_WORDS: Final = frozenset({"yes", "y", "true", "x"})
_FALSE_WORDS: Final = frozenset({"no", "n", "false"})
_DATE_MDY = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# M/D/YY, as URAR forms often print dates; tried after the four-digit form.
_DATE_MDY_SHORT = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b")
# A site area printed in acres ("1.39 ac", "0.25 acre"); one acre is 43,560 square feet.
_ACRES = re.compile(r"\b(?:ac|acres?)\b\.?", re.IGNORECASE)
SQUARE_FEET_PER_ACRE: Final = Decimal(43560)
_NOT_A_VALUE: Final = frozenset({"", "-", "--", "n/a", "na", "not applicable"})


def clean_text(value: Any) -> str:
    """Text without checkbox markers, OCR word splits, stray trailing letters or extra spaces."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    text = _SELECTION_MARK.sub("", value)
    text = _SINGLE_LETTER_SPLIT.sub(r"\1\2", text)

    def join_two(match: re.Match[str]) -> str:
        head, tail = match.group(1), match.group(2)
        return match.group(0) if head.lower() in _TWO_LETTER_WORDS else head + tail

    text = _TWO_LETTER_SPLIT.sub(join_two, text)
    text = _TRAILING_LETTER.sub("", text)
    return _SPACES.sub(" ", text).strip()


def clean_currency(value: Any) -> Decimal | None:
    """A dollar amount: ``"=$692,831"`` -> 692831; ``None`` when there is no number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    text = _SELECTION_MARK.sub("", value).strip().lstrip("=").strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("$", "").replace(",", "").replace(" ", "")
    if text.lower() in _NOT_A_VALUE:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        match = _NUMBER.search(text)
        if match is None:
            return None
        try:
            amount = Decimal(match.group(0))
        except InvalidOperation:  # pragma: no cover - the pattern only matches numbers
            return None
    if not amount.is_finite():
        return None
    return -amount if negative else amount


def clean_integer(value: Any) -> int | None:
    """A whole number (``"3,200"`` -> 3200, ``"900 sq ft"`` -> 900); decimals truncate."""
    amount = clean_currency(value)
    return None if amount is None else int(amount)


def clean_number(value: Any) -> Decimal | None:
    """A decimal number (``"2.1"`` baths)."""
    return clean_currency(value)


def clean_percent(value: Any) -> Decimal | None:
    """``"80%"`` -> 80."""
    if isinstance(value, str):
        value = value.replace("%", "")
    return clean_currency(value)


def clean_checkbox(value: Any) -> bool | None:
    """``True`` for selected/yes, ``False`` for unselected/no, ``None`` when unclear."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    text = value.lower()
    if ":selected:" in text:
        return True
    if ":unselected:" in text:
        return False
    words = set(re.findall(r"[a-z]+", text))
    if text.strip() == "x":
        return True
    if words & _TRUE_WORDS and not words & _FALSE_WORDS:
        return True
    if words & _FALSE_WORDS and not words & _TRUE_WORDS:
        return False
    return None


def extract_selection(value: Any, options: list[str]) -> str:
    """The option marked ``:selected:``, else one that appears unmarked, else ``""``."""
    if value is None or not isinstance(value, str):
        return ""
    for option in options:
        if re.search(rf"{re.escape(option)}\s*:selected:", value, re.IGNORECASE):
            return option
    for option in options:
        if option.lower() in value.lower() and not re.search(
            rf"{re.escape(option)}\s*:unselected:", value, re.IGNORECASE
        ):
            return option
    return ""


def split_concatenated_field(value: Any, field_patterns: Mapping[str, str]) -> dict[str, str]:
    """Pull several fields out of one concatenated value with a pattern per field."""
    if value is None or not isinstance(value, str):
        return {}
    results: dict[str, str] = {}
    for field_name, pattern in field_patterns.items():
        match = re.search(pattern, value, re.IGNORECASE)
        if match and match.groups():
            results[field_name] = match.group(1).strip()
    return results


def clean_address(value: Any) -> str:
    """A street address without a trailing ``City, ST 12345`` that was concatenated to it."""
    text = clean_text(value)
    match = re.match(r"^(.+?)\s+(?:[A-Z][a-z]+),?\s+[A-Z]{2}\s+\d{5}", text)
    return match.group(1).strip() if match else text


def full_year(two_digits: int, today: date | None = None) -> int:
    """The four-digit year of a two-digit one: this century unless that would put it more
    than a year after ``today`` (default: today in UTC), then the last. An appraisal's dates
    (effective, contract, comparable and prior sales) are past or near; a fixed pivot such
    as ``strptime``'s (69 -> 1969, 68 -> 2068) would read old sale dates as future ones."""
    reference = today or datetime.now(UTC).date()
    year = reference.year // 100 * 100 + two_digits
    return year if year <= reference.year + 1 else year - 100


def clean_date(value: Any) -> str:
    """A date as ``MM/DD/YYYY`` text (legacy format); the input unchanged when no date."""
    if value is None:
        return ""
    if isinstance(value, date):
        return value.strftime("%m/%d/%Y")
    text = str(value).strip()
    match = _DATE_MDY.search(text)
    if match:
        return f"{match.group(1)}/{match.group(2)}/{match.group(3)}"
    match = _DATE_ISO.search(text)
    if match:
        return f"{match.group(2)}/{match.group(3)}/{match.group(1)}"
    match = _DATE_MDY_SHORT.search(text)
    if match:
        return f"{match.group(1)}/{match.group(2)}/{full_year(int(match.group(3)))}"
    return text


def parse_date(value: Any, *, today: date | None = None) -> date | None:
    """A real date from ``MM/DD/YYYY``, ``YYYY-MM-DD`` or ``M/D/YY`` text (the century by
    :func:`full_year`); ``None`` when invalid."""
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value)
    for pattern, order in (
        (_DATE_MDY, (3, 1, 2)),
        (_DATE_ISO, (1, 2, 3)),
        (_DATE_MDY_SHORT, (3, 1, 2)),
    ):
        match = pattern.search(text)
        if match:
            year, month, day = (int(match.group(i)) for i in order)
            if pattern is _DATE_MDY_SHORT:
                year = full_year(year, today)
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def clean_comparable(comp_data: Mapping[str, Any] | None) -> dict[str, Any]:
    """One comparable sale from the sales grid, cleaned (``{}`` for no data)."""
    if not comp_data:
        return {}
    return {
        "id": comp_data.get("id", 0),
        "address": clean_address(comp_data.get("address", "")),
        "city": clean_text(comp_data.get("city", "")),
        "state": clean_text(comp_data.get("state", "")),
        "proximity": clean_text(comp_data.get("proximity", "")),
        "sale_price": clean_currency(comp_data.get("sale_price")),
        "adjusted_sale_price": clean_currency(comp_data.get("adjusted_sale_price")),
        "price_per_sqft": clean_currency(comp_data.get("price_per_sqft")),
        "gla_sqft": clean_integer(comp_data.get("gla_sqft")),
        "lot_size": clean_text(comp_data.get("lot_size", "")),
        "design": clean_text(comp_data.get("design", "")),
        "condition": clean_text(comp_data.get("condition", "")),
        "sale_date": clean_date(comp_data.get("sale_date", "")),
    }


# --------------------------------------------------------------------------- typed values


@dataclass(frozen=True, slots=True)
class CleanValue:
    """A field value in the three typed columns of ``extraction_fields``.

    ``text`` is always the cleaned display text; ``num``/``day`` are set for numeric and
    date types. ``error`` explains why a value of a typed field could not be parsed (the
    field then fails validation; the text is kept for the reviewer).
    """

    text: str | None
    num: Decimal | None = None
    day: date | None = None
    error: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.text and self.num is None and self.day is None

    @property
    def typed(self) -> str | Decimal | date | bool | None:
        """The value to compare against golden data (number, date, or text)."""
        if self.day is not None:
            return self.day
        if self.num is not None:
            return self.num
        return self.text or None


def clean_value(kind: FieldType, raw: Any) -> CleanValue:
    """Clean ``raw`` as a field of catalogue type ``kind``."""
    text = clean_text(raw)
    if not text or text.lower() in _NOT_A_VALUE:
        return CleanValue(text=None)
    if kind == "text":
        return CleanValue(text=text)
    if kind == "date":
        day = parse_date(text)
        return CleanValue(text=text, day=day, error=None if day else "not a date")
    if kind == "bool":
        flag = clean_checkbox(text)
        if flag is None:
            return CleanValue(text=text, error="not yes/no")
        return CleanValue(text="Yes" if flag else "No", num=Decimal(int(flag)))
    if kind == "currency_or_text":
        amount = clean_currency(text) if _NUMBER.search(text) else None
        return CleanValue(text=text, num=amount)
    if kind == "int":
        whole = clean_integer(text)
        return CleanValue(
            text=text,
            num=None if whole is None else Decimal(whole),
            error=None if whole is not None else "not a number",
        )
    parse = {"currency": clean_currency, "float": clean_number, "percent": clean_percent}[kind]
    amount = parse(text)
    return CleanValue(text=text, num=amount, error=None if amount is not None else "not a number")


def clean_site_area(raw: Any) -> CleanValue:
    """``site.area_sqft`` in square feet: a value printed in acres is converted (the URAR
    box holds either unit), anything else is read as square feet."""
    value = clean_value("int", raw)
    if value.text is None or not _ACRES.search(value.text):
        return value
    acres = clean_number(value.text)
    if acres is None:
        return value
    whole = int((acres * SQUARE_FEET_PER_ACRE).to_integral_value())
    return CleanValue(text=value.text, num=Decimal(whole))


def clean_field(key: str, raw: Any) -> CleanValue:
    """Clean ``raw`` as the catalogue field ``key`` (unknown keys are cleaned as text)."""
    spec = FIELD_BY_KEY.get(key)
    if spec is not None and spec.key == "subject.property_address":
        return clean_value("text", clean_address(raw))
    if spec is not None and spec.key == "site.area_sqft":
        return clean_site_area(raw)
    return clean_value(spec.type if spec is not None else "text", raw)


def clean_fields(raw: Mapping[str, Any]) -> dict[str, CleanValue]:
    """Clean every ``{"section.field": raw}`` value by its catalogue type."""
    return {key: clean_field(key, value) for key, value in raw.items()}
