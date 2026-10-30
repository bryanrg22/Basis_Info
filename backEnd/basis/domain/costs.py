"""Cost tables and the deterministic estimate: unit cost x quantity x year x location.

Migrated from ``agentic/agents/cost_agent.py`` (``TYPICAL_UNIT_COSTS``,
``MATERIAL_LABOR_SPLIT`` and ``estimate_cost_static``). The unit costs are the legacy
"typical RSMeans" values on a 2020 base; they are placeholders until the engineer's cost
source is loaded, and every estimate names the row it used (``source_row``) so it can be
audited.

Changes from legacy, each a bug fix:

* A component without a cost row raises :class:`MissingCostRow` (legacy returned a zero
  cost marked for review, which summed silently into totals).
* A cost year before the index table raises :class:`UnknownCostYear` (legacy used 1.15
  for any unknown year).
* Units are reconciled: a carpet takeoff in square yards is priced with the per-SF row
  times 9 (legacy multiplied SY by the per-SF price, a 9x undercount). Any other unit
  mismatch raises :class:`UnitMismatch`.
* Money is ``Decimal`` rounded half-up to cents (legacy used floats).

v0.9 (EVAL_REPORT section 27): a component the six legacy rows do not price is priced
from the configured cost table (:mod:`basis.domain.cost_table`, ``COST_TABLE_PATH``: the
engineer's licensed unit costs, by component label). A table row in another unit than the
takeoff raises :class:`NeedsQuantity` (the component is priced per area or length that
the takeoff does not measure), which the cross-validation reports apart from a missing
row.

Cost indices (EVAL_REPORT section 45, docs/review_2026-10-15/COST_INDICES.md): the year
and location factors are RSMeans' published indices (:mod:`basis.domain.cost_indices`),
not the hand-typed tables the legacy code carried. The year factor is the Historical Cost
Index of the cost year over the books' Jan. 1, 2020 basis; the location factor is the
Residential Location Factor of the study's city (by ZIP) or the mean of its state's
cities. Every cost row cites the index values, book and page it used; a cost year after
the latest published index carries the assumption on the row (``year_assumption``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

from basis.domain import cost_indices
from basis.domain.cost_indices import YearFactor, YearIndex, year_factor_for
from basis.domain.cost_table import CostTable
from basis.domain.takeoffs import SF_PER_SY, Takeoff

Tier = Literal["economy", "standard", "premium", "luxury"]
CENTS: Final = Decimal("0.01")
BASE_YEAR: Final = 2020  # the RSMeans 2020 books' cost basis (Jan. 1, 2020)


class CostError(ValueError):
    """The estimate cannot be computed from the tables."""


class MissingCostRow(CostError):
    pass


class UnknownCostYear(CostError):
    """The cost year is before the first published index (1970)."""


class UnitMismatch(CostError):
    pass


class NeedsQuantity(UnitMismatch):
    """The cost table prices the component per ``unit``; the takeoff is in another unit."""

    def __init__(self, message: str, unit: str) -> None:
        super().__init__(message)
        self.unit = unit


@dataclass(frozen=True, slots=True)
class UnitCost:
    material: Decimal
    labor: Decimal
    equipment: Decimal
    unit: str

    @property
    def total(self) -> Decimal:
        return self.material + self.labor + self.equipment


def _uc(material: str, labor: str, equipment: str, unit: str) -> UnitCost:
    return UnitCost(Decimal(material), Decimal(labor), Decimal(equipment), unit)


TYPICAL_UNIT_COSTS: Final[dict[str, dict[Tier, UnitCost]]] = {
    "light_fixture": {
        "economy": _uc("35", "25", "5", "EA"),
        "standard": _uc("85", "35", "5", "EA"),
        "premium": _uc("200", "50", "10", "EA"),
        "luxury": _uc("500", "75", "15", "EA"),
    },
    "electrical_outlet": {
        "economy": _uc("8", "25", "2", "EA"),
        "standard": _uc("15", "35", "3", "EA"),
        "premium": _uc("35", "45", "5", "EA"),
        "luxury": _uc("75", "55", "8", "EA"),
    },
    "carpet": {
        "economy": _uc("2.50", "1.50", "0.25", "SF"),
        "standard": _uc("4.50", "2.00", "0.30", "SF"),
        "premium": _uc("8.00", "2.50", "0.40", "SF"),
        "luxury": _uc("15.00", "3.00", "0.50", "SF"),
    },
    "tile": {
        "economy": _uc("4.00", "6.00", "0.50", "SF"),
        "standard": _uc("8.00", "8.00", "0.75", "SF"),
        "premium": _uc("15.00", "10.00", "1.00", "SF"),
        "luxury": _uc("30.00", "15.00", "1.50", "SF"),
    },
    "cabinet": {
        "economy": _uc("100", "35", "5", "LF"),
        "standard": _uc("200", "50", "8", "LF"),
        "premium": _uc("400", "75", "12", "LF"),
        "luxury": _uc("800", "100", "20", "LF"),
    },
    "hvac_unit": {
        "economy": _uc("2500", "1000", "250", "EA"),
        "standard": _uc("4000", "1500", "400", "EA"),
        "premium": _uc("6500", "2000", "600", "EA"),
        "luxury": _uc("10000", "2500", "800", "EA"),
    },
}

MATERIAL_LABOR_SPLIT: Final[dict[str, tuple[Decimal, Decimal, Decimal]]] = {
    name: (Decimal(m), Decimal(lab), Decimal(e))
    for name, (m, lab, e) in {
        "light_fixture": ("0.65", "0.30", "0.05"),
        "electrical_outlet": ("0.45", "0.50", "0.05"),
        "hvac_unit": ("0.55", "0.35", "0.10"),
        "carpet": ("0.60", "0.35", "0.05"),
        "tile": ("0.50", "0.45", "0.05"),
        "cabinet": ("0.70", "0.28", "0.02"),
        "countertop": ("0.65", "0.30", "0.05"),
        "plumbing_fixture": ("0.60", "0.35", "0.05"),
        "paint": ("0.35", "0.60", "0.05"),
        "door": ("0.65", "0.32", "0.03"),
        "window": ("0.70", "0.25", "0.05"),
        "roofing": ("0.45", "0.45", "0.10"),
        "siding": ("0.55", "0.40", "0.05"),
        "default": ("0.55", "0.40", "0.05"),
    }.items()
}


@dataclass(frozen=True, slots=True)
class CostEstimate:
    unit_cost: Decimal  # per takeoff unit, after year and location adjustment
    total: Decimal
    source_row: str
    base_unit_cost: Decimal  # per takeoff unit, base year, before adjustments
    year_factor: Decimal
    location_factor: Decimal
    cost_year: int
    unit: str
    year_assumption: str | None = None  # set when the cost year has no published index


def location_factor_for_state(state: str) -> Decimal:
    """The mean Residential Location Factor of the state's listed cities (three decimals);
    :class:`CostError` for a code the table does not have."""
    found = cost_indices.location_factor_for_state_code(state)
    if found is None:
        raise CostError(f"no location factor for state {state!r}")
    return found.factor


def year_factor_index(
    cost_year: int, supplement: Mapping[int, YearIndex] | None = None
) -> YearFactor:
    """The cost year's time factor with its index values and source; a year after the
    latest published index is extrapolated with the assumption recorded (the
    ``extrapolated`` flag); :class:`UnknownCostYear` before the table."""
    try:
        return year_factor_for(cost_year, supplement)
    except cost_indices.UnknownCostYear as exc:
        raise UnknownCostYear(str(exc)) from exc


def year_factor(cost_year: int, supplement: Mapping[int, YearIndex] | None = None) -> Decimal:
    """``Historical Cost Index(cost_year) / Historical Cost Index(Jan. 1, 2020)``."""
    return year_factor_index(cost_year, supplement).factor


def has_cost_row(component: str) -> bool:
    return component in TYPICAL_UNIT_COSTS


def _per_takeoff_unit(row: UnitCost, unit: str, component: str) -> Decimal:
    if unit == row.unit:
        return row.total
    if unit == "SY" and row.unit == "SF":
        return row.total * SF_PER_SY
    if unit == "SF" and row.unit == "SY":
        return row.total / SF_PER_SY
    raise UnitMismatch(f"{component}: takeoff is in {unit} but the cost row is per {row.unit}")


def estimate_cost(
    takeoff: Takeoff,
    cost_year: int,
    location_factor: Decimal | float | str,
    *,
    tier: Tier = "standard",
    table: CostTable | None = None,
    label: str | None = None,
    year_source: str | None = None,
    location_source: str | None = None,
    year: YearFactor | None = None,
) -> CostEstimate:
    """Price a takeoff: ``unit_cost = base x year factor x location``; ``total = unit_cost x qty``.

    The row is the legacy ``TYPICAL_UNIT_COSTS[takeoff.component][tier]``, else the cost
    table's row for ``label`` (the component's label), else its row for the takeoff
    component. Raises :class:`MissingCostRow`, :class:`NeedsQuantity` (a table row in
    another unit), :class:`UnknownCostYear` or :class:`UnitMismatch` instead of guessing.
    ``year`` is the resolved time factor of the study (``CostIndex.year``, which may carry
    the firm's supplement); without it the cost year's factor comes from the released book
    alone. ``year_source`` and ``location_source`` (where the study's cost year and
    location factor came from: :func:`basis.domain.measurements.resolve_cost_index`) are
    written into ``source_row`` after the factors when given.
    """
    location = Decimal(str(location_factor))
    if not location.is_finite() or location <= 0:
        raise CostError(f"location factor must be positive, got {location_factor!r}")
    rows = TYPICAL_UNIT_COSTS.get(takeoff.component)
    if rows is not None:
        row = rows[tier]
        base = _per_takeoff_unit(row, takeoff.unit, takeoff.component)
        source = f"TYPICAL_UNIT_COSTS[{takeoff.component}][{tier}] per {row.unit}"
    else:
        found = None
        if table is not None:
            found = table.row(label) or table.row(takeoff.component)
        if found is None:
            raise MissingCostRow(f"no cost row for {takeoff.component!r}")
        if table is not None and table.base_year != BASE_YEAR:
            raise CostError(
                f"cost table {table.name} is based on {table.base_year}; the year factors "
                f"are based on {BASE_YEAR}"
            )
        if found.unit != takeoff.unit:
            raise NeedsQuantity(
                f"{found.label} is priced per {found.unit}; the takeoff is "
                f"{takeoff.quantity} {takeoff.unit}",
                found.unit,
            )
        base = found.total
        assert table is not None
        source = f"{table.name}[{found.label}] {found.source} per {found.unit}"
        if found.factor != 1:
            source += f" x{found.factor} from per {found.source_unit}"
        if found.assumption:
            source += f"; assumes {found.assumption}"
    if year is None or year.cost_year != cost_year:
        year = year_factor_index(cost_year)
    factor = year.factor
    unit_cost = (base * factor * location).quantize(CENTS, rounding=ROUND_HALF_UP)
    total = (unit_cost * takeoff.quantity).quantize(CENTS, rounding=ROUND_HALF_UP)
    year_text = f"x{factor} for {cost_year} = {year.citation()}"
    if year.extrapolated:
        year_text += f"; no {cost_year} index loaded, the latest applied (assumption)"
    if year_source:
        year_text += f", from {year_source}"
    location_text = f"x{location} location"
    if location_source:
        location_text += f" from {location_source}"
    return CostEstimate(
        unit_cost=unit_cost,
        total=total,
        source_row=f"{source} (base {BASE_YEAR}, {year_text}; {location_text})",
        base_unit_cost=base.quantize(CENTS, rounding=ROUND_HALF_UP),
        year_factor=factor,
        location_factor=location,
        cost_year=cost_year,
        unit=takeoff.unit,
        year_assumption=year.assumption,
    )
