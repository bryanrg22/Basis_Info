"""Cost tables and the deterministic estimate: unit cost x quantity x year x location.

Migrated from ``agentic/agents/cost_agent.py`` (``TYPICAL_UNIT_COSTS``,
``REGIONAL_COST_FACTORS``, ``YEAR_ADJUSTMENT_FACTORS``, ``MATERIAL_LABOR_SPLIT`` and
``estimate_cost_static``). The unit costs are the legacy "typical RSMeans" values on a
2020 base; they are placeholders until the engineer's cost source is loaded, and every
estimate names the row it used (``source_row``) so it can be audited.

Changes from legacy, each a bug fix:

* A component without a cost row raises :class:`MissingCostRow` (legacy returned a zero
  cost marked for review, which summed silently into totals).
* A cost year outside the adjustment table raises :class:`UnknownCostYear` (legacy used
  1.15 for any unknown year).
* Units are reconciled: a carpet takeoff in square yards is priced with the per-SF row
  times 9 (legacy multiplied SY by the per-SF price, a 9x undercount). Any other unit
  mismatch raises :class:`UnitMismatch`.
* Money is ``Decimal`` rounded half-up to cents (legacy used floats).

v0.9 (EVAL_REPORT section 26): a component the six legacy rows do not price is priced
from the configured cost table (:mod:`basis.domain.cost_table`, ``COST_TABLE_PATH``: the
engineer's licensed unit costs, by component label). A table row in another unit than the
takeoff raises :class:`NeedsQuantity` (the component is priced per area or length that
the takeoff does not measure), which the cross-validation reports apart from a missing
row.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

from basis.domain.cost_table import CostTable
from basis.domain.takeoffs import SF_PER_SY, Takeoff

Tier = Literal["economy", "standard", "premium", "luxury"]
CENTS: Final = Decimal("0.01")
BASE_YEAR: Final = 2020


class CostError(ValueError):
    """The estimate cannot be computed from the tables."""


class MissingCostRow(CostError):
    pass


class UnknownCostYear(CostError):
    pass


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

# RSMeans-style city cost index per state (legacy values).
REGIONAL_COST_FACTORS: Final[dict[str, Decimal]] = {
    code: Decimal(value)
    for code, value in {
        "AL": "0.85",
        "AK": "1.25",
        "AZ": "0.95",
        "AR": "0.82",
        "CA": "1.15",
        "CO": "0.98",
        "CT": "1.12",
        "DE": "1.02",
        "FL": "0.92",
        "GA": "0.89",
        "HI": "1.30",
        "ID": "0.90",
        "IL": "1.05",
        "IN": "0.95",
        "IA": "0.92",
        "KS": "0.90",
        "KY": "0.88",
        "LA": "0.87",
        "ME": "0.95",
        "MD": "0.98",
        "MA": "1.15",
        "MI": "1.00",
        "MN": "1.02",
        "MS": "0.80",
        "MO": "0.95",
        "MT": "0.92",
        "NE": "0.88",
        "NV": "1.02",
        "NH": "1.00",
        "NJ": "1.15",
        "NM": "0.90",
        "NY": "1.20",
        "NC": "0.85",
        "ND": "0.88",
        "OH": "0.95",
        "OK": "0.85",
        "OR": "1.02",
        "PA": "1.00",
        "RI": "1.08",
        "SC": "0.82",
        "SD": "0.85",
        "TN": "0.85",
        "TX": "0.88",
        "UT": "0.92",
        "VT": "0.95",
        "VA": "0.92",
        "WA": "1.05",
        "WV": "0.92",
        "WI": "0.98",
        "WY": "0.88",
        "DC": "1.02",
    }.items()  # fmt: skip
}

# Escalation from the 2020 base year (legacy values).
YEAR_ADJUSTMENT_FACTORS: Final[dict[int, Decimal]] = {
    2020: Decimal("1.00"),
    2021: Decimal("1.03"),
    2022: Decimal("1.08"),
    2023: Decimal("1.12"),
    2024: Decimal("1.15"),
    2025: Decimal("1.18"),
    2026: Decimal("1.22"),
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


def location_factor_for_state(state: str) -> Decimal:
    code = state.strip().upper()
    if code not in REGIONAL_COST_FACTORS:
        raise CostError(f"no regional cost factor for state {state!r}")
    return REGIONAL_COST_FACTORS[code]


def year_factor(cost_year: int) -> Decimal:
    if cost_year not in YEAR_ADJUSTMENT_FACTORS:
        known = f"{min(YEAR_ADJUSTMENT_FACTORS)}-{max(YEAR_ADJUSTMENT_FACTORS)}"
        raise UnknownCostYear(f"no cost adjustment for {cost_year}; the table covers {known}")
    return YEAR_ADJUSTMENT_FACTORS[cost_year]


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
) -> CostEstimate:
    """Price a takeoff: ``unit_cost = base x year factor x location``; ``total = unit_cost x qty``.

    The row is the legacy ``TYPICAL_UNIT_COSTS[takeoff.component][tier]``, else the cost
    table's row for ``label`` (the component's label), else its row for the takeoff
    component. Raises :class:`MissingCostRow`, :class:`NeedsQuantity` (a table row in
    another unit), :class:`UnknownCostYear` or :class:`UnitMismatch` instead of guessing.
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
    factor = year_factor(cost_year)
    unit_cost = (base * factor * location).quantize(CENTS, rounding=ROUND_HALF_UP)
    total = (unit_cost * takeoff.quantity).quantize(CENTS, rounding=ROUND_HALF_UP)
    return CostEstimate(
        unit_cost=unit_cost,
        total=total,
        source_row=f"{source} (base {BASE_YEAR}, x{factor} for {cost_year}, x{location} location)",
        base_unit_cost=base.quantize(CENTS, rounding=ROUND_HALF_UP),
        year_factor=factor,
        location_factor=location,
        cost_year=cost_year,
        unit=takeoff.unit,
    )
