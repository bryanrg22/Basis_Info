"""Exact comparison of extracted fields with ``urar_sample.expected.json``.

"Exact" means the typed value equals the golden typed value: numbers as decimals
(``"$4,321"`` -> 4321; ``2.1`` baths compares as ``Decimal("2.1")``, not a float), dates
as dates, booleans as 1/0, text as the printed text after whitespace cleaning only.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from fixtures import load_fixture_json

from basis.extraction.results import ExtractedField

GOLDEN: dict[str, Any] = load_fixture_json("urar_sample.expected.json")
NUMERIC = {"int", "float", "currency", "percent"}


def expected_value(field: Mapping[str, Any]) -> Any:
    value = field["value"]
    kind = field["type"]
    if kind == "date":
        return date.fromisoformat(value)
    if kind == "bool":
        return Decimal(int(value))
    if kind in NUMERIC or (isinstance(value, int | float) and not isinstance(value, bool)):
        return Decimal(str(value))
    return value


def actual_value(field: Mapping[str, Any], item: ExtractedField | None) -> Any:
    if item is None:
        return None
    kind = field["type"]
    if kind == "date":
        return item.value_date
    if kind == "bool" or kind in NUMERIC or isinstance(field["value"], int | float):
        return item.value_num
    return item.value_text


def compare(
    fields: Mapping[str, ExtractedField],
) -> tuple[list[str], list[tuple[str, Any, Any]]]:
    """``(hits, misses)``; a miss is ``(key, expected, actual)``."""
    hits: list[str] = []
    misses: list[tuple[str, Any, Any]] = []
    for field in GOLDEN["fields"]:
        expected = expected_value(field)
        actual = actual_value(field, fields.get(field["key"]))
        if actual == expected:
            hits.append(field["key"])
        else:
            misses.append((field["key"], expected, actual))
    return hits, misses
