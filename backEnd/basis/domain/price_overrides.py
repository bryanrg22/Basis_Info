"""The engineer's unit price (EVAL_REPORT 90, docs/api/OVERRIDES.md).

An ``asset`` correction (assets review) or a ``component`` correction (components review)
may carry ``unit_cost``, the engineer's price in dollars per takeoff unit, with
``unit_cost_source`` (where the price comes from: a contractor's quote, an invoice, a
published guide's line) and the correction's ``reason``. ``unit_cost: null`` clears an
earlier override. The cost segregation guide's detailed estimate approach takes its costs
"from contractor estimates or estimating guides" with "the sources of estimating data ...
clearly referenced" (IRS Publication 5653, Rev. 2-2025, ch. 3 C.2, p.20; ch. 3 D, p.22: "the
methodology and the source of any cost data should be clearly documented"), so a price
without a source is refused, and the cost table's price it replaces is kept beside it.

Pure, so the decision route (a 422 before anything is recorded) and the activities (a
``ValidationError``) check the same rules:

* ``unit_cost``: a JSON number (not a boolean, not a string) above 0 and at most
  :data:`MAX_UNIT_COST`, in dollars to the cent (at most two decimals: the price used is
  the price entered, never rounded), or null to clear;
* ``unit_cost_source``: 1 to :data:`MAX_SOURCE_CHARS` characters, not blank, required with a
  number; absent or null when clearing;
* the correction's ``reason``: required, not blank.

Where an override lives: on the component row it prices (``components.raw``), which every
later revision copies, so the price holds through every rejection, reopen and restore that
carries the component until an engineer clears it. :data:`COMPONENT_KEY` holds a component
correction's override, :data:`ASSET_KEY` an asset correction's; an asset's own override
wins over its component's (:func:`active`). Each is a :func:`record`: the price as a string,
the source, the reason, who (id and display name at the time), when, the correction row and
the revision it first priced.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final, Literal

from basis.domain.costs import CENTS, CostEstimate
from basis.domain.takeoffs import Takeoff

UNIT_COST: Final = "unit_cost"
UNIT_COST_SOURCE: Final = "unit_cost_source"
FIELDS: Final = frozenset({UNIT_COST, UNIT_COST_SOURCE})
MAX_UNIT_COST: Final = Decimal(10_000_000)
MAX_SOURCE_CHARS: Final = 300
# Where the override is kept on the component row (``components.raw``).
COMPONENT_KEY: Final = "unit_cost_override"
ASSET_KEY: Final = "asset_unit_cost_override"
Level = Literal["asset", "component"]
KEYS: Final[dict[str, str]] = {"asset": ASSET_KEY, "component": COMPONENT_KEY}


def has_price(after: Mapping[str, Any]) -> bool:
    """Whether the correction's ``after`` sets or clears a unit price."""
    return bool(FIELDS & set(after))


def split(after: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(everything else, the price fields)`` of a correction's ``after``."""
    rest = {k: v for k, v in after.items() if k not in FIELDS}
    price = {k: v for k, v in after.items() if k in FIELDS}
    return rest, price


def _amount(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:  # pragma: no cover - str() of an int or float always parses
        return None
    return number if number.is_finite() else None


def problems(after: Mapping[str, Any], reason: str | None) -> list[str]:
    """Why the price in ``after`` cannot be applied (empty when it can, or when ``after``
    carries no price)."""
    if not has_price(after):
        return []
    found: list[str] = []
    if UNIT_COST not in after:
        return [f"{UNIT_COST_SOURCE} goes with a {UNIT_COST}"]
    value = after[UNIT_COST]
    source = after.get(UNIT_COST_SOURCE)
    if value is None:
        if source is not None:
            found.append(f"{UNIT_COST_SOURCE} goes with a price; clearing takes none")
    else:
        number = _amount(value)
        if number is None or number <= 0 or number > MAX_UNIT_COST:
            found.append(
                f"{UNIT_COST} must be a number above 0 and at most {MAX_UNIT_COST:,} (dollars "
                f"per takeoff unit), or null to clear an override; got {value!r}"
            )
        elif number.as_tuple().exponent < -2:  # type: ignore[operator]
            found.append(
                f"{UNIT_COST} is dollars to the cent (at most two decimals); got {value!r}"
            )
        if not isinstance(source, str) or not source.strip() or len(source) > MAX_SOURCE_CHARS:
            found.append(
                f"{UNIT_COST_SOURCE} must say where the price comes from (1 to "
                f"{MAX_SOURCE_CHARS} characters)"
            )
    if reason is None or not reason.strip():
        found.append("a unit price override needs the correction's reason")
    return found


def parse(after: Mapping[str, Any]) -> Decimal | None:
    """The price of an ``after`` that :func:`problems` accepts (None: clear the override)."""
    value = after.get(UNIT_COST)
    return None if value is None else Decimal(str(value))


def record(
    after: Mapping[str, Any],
    reason: str,
    *,
    level: Level,
    by: uuid.UUID | None,
    by_name: str | None,
    at: datetime,
    correction_id: uuid.UUID | None,
    revision: int,
) -> dict[str, Any] | None:
    """The override as stored on the component row, or None for a clearing correction."""
    price = parse(after)
    if price is None:
        return None
    return {
        UNIT_COST: format(price, "f"),
        "source": str(after[UNIT_COST_SOURCE]).strip(),
        "reason": reason.strip(),
        "by": None if by is None else str(by),
        "by_name": by_name,
        "at": at.isoformat(),
        "level": level,
        "correction_id": None if correction_id is None else str(correction_id),
        "revision": revision,
    }


def active(raw: Any) -> dict[str, Any] | None:
    """The override that prices a component row: the asset's own, else the component's;
    None when neither is set."""
    if not isinstance(raw, Mapping):
        return None
    for key in (ASSET_KEY, COMPONENT_KEY):
        found = raw.get(key)
        if isinstance(found, Mapping) and found.get(UNIT_COST) is not None:
            return dict(found)
    return None


def unit_cost_of(override: Mapping[str, Any]) -> Decimal:
    return Decimal(str(override[UNIT_COST]))


def source_text(override: Mapping[str, Any], unit: str, replaced: str) -> str:
    """The cost row's ``source_row`` for an engineer's price: the price, its source, who set
    it and when, and the table pricing it replaced (``replaced``, that row's own text)."""
    who = override.get("by_name") or override.get("by") or "the engineer"
    when = str(override.get("at") or "")[:10]
    return (
        f"engineer's unit price {unit_cost_of(override)} per {unit}: {override.get('source')} "
        f"(set by {who}{' on ' + when if when else ''}; reason: {override.get('reason')}); "
        f"replaces: {replaced}"
    )


def estimate(
    takeoff: Takeoff, override: Mapping[str, Any], *, cost_year: int, replaced: str
) -> CostEstimate:
    """The row priced at the engineer's unit price: ``total = unit_cost x quantity``, half
    up to the cent, no factor applied (``year_factor`` and ``location_factor`` 1: the
    estimate carries no adjustment). ``replaced`` is the table's ``source_row`` text."""
    unit_cost = unit_cost_of(override)
    return CostEstimate(
        unit_cost=unit_cost,
        total=(unit_cost * takeoff.quantity).quantize(CENTS, rounding=ROUND_HALF_UP),
        source_row=source_text(override, takeoff.unit, replaced),
        base_unit_cost=unit_cost,
        year_factor=Decimal(1),
        location_factor=Decimal(1),
        cost_year=cost_year,
        unit=takeoff.unit,
    )


__all__ = [
    "ASSET_KEY",
    "COMPONENT_KEY",
    "FIELDS",
    "KEYS",
    "MAX_SOURCE_CHARS",
    "MAX_UNIT_COST",
    "UNIT_COST",
    "UNIT_COST_SOURCE",
    "Level",
    "active",
    "estimate",
    "has_price",
    "parse",
    "problems",
    "record",
    "source_text",
    "split",
    "unit_cost_of",
]
