"""Takeoff formulas: how much of a component a room holds, as pure functions.

Migrated from ``agentic/agents/takeoff_agent.py`` (``QUANTITY_ESTIMATES``,
``INSTALLATION_RATES`` and ``calculate_takeoff_static``). Every :class:`Takeoff` carries
the formula as text and the inputs it used, which ``process_assets`` stores in
``takeoffs.formula`` and ``takeoffs.inputs`` so a reviewer can recompute the number.

Changes from legacy, each a bug fix:

* An area-based component without an area raises :class:`MissingTakeoffInput`. Legacy
  fell back to the detection count but kept the area unit, producing "2 SY of carpet".
* ``countertop`` had ``per_sf=None`` in an ``SF`` row, so the legacy area branch
  multiplied by ``None`` and raised ``TypeError``; it is now the cabinet run (the
  perimeter estimate) times the legacy 25-inch depth.
* Hard floor coverings (vinyl, laminate, hardwood) had no row and became "1 EA"; they are
  measured as the floor area (no waste factor: the legacy table has none for them).

Known limits, recorded in each takeoff's ``assumptions`` for the reviewer: the cabinet run
is the legacy perimeter estimate (4 x sqrt(area)), which overstates a typical kitchen; the
room area is the legacy typical area when nothing was measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

from basis.domain.rules import normalize_label

Method = Literal["count", "area", "perimeter"]

QUANTUM: Final = Decimal("0.01")
SF_PER_SY: Final = Decimal(9)
COUNTERTOP_DEPTH_IN: Final = Decimal(25)


class MissingTakeoffInput(ValueError):
    """The formula needs an input (usually the room area) that was not given."""


@dataclass(frozen=True, slots=True)
class QuantitySpec:
    unit: str
    method: Method
    per_sf: Decimal = Decimal(1)
    waste_factor: Decimal = Decimal(1)
    min_per_room: int = 1
    note: str = ""


QUANTITY_ESTIMATES: Final[dict[str, QuantitySpec]] = {
    "light_fixture": QuantitySpec(
        "EA", "count", min_per_room=1, note="Varies by room type and lighting design"
    ),
    "electrical_outlet": QuantitySpec(
        "EA", "count", min_per_room=2, note="NEC requires outlets every 12 feet along walls"
    ),
    "hvac_vent": QuantitySpec(
        "EA", "count", min_per_room=1, note="Based on CFM requirements and room size"
    ),
    "fire_sprinkler": QuantitySpec(
        "EA", "count", min_per_room=1, note="NFPA 13 coverage requirements"
    ),
    "door": QuantitySpec("EA", "count", min_per_room=1, note="At least one entry door per room"),
    "window": QuantitySpec("EA", "count", min_per_room=0, note="Highly variable by room"),
    "carpet": QuantitySpec(
        "SY",
        "area",
        per_sf=Decimal(1) / SF_PER_SY,
        waste_factor=Decimal("1.10"),
        note="Add 10% for seams and waste",
    ),
    "tile": QuantitySpec(
        "SF", "area", waste_factor=Decimal("1.15"), note="Add 15% for cuts and waste"
    ),
    "flooring": QuantitySpec("SF", "area", note="Floor area; no waste factor in the table"),
    "paint": QuantitySpec(
        "SF", "area", per_sf=Decimal("3.5"), note="Wall area estimate assumes 8ft ceiling"
    ),
    "baseboard": QuantitySpec("LF", "perimeter", note="Perimeter estimate for rectangular rooms"),
    "cabinet": QuantitySpec(
        "LF",
        "perimeter",
        note="Legacy estimate: base/wall cabinet run taken as the room perimeter "
        "(overstates a typical kitchen; confirm against the drawings)",
    ),
    "countertop": QuantitySpec(
        "SF",
        "perimeter",
        per_sf=COUNTERTOP_DEPTH_IN / Decimal(12),
        note="Cabinet run (perimeter estimate) x 25-inch depth",
    ),
}
DEFAULT_SPEC: Final = QuantitySpec("EA", "count", min_per_room=1, note="Detection count")

# Rule keys (basis.domain.rules) whose takeoff row has another name.
TAKEOFF_KEYS: Final[dict[str, str]] = {
    "kitchen_cabinet": "cabinet",
    "vinyl_flooring": "flooring",
    "laminate_flooring": "flooring",
    "hardwood_floor": "flooring",
    "electrical_outlets": "electrical_outlet",
}


@dataclass(frozen=True, slots=True)
class InstallationRate:
    labor_hours_per_unit: Decimal
    typical_crew: str
    daily_production: int
    unit: str


INSTALLATION_RATES: Final[dict[str, InstallationRate]] = {
    "light_fixture": InstallationRate(Decimal("0.5"), "1 electrician", 12, "EA"),
    "electrical_outlet": InstallationRate(Decimal("0.75"), "1 electrician", 10, "EA"),
    "carpet": InstallationRate(Decimal("0.025"), "2 carpet installers", 500, "SF"),
    "tile": InstallationRate(Decimal("0.1"), "1 tile setter + 1 helper", 80, "SF"),
    "cabinet": InstallationRate(Decimal("1.0"), "1 carpenter", 8, "LF"),
    "hvac_unit": InstallationRate(Decimal("8.0"), "1 HVAC technician + 1 helper", 1, "EA"),
    "paint": InstallationRate(Decimal("0.02"), "1 painter", 400, "SF"),
    "door": InstallationRate(Decimal("2.0"), "1 carpenter", 4, "EA"),
}


@dataclass(frozen=True, slots=True)
class Takeoff:
    component: str  # the takeoff row used (``cabinet``, ``carpet``, ...)
    quantity: Decimal
    unit: str
    formula: str
    inputs: dict[str, str | int | bool | None] = field(default_factory=dict)
    method: Method = "count"
    assumptions: tuple[str, ...] = ()

    @property
    def is_measured(self) -> bool:
        """True for area/perimeter takeoffs (one per room, not one per detection)."""
        return self.method != "count"


def takeoff_key(component: str) -> str:
    """The takeoff row for a rule key or label (``kitchen_cabinet`` -> ``cabinet``)."""
    name = normalize_label(component)
    return TAKEOFF_KEYS.get(name, name)


def spec_for(component: str) -> QuantitySpec:
    return QUANTITY_ESTIMATES.get(takeoff_key(component), DEFAULT_SPEC)


def _q(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


def estimate_quantity(
    component: str,
    room_area_sf: Decimal | float | int | None,
    count: int = 1,
) -> Takeoff:
    """The quantity of ``component`` in a room of ``room_area_sf`` with ``count`` detections.

    Count-based rows: ``max(count, min_per_room)`` EA. Area rows: ``area x per_sf x waste``.
    Perimeter rows: ``4 x sqrt(area)`` (a square room), times ``per_sf`` for countertops.
    Quantities are rounded half-up to 0.01.
    """
    if count < 1:
        raise ValueError(f"count must be at least 1, got {count}")
    key = takeoff_key(component)
    spec = QUANTITY_ESTIMATES.get(key, DEFAULT_SPEC)
    area = None if room_area_sf is None else Decimal(str(room_area_sf))
    if area is not None and (not area.is_finite() or area <= 0):
        raise ValueError(f"room_area_sf must be a positive number, got {room_area_sf!r}")
    inputs: dict[str, str | int | bool | None] = {
        "component": key,
        "count": count,
        "room_area_sf": None if area is None else _fmt(area),
    }
    assumptions = (spec.note,) if spec.note else ()

    if spec.method == "count":
        quantity = Decimal(max(count, spec.min_per_room))
        inputs["min_per_room"] = spec.min_per_room
        return Takeoff(
            component=key,
            quantity=_q(quantity),
            unit=spec.unit,
            formula=f"max(count={count}, min_per_room={spec.min_per_room})",
            inputs=inputs,
            method="count",
            assumptions=assumptions,
        )
    if area is None:
        raise MissingTakeoffInput(f"{key} is measured from the room area; none was given")
    if spec.method == "area":
        quantity = area * spec.per_sf * spec.waste_factor
        if spec.unit == "SY":
            formula = f"{_fmt(area)} SF / 9 x {_fmt(spec.waste_factor)} waste"
        else:
            formula = f"{_fmt(area)} SF x {_fmt(spec.per_sf)} x {_fmt(spec.waste_factor)} waste"
        inputs["waste_factor"] = _fmt(spec.waste_factor)
        inputs["per_sf"] = _fmt(spec.per_sf) if spec.unit != "SY" else "1/9"
        return Takeoff(key, _q(quantity), spec.unit, formula, inputs, "area", assumptions)
    perimeter = Decimal(4) * area.sqrt()
    quantity = perimeter * spec.per_sf
    formula = f"4 x sqrt({_fmt(area)} SF)"
    if spec.per_sf != 1:
        formula += f" x {_fmt(COUNTERTOP_DEPTH_IN)}/12 ft depth"
        inputs["depth_in"] = _fmt(COUNTERTOP_DEPTH_IN)
    return Takeoff(key, _q(quantity), spec.unit, formula, inputs, "perimeter", assumptions)


def labor_hours(takeoff: Takeoff) -> Decimal | None:
    """Installation labor hours for a takeoff when the rate's unit matches, else ``None``."""
    rate = INSTALLATION_RATES.get(takeoff.component)
    if rate is None:
        return None
    quantity = takeoff.quantity * SF_PER_SY if takeoff.unit == "SY" else takeoff.quantity
    unit = "SF" if takeoff.unit == "SY" else takeoff.unit
    if unit != rate.unit:
        return None
    return _q(rate.labor_hours_per_unit * quantity)
