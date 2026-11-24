"""A component's own age: the seller's renovation before the sale (RENOVATIONS.md section 4.1).

The structure model depreciates every row at one rate, the appraiser's for the improvements as
a whole (:func:`basis.domain.structure.depreciation`). A kitchen redone two years before the
sale is not as worn as the original structure, and the cost segregation guide asks for the
difference: "a quality study always accounts for the physical deterioration and functional
obsolescence of assets" (IRS Publication 5653, rev. 2-2025, p.38), and "The value of used
components must be reduced from the replacement cost new value in proportion to the observed
economic obsolescence or physical depreciation as compared to similar new assets" (p.67).

An ``asset`` correction at the assets review may carry the row's age (the engineer's
judgement; docs/api/OVERRIDES.md section 7):

* ``effective_age`` (years, 0 or more, at most two decimals) **or** ``year_installed`` (a
  calendar year): exactly one of them;
* ``economic_life`` (years, above 0): the row's total economic life; the effective age must be
  below it, so the age-life ratio is below 1;
* ``age_source``: where the age comes from, one of :data:`SOURCES`;
* ``age_source_detail``: the document, view or statement it rests on (1 to 300 characters);
* the correction's ``reason``: required.

``effective_age: null`` (or ``year_installed: null``) alone clears it. The record is kept on the
row's takeoff (``takeoffs.inputs["component_age"]``) and carried into every later revision with
the row's classification (``basis.activities.process_assets``), like the engineer's other
decisions on a line.

The row's physical depreciation becomes the age-life ratio on its own life (the guide's
age-life method, the one :func:`basis.domain.structure.depreciation` already uses for the
building as a whole); every row without an age keeps the appraiser's rate. A year installed is
turned into an age as of the year the acquisition was placed in service.

The UAD 3.6 appraisal records, per room, an update status and the time frame of the update
(``RoomUpdateStatusType``, ``RoomUpdatedTimeframeType``; GSE URAR Delivery Specification
Appendix A-1, unique IDs 0700.0036 and 0700.0034). :func:`suggestion` turns them into a range
of effective ages: a suggestion for the engineer, never applied by itself.

Pure: ``Decimal`` end to end, no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any, Final, Literal

EFFECTIVE_AGE: Final = "effective_age"
YEAR_INSTALLED: Final = "year_installed"
ECONOMIC_LIFE: Final = "economic_life"
AGE_SOURCE: Final = "age_source"
AGE_SOURCE_DETAIL: Final = "age_source_detail"
FIELDS: Final = frozenset(
    {EFFECTIVE_AGE, YEAR_INSTALLED, ECONOMIC_LIFE, AGE_SOURCE, AGE_SOURCE_DETAIL}
)
# Where the age is kept on the row (``takeoffs.inputs``).
INPUT_KEY: Final = "component_age"
MAX_YEARS: Final = Decimal(200)
MIN_YEAR: Final = 1800
MAX_YEAR: Final = 2200
MAX_DETAIL_CHARS: Final = 300
RATE_PLACES: Final = Decimal("0.000001")
AgeSource = Literal[
    "engineer_inspection",
    "appraisal_update_status",
    "aerial_history",
    "permit",
    "invoice",
    "taxpayer_statement",
]
SOURCES: Final[tuple[str, ...]] = (
    "engineer_inspection",
    "appraisal_update_status",
    "aerial_history",
    "permit",
    "invoice",
    "taxpayer_statement",
)


def has_age(after: Mapping[str, Any]) -> bool:
    """Whether the correction's ``after`` sets or clears a row's age."""
    return bool(FIELDS & set(after))


def split(after: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(everything else, the age fields)`` of a correction's ``after``."""
    return (
        {k: v for k, v in after.items() if k not in FIELDS},
        {k: v for k, v in after.items() if k in FIELDS},
    )


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _clearing(after: Mapping[str, Any]) -> bool:
    given = FIELDS & set(after)
    return (
        bool(given)
        and given <= {EFFECTIVE_AGE, YEAR_INSTALLED}
        and all(after[k] is None for k in given)
    )


def problems(after: Mapping[str, Any], reason: str | None) -> list[str]:
    """Why the age in ``after`` cannot be applied (empty when it can, or when ``after``
    carries none)."""
    if not has_age(after):
        return []
    found: list[str] = []
    if reason is None or not reason.strip():
        found.append("a component age needs the correction's reason")
    if _clearing(after):
        return found
    age = after.get(EFFECTIVE_AGE)
    year = after.get(YEAR_INSTALLED)
    if (age is None) == (year is None):
        found.append(
            f"give exactly one of {EFFECTIVE_AGE} and {YEAR_INSTALLED} (or {EFFECTIVE_AGE}: null "
            "alone to clear the age)"
        )
    age_value: Decimal | None = None
    if age is not None:
        age_value = _number(age) if not isinstance(age, str) else None
        if age_value is None or age_value < 0 or age_value >= MAX_YEARS:
            found.append(f"{EFFECTIVE_AGE} must be a number of years from 0 to below 200")
            age_value = None
        elif age_value.as_tuple().exponent < -2:  # type: ignore[operator]
            found.append(f"{EFFECTIVE_AGE} takes at most two decimals")
    if year is not None and (
        isinstance(year, bool) or not isinstance(year, int) or not MIN_YEAR <= year <= MAX_YEAR
    ):
        found.append(f"{YEAR_INSTALLED} must be a whole calendar year ({MIN_YEAR}-{MAX_YEAR})")
    life_raw = after.get(ECONOMIC_LIFE)
    life = None if isinstance(life_raw, str) else _number(life_raw)
    if life is None or life <= 0 or life > MAX_YEARS:
        found.append(f"{ECONOMIC_LIFE} must be a number of years above 0 and at most 200")
        life = None
    elif life.as_tuple().exponent < -2:  # type: ignore[operator]
        found.append(f"{ECONOMIC_LIFE} takes at most two decimals")
    if age_value is not None and life is not None and age_value >= life:
        found.append(
            f"{EFFECTIVE_AGE} {age_value} is not below {ECONOMIC_LIFE} {life}: a component at "
            "or past the end of its life has no age-life ratio below 1"
        )
    if after.get(AGE_SOURCE) not in SOURCES:
        found.append(f"{AGE_SOURCE} must be one of {', '.join(SOURCES)}")
    detail = after.get(AGE_SOURCE_DETAIL)
    if not isinstance(detail, str) or not detail.strip() or len(detail) > MAX_DETAIL_CHARS:
        found.append(
            f"{AGE_SOURCE_DETAIL} must say what the age rests on (1 to {MAX_DETAIL_CHARS} "
            "characters)"
        )
    return found


def record(
    after: Mapping[str, Any], reason: str, *, from_asset_id: str, revision: int
) -> dict[str, Any] | None:
    """The age as stored on the row's takeoff, or None for a clearing correction. ``after``
    must pass :func:`problems`."""
    if _clearing(after):
        return None
    age = after.get(EFFECTIVE_AGE)
    year = after.get(YEAR_INSTALLED)
    return {
        EFFECTIVE_AGE: None if age is None else format(Decimal(str(age)), "f"),
        YEAR_INSTALLED: year,
        ECONOMIC_LIFE: format(Decimal(str(after[ECONOMIC_LIFE])), "f"),
        "source": after[AGE_SOURCE],
        "source_detail": str(after[AGE_SOURCE_DETAIL]).strip(),
        "reason": reason.strip(),
        "from_asset_id": from_asset_id,
        "revision": revision,
    }


@dataclass(frozen=True, slots=True)
class ComponentAge:
    """A row's age as the result uses it."""

    effective_age: Decimal | None
    year_installed: int | None
    economic_life: Decimal
    source: str
    source_detail: str
    reason: str

    def age_at(self, as_of_year: int | None) -> Decimal | None:
        """The effective age in years: as given, else the years from installation to
        ``as_of_year`` (the acquisition's placed-in-service year); None without one."""
        if self.effective_age is not None:
            return self.effective_age
        if self.year_installed is None or as_of_year is None:
            return None
        return Decimal(as_of_year - self.year_installed)


def from_inputs(inputs: Any) -> ComponentAge | None:
    """The age recorded on a takeoff's inputs, or None."""
    if not isinstance(inputs, Mapping):
        return None
    found = inputs.get(INPUT_KEY)
    if not isinstance(found, Mapping):
        return None
    age = found.get(EFFECTIVE_AGE)
    year = found.get(YEAR_INSTALLED)
    return ComponentAge(
        effective_age=None if age is None else Decimal(str(age)),
        year_installed=None if year is None else int(year),
        economic_life=Decimal(str(found[ECONOMIC_LIFE])),
        source=str(found.get("source") or ""),
        source_detail=str(found.get("source_detail") or ""),
        reason=str(found.get("reason") or ""),
    )


def age_life_rate(age: Decimal, life: Decimal) -> Decimal:
    """``age / life`` to six places, half up (the structure model's rounding)."""
    with localcontext() as ctx:
        ctx.prec = 34
        return (age / life).quantize(RATE_PLACES, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ the appraisal's suggestion

# A-1 enumerations of RoomUpdatedTimeframeType with their display text ("Less than 1 year",
# "1-5 years", "5-10 years", "10 or more years"), as a range of years since the update.
TIMEFRAME_YEARS: Final[dict[str, tuple[Decimal, Decimal | None]]] = {
    "LessThanOneYear": (Decimal(0), Decimal(1)),
    "OneToFiveYears": (Decimal(1), Decimal(5)),
    "FiveToTenYears": (Decimal(5), Decimal(10)),
    "TenOrMoreYears": (Decimal(10), None),
}
UPDATE_STATUSES: Final = ("FullyUpdated", "PartiallyUpdated", "NotUpdated")


@dataclass(frozen=True, slots=True)
class Suggestion:
    """What the appraisal says about a room's update, for the engineer to judge."""

    room_type: str
    level: str | None
    status: str
    timeframe: str | None
    age_from: Decimal | None
    age_to: Decimal | None
    applies_to: Literal["the room's finishes", "some of the room's components", "none"]
    note: str


def suggestion(room_type: str, level: str | None, status: str, timeframe: str | None) -> Suggestion:
    """The range of effective ages a room's UAD 3.6 update status and time frame suggest.
    ``FullyUpdated`` suggests the age for the room's finishes and fixtures, ``PartiallyUpdated``
    for some of them (which ones is the engineer's, from the photographs), ``NotUpdated``
    none. Without a time frame there is no range: the status alone does not date the work."""
    span = TIMEFRAME_YEARS.get(timeframe or "")
    applies: Literal["the room's finishes", "some of the room's components", "none"]
    if status == "FullyUpdated":
        applies = "the room's finishes"
    elif status == "PartiallyUpdated":
        applies = "some of the room's components"
    else:
        applies = "none"
    if applies == "none":
        note = "not updated: the appraiser's overall rate applies"
    elif span is None:
        note = "updated, with no time frame recorded: ask when the work was done"
    else:
        upper = "or more" if span[1] is None else f"to {span[1]}"
        note = f"updated {span[0]} {upper} years before the appraisal: a suggestion only"
    return Suggestion(
        room_type=room_type,
        level=level,
        status=status,
        timeframe=timeframe,
        age_from=None if span is None or applies == "none" else span[0],
        age_to=None if span is None or applies == "none" else span[1],
        applies_to=applies,
        note=note,
    )


__all__ = [
    "AGE_SOURCE",
    "AGE_SOURCE_DETAIL",
    "ECONOMIC_LIFE",
    "EFFECTIVE_AGE",
    "FIELDS",
    "INPUT_KEY",
    "SOURCES",
    "TIMEFRAME_YEARS",
    "UPDATE_STATUSES",
    "YEAR_INSTALLED",
    "AgeSource",
    "ComponentAge",
    "Suggestion",
    "age_life_rate",
    "from_inputs",
    "has_age",
    "problems",
    "record",
    "split",
    "suggestion",
]
