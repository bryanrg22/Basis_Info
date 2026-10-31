"""An appraisal field the extraction missed, added by the engineer (``extraction_field_add``).

A correction at the appraisal review whose ``entity_id`` is the extraction (``extractions.id``)
of the decided revision and whose ``after`` is::

    {"key": "improvements.year_built", "value_num": 1987, "page": 3,
     "evidence": "Year Built 1987"}

* ``key``: a ``<section>.<field>`` key of the URAR field catalogue
  (:data:`basis.extraction.field_mappings.FIELD_BY_KEY`), absent from that extraction (a field
  the extraction has is corrected with an ``extraction_field`` correction instead);
* exactly one of ``value_text``, ``value_num``, ``value_date`` is non-null, the member the
  catalogue type of the key stores (:data:`TYPE_MEMBERS`): a date in ``value_date`` (ISO), a
  number in ``value_num`` (a whole number for an ``int`` field), ``Yes``/``No`` in
  ``value_text`` for a check box, text in ``value_text``; ``currency_or_text`` takes either;
* ``page``: the page of the appraisal PDF the value is printed on, a whole number from 1. It
  must be within the PDF's page count when that is known (the extraction's
  ``raw["page_count"]``, the pages the extraction read); a bare UAD 3.6 XML with no report PDF
  records none, and then only ``page >= 1`` is checked;
* ``evidence``: the text as printed in the source, non-blank, at most :data:`MAX_EVIDENCE`
  characters (the field table has no column for the printed text, so it is kept in the new
  extraction's ``raw["added_fields"]`` and on the ``entity.added`` event);
* nothing else; and the correction's ``reason`` is required (checked by the callers).

Pure, so the decision route (a 422 before anything is recorded), ``apply_corrections`` and
``extract_fields`` (a ``ValidationError`` with the problem's code) check the same rules.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from basis.errors.codes import E
from basis.extraction.field_mappings import FIELD_BY_KEY, FieldType

KEY: Final = "key"
PAGE: Final = "page"
EVIDENCE: Final = "evidence"
VALUE_MEMBERS: Final = ("value_text", "value_num", "value_date")
ADDED_FIELD_MEMBERS: Final = frozenset({KEY, PAGE, EVIDENCE, *VALUE_MEMBERS})
MAX_EVIDENCE: Final = 1000
# The value member each catalogue type stores (``currency_or_text``: a number or the text).
TYPE_MEMBERS: Final[dict[FieldType, frozenset[str]]] = {
    "text": frozenset({"value_text"}),
    "int": frozenset({"value_num"}),
    "float": frozenset({"value_num"}),
    "currency": frozenset({"value_num"}),
    "percent": frozenset({"value_num"}),
    "currency_or_text": frozenset({"value_num", "value_text"}),
    "date": frozenset({"value_date"}),
    "bool": frozenset({"value_text"}),
}
BOOL_TEXT: Final = frozenset({"Yes", "No"})


@dataclass(frozen=True, slots=True)
class Problem:
    """Why an added field is refused, with the catalogue code ``apply_corrections`` and
    ``extract_fields`` raise it under (docs/ERROR_CODES.md)."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class AddedField:
    key: str
    value_text: str | None
    value_num: Decimal | None
    value_date: date | None
    page: int
    evidence: str

    @property
    def section(self) -> str:
        return self.key.split(".", 1)[0]

    @property
    def field(self) -> str:
        return self.key.split(".", 1)[1]


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def problems(after: Mapping[str, Any]) -> list[Problem]:
    """Everything wrong with an ``extraction_field_add`` ``after`` by itself (the target's
    fields and page count are :func:`target_problems`)."""
    found: list[Problem] = []
    unknown = sorted(set(after) - ADDED_FIELD_MEMBERS)
    if unknown:
        found.append(
            Problem(
                E.CORRECT_FIELD_ADD_SHAPE,
                f"an extraction_field_add takes key, one of value_text/value_num/value_date, "
                f"page and evidence; not {', '.join(unknown)}",
            )
        )
    key = after.get(KEY)
    spec = FIELD_BY_KEY.get(key) if isinstance(key, str) else None
    if not isinstance(key, str):
        found.append(Problem(E.CORRECT_FIELD_ADD_SHAPE, "key must be a <section>.<field> key"))
    elif spec is None:
        found.append(
            Problem(
                E.CORRECT_FIELD_ADD_UNKNOWN_KEY,
                f"key {key!r} is not a field of the appraisal field catalogue",
            )
        )
    given = [member for member in VALUE_MEMBERS if after.get(member) is not None]
    if len(given) != 1:
        found.append(
            Problem(
                E.CORRECT_FIELD_ADD_SHAPE,
                "exactly one of value_text, value_num, value_date must be given (non-null); "
                f"got {len(given)}",
            )
        )
    else:
        found += _value_problems(given[0], after[given[0]], spec.type if spec else None)
    page = after.get(PAGE)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        found.append(
            Problem(E.CORRECT_FIELD_ADD_SHAPE, f"page {page!r} must be a whole number from 1")
        )
    evidence = after.get(EVIDENCE)
    if not isinstance(evidence, str) or not evidence.strip():
        found.append(
            Problem(
                E.CORRECT_FIELD_ADD_SHAPE,
                "evidence must be the value's text as printed in the source (non-blank)",
            )
        )
    elif len(evidence) > MAX_EVIDENCE:
        found.append(
            Problem(
                E.CORRECT_FIELD_ADD_SHAPE,
                f"evidence is {len(evidence)} characters; at most {MAX_EVIDENCE}",
            )
        )
    return found


def _value_problems(member: str, value: Any, kind: FieldType | None) -> list[Problem]:
    if member == "value_text" and (not isinstance(value, str) or not value.strip()):
        return [Problem(E.CORRECT_FIELD_ADD_SHAPE, "value_text must be non-blank text")]
    if member == "value_num" and _number(value) is None:
        return [Problem(E.CORRECT_NOT_A_NUMBER, f"value_num {value!r} is not a finite number")]
    if member == "value_date" and _day(value) is None:
        return [Problem(E.CORRECT_NOT_A_DATE, f"value_date {value!r} is not an ISO date")]
    if kind is None:
        return []  # an unknown key is reported once, as such
    allowed = TYPE_MEMBERS[kind]
    if member not in allowed:
        return [
            Problem(
                E.CORRECT_FIELD_ADD_TYPE,
                f"a {kind} field takes {' or '.join(sorted(allowed))}, not {member}",
            )
        ]
    if kind == "int":
        number = _number(value)
        if number is not None and number != number.to_integral_value():
            return [
                Problem(
                    E.CORRECT_FIELD_ADD_TYPE, f"an int field takes a whole number, not {value!r}"
                )
            ]
    if kind == "bool" and value not in BOOL_TEXT:
        return [Problem(E.CORRECT_FIELD_ADD_TYPE, f"a check box takes Yes or No, not {value!r}")]
    return []


def target_problems(
    key: str, page: int, *, existing_keys: Collection[str], page_count: int | None
) -> list[Problem]:
    """What the target extraction refuses: a key it already has, a page past its PDF."""
    found: list[Problem] = []
    if key in existing_keys:
        found.append(
            Problem(
                E.CORRECT_FIELD_ADD_EXISTS,
                f"the extraction already has {key}: correct that field with an "
                "extraction_field correction instead of adding it",
            )
        )
    if page_count is not None and page > page_count:
        found.append(
            Problem(
                E.CORRECT_FIELD_ADD_PAGE,
                f"page {page} is past the appraisal PDF's {page_count} pages",
            )
        )
    return found


def page_count(raw: object) -> int | None:
    """The page count an extraction recorded (``raw["page_count"]``), or None when unknown
    (zero or absent: a bare XML without its PDF, an extraction stored before it was kept)."""
    value = raw.get("page_count") if isinstance(raw, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def parse(after: Mapping[str, Any]) -> AddedField:
    """The added field of a valid ``after`` (:func:`problems` empty); ``ValueError`` else."""
    found = problems(after)
    if found:
        raise ValueError(found[0].message)
    text = after.get("value_text")
    return AddedField(
        key=str(after[KEY]),
        value_text=text.strip() if isinstance(text, str) else None,
        value_num=_number(after["value_num"]) if after.get("value_num") is not None else None,
        value_date=_day(after["value_date"]) if after.get("value_date") is not None else None,
        page=int(after[PAGE]),
        evidence=str(after[EVIDENCE]),
    )
