"""Arithmetic and cross-box validators over extracted fields (research section 5, stage 4).

Model-independent checks of figures the appraiser prints twice or derives from each other.
Rule names match the client truth's ``expected_issues`` where they correspond. Numbers agree
within :func:`close`: ``|a - b| <= max($1, 0.5 % of b)`` (rounding of a printed rate or
total; research: "±$1 or ±0.5%"). A rule runs only when every figure it needs was read; a
box read as text ("N/A") has no number and skips it.

* ``cost_row_arithmetic``: per quantity row of the cost approach (dwelling, basement,
  garage/carport): ``sq ft x $/sq ft = row cost``.
* ``cost_new_sum``: the row costs (dwelling, basement, garage/carport, the
  mechanicals/misc. row) add up to the total estimate of cost-new. Runs when the dwelling row
  and the total were read; a document with a row the catalogue does not carry raises it
  (counted as a false alarm by the scorer).
* ``cost_depreciated_arithmetic``: total cost-new minus depreciation (the "Total
  Depreciation" box, else physical + functional + external) = depreciated cost of
  improvements. Depreciation is taken as a magnitude: the form prints it as a subtraction,
  often in parentheses (``=$( 156,240)``), which the production cleaner stores negative.
* ``cost_approach_total``: depreciated cost of improvements (else total - depreciation)
  + "as-is" value of site improvements + site value = indicated value by cost approach.
* ``reconciliation_cost_mismatch``: the reconciliation's indicated value by cost approach =
  the cost section's indicated value.
* ``final_value_mismatch``: the reconciliation's final opinion of market value = the
  certification's appraised value; ``effective_date_mismatch``: the two effective dates.
* ``gla_vs_sketch``: the improvements GLA = every other GLA the document prints: lines that
  start with "Gross Living Area", "Living Area", "Total Living Area" or "GLA" (the sales
  grid's subject column is the first figure on its row; sketch and area-calculation pages),
  first figure on the line, >= 100 sq ft, within 1 sq ft (sketches print decimals).
* ``checkbox_multiple_selected``: a single-choice checkbox key (:data:`SINGLE_CHECKBOX_KEYS`)
  whose value names more than one option.

Guards for reading defects measured on the live baselines (2026-10-26):

* ``concatenated_cells``: a money box (catalogue type currency, or any cost-approach amount)
  whose read text holds more than one figure: an Azure DI key-value value that joined a
  row's cells ("130,708 0 0 =$( 130,708)", which the cleaner glues into 13,070,800;
  "2,071 Sq. Ft. @ $ 194.95 =$ 403,741", whose first figure 2,071 became the dwelling cost).
* ``implausible_amount``: a money box more than :data:`IMPLAUSIBLE_FACTOR` times the
  document's headline value (the median of the final market value, the certification's
  appraised value, the contract price and the sales-comparison indication that were read; a
  median so one glued headline cannot move it). Glued digits multiply a figure by 100 or more.
* ``effective_date_vs_signature``: the effective date of the appraisal (reconciliation, else
  certification) after the signature date, or more than 365 days before it (the cost data's
  effective date read as the appraisal's is months to years old).

Each issue names every key it compares (the scorer flags them for review; a re-ask re-reads
them).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Final

from basis.extraction.field_mappings import FIELD_BY_KEY
from basis.extraction.results import ExtractedField, ValidationIssue
from evals.appraisal.systems.base import SINGLE_CHECKBOX_KEYS, value_parts

ABSOLUTE: Final = Decimal(1)
RELATIVE: Final = Decimal("0.005")
GLA_TOLERANCE: Final = Decimal(1)
MIN_GLA: Final = Decimal(100)
IMPLAUSIBLE_FACTOR: Final = Decimal(5)
MAX_EFFECTIVE_AGE_DAYS: Final = 365
HEADLINE_KEYS: Final = (
    "reconciliation.final_market_value",
    "appraiser.appraised_value",
    "listing_and_contract.contract_price",
    "reconciliation.indicated_value_sales_comparison",
)
_FIGURE: Final = re.compile(r"\d[\d,]*(?:\.\d+)?")

ROWS: Final = (
    (
        "cost_approach.dwelling_sqft",
        "cost_approach.dwelling_cost_per_sqft",
        "cost_approach.dwelling_cost",
    ),
    (
        "cost_approach.basement_sqft",
        "cost_approach.basement_cost_per_sqft",
        "cost_approach.basement_cost",
    ),
    (
        "cost_approach.garage_sqft",
        "cost_approach.garage_cost_per_sqft",
        "cost_approach.garage_cost",
    ),
)
LINE_KEYS: Final = (
    "cost_approach.dwelling_cost",
    "cost_approach.basement_cost",
    "cost_approach.garage_cost",
    "cost_approach.mechanicals_misc",
)
DEPRECIATION_PARTS: Final = (
    "cost_approach.depreciation_physical",
    "cost_approach.depreciation_functional",
    "cost_approach.depreciation_external",
)
VALIDATOR_RULES: Final = frozenset(
    {
        "cost_row_arithmetic",
        "cost_new_sum",
        "cost_depreciated_arithmetic",
        "cost_approach_total",
        "reconciliation_cost_mismatch",
        "final_value_mismatch",
        "effective_date_mismatch",
        "gla_vs_sketch",
        "checkbox_multiple_selected",
        "concatenated_cells",
        "implausible_amount",
        "effective_date_vs_signature",
    }
)
_GLA_LINE: Final = re.compile(
    r"^\s*(?:total\s+)?(?:(?:gross\s+)?living\s+area|gla)\b[^\d\n]{0,40}?(\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)


def close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= max(ABSOLUTE, abs(b) * RELATIVE)


def _num(fields: Mapping[str, ExtractedField], key: str) -> Decimal | None:
    item = fields.get(key)
    return None if item is None else item.value_num


def _issue(rule: str, message: str, keys: Sequence[str]) -> ValidationIssue:
    return ValidationIssue(rule=rule, message=message, keys=tuple(keys), severity="error")


def cost_issues(fields: Mapping[str, ExtractedField]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for sqft_key, rate_key, cost_key in ROWS:
        sqft, rate, cost = _num(fields, sqft_key), _num(fields, rate_key), _num(fields, cost_key)
        if (
            sqft is not None
            and rate is not None
            and cost is not None
            and not close(sqft * rate, cost)
        ):
            issues.append(
                _issue(
                    "cost_row_arithmetic",
                    f"{cost_key}: {sqft} sq ft x ${rate} = {sqft * rate}, printed {cost}",
                    (cost_key, sqft_key, rate_key),
                )
            )
    total = _num(fields, "cost_approach.total_cost_new")
    lines = {k: v for k in LINE_KEYS if (v := _num(fields, k)) is not None}
    if total is not None and "cost_approach.dwelling_cost" in lines:
        summed = sum(lines.values(), Decimal(0))
        if not close(summed, total):
            issues.append(
                _issue(
                    "cost_new_sum",
                    f"cost lines add up to {summed}, total cost-new printed {total}",
                    ("cost_approach.total_cost_new", *lines),
                )
            )
    depreciation = _num(fields, "cost_approach.depreciation")
    depreciation = abs(depreciation) if depreciation is not None else None
    depreciation_keys: tuple[str, ...] = ("cost_approach.depreciation",)
    if depreciation is None:
        parts = {k: abs(v) for k in DEPRECIATION_PARTS if (v := _num(fields, k)) is not None}
        if parts:
            depreciation = sum(parts.values(), Decimal(0))
            depreciation_keys = tuple(parts)
    depreciated = _num(fields, "cost_approach.depreciated_cost_improvements")
    if total is not None and depreciation is not None and depreciated is not None:
        if not close(total - depreciation, depreciated):
            issues.append(
                _issue(
                    "cost_depreciated_arithmetic",
                    f"{total} - {depreciation} = {total - depreciation}, depreciated cost "
                    f"printed {depreciated}",
                    (
                        "cost_approach.depreciated_cost_improvements",
                        "cost_approach.total_cost_new",
                        *depreciation_keys,
                    ),
                )
            )
    indicated = _num(fields, "cost_approach.indicated_value")
    site = _num(fields, "cost_approach.site_value")
    improvements = _num(fields, "cost_approach.site_improvements_value")
    base_keys: tuple[str, ...] = ("cost_approach.depreciated_cost_improvements",)
    base = depreciated
    if base is None and total is not None and depreciation is not None:
        base = total - depreciation
        base_keys = ("cost_approach.total_cost_new", *depreciation_keys)
    if None not in (indicated, site, improvements, base):
        assert indicated is not None and site is not None and improvements is not None
        assert base is not None
        expected = base + improvements + site
        if not close(expected, indicated):
            issues.append(
                _issue(
                    "cost_approach_total",
                    f"{base} + {improvements} + {site} = {expected}, indicated value "
                    f"printed {indicated}",
                    (
                        "cost_approach.indicated_value",
                        *base_keys,
                        "cost_approach.site_improvements_value",
                        "cost_approach.site_value",
                    ),
                )
            )
    return issues


def cross_box_issues(fields: Mapping[str, ExtractedField]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for rule, a, b in (
        (
            "reconciliation_cost_mismatch",
            "reconciliation.indicated_value_cost_approach",
            "cost_approach.indicated_value",
        ),
        ("final_value_mismatch", "reconciliation.final_market_value", "appraiser.appraised_value"),
    ):
        x, y = _num(fields, a), _num(fields, b)
        if x is not None and y is not None and not close(x, y):
            issues.append(_issue(rule, f"{a} = {x}, {b} = {y}", (a, b)))
    first = fields.get("reconciliation.effective_date_of_appraisal")
    second = fields.get("appraiser.effective_date")
    if (
        first is not None
        and second is not None
        and first.value_date is not None
        and second.value_date is not None
        and first.value_date != second.value_date
    ):
        issues.append(
            _issue(
                "effective_date_mismatch",
                f"reconciliation {first.value_date}, certification {second.value_date}",
                ("reconciliation.effective_date_of_appraisal", "appraiser.effective_date"),
            )
        )
    for key in sorted(SINGLE_CHECKBOX_KEYS):
        item = fields.get(key)
        if item is not None and len(value_parts(item.value_text)) > 1:
            issues.append(
                _issue(
                    "checkbox_multiple_selected",
                    f"{key}: more than one box is read as checked ({item.value_text!r})",
                    (key,),
                )
            )
    return issues


def gla_mentions(pages: Sequence[str]) -> list[tuple[int, Decimal]]:
    """``(page, sq ft)`` of each line that starts with a GLA caption (module doc)."""
    found: list[tuple[int, Decimal]] = []
    for number, text in enumerate(pages, start=1):
        for line in text.splitlines():
            match = _GLA_LINE.match(line)
            if match is None:
                continue
            value = Decimal(match.group(1).replace(",", ""))
            if value >= MIN_GLA:
                found.append((number, value))
    return found


def gla_issues(fields: Mapping[str, ExtractedField], pages: Sequence[str]) -> list[ValidationIssue]:
    gla = _num(fields, "improvements.gla_sqft")
    if gla is None:
        return []
    differing = [(p, v) for p, v in gla_mentions(pages) if abs(v - gla) > GLA_TOLERANCE]
    if not differing:
        return []
    listed = ", ".join(f"{v} on page {p}" for p, v in differing[:5])
    return [
        _issue(
            "gla_vs_sketch",
            f"improvements GLA {gla} sq ft; the document also prints {listed}",
            ("improvements.gla_sqft",),
        )
    ]


def money_keys() -> list[str]:
    """Catalogue keys holding a dollar amount: currency types and the cost-approach amounts."""
    return [
        key
        for key, spec in FIELD_BY_KEY.items()
        if spec.type in ("currency", "currency_or_text")
        or (
            key.startswith("cost_approach.")
            and spec.type in ("currency", "float", "int")
            and not key.endswith(("_sqft", "remaining_economic_life"))  # areas, rates, years
        )
    ]


def reading_issues(fields: Mapping[str, ExtractedField]) -> list[ValidationIssue]:
    """``concatenated_cells``, ``implausible_amount``, ``effective_date_vs_signature``."""
    issues: list[ValidationIssue] = []
    money = money_keys()
    for key in money:
        item = fields.get(key)
        if item is not None and item.raw_text and len(_FIGURE.findall(item.raw_text)) > 1:
            issues.append(
                _issue(
                    "concatenated_cells",
                    f"{key}: the box read holds several figures ({item.raw_text!r})",
                    (key,),
                )
            )
    headline = sorted(abs(v) for k in HEADLINE_KEYS if (v := _num(fields, k)) is not None and v)
    if headline:
        reference = headline[(len(headline) - 1) // 2]
        for key in money:
            value = _num(fields, key)
            if value is not None and abs(value) > reference * IMPLAUSIBLE_FACTOR:
                issues.append(
                    _issue(
                        "implausible_amount",
                        f"{key} = {value} is more than {IMPLAUSIBLE_FACTOR} x the headline "
                        f"value {reference}",
                        (key,),
                    )
                )
    effective_key = "reconciliation.effective_date_of_appraisal"
    effective = fields.get(effective_key)
    if effective is None or effective.value_date is None:
        effective_key = "appraiser.effective_date"
        effective = fields.get(effective_key)
    signed = fields.get("appraiser.signature_date")
    if (
        effective is not None
        and effective.value_date is not None
        and signed is not None
        and signed.value_date is not None
    ):
        days = (signed.value_date - effective.value_date).days
        if days < 0 or days > MAX_EFFECTIVE_AGE_DAYS:
            issues.append(
                _issue(
                    "effective_date_vs_signature",
                    f"effective date {effective.value_date}, signed {signed.value_date}",
                    (effective_key, "appraiser.signature_date"),
                )
            )
    return issues


def validate(fields: Mapping[str, ExtractedField], pages: Sequence[str]) -> list[ValidationIssue]:
    """Every validator issue for ``fields`` (``pages``: the page texts, for GLA mentions)."""
    return [
        *cost_issues(fields),
        *cross_box_issues(fields),
        *gla_issues(fields, pages),
        *reading_issues(fields),
    ]
