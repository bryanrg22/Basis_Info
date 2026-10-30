"""Measured takeoff inputs from the appraisal, and what they derive (TAKEOFF_RESEARCH.md).

The product's takeoffs ran on a typical area per room type (kitchen 150 SF, bathroom 75,
default 150) and could not size the 11 exterior and site labels the cost table prices per
SF or LF at all (WORKFLOW_REVIEW 2026-10-29, section 3, items 2 and 3). The appraisal
extraction carries the building's areas, stories, garage, site, state and effective date;
this module turns those fields into:

* :class:`BuildingMeasurements`: the typed inputs, each a :class:`Reading` that remembers
  the extraction field and source it came from (``measurements_from_fields``);
* per-room floor areas (:func:`room_areas`): the GLA allocated to the above-grade rooms
  in proportion to their typical area, with the appraisal's room program as the
  denominator so unphotographed rooms are counted and the areas never sum to more than
  the GLA; basement rooms at the same scale capped by the finished basement area; the
  garage from its area or car count (TAKEOFF_RESEARCH section 3.1);
* property-level quantities (:func:`property_quantities`): roof, siding and gutters from
  the footprint and perimeter, landscaping from the site area (sections 3.2-3.7), and the
  measurement a reviewer must supply for the rest (3.8, :data:`NEEDS_MEASUREMENT`);
* the cost index (:func:`resolve_cost_index`): the year of the intake's placed-in-service
  date and the factor of the intake's state, else the appraisal's effective-date year and
  the appraisal's state, else the settings (3.9).

Every number is a :class:`Derived` carrying its formula, its named inputs (value, field
and extraction source) and the assumptions it rests on, so ``process_assets`` can write
them on the takeoff and the export can show "GLA 1,850 x 150 / 1,390". The constants are
the assumption table of TAKEOFF_RESEARCH section 4. Pure functions: no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal, Protocol

from basis.domain.cost_indices import (
    LocationFactor,
    LocationTable,
    YearFactor,
    YearIndex,
    indices,
    location_factor_for_state_code,
    location_factor_for_zip,
)
from basis.domain.costs import year_factor_index
from basis.domain.rooms import normalize_room_type, room_weight_sf, typical_area_sf

QUANTUM: Final = Decimal("0.01")

# TAKEOFF_RESEARCH section 4: the named assumptions.
DEFAULT_STORIES: Final = Decimal(1)
FOOTPRINT_ASPECT_RATIO: Final = Decimal("1.5")  # long side = 1.5 x short side
ROOF_PITCH_FACTOR: Final = Decimal("1.118")  # 6/12 pitch: sqrt(1 + (6/12)^2)
EAVE_OVERHANG_FT: Final = Decimal(1)
STORY_WALL_HEIGHT_FT: Final = Decimal(9)  # 8 ft ceiling plus the floor structure
WALL_OPENINGS_SHARE: Final = Decimal("0.15")  # residential window-to-wall ratio
GUTTER_EAVES: Final = 2  # a gable roof: gutters on the two long eaves
GARAGE_SF_PER_CAR: Final = Decimal(240)  # a 12 x 20 ft one-car garage
SF_PER_ACRE: Final = Decimal(43560)
BASEMENT_FINISH_DEFAULT_PCT: Final = Decimal(100)
OTHER_ROOM_SF: Final = Decimal(200)  # the appraisal's rooms that are not kitchen or bedroom
HALF_BATH_SF: Final = Decimal(40)
KITCHEN_SF: Final = Decimal(150)
BEDROOM_SF: Final = Decimal(150)
FULL_BATH_SF: Final = Decimal(75)

# Extraction keys read for each input, in order of preference.
FIELD_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "gla_sf": ("improvements.gla_sqft", "cost_approach.dwelling_sqft"),
    "stories": ("improvements.stories",),
    "basement_sf": ("improvements.basement_area_sqft", "cost_approach.basement_sqft"),
    "basement_finish_pct": ("improvements.basement_finish_percent",),
    "garage_sf": ("cost_approach.garage_sqft",),
    "garage_cars": ("improvements.garage_cars",),
    "site_sf": ("site.area_sqft",),
    "site_acres": ("site.area_acres",),
    "total_rooms": ("improvements.total_rooms",),
    "bedrooms": ("improvements.bedrooms",),
    "bathrooms": ("improvements.bathrooms",),
    "year_built": ("improvements.year_built",),
}
TEXT_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "state": ("subject.state",),
    "zip": ("subject.zip",),
}
DATE_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "effective_date": ("reconciliation.effective_date_of_appraisal", "appraiser.effective_date"),
}

# Labels the cost table prices per SF/LF that the appraisal cannot size (section 3.8):
# the measurement a reviewer must supply, named on the takeoff.
NEEDS_MEASUREMENT: Final[dict[str, str]] = {
    "driveway": "driveway area in SF (site plan or aerial measurement)",
    "sidewalk": "sidewalk area in SF (site plan or aerial measurement)",
    "patio": "patio area in SF (site plan or aerial measurement)",
    "fence": "fence length in LF (site plan or aerial measurement)",
    "retaining_wall": "retaining wall face area in SF (site measurement)",
    "concrete_pad": "pad area in SF (site measurement)",
    "parking_lot": "paved area in SF (site plan or aerial measurement)",
}
PROPERTY_LABELS: Final = ("roof", "siding", "gutter", "landscaping")

Level = Literal["above_grade", "basement", "garage", "exterior"]


class FieldRow(Protocol):
    """One stored extraction field (``basis.db.models.ExtractionField`` satisfies it)."""

    @property
    def section(self) -> str: ...
    @property
    def field(self) -> str: ...
    @property
    def value_num(self) -> Decimal | None: ...
    @property
    def value_text(self) -> str | None: ...
    @property
    def value_date(self) -> date | None: ...
    @property
    def source(self) -> str: ...


class RoomView(Protocol):
    """What :func:`room_areas` needs of a room (``basis.db.models.Room`` satisfies it)."""

    @property
    def id(self) -> object: ...
    @property
    def room_type(self) -> str: ...
    @property
    def floor(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class Reading:
    """One numeric input and where it was read."""

    value: Decimal
    field: str
    source: str

    def describe(self) -> str:
        return f"{self.field} ({self.source})"


@dataclass(frozen=True, slots=True)
class TextReading:
    value: str
    field: str
    source: str

    def describe(self) -> str:
        return f"{self.field} ({self.source})"


@dataclass(frozen=True, slots=True)
class DateReading:
    value: date
    field: str
    source: str

    def describe(self) -> str:
        return f"{self.field} ({self.source})"


@dataclass(frozen=True, slots=True)
class BuildingMeasurements:
    """The appraisal's measurements; every field ``None`` when it was not read."""

    gla_sf: Reading | None = None
    stories: Reading | None = None
    basement_sf: Reading | None = None
    basement_finish_pct: Reading | None = None
    garage_sf: Reading | None = None
    garage_cars: Reading | None = None
    site_sf: Reading | None = None
    total_rooms: Reading | None = None
    bedrooms: Reading | None = None
    bathrooms: Reading | None = None
    year_built: Reading | None = None
    state: TextReading | None = None
    zip: TextReading | None = None
    effective_date: DateReading | None = None

    @property
    def is_empty(self) -> bool:
        return all(getattr(self, f.name) is None for f in fields(self))


@dataclass(frozen=True, slots=True)
class Derived:
    """A derived quantity with everything a reviewer needs to recompute it."""

    quantity: Decimal
    unit: str
    formula: str
    inputs: dict[str, str] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoomArea:
    """A room's floor area for the takeoff and how it was arrived at."""

    area_sf: Decimal
    source: str  # gla_share | basement_share | garage_area | typical_for_room_type | default_150_sf
    formula: str
    inputs: dict[str, str] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CostIndex:
    cost_year: int
    year_source: str | None  # None: the settings, with no study input consulted
    location_factor: Decimal
    location_source: str | None
    # The resolved indices (EVAL_REPORT 45): the time factor with its published index
    # values and, when the cost year has none, the assumption; the location row(s).
    year: YearFactor | None = None
    location: LocationFactor | None = None

    @property
    def year_assumption(self) -> str | None:
        return None if self.year is None else self.year.assumption

    @property
    def year_conversion(self) -> str | None:
        return None if self.year is None else self.year.conversion


# ------------------------------------------------------------------ reading the fields


def _q(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f") if value == value.to_integral_value() else f"{value:.2f}"


def _positive(value: Decimal | None) -> bool:
    return value is not None and value.is_finite() and value > 0


def measurements_from_fields(rows: Iterable[FieldRow]) -> BuildingMeasurements:
    """The measurements in the stored extraction fields. A numeric field is used when it
    is a finite positive number (stories and counts at least 1); the first key of
    :data:`FIELD_KEYS` that has one wins. A site area printed in acres only is converted
    (43,560 SF per acre)."""
    by_key: dict[str, FieldRow] = {}
    for row in rows:
        by_key.setdefault(f"{row.section}.{row.field}", row)

    def number(name: str) -> Reading | None:
        for key in FIELD_KEYS[name]:
            row = by_key.get(key)
            if row is None:
                continue
            value = row.value_num
            if value is None or isinstance(value, bool):
                continue
            value = Decimal(str(value))
            if name == "basement_finish_pct":
                if value.is_finite() and 0 <= value <= 100:
                    return Reading(value, key, row.source)
                continue
            if name in ("stories", "garage_cars", "total_rooms", "bedrooms"):
                if value.is_finite() and value >= 1 and value == value.to_integral_value():
                    return Reading(value, key, row.source)
                continue
            if _positive(value):
                return Reading(value, key, row.source)
        return None

    def text(name: str) -> TextReading | None:
        for key in TEXT_KEYS[name]:
            row = by_key.get(key)
            if row is not None and row.value_text and row.value_text.strip():
                return TextReading(row.value_text.strip(), key, row.source)
        return None

    def when(name: str) -> DateReading | None:
        for key in DATE_KEYS[name]:
            row = by_key.get(key)
            if row is not None and row.value_date is not None:
                return DateReading(row.value_date, key, row.source)
        return None

    site = number("site_sf")
    if site is None:
        acres = number("site_acres")
        if acres is not None:
            site = Reading(_q(acres.value * SF_PER_ACRE), acres.field, acres.source)
    return BuildingMeasurements(
        gla_sf=number("gla_sf"),
        stories=number("stories"),
        basement_sf=number("basement_sf"),
        basement_finish_pct=number("basement_finish_pct"),
        garage_sf=number("garage_sf"),
        garage_cars=number("garage_cars"),
        site_sf=site,
        total_rooms=number("total_rooms"),
        bedrooms=number("bedrooms"),
        bathrooms=number("bathrooms"),
        year_built=number("year_built"),
        state=text("state"),
        zip=text("zip"),
        effective_date=when("effective_date"),
    )


def bath_counts(bathrooms: Decimal) -> tuple[int, int]:
    """``(full, half)`` from the URAR ``full.half`` notation: ``2.1`` is two full baths and
    one half bath. A ``.5`` is read as one half bath (the colloquial "2.5 baths")."""
    full = int(bathrooms.to_integral_value(rounding="ROUND_DOWN"))
    fraction = bathrooms - full
    if fraction == 0:
        return full, 0
    if fraction == Decimal("0.5"):
        return full, 1
    return full, int((fraction * 10).to_integral_value(rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------ building geometry


def perimeter_ft(area_sf: Decimal) -> Decimal:
    """The perimeter of a rectangle of ``area_sf`` with :data:`FOOTPRINT_ASPECT_RATIO`."""
    short = (area_sf / FOOTPRINT_ASPECT_RATIO).sqrt()
    return 2 * short * (1 + FOOTPRINT_ASPECT_RATIO)


def long_side_ft(area_sf: Decimal) -> Decimal:
    return (area_sf / FOOTPRINT_ASPECT_RATIO).sqrt() * FOOTPRINT_ASPECT_RATIO


def _stories(m: BuildingMeasurements) -> tuple[Decimal, dict[str, str], tuple[str, ...]]:
    if m.stories is not None:
        inputs = {"stories": _fmt(m.stories.value), "stories_field": m.stories.describe()}
        return m.stories.value, inputs, ()
    return (
        DEFAULT_STORIES,
        {"stories": _fmt(DEFAULT_STORIES), "stories_field": "assumed"},
        (f"{_fmt(DEFAULT_STORIES)} story assumed: stories not printed",),
    )


def garage_area(m: BuildingMeasurements) -> Derived | None:
    """The garage area: the cost approach's garage line, else cars x 240 SF (section 3.6)."""
    if m.garage_sf is not None:
        return Derived(
            _q(m.garage_sf.value),
            "SF",
            f"{_fmt(m.garage_sf.value)} SF garage",
            {"garage_sf": _fmt(m.garage_sf.value), "garage_sf_field": m.garage_sf.describe()},
        )
    if m.garage_cars is not None:
        area = m.garage_cars.value * GARAGE_SF_PER_CAR
        return Derived(
            _q(area),
            "SF",
            f"{_fmt(m.garage_cars.value)} cars x {_fmt(GARAGE_SF_PER_CAR)} SF",
            {
                "garage_cars": _fmt(m.garage_cars.value),
                "garage_cars_field": m.garage_cars.describe(),
                "sf_per_car": _fmt(GARAGE_SF_PER_CAR),
            },
            (
                f"{_fmt(GARAGE_SF_PER_CAR)} SF per car (a one-car garage; two-car garages "
                "run larger)",
            ),
        )
    return None


def footprint(m: BuildingMeasurements) -> Derived | None:
    """The dwelling's footprint, GLA / stories (section 3.2)."""
    if m.gla_sf is None:
        return None
    stories, inputs, assumptions = _stories(m)
    area = m.gla_sf.value / stories
    return Derived(
        _q(area),
        "SF",
        f"{_fmt(m.gla_sf.value)} SF GLA / {_fmt(stories)} stories",
        {"gla_sf": _fmt(m.gla_sf.value), "gla_field": m.gla_sf.describe(), **inputs},
        assumptions,
    )


def ground_footprint(m: BuildingMeasurements) -> Derived | None:
    """The footprint plus the garage (an attached garage shares the roof and the
    ground-story walls)."""
    base = footprint(m)
    if base is None:
        return None
    garage = garage_area(m)
    if garage is None:
        return Derived(
            base.quantity,
            "SF",
            base.formula,
            base.inputs,
            (*base.assumptions, "no garage area: the footprint is the dwelling alone"),
        )
    return Derived(
        _q(base.quantity + garage.quantity),
        "SF",
        f"({base.formula}) + {garage.formula}",
        {**base.inputs, **garage.inputs},
        (*base.assumptions, *garage.assumptions, "the garage is attached and shares the roof"),
    )


def roof_area(m: BuildingMeasurements) -> Derived | None:
    """(ground footprint + perimeter x overhang) x pitch factor (section 3.3)."""
    ground = ground_footprint(m)
    if ground is None:
        return None
    perimeter = perimeter_ft(ground.quantity)
    plan = ground.quantity + perimeter * EAVE_OVERHANG_FT
    area = plan * ROOF_PITCH_FACTOR
    return Derived(
        _q(area),
        "SF",
        f"({ground.formula} + {_fmt(_q(perimeter))} ft perimeter x {_fmt(EAVE_OVERHANG_FT)} ft "
        f"overhang) x {ROOF_PITCH_FACTOR} pitch",
        {
            **ground.inputs,
            "ground_footprint_sf": _fmt(ground.quantity),
            "perimeter_ft": _fmt(_q(perimeter)),
            "aspect_ratio": _fmt(FOOTPRINT_ASPECT_RATIO),
            "overhang_ft": _fmt(EAVE_OVERHANG_FT),
            "pitch_factor": str(ROOF_PITCH_FACTOR),
        },
        (
            *ground.assumptions,
            f"rectangular footprint, {_fmt(FOOTPRINT_ASPECT_RATIO)}:1",
            "6/12 pitch (factor 1.118); 1 ft eave overhang; no waste",
        ),
    )


def siding_area(m: BuildingMeasurements) -> Derived | None:
    """Ground perimeter x 9 ft, plus the dwelling perimeter x 9 ft per upper story, less 15%
    openings (section 3.4)."""
    ground = ground_footprint(m)
    dwelling = footprint(m)
    if ground is None or dwelling is None:
        return None
    stories, _, _ = _stories(m)
    ground_perimeter = perimeter_ft(ground.quantity)
    upper_perimeter = perimeter_ft(dwelling.quantity)
    gross = ground_perimeter * STORY_WALL_HEIGHT_FT + upper_perimeter * STORY_WALL_HEIGHT_FT * (
        stories - 1
    )
    net = gross * (1 - WALL_OPENINGS_SHARE)
    formula = f"{_fmt(_q(ground_perimeter))} ft ground perimeter x {_fmt(STORY_WALL_HEIGHT_FT)} ft"
    if stories > 1:
        formula += (
            f" + {_fmt(_q(upper_perimeter))} ft x {_fmt(STORY_WALL_HEIGHT_FT)} ft x "
            f"{_fmt(stories - 1)} upper stories"
        )
    formula += f", less {_fmt(WALL_OPENINGS_SHARE * 100)}% openings"
    return Derived(
        _q(net),
        "SF",
        formula,
        {
            **ground.inputs,
            "ground_perimeter_ft": _fmt(_q(ground_perimeter)),
            "dwelling_perimeter_ft": _fmt(_q(upper_perimeter)),
            "aspect_ratio": _fmt(FOOTPRINT_ASPECT_RATIO),
            "wall_height_ft": _fmt(STORY_WALL_HEIGHT_FT),
            "openings_share": str(WALL_OPENINGS_SHARE),
        },
        (
            *ground.assumptions,
            f"rectangular footprint, {_fmt(FOOTPRINT_ASPECT_RATIO)}:1",
            "9 ft of exterior wall per story; 15% openings; gable ends not added",
        ),
    )


def gutter_length(m: BuildingMeasurements) -> Derived | None:
    """A gable roof's two long eaves, each the long side plus the overhangs (section 3.5)."""
    ground = ground_footprint(m)
    if ground is None:
        return None
    long = long_side_ft(ground.quantity)
    length = GUTTER_EAVES * (long + 2 * EAVE_OVERHANG_FT)
    return Derived(
        _q(length),
        "LF",
        f"{GUTTER_EAVES} eaves x ({_fmt(_q(long))} ft long side + 2 x "
        f"{_fmt(EAVE_OVERHANG_FT)} ft overhang)",
        {
            **ground.inputs,
            "ground_footprint_sf": _fmt(ground.quantity),
            "long_side_ft": _fmt(_q(long)),
            "aspect_ratio": _fmt(FOOTPRINT_ASPECT_RATIO),
            "eaves": str(GUTTER_EAVES),
            "overhang_ft": _fmt(EAVE_OVERHANG_FT),
        },
        (
            *ground.assumptions,
            f"rectangular footprint, {_fmt(FOOTPRINT_ASPECT_RATIO)}:1",
            "gable roof: gutters on the two long eaves (a hip roof takes the whole perimeter)",
        ),
    )


def landscaping_area(m: BuildingMeasurements) -> Derived | None:
    """Site area less the ground footprint; hardscape not deducted (section 3.7). An upper
    bound (``inputs["quantity_bound"] = "upper"``): the whole unbuilt lot is not lawn, so
    ``process_assets`` sends the asset to review (``takeoff:upper_bound``)."""
    ground = ground_footprint(m)
    if m.site_sf is None or ground is None:
        return None
    area = m.site_sf.value - ground.quantity
    if area <= 0:
        return None
    return Derived(
        _q(area),
        "SF",
        f"{_fmt(m.site_sf.value)} SF site - ({ground.formula})",
        {
            "site_sf": _fmt(m.site_sf.value),
            "site_field": m.site_sf.describe(),
            **ground.inputs,
            "ground_footprint_sf": _fmt(ground.quantity),
            "quantity_bound": "upper",
        },
        (
            *ground.assumptions,
            "hardscape (driveway, walks, patio) not deducted: an upper bound",
        ),
    )


def property_quantities(m: BuildingMeasurements) -> dict[str, Derived]:
    """The property-level takeoffs the appraisal can size, by takeoff label."""
    derived = {
        "roof": roof_area(m),
        "siding": siding_area(m),
        "gutter": gutter_length(m),
        "landscaping": landscaping_area(m),
    }
    return {label: d for label, d in derived.items() if d is not None}


# ------------------------------------------------------------------ room areas


def room_level(room_type: str | None, floor: str | None) -> Level:
    """Which floor-area pool a room draws from."""
    name = normalize_room_type(room_type)
    if name == "garage":
        return "garage"
    if name in ("exterior", "parking"):
        return "exterior"
    if name == "basement" or (floor or "").strip() == "0":
        return "basement"
    return "above_grade"


def program_weight(m: BuildingMeasurements) -> Derived | None:
    """The weight of the rooms the appraisal lists (section 3.1): one kitchen, the
    bedrooms, the full and half baths, and the other rooms at :data:`OTHER_ROOM_SF`.
    None without a room count."""
    if m.total_rooms is None:
        return None
    bedrooms = m.bedrooms.value if m.bedrooms is not None else Decimal(0)
    full, half = bath_counts(m.bathrooms.value) if m.bathrooms is not None else (0, 0)
    others = max(m.total_rooms.value - bedrooms - 1, Decimal(0))
    total = (
        KITCHEN_SF
        + bedrooms * BEDROOM_SF
        + full * FULL_BATH_SF
        + half * HALF_BATH_SF
        + others * OTHER_ROOM_SF
    )
    inputs = {
        "program_rooms": _fmt(m.total_rooms.value),
        "program_rooms_field": m.total_rooms.describe(),
        "program_bedrooms": _fmt(bedrooms),
        "program_full_baths": str(full),
        "program_half_baths": str(half),
        "program_other_rooms": _fmt(others),
    }
    if m.bedrooms is not None:
        inputs["program_bedrooms_field"] = m.bedrooms.describe()
    if m.bathrooms is not None:
        inputs["program_baths_field"] = m.bathrooms.describe()
    formula = (
        f"kitchen {_fmt(KITCHEN_SF)} + {_fmt(bedrooms)} bedrooms x {_fmt(BEDROOM_SF)} + "
        f"{full} full baths x {_fmt(FULL_BATH_SF)} + {half} half baths x {_fmt(HALF_BATH_SF)} + "
        f"{_fmt(others)} other rooms x {_fmt(OTHER_ROOM_SF)}"
    )
    return Derived(total, "SF", formula, inputs)


def room_areas(
    m: BuildingMeasurements, rooms: Sequence[RoomView], property_class: str
) -> dict[object, RoomArea]:
    """Each room's floor area for the takeoff (section 3.1), keyed by ``room.id``.

    Above-grade rooms: ``GLA x weight / W`` with ``W = max(program weight, sum of the
    study's above-grade weights)``. Basement rooms: ``weight x GLA / W`` capped so that
    together they do not exceed the finished basement area. A garage room: the garage
    area (shared equally by several). Exterior rooms, and every room when the GLA is
    unknown: today's typical area, recorded as such.
    """
    levels = {room.id: room_level(room.room_type, room.floor) for room in rooms}
    weights = {room.id: room_weight_sf(room.room_type) for room in rooms}
    areas: dict[object, RoomArea] = {}

    def typical(room: RoomView) -> RoomArea:
        area, known = typical_area_sf(room.room_type, property_class)
        return RoomArea(
            area,
            "typical_for_room_type" if known else "default_150_sf",
            f"{_fmt(area)} SF typical for {normalize_room_type(room.room_type) or 'unknown'}",
        )

    garage = garage_area(m)
    garage_rooms = [r for r in rooms if levels[r.id] == "garage"]
    for room in garage_rooms:
        if garage is None:
            areas[room.id] = typical(room)
            continue
        share = garage.quantity / len(garage_rooms)
        formula = garage.formula
        if len(garage_rooms) > 1:
            formula += f" / {len(garage_rooms)} garage rooms"
        areas[room.id] = RoomArea(
            _q(share), "garage_area", formula, garage.inputs, garage.assumptions
        )

    if m.gla_sf is None:
        for room in rooms:
            areas.setdefault(room.id, typical(room))
        return areas

    above = [r for r in rooms if levels[r.id] == "above_grade"]
    study_weight = sum((weights[r.id] for r in above), Decimal(0))
    program = program_weight(m)
    total_weight = max(program.quantity if program is not None else Decimal(0), study_weight)
    if total_weight <= 0:
        total_weight = study_weight or Decimal(1)
    scale = m.gla_sf.value / total_weight
    from_program = program is not None and program.quantity >= study_weight
    denominator = "program weight" if from_program else "study rooms' weight"
    base_inputs = {
        "gla_sf": _fmt(m.gla_sf.value),
        "gla_field": m.gla_sf.describe(),
        "weight_total_sf": _fmt(_q(total_weight)),
        "weight_total_from": denominator,
        "study_weight_sf": _fmt(study_weight),
        "scale": f"{scale:.4f}",
    }
    if program is not None:
        base_inputs.update(program.inputs)
        base_inputs["program_weight_sf"] = _fmt(program.quantity)
        base_inputs["program_formula"] = program.formula
    assumptions = (
        "room area = GLA x typical area of the room type / the house's total typical area "
        f"({denominator})",
    )
    for room in above:
        weight = weights[room.id]
        area = m.gla_sf.value * weight / total_weight
        areas[room.id] = RoomArea(
            _q(area),
            "gla_share",
            f"{_fmt(m.gla_sf.value)} SF GLA x {_fmt(weight)} / {_fmt(_q(total_weight))} "
            f"({normalize_room_type(room.room_type) or 'unknown'} weight / {denominator})",
            {**base_inputs, "room_weight_sf": _fmt(weight)},
            assumptions,
        )

    basement_rooms = [r for r in rooms if levels[r.id] == "basement"]
    if basement_rooms:
        finished: Decimal | None = None
        basement_inputs: dict[str, str] = {}
        basement_assumptions: tuple[str, ...] = ()
        if m.basement_sf is not None:
            if m.basement_finish_pct is not None:
                finish = m.basement_finish_pct.value
                basement_inputs["basement_finish_pct"] = _fmt(finish)
                basement_inputs["basement_finish_field"] = m.basement_finish_pct.describe()
            else:
                finish = BASEMENT_FINISH_DEFAULT_PCT
                basement_inputs["basement_finish_pct"] = _fmt(finish)
                basement_inputs["basement_finish_field"] = "assumed"
                basement_assumptions = ("basement finish not printed: 100% assumed",)
            finished = m.basement_sf.value * finish / 100
            basement_inputs["basement_sf"] = _fmt(m.basement_sf.value)
            basement_inputs["basement_field"] = m.basement_sf.describe()
            basement_inputs["finished_basement_sf"] = _fmt(_q(finished))
        basement_weight = sum((weights[r.id] for r in basement_rooms), Decimal(0))
        scaled_total = basement_weight * scale
        cap = finished is not None and finished > 0 and scaled_total > finished
        for room in basement_rooms:
            weight = weights[room.id]
            if cap:
                assert finished is not None
                area = finished * weight / basement_weight
                formula = (
                    f"{_fmt(_q(finished))} SF finished basement x {_fmt(weight)} / "
                    f"{_fmt(basement_weight)} (basement rooms' weight; the house scale "
                    f"{scale:.2f} would exceed the finished area)"
                )
            else:
                area = weight * scale
                formula = (
                    f"{_fmt(weight)} SF typical x {scale:.4f} house scale "
                    f"({_fmt(m.gla_sf.value)} SF GLA / {_fmt(_q(total_weight))})"
                )
            areas[room.id] = RoomArea(
                _q(area),
                "basement_share",
                formula,
                {**base_inputs, **basement_inputs, "room_weight_sf": _fmt(weight)},
                (
                    *assumptions,
                    "basement rooms are sized at the house scale, capped by the finished "
                    "basement area",
                    *basement_assumptions,
                ),
            )

    for room in rooms:
        areas.setdefault(room.id, typical(room))
    return areas


# ------------------------------------------------------------------ cost index


def location_table_for(
    cost_year: int, tables: Mapping[int, LocationTable] | None
) -> LocationTable | None:
    """The table of the latest year at or before ``cost_year``, else the earliest later
    one; None (the released book's) when no tables are given."""
    if not tables:
        return None
    years = sorted(tables)
    before = [y for y in years if y <= cost_year]
    return tables[before[-1] if before else years[0]]


def resolve_cost_index(
    m: BuildingMeasurements | None,
    *,
    cost_year: int,
    location_factor: Decimal,
    placed_in_service: date | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    supplement: Mapping[int, YearIndex] | None = None,
    location_tables: Mapping[int, LocationTable] | None = None,
) -> CostIndex:
    """The cost year and location factor for the study (TAKEOFF_RESEARCH section 3.9,
    COST_INDICES.md sections 5 and 10).

    Year: ``placed_in_service`` (the intake's date) when the index table reaches it (1970
    or later: a year after the latest published index is allowed and carries the
    assumption), else the appraisal's effective date when it does, else the setting. The
    factor is the Historical Cost Index ratio (``year``), from the store's values
    (``supplement``) or the released book. Location: from the table of the latest year at
    or before the cost year among ``location_tables`` (the store's; the released book's
    alone when None), the city factor of ``zip_code`` (the intake's) when the table lists
    its three-digit prefix, else the mean of ``state``'s cities (the intake's), else the
    appraisal's ZIP, then state, else the setting. Each carries the
    source it came from, with the index values, book and page; a candidate that was given
    but could not be used is named in the source so the reviewer sees why the next one
    applied.
    """
    book = indices()
    table_range = f"{book.first_year}-{book.last_year}"
    year, year_source = cost_year, "COST_YEAR setting"
    skipped: list[str] = []
    if placed_in_service is not None:
        if placed_in_service.year >= book.first_year:
            year = placed_in_service.year
            year_source = f"placed in service {placed_in_service} (intake)"
        else:
            skipped.append(
                f"placed in service {placed_in_service} (intake) is before the {table_range} "
                "Historical Cost Index"
            )
    if year_source == "COST_YEAR setting" and m is not None and m.effective_date is not None:
        when = m.effective_date
        if when.value.year >= book.first_year:
            year = when.value.year
            year_source = f"appraisal effective date {when.value} ({when.describe()})"
        else:
            skipped.append(
                f"appraisal effective date {when.value} is before the {table_range} "
                "Historical Cost Index"
            )
    if year_source == "COST_YEAR setting" and skipped:
        year_source = "COST_YEAR setting: " + "; ".join(skipped)
    elif skipped:
        year_source = f"{year_source}; {'; '.join(skipped)}"
    year_factor = year_factor_index(year, supplement)
    table = location_table_for(year, location_tables)

    factor, location_source = location_factor, "COST_LOCATION_FACTOR setting"
    location: LocationFactor | None = None
    unused: list[str] = []
    candidates: list[tuple[str, str, str]] = []  # (kind, value, origin)
    if zip_code:
        candidates.append(("zip", zip_code.strip(), "intake"))
    if state:
        candidates.append(("state", state.strip().upper(), "intake"))
    if m is not None and m.zip is not None:
        candidates.append(("zip", m.zip.value.strip(), m.zip.describe()))
    if m is not None and m.state is not None:
        candidates.append(("state", m.state.value.strip().upper(), m.state.describe()))
    for kind, value, origin in candidates:
        found = (
            location_factor_for_zip(value, table)
            if kind == "zip"
            else location_factor_for_state_code(value, table)
        )
        if found is None:
            unused.append(f"no location factor for {kind} {value!r} ({origin})")
            continue
        factor, location = found.factor, found
        location_source = f"{kind} {value} ({origin}): {found.citation()}"
        break
    if location is None and unused:
        location_source = "COST_LOCATION_FACTOR setting: " + "; ".join(unused)
    elif unused:
        location_source = f"{location_source}; {'; '.join(unused)}"
    return CostIndex(year, year_source, factor, location_source, year_factor, location)


__all__ = [
    "NEEDS_MEASUREMENT",
    "PROPERTY_LABELS",
    "BuildingMeasurements",
    "CostIndex",
    "DateReading",
    "Derived",
    "Reading",
    "RoomArea",
    "TextReading",
    "bath_counts",
    "footprint",
    "garage_area",
    "ground_footprint",
    "gutter_length",
    "landscaping_area",
    "long_side_ft",
    "measurements_from_fields",
    "perimeter_ft",
    "program_weight",
    "property_quantities",
    "location_table_for",
    "resolve_cost_index",
    "roof_area",
    "room_areas",
    "room_level",
    "siding_area",
]
