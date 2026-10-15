"""Reviewer corrections: which fields each entity type accepts and how values parse.

``apply_corrections`` validates every correction of a rejection here before anything is
written (target exists in the rejected revision of this study, only known fields, values
parse); ``extract_fields`` and ``process_assets`` apply the validated ``extraction_field``
and ``asset`` corrections with the same parsers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from basis.activities._support import validation_error
from basis.db.enums import Bucket

EXTRACTION_FIELD_VALUES: Final = frozenset({"value_text", "value_num", "value_date"})
ROOM_FIELDS: Final = frozenset({"name", "room_type", "floor"})
COMPONENT_FIELDS: Final = frozenset({"label", "material", "attachment"})
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


def check_extraction_field(after: dict[str, Any]) -> None:
    check_fields("extraction_field", after, EXTRACTION_FIELD_VALUES)
    parse_number(after.get("value_num"))
    parse_date(after.get("value_date"))


def check_asset(after: dict[str, Any]) -> None:
    check_fields("asset", after, ASSET_FIELDS)
    if "bucket" in after:
        parse_bucket(after["bucket"])
    if "recovery_period" in after:
        parse_recovery_period(after["recovery_period"])
