"""Reviewer corrections: which fields each entity type accepts and how values parse.

``apply_corrections`` validates every correction of a rejection here before anything is
written (target exists in the rejected revision of this study, only known fields, values
parse); ``extract_fields`` and ``process_assets`` apply the validated ``extraction_field``
and ``asset`` corrections with the same parsers.

Counts (v0.11, manual add): a ``component`` correction may set ``count``, the quantity
``process_assets`` takes off and prices (``components.raw["count"]``); a ``room_component``
correction adds a component the room's inventory is missing (``entity_id`` is the room).
Their values follow :mod:`basis.domain.counts`, which the decision route checks too.
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

EXTRACTION_FIELD_VALUES: Final = frozenset({"value_text", "value_num", "value_date"})
ROOM_FIELDS: Final = frozenset({"name", "room_type", "floor", "merge_into"})
# A ``room`` correction ``{"merge_into": "<room id>"}`` merges the room into another room of
# the same revision (its photos and per-photo rows move there); it takes no other field.
MERGE_INTO: Final = "merge_into"
COMPONENT_FIELDS: Final = frozenset({"label", "material", "attachment", "count"})
ASSET_FIELDS: Final = frozenset({"category", "bucket", "recovery_period", "irs_section"})
PHOTO_FIELDS: Final = frozenset[str]()


def check_fields(kind: str, after: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = set(after) - allowed
    if unknown:
        raise validation_error(f"unknown {kind} fields: {', '.join(sorted(unknown))}")


def parse_text(value: Any) -> str | None:
    return None if value is None else str(value)


def parse_number(value: Any, what: str = "value_num") -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise validation_error(f"{what} {value!r} is not a number") from None
    if not number.is_finite():
        raise validation_error(f"{what} {value!r} is not a finite number")
    return number


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise validation_error(f"value_date {value!r} is not an ISO date") from None


def parse_bucket(value: Any) -> Bucket:
    try:
        return Bucket(str(value))
    except ValueError:
        raise validation_error(f"unknown bucket {value!r}") from None


def parse_recovery_period(value: Any) -> Decimal:
    number = parse_number(value, "recovery_period")
    if number is None or not Decimal(0) < number < Decimal(1000):
        raise validation_error(f"recovery_period {value!r} must be a positive number of years")
    return number


def check_component(after: dict[str, Any]) -> None:
    """Known fields and, when given, a count (:func:`basis.domain.counts.count_problem`)."""
    check_fields("component", after, COMPONENT_FIELDS)
    problem = count_problem(after["count"]) if "count" in after else None
    if problem is not None:
        raise validation_error(problem)


def check_room_component(after: dict[str, Any]) -> None:
    """A component added to a room (:func:`basis.domain.counts.room_component_problems`)."""
    problems = room_component_problems(after)
    if problems:
        raise validation_error("; ".join(problems))


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
    bucket = parse_bucket(after["bucket"]) if "bucket" in after else None
    period = parse_recovery_period(after["recovery_period"]) if "recovery_period" in after else None
    section = after.get("irs_section")
    if section is not None and str(section) not in VALID_SECTION_BUCKETS:
        raise validation_error(f"irs_section {section!r} must be 1245 or 1250")
    if bucket is not None and period is not None:
        issues = check_recovery_period(bucket, period)
        if issues:
            raise validation_error(issues[0].message)
    if bucket is not None and section is not None:
        issues = validate_section_bucket(str(section), bucket)
        if issues:
            raise validation_error(issues[0].message)
