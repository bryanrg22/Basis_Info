"""Exact comparison of extracted fields with ``urar_sample.expected.json``.

"Exact" means the typed value equals the golden typed value: numbers as decimals
(``"$4,321"`` -> 4321; ``2.1`` baths compares as ``Decimal("2.1")``, not a float), dates
as dates, booleans as 1/0, text as the printed text after whitespace cleaning only.

The fixture prints the income approach "Not Developed" and the functional and external
depreciation lines "$0": the presence rules (:mod:`basis.extraction.presence`, SPEC
principle 2) read those as not developed, so the pipeline's result leaves them empty
(:data:`NOT_DEVELOPED`; ``compare(..., presence=True)``); the readers still read them.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from fixtures import load_fixture_json

from basis.extraction.results import ExtractedField

GOLDEN: dict[str, Any] = load_fixture_json("urar_sample.expected.json")
NOT_DEVELOPED = frozenset(
    {
        "reconciliation.indicated_value_income_approach",
        "cost_approach.depreciation_functional",
        "cost_approach.depreciation_external",
    }
)
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
    fields: Mapping[str, ExtractedField], *, presence: bool = False
) -> tuple[list[str], list[tuple[str, Any, Any]]]:
    """``(hits, misses)``; a miss is ``(key, expected, actual)``. ``presence``: ``fields`` is
    the pipeline's result, after the presence rules (module doc)."""
    hits: list[str] = []
    misses: list[tuple[str, Any, Any]] = []
    for field in GOLDEN["fields"]:
        dropped = presence and field["key"] in NOT_DEVELOPED
        expected = None if dropped else expected_value(field)
        actual = actual_value(field, fields.get(field["key"]))
        if actual == expected:
            hits.append(field["key"])
        else:
            misses.append((field["key"], expected, actual))
    return hits, misses
