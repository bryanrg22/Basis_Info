"""The per-asset cost trail: how a cost row's dollars were reached, recorded once at pricing.

A cost segregation firm signs every study, so every dollar must trace to its source. A
cost row's columns (``unit_cost``, ``total``, ``location_factor``, ``cost_year``) and its
``source_row`` sentence say what was priced; the trail (``cost_estimates.trail``,
migration ``0018``) says it as data, from the objects ``process_assets`` priced with:
the quantity and the takeoff inputs it came from, the base unit cost with the cost row it
was read from, the year factor with both Historical Cost Index values (and the store row
each was read from), the location factor with the city row or the state mean and the
table it came from, the intake version and property class, and the rounding rule. A
reader (the assets review, the export) shows the trail; it never derives a base price
from the rounded amounts and never substitutes today's index for the one recorded here.

The trail is written with its cost row and never rewritten (``cost_estimates`` refuses
updates); a row priced before the trail existed has ``trail`` NULL.

Shape (``version`` 1), every Decimal a string exactly as used (``"1.2330"``, never a
float or a re-rounded value), every date ISO 8601::

    {"version": 1,
     "kind": "unit_price" | "unpriced" | "structure_residual",
     "quantity": {"value", "unit", "formula", "method", "inputs", "assumptions"},
     "base": {"unit_cost", "unit", "table", "row", "tier", "source", "base_year",
              "row_unit", "conversion", "assumption"} | null,
     "year": {"cost_year", "factor", "index": INDEX, "base": INDEX, "assumption",
              "conversion", "citation", "from"} | null,
     "location": {"factor", "from", "lookup", "zip", "lookup_from", "state", "city",
                  "zip_prefixes", "cities", "low", "high", "pages", "table_year",
                  "origin", "citation", "store": STORE | null} | null,
     "structure": {"label", "system", "component", "source", "rcn",
                   "building": {"amount", "source", "formula", "inputs",
                                "assumptions"} | null} | null,
     "intake_version", "property_class", "cost_year", "location_factor",
     "unit_cost", "total", "rounding", "priced_per", "missing"}

    INDEX = {"year", "value", "as_of", "citation", "estimated", "derived",
             "store": STORE | null}
    STORE = {"id", "source", "version"}

``kind`` is how the dollars were computed: ``unit_price`` (a cost row's base unit cost x
the year factor x the location factor, x the quantity), ``unpriced`` (no cost row, or a row
in another unit: ``missing`` says why, ``priced_per`` names the row's unit; the year and
location the study would price with are still recorded) or ``structure_residual`` (a
concealed row of the structure model whose total is the residual carved from the
building's replacement cost new: ``base`` null; ``year`` and ``location`` recorded when
the square-foot model applied them, null when the appraiser's cost new was used, whose own
time adjustment is in ``structure.building.inputs``). A small system of the structure
model (fire alarms, data outlets, ...) is priced from the cost table like any label: kind
``unit_price`` or ``unpriced`` with ``structure`` set. ``store`` is null for a value read
from the released book file (or the store's seed of the book's location table, which is
the file's table object).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Final
from uuid import UUID

from basis.domain.cost_indices import LocationFactor, StoreRow, YearFactor, YearIndex
from basis.domain.costs import CENTS, CostEstimate, PricedRow
from basis.domain.measurements import CostIndex
from basis.domain.structure import BuildingRcn, ConcealedRow
from basis.domain.takeoffs import Takeoff

TRAIL_VERSION: Final = 1
UNIT_PRICE: Final = "unit_price"
UNPRICED: Final = "unpriced"
STRUCTURE_RESIDUAL: Final = "structure_residual"
UNIT_PRICE_ROUNDING: Final = (
    "unit_cost = base x year factor x location factor, rounded half up to the cent; "
    "total = unit_cost x quantity, rounded half up to the cent"
)
STRUCTURE_ROUNDING: Final = (
    "total = the residual the structure model carved from the building's replacement cost "
    "new (structure.rcn); unit_cost = total / quantity, rounded half even to the cent "
    "(the total when the quantity is 0); the total is not recomputed from the unit cost"
)
YEAR_SETTING: Final = "COST_YEAR setting"
LOCATION_SETTING: Final = "COST_LOCATION_FACTOR setting"


@dataclass(frozen=True, slots=True)
class TrailContext:
    """What a revision's every trail shares: the intake version priced from (None when
    the study has no intake) and the property class."""

    intake_version: int | None
    property_class: str


def json_safe(value: Any) -> Any:
    """``value`` with every Decimal as its exact string (fixed point, no exponent), dates
    ISO, UUIDs and enums as strings and tuples as lists, so it stores as JSONB unchanged."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        items = sorted(value, key=str) if isinstance(value, set | frozenset) else value
        return [json_safe(v) for v in items]
    raise TypeError(f"the cost trail cannot hold a {type(value).__name__}")


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def store_block(store: StoreRow | None) -> dict[str, Any] | None:
    if store is None:
        return None
    return {
        "id": None if store.id is None else str(store.id),
        "source": store.source,
        "version": store.version,
    }


def index_block(index: YearIndex) -> dict[str, Any]:
    """One Historical Cost Index value as used: year, value, date, citation, flags and the
    store row it was read from."""
    return {
        "year": index.year,
        "value": _dec(index.index),
        "as_of": index.as_of.isoformat(),
        "citation": index.source,
        "estimated": index.estimated,
        "derived": index.derived,
        "store": store_block(index.store),
    }


def year_block(year: YearFactor, year_source: str | None) -> dict[str, Any]:
    """The time factor as used: ``factor = index / base`` to four decimals, both index
    values, the missing-year assumption or the conversion when set, and where the cost
    year came from (``CostIndex.year_source``, or the setting)."""
    return {
        "cost_year": year.cost_year,
        "factor": _dec(year.factor),
        "index": index_block(year.index),
        "base": index_block(year.base),
        "assumption": year.assumption,
        "conversion": year.conversion,
        "citation": year.citation(),
        "from": year_source or YEAR_SETTING,
    }


def location_block(index: CostIndex, factor: Decimal) -> dict[str, Any]:
    """The location factor as used, with the city row (or the state mean) and the table it
    came from; only ``factor`` and ``from`` when the factor is the setting's."""
    found: LocationFactor | None = index.location
    city = None if found is None else found.city
    return {
        "factor": _dec(factor),
        "from": index.location_source or LOCATION_SETTING,
        "lookup": index.location_lookup,
        "zip": index.location_value if index.location_lookup == "zip" else None,
        "lookup_from": index.location_from,
        "state": None if found is None else found.state,
        "city": None if city is None else city.city,
        "zip_prefixes": None if city is None else city.zip_prefixes,
        "cities": None if found is None else found.cities,
        "low": None if found is None else _dec(found.low),
        "high": None if found is None else _dec(found.high),
        "pages": None if found is None else found.pages,
        "table_year": None if found is None else found.table_year,
        "origin": None if found is None else found.origin,
        "citation": None if found is None else found.citation(),
        "store": None if city is None else store_block(city.store),
    }


def base_block(row: PricedRow) -> dict[str, Any]:
    """The base unit cost exactly as multiplied (per takeoff unit, base year, before the
    year and location factors; not rounded) and the cost row it was read from."""
    return {
        "unit_cost": _dec(row.unit_cost),
        "unit": row.unit,
        "table": row.table,
        "row": row.key,
        "tier": row.tier,
        "source": row.source,
        "base_year": row.base_year,
        "row_unit": row.row_unit,
        "conversion": row.conversion,
        "assumption": row.assumption,
    }


def quantity_block(
    takeoff: Takeoff, inputs: Mapping[str, Any], assumptions: Sequence[str] | None = None
) -> dict[str, Any]:
    """The quantity priced and how it was measured: ``inputs`` are the takeoff inputs as
    written to ``takeoffs.inputs``; ``assumptions`` the takeoff's (``takeoff.assumptions``
    when not given)."""
    return {
        "value": _dec(takeoff.quantity),
        "unit": takeoff.unit,
        "formula": takeoff.formula,
        "method": takeoff.method,
        "inputs": json_safe(dict(inputs)),
        "assumptions": list(takeoff.assumptions if assumptions is None else assumptions),
    }


def structure_block(row: ConcealedRow, building: BuildingRcn | None) -> dict[str, Any]:
    """A concealed row's identity and, for a building residual, the building RCN it was
    carved from (amount, source, formula, inputs and assumptions as the model built them)."""
    return {
        "label": row.label,
        "system": row.system,
        "component": row.component,
        "source": row.source,
        "rcn": _dec(row.rcn),
        "building": None
        if building is None or row.rcn is None
        else {
            "amount": _dec(building.amount),
            "source": building.source,
            "formula": building.formula,
            "inputs": json_safe(building.inputs),
            "assumptions": list(building.assumptions),
        },
    }


def priced_trail(
    quantity: dict[str, Any],
    estimate: CostEstimate | None,
    index: CostIndex,
    *,
    context: TrailContext,
    missing: str | None = None,
    priced_per: str | None = None,
    structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The trail of a row priced by :func:`basis.domain.costs.estimate_cost` (an itemized
    component, an asset correction's row, a small system), or left unpriced (``estimate``
    None: ``missing`` is the row's ``source_row`` text, ``priced_per`` the unit a table row
    prices the label in when the takeoff is in another). The year factor is the one the
    estimate used (the study's, ``index.year``, for an unpriced row)."""
    if estimate is not None:
        year = estimate.year if estimate.year is not None else index.year
        factor = estimate.location_factor
    else:
        year, factor = index.year, index.location_factor
    return {
        "version": TRAIL_VERSION,
        "kind": UNPRICED if estimate is None else UNIT_PRICE,
        "quantity": quantity,
        "base": None if estimate is None or estimate.row is None else base_block(estimate.row),
        "year": None if year is None else year_block(year, index.year_source),
        "location": location_block(index, factor),
        "structure": structure,
        "intake_version": context.intake_version,
        "property_class": context.property_class,
        "cost_year": index.cost_year if estimate is None else estimate.cost_year,
        "location_factor": _dec(factor),
        "unit_cost": None if estimate is None else _dec(estimate.unit_cost),
        "total": None if estimate is None else _dec(estimate.total),
        "rounding": None if estimate is None else UNIT_PRICE_ROUNDING,
        "priced_per": priced_per if estimate is None else None,
        "missing": missing if estimate is None else None,
    }


def structure_residual_trail(
    quantity: dict[str, Any],
    *,
    unit_cost: Decimal | None,
    total: Decimal | None,
    cost_year: int,
    location_factor: Decimal | None,
    index: CostIndex | None,
    structure: dict[str, Any],
    context: TrailContext,
) -> dict[str, Any]:
    """The trail of a building residual (a concealed row whose total is its RCN):
    ``index`` is the study's cost index when the square-foot model applied it (its year
    and location recorded), None when the building RCN is the appraiser's cost new (no
    location factor; its own time adjustment is in ``structure.building.inputs``)."""
    return {
        "version": TRAIL_VERSION,
        "kind": STRUCTURE_RESIDUAL,
        "quantity": quantity,
        "base": None,
        "year": None
        if index is None or index.year is None
        else year_block(index.year, index.year_source),
        "location": None
        if index is None or location_factor is None
        else location_block(index, location_factor),
        "structure": structure,
        "intake_version": context.intake_version,
        "property_class": context.property_class,
        "cost_year": cost_year,
        "location_factor": _dec(location_factor),
        "unit_cost": _dec(unit_cost),
        "total": _dec(total),
        "rounding": STRUCTURE_ROUNDING,
        "priced_per": None,
        "missing": None,
    }


def check(trail: Mapping[str, Any]) -> list[str]:
    """What does not hold in a ``unit_price`` trail's arithmetic (empty when it all holds):
    ``base x year factor x location factor``, rounded half up to the cent, is ``unit_cost``,
    and ``unit_cost x quantity`` so rounded is ``total``; the factors are the ones the
    year and location blocks record. Other kinds are not recomputed (empty)."""
    if trail.get("kind") != UNIT_PRICE:
        return []
    problems: list[str] = []
    base, year, location = trail["base"], trail["year"], trail["location"]
    if base is None or year is None or location is None:
        return ["a unit_price trail needs its base, year and location"]
    if location["factor"] != trail["location_factor"]:
        problems.append("location.factor is not location_factor")
    if year["cost_year"] != trail["cost_year"]:
        problems.append("year.cost_year is not cost_year")
    unit_cost = (
        Decimal(base["unit_cost"]) * Decimal(year["factor"]) * Decimal(location["factor"])
    ).quantize(CENTS, rounding=ROUND_HALF_UP)
    if unit_cost != Decimal(trail["unit_cost"]):
        problems.append(f"base x year x location = {unit_cost}, not {trail['unit_cost']}")
    total = (Decimal(trail["unit_cost"]) * Decimal(trail["quantity"]["value"])).quantize(
        CENTS, rounding=ROUND_HALF_UP
    )
    if total != Decimal(trail["total"]):
        problems.append(f"unit_cost x quantity = {total}, not {trail['total']}")
    return problems


__all__ = [
    "STRUCTURE_RESIDUAL",
    "STRUCTURE_ROUNDING",
    "TRAIL_VERSION",
    "UNIT_PRICE",
    "UNIT_PRICE_ROUNDING",
    "UNPRICED",
    "TrailContext",
    "base_block",
    "check",
    "index_block",
    "json_safe",
    "location_block",
    "priced_trail",
    "quantity_block",
    "store_block",
    "structure_block",
    "structure_residual_trail",
    "year_block",
]
