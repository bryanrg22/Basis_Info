"""Unit costs per component label, loaded from a file (EVAL_REPORT section 25, item 6).

Cost segregation prices each component with a published unit cost (the IRS Cost
Segregation Audit Techniques Guide, chapter 4, describes the detailed engineering
approach: quantity takeoffs priced from a cost source such as RSMeans), adjusted to the
placed-in-service year and the location. The static table in :mod:`basis.domain.costs`
had rows for six components; on the live client study 106 of 203 assets were flagged
only because their component had no row.

The engineer's cost source is licensed data (RSMeans line items), so it is not in the
repository: :data:`COST_TABLE_PATH <basis.config.Settings.cost_table_path>` names a JSON
file the deployment provides (a mounted secret or a private bucket object copied to the
worker). Its schema (``schema_version`` 1)::

    {"schema_version": 1, "name": "rsmeans_2020", "base_year": 2020,
     "basis": "...", "rows": {"<component label>": {
        "unit": "EA" | "SF" | "LF" | "SY",   # the takeoff unit the product measures
        "factor": 1,                          # source unit -> takeoff unit (e.g. 0.01 for
                                              # a per-square roofing line priced per SF)
        "material": 247, "labor": 32, "equipment": 0,   # bare costs per source unit
        "total_incl_op": 325,                 # Total Incl O&P per source unit (reference)
        "source_unit": "Ea.", "source": "<book, line, page>",
        "description": "...", "assumption": "..." | null}}}

A row prices the takeoff of its label in its unit: the bare material + labor +
equipment per source unit times ``factor`` (the same basis as the legacy rows), with the
year and location factors of :func:`basis.domain.costs.estimate_cost` applied after.
``total_incl_op`` is kept for the reviewer and is not used. A file that does not parse
or breaks the schema raises :class:`CostTableError` at load, so a worker never starts on
a broken table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = 1
UNITS: Final = frozenset({"EA", "SF", "LF", "SY"})


class CostTableError(ValueError):
    """The cost table file is missing, unreadable, or breaks the schema."""


@dataclass(frozen=True, slots=True)
class CostRow:
    label: str
    unit: str
    material: Decimal  # per takeoff unit (source cost x factor)
    labor: Decimal
    equipment: Decimal
    total_incl_op: Decimal | None
    source: str
    factor: Decimal = Decimal(1)
    source_unit: str = ""
    description: str = ""
    assumption: str | None = None

    @property
    def total(self) -> Decimal:
        return self.material + self.labor + self.equipment


@dataclass(frozen=True, slots=True)
class CostTable:
    name: str
    base_year: int
    rows: dict[str, CostRow] = field(default_factory=dict)

    def row(self, label: str | None) -> CostRow | None:
        return None if label is None else self.rows.get(label)


def _decimal(value: Any, what: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise CostTableError(f"{what}: {value!r} is not a number")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise CostTableError(f"{what}: {value!r} is not a number") from None
    if not number.is_finite() or number < 0:
        raise CostTableError(f"{what}: {value!r} must be a finite non-negative number")
    return number


def parse_cost_table(data: Any) -> CostTable:
    """A :class:`CostTable` from the parsed JSON document (schema in the module doc)."""
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise CostTableError(f"a cost table needs schema_version {SCHEMA_VERSION}")
    name, base_year, raw_rows = data.get("name"), data.get("base_year"), data.get("rows")
    if not isinstance(name, str) or not name:
        raise CostTableError("a cost table needs a name")
    if not isinstance(base_year, int) or isinstance(base_year, bool):
        raise CostTableError("a cost table needs an integer base_year")
    if not isinstance(raw_rows, dict) or not raw_rows:
        raise CostTableError("a cost table needs rows")
    rows: dict[str, CostRow] = {}
    for label, raw in raw_rows.items():
        if not isinstance(raw, dict):
            raise CostTableError(f"row {label!r} is not an object")
        unit = raw.get("unit")
        if unit not in UNITS:
            raise CostTableError(f"row {label!r}: unit {unit!r} is not one of {sorted(UNITS)}")
        factor = _decimal(raw.get("factor", 1), f"row {label!r} factor")
        if factor == 0:
            raise CostTableError(f"row {label!r}: factor must be positive")
        source = raw.get("source")
        if not isinstance(source, str) or not source:
            raise CostTableError(f"row {label!r} needs a source")
        op = raw.get("total_incl_op")
        rows[label] = CostRow(
            label=label,
            unit=unit,
            material=_decimal(raw.get("material"), f"row {label!r} material") * factor,
            labor=_decimal(raw.get("labor"), f"row {label!r} labor") * factor,
            equipment=_decimal(raw.get("equipment", 0), f"row {label!r} equipment") * factor,
            total_incl_op=None if op is None else _decimal(op, f"row {label!r} O&P") * factor,
            source=source,
            factor=factor,
            source_unit=str(raw.get("source_unit") or ""),
            description=str(raw.get("description") or ""),
            assumption=raw.get("assumption") if isinstance(raw.get("assumption"), str) else None,
        )
    return CostTable(name=name, base_year=base_year, rows=rows)


@cache
def load_cost_table(path: str) -> CostTable:
    """The table in ``path`` (read once per process)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CostTableError(f"cost table {path} does not exist") from None
    except (OSError, ValueError) as exc:
        raise CostTableError(f"cost table {path} cannot be read: {exc}") from None
    return parse_cost_table(data)


def cost_table_for(path: str | None) -> CostTable | None:
    """The configured table (``COST_TABLE_PATH``), or ``None`` when none is configured."""
    return None if not path else load_cost_table(path)


__all__ = [
    "CostRow",
    "CostTable",
    "CostTableError",
    "cost_table_for",
    "load_cost_table",
    "parse_cost_table",
]
