"""Normalizers, comparison and display strings for the appraisal evaluation (``SPEC.md`` §4, §8).

One function per truth type turns a printed or stored value into a comparable form;
:func:`matches` compares a normalized prediction with a normalized truth (exact, unless the
truth entry carries an absolute ``tolerance``, or the box was printed in acres).

Which stored column a prediction is read from (:func:`prediction_value`): the typed column
the pipeline wrote (``value_num`` for numbers, ``value_date`` for dates) when it is set,
since that is what ``extraction_fields`` stores and what cost seg consumes; otherwise
``value_text`` through the normalizer of the truth's type. Text, enums and months are read
from ``value_text``.

The enum synonym table (:data:`ENUMS`) is part of the contract: adding a synonym is a
reviewed change (tested in ``tests/unit/evals/test_appraisal_normalize.py``), never a silent
widening. A prediction that names no code of the vocabulary is "unmatched" (a wrong value),
except in a vocabulary with an ``other`` code, where it is ``other`` with that text (the
"Other" box's write-in, e.g. heating "Infloor").

:func:`review_display` mirrors ``fieldValue`` in ``web/live/live-review.js`` exactly (what the
review page shows today); :func:`canonical_display` is what it should show (§8).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final

from basis.extraction.cleaners import full_year

SQUARE_FEET_PER_ACRE: Final = Decimal(43560)
# The UI marks a field "Low confidence" below this (live-review.js, the appraisal page).
UI_LOW_CONFIDENCE: Final = 0.7
# A box printed in acres is correct within the rounding of the printed acres.
ACRE_ROUNDING: Final = Decimal("0.005")

_SPACES: Final = re.compile(r"\s+")
_NUMBER: Final = re.compile(r"-?\d[\d,]*(?:\.\d+)?|-?\.\d+")
_NOT_A_VALUE: Final = frozenset({"", "-", "--", "n/a", "na", "none", "not applicable"})
# In a currency box "No"/"None" is 0, not a missing value (SPEC principle 2, §4).
_CURRENCY_ZERO_WORDS: Final = frozenset({"no", "none"})
# A text truth listing several values joins them with "; " (borrowers, owners, parcel
# numbers in the GSE D-1 truth); such a truth is compared as a set of parts.
_TRUTH_PARTS: Final = re.compile(r"\s*[;|]\s*")
_PREDICTION_PARTS: Final = re.compile(r"\s*(?:,|&|/|\band\b)\s*", re.IGNORECASE)
_ACRES: Final = re.compile(r"\d\s*(?:ac|acres?)\b\.?", re.IGNORECASE)
_DATE_MDY: Final = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_DATE_MDY_SHORT: Final = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b")
_DATE_ISO: Final = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DATE_WORDS: Final = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_MONTH_MY: Final = re.compile(r"^(\d{1,2})/(\d{4})$")
_MONTH_MY_SHORT: Final = re.compile(r"^(\d{1,2})/(\d{2})$")
_MONTH_ISO: Final = re.compile(r"^(\d{4})-(\d{1,2})$")
_BATHS_WORDS: Final = re.compile(r"(\d+)\s*full\D+(\d+)\s*half", re.IGNORECASE)
_BATHS_NUMBER: Final = re.compile(r"(\d+)(?:\.(\d+))?")
_MONTHS: Final = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_TRUE: Final = frozenset({"yes", "y", "x", "true", "checked", "selected", "1"})
_FALSE: Final = frozenset({"no", "n", "false", "unchecked", "unselected", "0"})


# --------------------------------------------------------------------------- folding


_QUOTES: Final = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"'})


def fold_text(value: object) -> str:
    """NFKC, casefold, whitespace collapsed, trailing ``.``/``,`` stripped (§4 ``text``);
    typographic quotes read as straight ones (NFKC keeps them apart)."""
    text = unicodedata.normalize("NFKC", str(value)).translate(_QUOTES).casefold()
    return _SPACES.sub(" ", text).strip().rstrip(".,").rstrip()


def fold_enum(value: object) -> str:
    """Case- and punctuation-folded: letters and digits only (``<``/``>`` read as words)."""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = text.replace("<", " under ").replace(">", " over ")
    return "".join(ch for ch in text if ch.isalnum())


def _is_nothing(text: str) -> bool:
    return fold_text(text) in _NOT_A_VALUE


# --------------------------------------------------------------------------- text similarity


def levenshtein(a: str, b: str) -> int:
    """Edit distance (insertions, deletions, substitutions; each costs 1)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def anls(prediction: str | None, truth: str, threshold: float = 0.5) -> float:
    """Normalized Levenshtein similarity of the folded strings, 0 below ``threshold``
    (ANLS, Biten et al. 2019: ``1 - NL`` when ``NL < 1 - threshold``, else 0)."""
    if prediction is None:
        return 0.0
    a, b = fold_text(prediction), fold_text(truth)
    if not a and not b:
        return 1.0
    similarity = 1.0 - levenshtein(a, b) / max(len(a), len(b))
    return similarity if similarity >= threshold else 0.0


# --------------------------------------------------------------------------- numbers


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int | float):
        return Decimal(str(value))
    return None


def first_number(value: object) -> Decimal | None:
    """The first number in the text, commas removed (``"1,740 Sq.Ft."`` -> 1740)."""
    number = _decimal(value)
    if number is not None or value is None or isinstance(value, bool):
        return number
    text = unicodedata.normalize("NFKC", str(value))
    if _is_nothing(text):
        return None
    match = _NUMBER.search(text)
    if match is None:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:  # pragma: no cover - the pattern only matches numbers
        return None


def parse_currency(value: object, *, key: str | None = None) -> Decimal | None:
    """Dollars: ``$``, ``=``, spaces and commas stripped; ``-x`` negative; ``(x)`` negative
    except right after ``=$`` or ``$`` in the cost approach (``key`` under ``cost_approach.``),
    where the parentheses are the form's subtraction marks (``"=$( 156,240)"`` is 156240);
    ``no`` and ``none`` are 0 (a box such as "Known Sales Concessions No"); only ``n/a`` and an
    empty box are no value. A cost-approach line prints the column amount and then ``=$`` and
    the line's amount (``"156,240 =$( 156,240)"``): the amount after the last ``=$`` is the
    value. ``$0`` is a value (0)."""
    number = _decimal(value)
    if number is not None or value is None or isinstance(value, bool):
        return number
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if fold_enum(text) in _CURRENCY_ZERO_WORDS:
        return Decimal(0)
    if _is_nothing(text):
        return None
    squeezed = _SPACES.sub("", text)
    if "=$" in squeezed:
        squeezed = squeezed.rsplit("=$", 1)[1]
        after_dollar = True
    else:
        after_dollar = squeezed.lstrip("=").startswith("$")
    squeezed = squeezed.replace("$", "").replace("=", "").replace(",", "")
    parenthesized = squeezed.startswith("(") and squeezed.endswith(")")
    subtraction_mark = after_dollar and (key or "").startswith("cost_approach.")
    negative = parenthesized and not subtraction_mark
    squeezed = squeezed.strip("()")
    if _is_nothing(squeezed):
        return None
    match = _NUMBER.search(squeezed)
    if match is None:
        return None
    amount = Decimal(match.group(0))
    return -abs(amount) if negative else amount


def parse_percent(value: object) -> Decimal | None:
    if isinstance(value, str):
        value = value.replace("%", "")
    return first_number(value)


def parse_area_sqft(value: object) -> Decimal | None:
    """Square feet: ``sq ft``/``sf``/``sq.ft.`` and bare numbers as is, acres x 43,560
    rounded to the foot (``"1.39 ac"`` -> 60548)."""
    number = first_number(value)
    if number is None or not isinstance(value, str) or not _ACRES.search(value):
        return number
    return (number * SQUARE_FEET_PER_ACRE).to_integral_value(rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- dates


def parse_date(value: object, *, today: date | None = None) -> str | None:
    """ISO ``YYYY-MM-DD`` from M/D/YYYY, M/D/YY (century: not more than a year ahead of
    ``today``), ISO, or "Month D, YYYY"; ``None`` when there is no valid date."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return None
    text = str(value).strip()
    candidates: list[tuple[int, int, int]] = []
    if match := _DATE_MDY.search(text):
        candidates.append((int(match[3]), int(match[1]), int(match[2])))
    elif match := _DATE_ISO.search(text):
        candidates.append((int(match[1]), int(match[2]), int(match[3])))
    elif match := _DATE_MDY_SHORT.search(text):
        reference = today or datetime.now(UTC).date()
        candidates.append((full_year(int(match[3]), reference), int(match[1]), int(match[2])))
    elif (match := _DATE_WORDS.search(text)) and match[1].casefold() in _MONTHS:
        candidates.append((int(match[3]), _MONTHS[match[1].casefold()], int(match[2])))
    for year, month, day in candidates:
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def parse_month(value: object, *, today: date | None = None) -> str | None:
    """``YYYY-MM`` from MM/YYYY, M/YY (century as for dates) or ISO ``YYYY-MM``."""
    if value is None:
        return None
    text = str(value).strip()
    year: int
    if match := _MONTH_MY.match(text):
        year, month = int(match[2]), int(match[1])
    elif match := _MONTH_MY_SHORT.match(text):
        year = full_year(int(match[2]), today or datetime.now(UTC).date())
        month = int(match[1])
    elif match := _MONTH_ISO.match(text):
        year, month = int(match[1]), int(match[2])
    else:
        return None
    return f"{year:04d}-{month:02d}" if 1 <= month <= 12 else None


# A checked box as the text layer reads it: an "X" (or a box glyph) before the option.
_MARK: Final = re.compile(r"(?:^|(?<=[\s;,]))(?:[xX]|\u2612|\u2611|\u25a0|\[[xX]\])(?=\s)")


def marked_options(text: str) -> list[str] | None:
    """The options a box marks with an ``X`` ("Asphalt; X Public" -> ["Public"]; "X Det.;
    X Garage" -> ["Det.", "Garage"]): what follows each mark up to the next ``;``, or the
    next mark. ``None`` when nothing is marked (the text is read as it is)."""
    pieces = _MARK.split(text)
    if len(pieces) < 2:
        return None
    options = [piece.split(";", 1)[0].strip(" ,;") for piece in pieces[1:]]
    return [option for option in options if option]


def parse_bool(value: object) -> bool | None:
    """yes/no/x/checked/selected, read by words as the production ``clean_checkbox`` reads
    them ("No Hazard Zone Noted" is no); a word for each side is unclear (``None``)."""
    if isinstance(value, bool):
        return value
    number = _decimal(value)
    if number is not None:
        return None if number not in (0, 1) else bool(number)
    if value is None:
        return None
    text = str(value)
    lowered = text.casefold()
    if ":unselected:" in lowered:
        return False
    if ":selected:" in lowered or lowered.strip() == "x":
        return True
    marked = marked_options(text)
    if marked is not None:
        text = " ".join(marked)
    words = set(re.findall(r"[a-z0-9]+", text.casefold()))
    yes, no = bool(words & _TRUE), bool(words & _FALSE)
    if yes != no:
        return yes
    return None


# --------------------------------------------------------------------------- baths


@dataclass(frozen=True, slots=True)
class Baths:
    full: int
    half: int

    def display(self) -> str:
        return f"{self.full}.{self.half}"


def parse_baths(value: object) -> Baths | None:
    """UAD ``2.1`` (2 full, 1 half); ``2.5`` read as 2 full 1 half; ``{"full", "half"}``;
    "2 full 1 half"."""
    if isinstance(value, Baths):
        return value
    if isinstance(value, Mapping):
        try:
            return Baths(int(value["full"]), int(value.get("half", 0)))
        except (KeyError, TypeError, ValueError):
            return None
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if _is_nothing(text):
        return None
    if match := _BATHS_WORDS.search(text):
        return Baths(int(match[1]), int(match[2]))
    match = _BATHS_NUMBER.search(text)
    if match is None:
        return None
    fraction = (match[2] or "").rstrip("0")
    half = 0 if not fraction else 1 if fraction == "5" else int(fraction)
    return Baths(int(match[1]), half)


# --------------------------------------------------------------------------- enums


@dataclass(frozen=True, slots=True)
class EnumCode:
    """One code of a vocabulary: the form's label (the display) and its printed synonyms."""

    code: str
    label: str
    synonyms: tuple[str, ...] = ()


def _vocab(*codes: EnumCode) -> tuple[EnumCode, ...]:
    return codes


_UTILITIES: Final = _vocab(
    EnumCode("public", "Public"), EnumCode("other", "Other"), EnumCode("none", "None")
)
_OFF_SITE: Final = _vocab(
    EnumCode("public", "Public", ("Public Street",)),
    EnumCode("private", "Private", ("Private Street",)),
)

# Legacy 1004 checkbox vocabularies (SPEC §4). Matching is fold_enum equality with the code,
# the label or a synonym.
ENUMS: Final[dict[str, tuple[EnumCode, ...]]] = {
    "neighborhood.location": _vocab(
        EnumCode("urban", "Urban"), EnumCode("suburban", "Suburban"), EnumCode("rural", "Rural")
    ),
    "neighborhood.built_up": _vocab(
        EnumCode("over_75", "Over 75%", ("over 75", "> 75%")),
        EnumCode("25_75", "25-75%", ("25 to 75%", "25–75%")),
        EnumCode("under_25", "Under 25%", ("under 25", "< 25%")),
    ),
    "neighborhood.growth": _vocab(
        EnumCode("rapid", "Rapid"), EnumCode("stable", "Stable"), EnumCode("slow", "Slow")
    ),
    "neighborhood.property_values": _vocab(
        EnumCode("increasing", "Increasing"),
        EnumCode("stable", "Stable"),
        EnumCode("declining", "Declining"),
    ),
    "neighborhood.demand_supply": _vocab(
        EnumCode("shortage", "Shortage"),
        EnumCode("in_balance", "In Balance"),
        EnumCode("over_supply", "Over Supply", ("oversupply",)),
    ),
    "neighborhood.marketing_time": _vocab(
        EnumCode("under_3_mths", "Under 3 mths", ("under 3 months", "< 3 mths")),
        EnumCode("3_6_mths", "3-6 mths", ("3-6 months", "3 to 6 months")),
        EnumCode("over_6_mths", "Over 6 mths", ("over 6 months", "> 6 mths")),
    ),
    "subject.occupant": _vocab(
        EnumCode("owner", "Owner"), EnumCode("tenant", "Tenant"), EnumCode("vacant", "Vacant")
    ),
    "subject.property_rights_appraised": _vocab(
        EnumCode("fee_simple", "Fee Simple"),
        EnumCode("leasehold", "Leasehold"),
        EnumCode("other", "Other"),
    ),
    "subject.assignment_type": _vocab(
        EnumCode("purchase", "Purchase Transaction", ("purchase",)),
        EnumCode("refinance", "Refinance Transaction", ("refinance",)),
        EnumCode("other", "Other"),
    ),
    "site.zoning_compliance": _vocab(
        EnumCode("legal", "Legal"),
        EnumCode(
            "legal_nonconforming",
            "Legal Nonconforming",
            ("legal non-conforming", "Legal Nonconforming (Grandfathered Use)"),
        ),
        EnumCode("no_zoning", "No Zoning"),
        EnumCode("illegal", "Illegal"),
    ),
    "improvements.units": _vocab(
        EnumCode("one", "One", ("1",)),
        EnumCode("one_with_accessory_unit", "One with Accessory Unit"),
    ),
    "improvements.type": _vocab(
        EnumCode("det", "Det.", ("detached",)),
        EnumCode("att", "Att.", ("attached",)),
        EnumCode("s_det_end_unit", "S-Det./End Unit", ("S-Det/End Unit", "semi-detached/end unit")),
    ),
    "improvements.foundation_type": _vocab(
        EnumCode("concrete_slab", "Concrete Slab", ("slab",)),
        EnumCode("crawl_space", "Crawl Space"),
        EnumCode("full_basement", "Full Basement"),
        EnumCode("partial_basement", "Partial Basement"),
    ),
    "improvements.heating_type": _vocab(
        EnumCode("fwa", "FWA", ("forced warm air", "forced air")),
        EnumCode("hwbb", "HWBB", ("hot water baseboard",)),
        EnumCode("radiant", "Radiant"),
        EnumCode("other", "Other"),
    ),
    "improvements.car_storage": _vocab(
        EnumCode("none", "None"),
        EnumCode("driveway", "Driveway"),
        # UAD 3.6 "Parking Garage" (A-1 3200.0006 ParkingGarage: an enclosed area for the
        # vehicles of a project's units) is the legacy Garage box (the 1073's Car Storage
        # Garage; G-1 maps CarStorageType to the 1004 and 1073 car-storage boxes).
        EnumCode("garage", "Garage", ("parking garage",)),
        EnumCode("carport", "Carport"),
        EnumCode("attached", "Att.", ("att",)),
        EnumCode("detached", "Det.", ("det",)),
        EnumCode("built_in", "Built-in"),
    ),
    # The 1004 appliance checkboxes (the synthetic set labels them as codes; the client set
    # as the printed text, compared as text).
    "improvements.appliances": _vocab(
        EnumCode("refrigerator", "Refrigerator", ("fridge",)),
        EnumCode("range_oven", "Range/Oven", ("range", "oven", "range oven", "stove")),
        EnumCode("dishwasher", "Dishwasher"),
        EnumCode("disposal", "Disposal", ("garbage disposal",)),
        EnumCode("microwave", "Microwave"),
        EnumCode("washer_dryer", "Washer/Dryer", ("washer dryer", "washer and dryer")),
        EnumCode("other", "Other"),
    ),
    # An extra key (SPEC §3): the car-storage box's garage attachment, same codes.
    "improvements.garage_type": _vocab(
        EnumCode("attached", "Att.", ("att",)),
        EnumCode("detached", "Det.", ("det",)),
        EnumCode("built_in", "Built-in"),
    ),
    "reconciliation.appraisal_made": _vocab(
        EnumCode("as_is", "As is"),
        EnumCode(
            "subject_to_completion",
            "Subject to completion",
            ("subject to completion per plans and specifications",),
        ),
        EnumCode(
            "subject_to_repairs",
            "Subject to repairs",
            (
                "subject to repair",
                "subject to repairs or alterations",
                "subject to the following repairs or alterations",
            ),
        ),
        EnumCode(
            "subject_to_inspection",
            "Subject to inspection",
            ("subject to the following required inspection",),
        ),
    ),
    "site.utilities_electric": _UTILITIES,
    "site.utilities_gas": _UTILITIES,
    "site.utilities_water": _UTILITIES,
    "site.utilities_sewer": _UTILITIES,
    "site.off_site_improvements_street": _OFF_SITE,
    "site.off_site_improvements_alley": _OFF_SITE,
}


@dataclass(frozen=True, slots=True)
class EnumValue:
    """A normalized enum: a code, plus the folded write-in text of an ``other`` code.
    ``code=None`` is a prediction that named no code (always wrong)."""

    code: str | None
    text: str | None = None


def _lookup(key: str | None) -> dict[str, str]:
    """fold_enum(form) -> code for the key's vocabulary (empty for an unknown key)."""
    table: dict[str, str] = {}
    for item in ENUMS.get(key or "", ()):
        for form in (item.code, item.label, *item.synonyms):
            table.setdefault(fold_enum(form), item.code)
    return table


def _has_other(key: str | None) -> bool:
    return any(item.code == "other" for item in ENUMS.get(key or "", ()))


_OTHER_PREFIX: Final = re.compile(r"^\s*other\b[\s:;,\-]*", re.IGNORECASE)


def parse_enum(value: object, key: str | None) -> EnumValue | None:
    if value is None:
        return None
    if isinstance(value, EnumValue):
        return value
    if isinstance(value, Mapping):
        code = value.get("code")
        text = value.get("text")
        return EnumValue(
            str(code) if code is not None else None,
            fold_text(text) if text is not None else None,
        )
    text = str(value)
    marked = marked_options(text)
    if marked is not None:
        text = "; ".join(marked)
    folded = fold_enum(text)
    table = _lookup(key)
    if folded in table:  # before the "no value" words: "None" is a code of some boxes
        return EnumValue(table[folded])
    if _is_nothing(text):
        return None
    if not table:  # no vocabulary (an extra key): the folded text is the code
        return EnumValue(folded)
    if _has_other(key):
        return EnumValue("other", fold_text(_OTHER_PREFIX.sub("", text)) or None)
    return EnumValue(None)


@dataclass(frozen=True, slots=True)
class MultiValue:
    """A normalized multi-checkbox box: the sorted codes, plus the folded write-in text of
    an ``other`` element. ``"?"`` among the codes marks a part that named no code."""

    codes: tuple[str, ...]
    other_text: str | None = None


_MULTI_SEPARATORS: Final = re.compile(r"[,;&|\n]|\band\b", re.IGNORECASE)
_OTHER_WORD: Final = re.compile(r"\bother\b[\s:;,\-]*", re.IGNORECASE)


def _cover_prefix(words: list[str], table: Mapping[str, str]) -> tuple[list[str], int]:
    """Codes whose forms cover the longest run of ``words`` from the start (longest form
    first), and how many words they cover."""
    codes: list[str] = []
    i = 0
    while i < len(words):
        for j in range(len(words), i, -1):
            code = table.get(fold_enum(" ".join(words[i:j])))
            if code is not None:
                codes.append(code)
                i = j
                break
        else:
            break
    return codes, i


def _multi_part(part: str, table: Mapping[str, str], other: bool) -> tuple[list[str], str | None]:
    """Codes of one separated part and an ``other`` write-in. A part is read whole, or as
    consecutive words that are each a form ("Garage Att."). In a vocabulary with ``other``,
    what follows the word "Other" or the uncovered tail is the write-in ("FWA Other: Infloor",
    "FWA Infloor"); elsewhere an uncovered word makes the part unmatched (``"?"``)."""
    whole = table.get(fold_enum(part))
    if whole is not None:
        return [whole], None
    head, text = part, None
    if other and (match := _OTHER_WORD.search(part)):
        head, text = part[: match.start()], fold_text(part[match.end() :]) or None
    words = head.split()
    codes, covered = _cover_prefix(words, table)
    if covered < len(words):
        if not other:
            return [*codes, "?"], None
        tail = fold_text(" ".join(words[covered:]))
        text = " ".join(t for t in (tail, text) if t) or None
    if other and (text is not None or "other" in codes or head != part):
        codes = [c for c in codes if c != "other"] + ["other"]
    return codes, text


def parse_enum_multi(value: object, key: str | None) -> MultiValue | None:
    """Parts separated by ``,``/``;``/``&``/``and`` each map to codes (:func:`_multi_part`).
    A list (the truth form) holds codes and ``{"code": "other", "text": ...}`` elements."""
    if value is None:
        return None
    if isinstance(value, MultiValue):
        return value
    if isinstance(value, list | tuple):
        codes: set[str] = set()
        other_text: str | None = None
        for element in value:
            if isinstance(element, Mapping):
                code = str(element.get("code"))
                if code == "other" and element.get("text") is not None:
                    other_text = fold_text(element["text"])
            else:
                code = str(element)
            codes.add(code)
        return MultiValue(tuple(sorted(codes)), other_text)
    text = str(value)
    marked = marked_options(text)
    if marked is not None:
        text = "; ".join(marked)
    table = _lookup(key)
    if fold_enum(text) in table:  # "None" is a code of car storage
        return MultiValue((table[fold_enum(text)],))
    if _is_nothing(text):
        return None
    other = _has_other(key)
    found: set[str] = set()
    texts: list[str] = []
    for part in _MULTI_SEPARATORS.split(text):
        if not part.strip():
            continue
        if not table:
            found.add(fold_enum(part))
            continue
        codes_, write_in = _multi_part(part, table, other)
        found.update(codes_)
        if write_in:
            texts.append(write_in)
    if not found:
        return None
    return MultiValue(tuple(sorted(found)), " ".join(texts) or None)


def multi_matches(prediction: MultiValue, truth: MultiValue) -> bool:
    """Code sets equal; the ``other`` write-in compared as text when the truth has one."""
    if prediction.codes != truth.codes or "?" in prediction.codes:
        return False
    return truth.other_text is None or prediction.other_text == truth.other_text


# --------------------------------------------------------------------------- per type


@dataclass(frozen=True, slots=True)
class Area:
    sqft: Decimal
    printed_unit: str | None = None  # "ac" when the box was printed in acres


def normalize(kind: str, value: object, *, key: str | None = None) -> Any:
    """``value`` (a printed string or a typed value) normalized as truth type ``kind``;
    ``None`` when it carries no value."""
    if value is None:
        return None
    if kind == "text":
        text = fold_text(value)
        return text or None
    if kind in ("int", "float"):
        return first_number(value)
    if kind == "currency":
        return parse_currency(value, key=key)
    if kind == "percent":
        return parse_percent(value)
    if kind == "date":
        return parse_date(value)
    if kind == "month":
        return parse_month(value)
    if kind == "bool":
        return parse_bool(value)
    if kind == "enum":
        return parse_enum(value, key)
    if kind == "enum_multi":
        return parse_enum_multi(value, key)
    if kind == "area_sqft":
        if isinstance(value, Mapping):
            sqft = _decimal(value.get("sqft"))
            if sqft is None:
                return None
            unit = value.get("printed_unit")
            return Area(sqft, str(unit) if unit else None)
        area = parse_area_sqft(value)
        return None if area is None else Area(area)
    if kind == "baths":
        return parse_baths(value)
    raise ValueError(f"unknown truth type {kind!r}")


def normalize_truth(kind: str, value: object, *, key: str | None = None) -> Any:
    """The truth ``value`` (already normalized by its writer) in comparable form."""
    if kind in ("int", "float", "currency", "percent") and isinstance(value, int | float):
        return Decimal(str(value))
    if kind == "area_sqft" and isinstance(value, int | float):
        return Area(Decimal(str(value)))
    if kind == "enum" and isinstance(value, str):
        return EnumValue(value)
    if kind == "date" and isinstance(value, str):
        return value
    if kind == "month" and isinstance(value, str):
        return value
    return normalize(kind, value, key=key)


@dataclass(frozen=True, slots=True)
class Columns:
    """The stored columns of one predicted field (``extraction_fields``)."""

    value_text: str | None
    value_num: Decimal | None
    value_date: date | None

    @classmethod
    def from_json(cls, item: Mapping[str, Any]) -> Columns:
        num = item.get("value_num")
        day = item.get("value_date")
        return cls(
            value_text=item.get("value_text"),
            value_num=Decimal(str(num)) if num is not None else None,
            value_date=date.fromisoformat(day) if day else None,
        )

    @property
    def has_value(self) -> bool:
        """What the reviewer sees as a value (the pipeline's ``is_empty`` negated)."""
        return (
            (self.value_text is not None and self.value_text.strip() != "")
            or self.value_num is not None
            or self.value_date is not None
        )


def prediction_value(kind: str, columns: Columns, *, key: str | None = None) -> Any:
    """The prediction normalized as truth type ``kind`` (module doc: which column)."""
    text, num, day = columns.value_text, columns.value_num, columns.value_date
    fallback: object = text
    if text is None:
        fallback = num if num is not None else day
    if kind in ("int", "float", "currency", "percent", "area_sqft", "bool") and num is not None:
        return normalize(kind, num, key=key)
    if kind == "date" and day is not None:
        return day.isoformat()
    if kind == "baths" and text is None and num is not None:
        return parse_baths(format(num, "f"))
    return normalize(kind, fallback, key=key)


def matches(kind: str, prediction: Any, truth: Any, *, tolerance: float | None = None) -> bool:
    """Whether a normalized prediction equals a normalized truth (§4)."""
    if prediction is None or truth is None:
        return False
    if kind == "area_sqft":
        if not isinstance(prediction, Area) or not isinstance(truth, Area):
            return False
        allowed = Decimal(str(tolerance)) if tolerance is not None else Decimal(0)
        if truth.printed_unit in ("ac", "acre", "acres"):
            allowed = max(allowed, ACRE_ROUNDING * SQUARE_FEET_PER_ACRE)
        return abs(prediction.sqft - truth.sqft) <= allowed
    if isinstance(prediction, Decimal) and isinstance(truth, Decimal):
        if tolerance is not None:
            return abs(prediction - truth) <= Decimal(str(tolerance))
        return prediction == truth
    if kind == "enum_multi":
        if not isinstance(prediction, MultiValue) or not isinstance(truth, MultiValue):
            return False
        return multi_matches(prediction, truth)
    if kind == "enum":
        if not isinstance(prediction, EnumValue) or not isinstance(truth, EnumValue):
            return False
        if prediction.code is None or prediction.code != truth.code:
            return False
        if truth.code == "other" and truth.text is not None:
            return prediction.text == truth.text
        return True
    if kind == "text" and isinstance(prediction, str) and isinstance(truth, str):
        return text_matches(prediction, truth)
    return bool(prediction == truth)


def text_parts(text: str, *, truth: bool) -> frozenset[str]:
    """The folded parts of a multi-value text. The truth is split on ``;``; a prediction on
    ``;`` when it has one, else on ``,``, ``&``, ``/`` or ``and`` (how a reader joins names)."""
    if truth or ";" in text or "|" in text:
        pieces = _TRUTH_PARTS.split(text)
    else:
        pieces = _PREDICTION_PARTS.split(text)
    return frozenset(p for p in (fold_text(piece) for piece in pieces) if p)


def _without_separators(text: str) -> str:
    return " ".join(re.sub(r"[,;|]", " ", fold_text(text)).split())


def text_matches(prediction: str, truth: str) -> bool:
    """Folded equality; a truth that joins several values with ``;`` is compared as a set of
    parts, so the order and the join of the prediction do not matter."""
    if prediction == truth:
        return True
    # A list read without its separators ("Refrigerator Range/Oven Dishwasher" for the
    # printed "Refrigerator, Range/Oven, Dishwasher"): same words in the same order.
    if _without_separators(prediction) == _without_separators(truth):
        return True
    if ";" not in truth and "|" not in truth:
        return False
    return text_parts(prediction, truth=False) == text_parts(truth, truth=True)


# --------------------------------------------------------------------------- display


def _js_number(value: Decimal) -> str:
    """``Number(v).toLocaleString('en-US')``: grouped, at most 3 fraction digits, half away
    from zero."""
    rounded = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    text = f"{rounded:,.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def review_display(columns: Columns) -> str:
    """What the appraisal review page shows for a field (``fieldValue`` in
    ``web/live/live-review.js``): ``value_text``, else the number formatted, else the date
    as stored, else an em dash. JS ``??`` keeps an empty string."""
    if columns.value_text is not None:
        return columns.value_text
    if columns.value_num is not None:
        return _js_number(columns.value_num)
    if columns.value_date is not None:
        return columns.value_date.isoformat()
    return "—"


def _grouped(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value:,f}"


def _label(key: str | None, code: str) -> str:
    for item in ENUMS.get(key or "", ()):
        if item.code == code:
            return item.label
    return code


def canonical_display(
    kind: str, value: Any, *, key: str | None = None, printed: str | None = None
) -> str:
    """What the review page should show for the truth ``value`` (§8)."""
    truth = normalize_truth(kind, value, key=key)
    if truth is None:
        return "—"
    if kind == "currency":
        sign = "-" if truth < 0 else ""
        amount = abs(truth)
        body = f"{amount:,.2f}" if amount != amount.to_integral_value() else f"{int(amount):,}"
        return f"{sign}${body}"
    if kind in ("int", "float"):
        return _grouped(truth)
    if kind == "percent":
        return f"{_grouped(truth)}%"
    if kind == "area_sqft":
        text = f"{_grouped(truth.sqft)} sq ft"
        if truth.printed_unit in ("ac", "acre", "acres"):
            acres = first_number(printed) if printed else None
            if acres is None:
                acres = (truth.sqft / SQUARE_FEET_PER_ACRE).quantize(Decimal("0.01"))
            text += f" ({acres} ac)"
        return text
    if kind == "date":
        day = date.fromisoformat(truth)
        return day.strftime("%m/%d/%Y")
    if kind == "month":
        year, month = truth.split("-")
        return f"{month}/{year}"
    if kind == "bool":
        return "Yes" if truth else "No"
    if kind == "enum":
        if truth.code == "other" and isinstance(value, Mapping) and value.get("text"):
            return str(value["text"])
        return _label(key, truth.code or "")
    if kind == "enum_multi":
        order = [item.code for item in ENUMS.get(key or "", ())]
        codes = sorted(truth.codes, key=lambda c: order.index(c) if c in order else len(order))
        written = _other_written(value)
        return ", ".join(
            written if code == "other" and written else _label(key, code) for code in codes
        )
    if kind == "baths":
        return str(truth.display())
    return str(value).strip()


def _other_written(value: Any) -> str | None:
    """The ``other`` element's text as the truth writer gave it (for display)."""
    if isinstance(value, list | tuple):
        for element in value:
            if isinstance(element, Mapping) and element.get("code") == "other":
                text = element.get("text")
                return str(text) if text else None
    return None


def display_matches(shown: str, expected: str) -> bool:
    return _SPACES.sub(" ", shown).strip() == _SPACES.sub(" ", expected).strip()
