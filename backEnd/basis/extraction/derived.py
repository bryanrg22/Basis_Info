"""Fields derived from another field's printed value (EVAL_REPORT section 26).

The URAR's site Area box holds one printed value ("1.39 ac", "60,548 sf"). The catalogue
keeps three views of it, all from the same reading:

* ``site.area_sqft``: square feet, converted from acres when printed in acres (the
  cleaner, :func:`basis.extraction.cleaners.clean_site_area`);
* ``site.area``: the value as printed, unit included (what the reviewer compares with
  the form);
* ``site.area_acres``: acres, only when the box is printed in acres.

:func:`derive_fields` fills the two derived keys from whichever reading exists (Azure DI,
regex, table or the model fallback): ``site.area_sqft`` first, else a ``site.area`` the
model read, in which case ``site.area_sqft`` is derived from it too. A derived field has
the reading's source, page and confidence and is never asked of the model on its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from basis.extraction.cleaners import clean_number, printed_in_acres
from basis.extraction.results import Candidate, ExtractedField

AREA_SQFT: Final = "site.area_sqft"
AREA_TEXT: Final = "site.area"
AREA_ACRES: Final = "site.area_acres"
DERIVED_KEYS: Final = frozenset({AREA_TEXT, AREA_ACRES})


def _from(reading: ExtractedField, key: str, raw: str) -> ExtractedField:
    return ExtractedField.from_candidate(
        Candidate(
            key=key,
            raw_text=raw,
            page=reading.page,
            confidence=reading.confidence,
            source=reading.source,
        )
    )


def derive_fields(fields: Mapping[str, ExtractedField]) -> dict[str, ExtractedField]:
    """The derived fields missing from ``fields`` (an empty dict when there are none)."""
    reading = fields.get(AREA_SQFT)
    if reading is None or reading.is_empty or not reading.raw_text:
        reading = fields.get(AREA_TEXT)
    if reading is None or reading.is_empty or not reading.raw_text:
        return {}
    raw = reading.raw_text.strip()
    derived: dict[str, ExtractedField] = {}
    for key in (AREA_SQFT, AREA_TEXT):
        current = fields.get(key)
        if current is None or current.is_empty:
            derived[key] = _from(reading, key, raw)
    current = fields.get(AREA_ACRES)
    if (current is None or current.is_empty) and printed_in_acres(raw):
        acres = clean_number(raw)
        if acres is not None:
            derived[AREA_ACRES] = _from(reading, AREA_ACRES, raw)
    return {key: item for key, item in derived.items() if not item.parse_error}
