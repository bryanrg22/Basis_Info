"""Cost tables and the deterministic estimate: unit cost x quantity x year x location.

Migrated from ``agentic/agents/cost_agent.py`` (``estimate_cost_static``). Every unit cost
comes from the configured cost table (:mod:`basis.domain.cost_table`, ``COST_TABLE_PATH``: RSMeans
lines read from the books), and every estimate names the row it used (``source_row``) so it
can be audited. There is no built-in table: the six legacy "typical RSMeans" placeholder rows
(``TYPICAL_UNIT_COSTS``) were removed in EVAL_REPORT 78, because they priced light fixtures,
outlets, carpet, tile, cabinets and HVAC units ahead of the real table. A component the table
does not price raises :class:`MissingCostRow` and stays unpriced, which withholds the report
(:mod:`basis.domain.money_inputs`).

Changes from legacy, each a bug fix:

* A component without a cost row raises :class:`MissingCostRow` (legacy returned a zero
  cost marked for review, which summed silently into totals).
* A cost year before the index table raises :class:`UnknownCostYear` (legacy used 1.15
  for any unknown year).
* Units are reconciled: a table row carries the factor from its printed unit to the
  takeoff's (a carpet takeoff in square yards is the per-SF line times 9; legacy multiplied
  SY by the per-SF price, a 9x undercount). Any other unit mismatch raises
  :class:`UnitMismatch`.
* Money is ``Decimal`` rounded half-up to cents (legacy used floats).

v0.9 (EVAL_REPORT section 27): the configured cost table prices by component label. A table
row in another unit than the takeoff raises :class:`NeedsQuantity` (the component is priced
per area or length that the takeoff does not measure), which the cross-validation reports
apart from a missing row.

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
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

from basis.domain import cost_indices
from basis.domain.cost_indices import YearFactor, YearIndex, year_factor_for
from basis.domain.cost_table import CostTable
from basis.domain.takeoffs import Takeoff

CENTS: Final = Decimal("0.01")
BASE_YEAR: Final = 2020  # the RSMeans 2020 books' cost basis (Jan. 1, 2020)

# The basis a row is priced on (STRUCTURE_MODEL.md section 9.3). ``bare``: material + labor +
# equipment, RSMeans' "Bare Costs" (the behaviour before 2026-10-31). ``installed``: the line's
# "Total Incl O&P", the installing contractor's overhead and profit included, which is the
# basis of the square-foot models' components 1-9 ("The costs in components 1 through 9
# include overhead and profit for the installing contractor", RSMeans Residential 2020, How to
# Use the Residential Square Foot Cost Section, PDF p.21). A row without that column (the six
# legacy rows) takes the same page's conversion: "Ten percent has been added to bare material
# cost for profit"; "The average mark-up ... is 72% over and above bare labor costs"; "The
# equipment rates include 10% for profit".
CostBasis = Literal["bare", "installed"]
COST_BASES: Final[tuple[CostBasis, ...]] = ("bare", "installed")
DEFAULT_COST_BASIS: Final[CostBasis] = "installed"
MATERIAL_PROFIT: Final = Decimal("1.10")
LABOR_MARKUP: Final = Decimal("1.72")
EQUIPMENT_PROFIT: Final = Decimal("1.10")
INSTALLED_SOURCE: Final = (
    "RSMeans Residential 2020, How to Use the Residential Square Foot Cost Section, PDF p.21"
)


@dataclass(frozen=True, slots=True)
class Markup:
    """A pro-rata share of an indirect cost on a row's direct cost: the general contractor's
    overhead and profit of the square-foot model (its item 10 over components 1-9), for a row
    inside the model (ATG ch. 4 C.11(2))."""

    rate: Decimal  # e.g. 0.200129 for 20.0129 %
    source: str


def installed_cost(material: Decimal, labor: Decimal, equipment: Decimal) -> Decimal:
    """Bare material, labor and equipment brought to the installing contractor's price by the
    square-foot section's own markups (:data:`INSTALLED_SOURCE`)."""
    return material * MATERIAL_PROFIT + labor * LABOR_MARKUP + equipment * EQUIPMENT_PROFIT


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
class PricedRow:
    """The cost row an estimate used, as structured data (``source_row`` is the same in
    words): which table and row, the base unit cost exactly as multiplied, and how the
    row's unit became the takeoff's. Read by :mod:`basis.domain.cost_trail`."""

    table: str  # the cost table's name (``COST_TABLE_PATH``)
    key: str  # the table row's label
    tier: str | None  # always None (the legacy tiers went with the placeholder rows)
    unit_cost: Decimal  # per takeoff unit, base year, before adjustments, not rounded
    unit: str  # the takeoff unit the base is per
    row_unit: str  # the table row's source unit
    base_year: int
    source: str | None  # the table row's printed source
    conversion: str | None  # e.g. "x9 from per SF"; None when the row is per takeoff unit
    assumption: str | None  # the table row's assumption
    # The cost basis (STRUCTURE_MODEL.md 9.3): ``bare`` or ``installed`` (the installing
    # contractor's O&P), and how ``unit_cost`` reached it ("Total Incl O&P", or the markups).
    basis: str = "bare"
    basis_how: str | None = None
    # A pro-rata indirect cost applied on top (the model's general contractor's O&P):
    # ``unit_cost x (1 + markup_rate)`` is what the year and location factors multiply.
    markup_rate: Decimal | None = None
    markup_source: str | None = None


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
    # The row and the time factor the estimate used, as objects (the cost trail).
    row: PricedRow | None = None
    year: YearFactor | None = None


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


def estimate_cost(
    takeoff: Takeoff,
    cost_year: int,
    location_factor: Decimal | float | str,
    *,
    table: CostTable | None = None,
    label: str | None = None,
    year_source: str | None = None,
    location_source: str | None = None,
    year: YearFactor | None = None,
    basis: CostBasis = DEFAULT_COST_BASIS,
    markup: Markup | None = None,
) -> CostEstimate:
    """Price a takeoff: ``unit_cost = base x (1 + markup) x year factor x location``;
    ``total = unit_cost x qty``.

    The row is the cost table's row for ``label`` (the component's label), else its row for
    the takeoff component. Raises :class:`MissingCostRow` (no table, or no row),
    :class:`NeedsQuantity` (a table row in another unit), :class:`UnknownCostYear` or
    :class:`UnitMismatch` instead of guessing.
    ``year`` is the resolved time factor of the study (``CostIndex.year``, which may carry
    the firm's supplement); without it the cost year's factor comes from the released book
    alone. ``year_source`` and ``location_source`` (where the study's cost year and
    location factor came from: :func:`basis.domain.measurements.resolve_cost_index`) are
    written into ``source_row`` after the factors when given. ``basis`` is the cost basis
    (:data:`CostBasis`: bare, or installed with the installing contractor's O&P); ``markup``
    a pro-rata indirect cost on the row (the model's general contractor's O&P), recorded in
    ``source_row``.
    """
    location = Decimal(str(location_factor))
    if not location.is_finite() or location <= 0:
        raise CostError(f"location factor must be positive, got {location_factor!r}")
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
    assert table is not None
    source = f"{table.name}[{found.label}] {found.source} per {found.unit}"
    if found.factor != 1:
        source += f" x{found.factor} from per {found.source_unit}"
    basis_how = None
    if basis == "bare":
        base = found.total
    elif found.total_incl_op is not None:
        base = found.total_incl_op
        basis_how = "Total Incl O&P"
        source += f"; {basis_how}"
    else:
        base = installed_cost(found.material, found.labor, found.equipment)
        basis_how = (
            f"installed: material x{MATERIAL_PROFIT} + labor x{LABOR_MARKUP} + equipment "
            f"x{EQUIPMENT_PROFIT} ({INSTALLED_SOURCE})"
        )
        source += f"; {basis_how}"
    if found.assumption:
        source += f"; assumes {found.assumption}"
    priced = PricedRow(
        table=table.name,
        key=found.label,
        tier=None,
        unit_cost=base,
        unit=found.unit,
        row_unit=found.source_unit or found.unit,
        base_year=table.base_year,
        source=found.source,
        conversion=None if found.factor == 1 else f"x{found.factor} from per {found.source_unit}",
        assumption=found.assumption,
        basis=basis,
        basis_how=basis_how,
    )
    if year is None or year.cost_year != cost_year:
        year = year_factor_index(cost_year)
    factor = year.factor
    if markup is not None and markup.rate != 0:
        base = base * (1 + markup.rate)
        source += f"; x{1 + markup.rate} {markup.source}"
        priced = replace(priced, markup_rate=markup.rate, markup_source=markup.source)
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
        row=priced,
        year=year,
    )
