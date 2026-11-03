"""Reviewer corrections: which fields each entity type accepts and how values parse.

``apply_corrections`` validates every correction of a rejection here before anything is
written (target exists in the rejected revision of this study, only known fields, values
parse); ``extract_fields`` and ``process_assets`` apply the validated ``extraction_field``
and ``asset`` corrections with the same parsers.

Counts (v0.11, manual add): a ``component`` correction may set ``count``, the quantity
``process_assets`` takes off and prices (``components.raw["count"]``); a ``room_component``
correction adds a component the room's inventory is missing (``entity_id`` is the room).
Their values follow :mod:`basis.domain.counts`, which the decision route checks too. A
``photo_room`` correction (v0.12) moves a photo to another room of the revision.

A ``component_remove`` correction removes a component row from the next revision
(:mod:`basis.domain.removals`): ``after`` is ``{}`` and the reason is required. An
``extraction_field_add`` correction adds a field the extraction is missing
(:mod:`basis.extraction.added_fields`), with a required reason too: removing or adding a row
is engineer evidence, so it must say why.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from basis.activities._support import validation_error
from basis.db.enums import Bucket
from basis.domain.counts import count_problem, room_component_problems
from basis.domain.validators import (
    VALID_SECTION_BUCKETS,
    check_recovery_period,
    validate_section_bucket,
)
from basis.errors.codes import E
from basis.extraction import added_fields

EXTRACTION_FIELD_VALUES: Final = frozenset({"value_text", "value_num", "value_date"})
ROOM_FIELDS: Final = frozenset({"name", "room_type", "floor", "merge_into"})
# A ``room`` correction ``{"merge_into": "<room id>"}`` merges the room into another room of
# the same revision (its photos and per-photo rows move there); it takes no other field.
MERGE_INTO: Final = "merge_into"
COMPONENT_FIELDS: Final = frozenset({"label", "material", "attachment", "count"})
ASSET_FIELDS: Final = frozenset(
    {
        "category",
        "bucket",
        "recovery_period",
        "irs_section",
        # EVAL_REPORT 78: the engineer's measurement of an item the appraisal cannot size (a
        # driveway, a cabinet run, a room with no measured area); the three go together.
        "quantity",
        "quantity_unit",
        "quantity_source",
    }
)
QUANTITY_FIELDS: Final = frozenset({"quantity", "quantity_unit", "quantity_source"})
QUANTITY_UNITS: Final = frozenset({"EA", "SF", "LF", "SY"})
MAX_QUANTITY: Final = Decimal(10_000_000)
MAX_SOURCE_CHARS: Final = 300
PHOTO_FIELDS: Final = frozenset[str]()
# A ``photo_room`` correction (v0.12, the rooms review of patch ``components-gate-v1``)
# ``{"room_id": "<room id>"}`` moves the photo named by ``entity_id`` to that room of the
# same revision.
PHOTO_ROOM_FIELDS: Final = frozenset({"room_id"})


def check_fields(kind: str, after: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = set(after) - allowed
    if unknown:
        raise validation_error(
            f"unknown {kind} fields: {', '.join(sorted(unknown))}", E.CORRECT_UNKNOWN_FIELDS
        )


def parse_text(value: Any) -> str | None:
    return None if value is None else str(value)


def parse_number(value: Any, what: str = "value_num") -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise validation_error(
            f"{what} {value!r} is not a number", E.CORRECT_NOT_A_NUMBER
        ) from None
    if not number.is_finite():
        raise validation_error(f"{what} {value!r} is not a finite number", E.CORRECT_NOT_A_NUMBER)
    return number


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise validation_error(
            f"value_date {value!r} is not an ISO date", E.CORRECT_NOT_A_DATE
        ) from None


def parse_bucket(value: Any) -> Bucket:
    try:
        return Bucket(str(value))
    except ValueError:
        raise validation_error(f"unknown bucket {value!r}", E.CORRECT_UNKNOWN_BUCKET) from None


def parse_recovery_period(value: Any) -> Decimal:
    number = parse_number(value, "recovery_period")
    if number is None or not Decimal(0) < number < Decimal(1000):
        raise validation_error(
            f"recovery_period {value!r} must be a positive number of years",
            E.CORRECT_RECOVERY_PERIOD,
        )
    return number


def check_component(after: dict[str, Any]) -> None:
    """Known fields and, when given, a count (:func:`basis.domain.counts.count_problem`)."""
    check_fields("component", after, COMPONENT_FIELDS)
    problem = count_problem(after["count"]) if "count" in after else None
    if problem is not None:
        raise validation_error(problem, E.CORRECT_COUNT)


def check_photo_room(after: dict[str, Any]) -> None:
    """Exactly a ``room_id`` (the target room is checked against the revision by the caller)."""
    if set(after) != PHOTO_ROOM_FIELDS:
        raise validation_error(
            "a photo_room correction takes exactly room_id", E.CORRECT_PHOTO_ROOM_SHAPE
        )


def check_room_component(after: dict[str, Any]) -> None:
    """A component added to a room (:func:`basis.domain.counts.room_component_problems`)."""
    problems = room_component_problems(after)
    if problems:
        raise validation_error("; ".join(problems), E.CORRECT_ROOM_COMPONENT)


def check_reason(kind: str, reason: str | None) -> None:
    """A non-blank reason (``component_remove``, ``extraction_field_add``)."""
    if reason is None or not reason.strip():
        raise validation_error(f"a {kind} correction needs a reason", E.CORRECT_REASON_REQUIRED)


def check_component_remove(after: dict[str, Any], reason: str | None) -> None:
    """A removal takes no field (``after`` is ``{}``) and needs a reason."""
    if after:
        raise validation_error(
            f"a component_remove correction takes no fields (after must be {{}}); got "
            f"{', '.join(sorted(after))}",
            E.CORRECT_REMOVE_SHAPE,
        )
    check_reason("component_remove", reason)


def check_extraction_field_add(after: dict[str, Any], reason: str | None) -> None:
    """An added field's ``after`` (:func:`basis.extraction.added_fields.problems`, the first
    problem with its code) and a reason."""
    problems = added_fields.problems(after)
    if problems:
        raise validation_error(problems[0].message, problems[0].code)
    check_reason("extraction_field_add", reason)


def check_extraction_field(after: dict[str, Any]) -> None:
    check_fields("extraction_field", after, EXTRACTION_FIELD_VALUES)
    parse_number(after.get("value_num"))
    parse_date(after.get("value_date"))


def check_asset(after: dict[str, Any]) -> None:
    """Known fields, parseable values, and an internally consistent classification.

    A bucket given with a recovery period must agree with it (``y5`` is 5 years), and a
    section given with a bucket must allow it (section 1245 takes 5/7/15 years, 1250 takes
    15/27.5/39): the domain validators of ``basis.domain.validators``.
    """
    check_fields("asset", after, ASSET_FIELDS)
    check_quantity(after)
    bucket = parse_bucket(after["bucket"]) if "bucket" in after else None
    period = parse_recovery_period(after["recovery_period"]) if "recovery_period" in after else None
    section = after.get("irs_section")
    if section is not None and str(section) not in VALID_SECTION_BUCKETS:
        raise validation_error(
            f"irs_section {section!r} must be 1245 or 1250", E.CORRECT_IRS_SECTION
        )
    if bucket is not None and period is not None:
        issues = check_recovery_period(bucket, period)
        if issues:
            raise validation_error(issues[0].message, E.CORRECT_BUCKET_PERIOD_MISMATCH)
    if bucket is not None and section is not None:
        issues = validate_section_bucket(str(section), bucket)
        if issues:
            raise validation_error(issues[0].message, E.CORRECT_SECTION_BUCKET_MISMATCH)


def check_quantity(after: dict[str, Any]) -> Decimal | None:
    """The engineer's measured quantity in an asset correction (EVAL_REPORT 78), or None
    when the correction carries none: all of ``quantity`` (positive, at most 10,000,000),
    ``quantity_unit`` (EA, SF, LF, SY) and ``quantity_source`` (where it was measured, at
    most 300 characters), or a ``CORRECT_QUANTITY`` error."""
    given = QUANTITY_FIELDS & set(after)
    if not given:
        return None
    if given != QUANTITY_FIELDS:
        raise validation_error(
            "a measured quantity needs quantity, quantity_unit and quantity_source together "
            f"(missing: {', '.join(sorted(QUANTITY_FIELDS - given))})",
            E.CORRECT_QUANTITY,
        )
    try:
        quantity = Decimal(str(after["quantity"]))
    except InvalidOperation:
        raise validation_error(
            f"quantity {after['quantity']!r} is not a number", E.CORRECT_QUANTITY
        ) from None
    if not quantity.is_finite() or quantity <= 0 or quantity > MAX_QUANTITY:
        raise validation_error(
            f"quantity must be positive and at most {MAX_QUANTITY}, got {after['quantity']!r}",
            E.CORRECT_QUANTITY,
        )
    if after["quantity_unit"] not in QUANTITY_UNITS:
        raise validation_error(
            f"quantity_unit {after['quantity_unit']!r} is not one of "
            f"{', '.join(sorted(QUANTITY_UNITS))}",
            E.CORRECT_QUANTITY,
        )
    source = after["quantity_source"]
    if not isinstance(source, str) or not source.strip() or len(source) > MAX_SOURCE_CHARS:
        raise validation_error(
            f"quantity_source must say where it was measured (1-{MAX_SOURCE_CHARS} characters)",
            E.CORRECT_QUANTITY,
        )
    return quantity
