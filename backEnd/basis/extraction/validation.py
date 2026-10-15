"""Field validation: types, ranges, and cross-field rules (S3_SPEC section 4).

Migrated from ``evidence_layer/src/tiered_extraction/validation.py`` (``AppraisalValidator``)
as pure functions over :class:`ExtractedField` maps. Fixes carried in:

* The legacy checks looked fields up by names the mapping never produced
  (``reconciliation.final_opinion_of_market_value``, ``improvements.gross_living_area``,
  ``reconciliation.effective_date``), so the value, GLA and date checks never ran (A.8).
  Every rule here names catalogue keys and a test runs each rule against a field that
  violates it.
* Dates are real dates (cleaners parse them); a date field whose text does not parse is a
  type failure instead of being skipped.

Rules (``rule`` names are stable; they appear in ``extractions.raw`` and the review UI):

* ``missing_critical``: a critical field is absent.
* ``type``: a typed field's text did not parse (critical -> error, others -> warning).
* ``range``: a value outside its plausible range (table :data:`RANGES`).
* ``rooms_vs_bed_bath``: ``total_rooms >= bedrooms + bathrooms`` (as printed; S3_SPEC).
* ``value_vs_cost``: the final market value is within 50% of the cost approach value
  when both are present (S3_SPEC).
* ``value_vs_contract`` (warning, legacy): final value within 20% of the contract price.
* ``basement_vs_gla`` (warning, legacy): basement area at most 1.5 x GLA.

Any issue sends the extraction to review (``basis.extraction.confidence``); severity only
tells the reviewer whether a value is unusable (error) or suspicious (warning).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from basis.extraction.field_mappings import CRITICAL_FIELDS, FIELD_BY_KEY
from basis.extraction.results import ExtractedField, ValidationIssue

PER_FIELD_RULES: Final = frozenset({"missing_critical", "type", "range"})


@dataclass(frozen=True, slots=True)
class Range:
    low: Decimal | None = None
    high: Decimal | None = None


def _r(low: float | None, high: float | None) -> Range:
    return Range(
        None if low is None else Decimal(str(low)), None if high is None else Decimal(str(high))
    )


def _ranges(today: date) -> dict[str, Range]:
    year = today.year
    return {
        "improvements.year_built": _r(1700, year + 1),
        "subject.tax_year": _r(1900, year + 1),
        "improvements.gla_sqft": _r(100, 50_000),
        "improvements.basement_area_sqft": _r(0, 50_000),
        "improvements.total_rooms": _r(1, 200),
        "improvements.bedrooms": _r(0, 100),
        "improvements.bathrooms": _r(0, 100),
        "improvements.stories": _r(1, 150),
        "improvements.effective_age_years": _r(0, 300),
        "improvements.garage_cars": _r(0, 50),
        "improvements.fireplaces": _r(0, 50),
        "site.area_sqft": _r(1, 500_000_000),
        "listing_and_contract.contract_price": _r(1, 100_000_000),
        "listing_and_contract.offering_price": _r(1, 100_000_000),
        "listing_and_contract.days_on_market": _r(0, 10_000),
        "reconciliation.final_market_value": _r(1, 100_000_000),
        "appraiser.appraised_value": _r(1, 100_000_000),
        "cost_approach.remaining_economic_life": _r(0, 200),
    }


# Dates: no earlier than this, and no later than a year after today.
EARLIEST_DATE: Final = date(1900, 1, 1)


def _num(fields: Mapping[str, ExtractedField], key: str) -> Decimal | None:
    item = fields.get(key)
    return None if item is None else item.value_num


def _issue(rule: str, message: str, keys: tuple[str, ...], critical: bool) -> ValidationIssue:
    return ValidationIssue(
        rule=rule, message=message, keys=keys, severity="error" if critical else "warning"
    )


def validate_fields(
    fields: Mapping[str, ExtractedField], *, today: date | None = None
) -> list[ValidationIssue]:
    """Every rule failure for ``fields`` (an empty list means the fields validate)."""
    today = today or date.today()
    issues: list[ValidationIssue] = []
    for key in CRITICAL_FIELDS:
        item = fields.get(key)
        if item is None or item.is_empty:
            issues.append(
                ValidationIssue(
                    rule="missing_critical",
                    message=f"{key} is missing",
                    keys=(key,),
                    severity="error",
                )
            )

    ranges = _ranges(today)
    latest = date(today.year + 1, today.month, min(today.day, 28))
    for key, item in sorted(fields.items()):
        spec = FIELD_BY_KEY.get(key)
        critical = spec is not None and spec.critical
        if item.parse_error:
            issues.append(
                _issue("type", f"{key}: {item.raw_text!r} is {item.parse_error}", (key,), critical)
            )
            continue
        bounds = ranges.get(key)
        if bounds is not None and item.value_num is not None:
            low, high = bounds.low, bounds.high
            if (low is not None and item.value_num < low) or (
                high is not None and item.value_num > high
            ):
                issues.append(
                    _issue(
                        "range",
                        f"{key} = {item.value_num} is outside [{low}, {high}]",
                        (key,),
                        critical,
                    )
                )
        if spec is not None and spec.type == "percent" and item.value_num is not None:
            if not Decimal(0) <= item.value_num <= Decimal(100):
                issues.append(
                    _issue(
                        "range", f"{key} = {item.value_num}% is not a percentage", (key,), critical
                    )
                )
        if item.value_date is not None and not EARLIEST_DATE <= item.value_date <= latest:
            issues.append(
                _issue("range", f"{key} = {item.value_date} is not plausible", (key,), critical)
            )

    rooms = _num(fields, "improvements.total_rooms")
    beds = _num(fields, "improvements.bedrooms")
    baths = _num(fields, "improvements.bathrooms")
    if rooms is not None and beds is not None and baths is not None and rooms < beds + baths:
        issues.append(
            _issue(
                "rooms_vs_bed_bath",
                f"total rooms {rooms} < bedrooms {beds} + bathrooms {baths}",
                ("improvements.total_rooms", "improvements.bedrooms", "improvements.bathrooms"),
                False,
            )
        )

    value = _num(fields, "reconciliation.final_market_value")
    cost = _num(fields, "reconciliation.indicated_value_cost_approach")
    if cost is None:
        cost = _num(fields, "cost_approach.indicated_value")
    if value is not None and cost is not None and cost > 0:
        if abs(value - cost) / cost > Decimal("0.5"):
            issues.append(
                _issue(
                    "value_vs_cost",
                    f"final market value {value} is more than 50% from the cost approach {cost}",
                    ("reconciliation.final_market_value",),
                    True,
                )
            )

    contract = _num(fields, "listing_and_contract.contract_price")
    if value is not None and contract is not None and contract > 0:
        if abs(value - contract) / contract > Decimal("0.2"):
            issues.append(
                ValidationIssue(
                    rule="value_vs_contract",
                    message=f"final market value {value} differs from the contract price "
                    f"{contract} by more than 20%",
                    keys=("reconciliation.final_market_value",),
                    severity="warning",
                )
            )

    gla = _num(fields, "improvements.gla_sqft")
    basement = _num(fields, "improvements.basement_area_sqft")
    if gla is not None and basement is not None and gla > 0 and basement > gla * Decimal("1.5"):
        issues.append(
            ValidationIssue(
                rule="basement_vs_gla",
                message=f"basement {basement} sq ft is more than 1.5 x GLA {gla} sq ft",
                keys=("improvements.basement_area_sqft",),
                severity="warning",
            )
        )
    return issues


def failed_critical_keys(
    fields: Mapping[str, ExtractedField], issues: list[ValidationIssue]
) -> list[str]:
    """Critical fields that are missing or failed a per-field rule (the fallback's input).

    Cross-field rules are not included: asking a model to re-read one side of a
    disagreement between two printed values does not resolve it; a person does.
    """
    failed = {
        key
        for issue in issues
        if issue.rule in PER_FIELD_RULES
        for key in issue.keys
        if key in CRITICAL_FIELDS
    }
    for key in CRITICAL_FIELDS:
        item = fields.get(key)
        if item is None or item.is_empty:
            failed.add(key)
    return [key for key in CRITICAL_FIELDS if key in failed]
