"""Presence rules: values a reader produced for a box the appraiser left undeveloped or the
document does not carry. Applied once, after every reader (the model fallback included), so a
dropped value is never re-asked.

The appraisal eval spec (``evals/appraisal/SPEC.md`` principle 2) separates a box's value
from its absence: a printed ``0``, ``N/A`` or a reason for exclusion is *not developed* only
for a valuation approach or a cost-approach line the appraiser skipped; elsewhere (HOA dues,
special assessments, concessions, basement area, fireplaces) ``$0`` is a real value. The rules
follow that policy, key by key, and never touch a key outside their scope:

* **Approach not developed** (:data:`APPROACH_VALUE_KEYS`, :data:`COST_LINE_KEYS`): the
  indicated value of an approach (sales comparison, cost, income; in the approach's own
  section and in the reconciliation) and the amount and quantity lines of the cost approach.
  A value with no number in it ("Not Necessary for Credible Results", "Not Developed", a bare
  ``$`` or ``=$`` left by an empty box) or a number equal to 0 means the approach or the line
  was not developed: the value is dropped (``not_developed:no_number`` /
  ``not_developed:zero``). A value with a non-zero number is kept, however it is worded. The
  remaining economic life and the cost approach's text boxes (source of cost data, quality
  rating) are not lines: kept.
* **Certification restatements** (:data:`CERTIFICATION_KEYS`): the appraised value and the
  effective date in the appraiser's signature block restate the reconciliation's value and
  date; a reader asked for them on a document without the signature block copies the
  reconciliation's (the client 1004 delivered without its certification pages). They are kept
  only when cited on a page that carries the signature block (:func:`signature_pages`: the
  page prints the block's "Date of Signature" caption, or one of the catalogue's appraiser
  section headings as a line) and only when the document's form carries them (the UAD 3.6
  URAR has no such boxes): otherwise dropped (``absent:not_on_signature_page``,
  ``absent:no_page``, ``absent:not_on_form``).

The result records each dropped key with its reason (``dropped``) so the review page and the
eval can tell a rule's drop from a reader's miss.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from basis.extraction.field_mappings import FIELDS, SECTIONS, normalize_heading
from basis.extraction.results import ExtractedField

APPROACH_VALUE_KEYS: Final = frozenset(
    {
        "sales_comparison.indicated_value",
        "reconciliation.indicated_value_sales_comparison",
        "reconciliation.indicated_value_cost_approach",
        "reconciliation.indicated_value_income_approach",
        "cost_approach.indicated_value",
    }
)
# The cost approach's amount and quantity lines; not the remaining economic life (a property
# of the dwelling, not a line of the cost build-up) nor the text boxes.
_NUMERIC_TYPES: Final = frozenset({"currency", "currency_or_text", "int", "float"})
COST_LINE_KEYS: Final = frozenset(
    spec.key
    for spec in FIELDS
    if spec.section == "cost_approach"
    and spec.type in _NUMERIC_TYPES
    and spec.key != "cost_approach.remaining_economic_life"
)
NOT_DEVELOPED_KEYS: Final = APPROACH_VALUE_KEYS | COST_LINE_KEYS
CERTIFICATION_KEYS: Final = frozenset({"appraiser.appraised_value", "appraiser.effective_date"})

_DIGIT: Final = re.compile(r"\d")
# The signature block's caption: "Date of Signature and Report" (1004 / Form 70 page 6),
# "Date of Signature" (the supervisory appraiser's, and the UAD 3.6 Certifications section).
_SIGNATURE_CAPTION: Final = re.compile(r"\bdate\s+of\s+signature\b", re.IGNORECASE)
_APPRAISER_HEADINGS: Final = frozenset(
    normalize_heading(heading)
    for name, headings in SECTIONS
    if name == "appraiser"
    for heading in headings
)


@dataclass
class PresenceOutcome:
    """The fields after the rules, and why each dropped key was dropped."""

    fields: dict[str, ExtractedField]
    dropped: dict[str, str] = field(default_factory=dict)


def not_developed_reason(item: ExtractedField) -> str | None:
    """Why ``item`` (a key of :data:`NOT_DEVELOPED_KEYS`) is not a developed value, or
    ``None`` when it is one (module doc)."""
    if item.value_num is not None:
        return "zero" if item.value_num == 0 else None
    text = item.value_text if item.value_text is not None else item.raw_text or ""
    return None if _DIGIT.search(text) else "no_number"


def signature_pages(*sources: Sequence[str]) -> frozenset[int]:
    """1-based pages that carry the appraiser's signature block in any of the page-text
    ``sources`` (the text layer, Azure DI's page text): the block's "Date of Signature"
    caption, or a line that is one of the catalogue's appraiser section headings."""
    pages: set[int] = set()
    for texts in sources:
        for number, text in enumerate(texts, start=1):
            if _SIGNATURE_CAPTION.search(text) or any(
                normalize_heading(line) in _APPRAISER_HEADINGS
                for line in text.splitlines()
                if line.strip()
            ):
                pages.add(number)
    return frozenset(pages)


def apply_presence_rules(
    fields: Mapping[str, ExtractedField],
    pages: Sequence[Sequence[str]],
    *,
    not_on_form: frozenset[str] = frozenset(),
) -> PresenceOutcome:
    """``fields`` less the values the presence rules drop (module doc). ``pages``: the
    document's page texts, one sequence per reader (the citation's page numbers index them);
    ``not_on_form``: catalogue keys the document's form does not carry."""
    kept: dict[str, ExtractedField] = {}
    dropped: dict[str, str] = {}
    signed: frozenset[int] | None = None
    for key, item in fields.items():
        reason: str | None = None
        if item.is_empty:  # nothing shown (an "N/A" the cleaner reads as no value)
            kept[key] = item
            continue
        if key in NOT_DEVELOPED_KEYS:
            why = not_developed_reason(item)
            reason = f"not_developed:{why}" if why else None
        elif key in CERTIFICATION_KEYS:
            if key in not_on_form:
                reason = "absent:not_on_form"
            elif item.page is None:
                reason = "absent:no_page"
            else:
                if signed is None:
                    signed = signature_pages(*pages)
                if item.page not in signed:
                    reason = "absent:not_on_signature_page"
        if reason is None:
            kept[key] = item
        else:
            dropped[key] = reason
    return PresenceOutcome(kept, dropped)
