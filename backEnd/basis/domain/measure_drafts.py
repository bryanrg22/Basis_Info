"""AI-drafted measurements in the priced path, for the engineer to verify (EVAL_REPORT 110).

Step 6 of ``docs/review_2026-10-15/AI_MEASUREMENT_PLAN.md``: the methods measured to beat
today's formulas draft the quantities they beat, each as a **draft**: priced at once, flagged
for review, and holding the report until the engineer verifies it when the dollars it could
move are above ``MEASURE_DRAFT_VERIFY_USD`` (the maintainer's pricing rule (a), plan section
8.3). Pure: no I/O; ``process_assets`` reads the inputs and writes what this module returns.

**Exterior (M1, EVAL_REPORT 105; ``MEASURE_DRAFTS_EXTERIOR``).** From the building shell the
geo gather stored beside its lidar summary (``GEO_SHELL``; :func:`basis.geo.shell.as_record`):

* *siding*: the walls (the roof's outline less an 18 in overhang) as one closed line, times
  the main eave height (the measurement's ``height_ft``): the gross wall face; less today's
  15 % openings share (:data:`basis.domain.measurements.WALL_OPENINGS_SHARE`; no facade
  measurement is stored in a study yet, EVAL_REPORT 109);
* *roof*: the roof's outline as flown (eaves included) as an area, times the planes' slope
  factor (``roof_area_m2 / roof_plan_m2``: each roof cell's plan area over the cosine of its
  plane's slope, the standard plan x sqrt(1 + (rise/run)^2));
* *gutter*: the outline's eave edges as lines.

The footprint stays the appraisal's (EVAL_REPORT 105.1: the lidar's reads 12 to 43 % high under
porches), so nothing that rests on it changes. The perimeter default is M1's lidar shell, free
and within the noise of M5's vision model (EVAL_REPORT 108: perimeter 3.3 % on five houses
against M1's 5.8 % on six; paired, not separated), the cheapest within the noise of the best.

**Interior (M4d, EVAL_REPORT 107; ``MEASURE_DRAFTS_INTERIOR``).** From one vision call per
room of a measured type with photos (``basis.activities.measure_rooms``): the floor area sizes
the room's floor rows (carpet, tile, flooring, ceiling, concrete floor, with their waste
factors), the wall area its wall rows (paint, wall finish), the perimeter its trim rows
(baseboard, crown molding, wainscoting at 2 ft), and in a kitchen the countertop and cabinet
run their rows, which today stay unpriced (``input:cabinet_run``). A room whose call failed,
was refused or answered null for a quantity keeps today's row: a failed call never becomes a
number (EVAL_REPORT 78).

**The record.** Each drafted row's takeoff carries ``inputs["draft"]`` (:func:`draft_record`):
who drew it (``drawn_by``: ``model:lidar-shell@v1`` or
``model:<model>@measure_interior_reference-v1``), ``verified``, the quantity, its expected
error (the measured mean absolute error of the method for that quantity, with the section that
measured it), the dollars it could move (:func:`exposure_usd`) and whether that holds the report
(``blocks``), what it replaced (today's formula and quantity, or the missing input), and the
factor applied to the shape (``factor``). An exterior draft also carries
``inputs["measurement"]``: the shape as a measurement record (:mod:`basis.domain.traced`, the
same record an engineer's trace makes), so the measure tool draws it dashed with its edges and
Verified box (EVAL_REPORT 104). An interior draft has no shape (the model answers numbers).

**Verifying.** The existing asset correction: the measurement sent back with ``verified: true``
(an untouched draft), an edit of it (``drawn_by: engineer`` with the draft as its ``original``),
or a typed quantity. Each makes the row the engineer's (``quantity_source: engineer``); the
draft's record is kept beside it (:func:`resolved`), so the model's original is never lost. A
measurement descended from an exterior draft keeps the draft's factor (the roof's slope, the
siding's openings): what the engineer checks on the picture is the outline, not the pitch.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final, Literal

from basis.domain import traced
from basis.domain.measurements import WALL_OPENINGS_SHARE, Derived
from basis.domain.takeoffs import QUANTITY_ESTIMATES, Takeoff

DRAFT: Final = "draft"  # the takeoff input holding the draft's record
# The review reason on every row priced on a draft not yet verified
# (E.MEASURE_DRAFT_UNVERIFIED), and
# the blocker of the result while one's exposure is above the threshold.
DRAFT_UNVERIFIED: Final = "measure:DRAFT_UNVERIFIED"
BLOCKER: Final = "measure:draft_unverified"
BLOCKER_FIX: Final = "verify or edit this measurement in the measure tool"
QUANTITY_SOURCE: Final = "draft"
QUANTUM: Final = Decimal("0.01")
FT_PER_M: Final = Decimal(1) / Decimal("0.3048")
SF_PER_M2: Final = FT_PER_M * FT_PER_M

SHELL_DRAWN_BY: Final = "model:lidar-shell@v1"
INTERIOR_PROMPT: Final = "measure_interior_reference"
INTERIOR_PROMPT_VERSION: Final = "v1"
LIDAR_CELL_M: Final = 0.5  # the shell's grid (basis.geo.lidar.CELL_M): the draft's pixel

# The method's measured mean absolute error per drafted quantity, in percent, with the section
# that measured it. Exterior: M1 on the HABS houses standing unchanged (EVAL_REPORT 105.1, six
# houses; roof and gutter two). Interior: claude-sonnet-5-5 with the reference prompt on the
# ARKitScenes rooms (EVAL_REPORT 107.4: 24 rooms; countertop 3 and cabinet run 4 kitchens).
EXTERIOR_ERROR_PCT: Final[dict[str, tuple[Decimal, str]]] = {
    "siding": (Decimal("6.4"), "EVAL_REPORT 105.1: wall face, six houses"),
    "roof": (Decimal("10.4"), "EVAL_REPORT 105.1: roof surface, two houses"),
    "gutter": (Decimal("6.2"), "EVAL_REPORT 105.1: gutter, two houses"),
}
INTERIOR_ERROR_PCT: Final[dict[str, tuple[Decimal, str]]] = {
    "floor_area_sf": (Decimal("22.5"), "EVAL_REPORT 107.4: floor area, 24 rooms"),
    "wall_area_sf": (Decimal("11.9"), "EVAL_REPORT 107.4: wall area, 15 rooms"),
    "perimeter_ft": (Decimal("10.3"), "EVAL_REPORT 107.4: perimeter, 24 rooms"),
    "countertop_sf": (Decimal("20.8"), "EVAL_REPORT 107.4: countertop, 3 kitchens"),
    "cabinet_lf": (Decimal("29.1"), "EVAL_REPORT 107.4: cabinet run, 4 kitchens"),
    "ceiling_height_ft": (Decimal("5.3"), "EVAL_REPORT 107.4: ceiling height, 16 rooms"),
}
# The room types EVAL_REPORT 107 measured (kitchen, bedroom, bathroom, living room; a family
# room is drafted as a living room). Every other room keeps today's rows.
INTERIOR_ROOM_TYPES: Final[dict[str, str]] = {
    "kitchen": "kitchen",
    "bedroom": "bedroom",
    "bathroom": "bathroom",
    "living_room": "living room",
    "family_room": "living room",
}
# Which answer quantity sizes which takeoff row (basis.domain.takeoffs.QUANTITY_ESTIMATES).
FLOOR_ROWS: Final = frozenset({"carpet", "tile", "flooring", "concrete_floor", "ceiling_finish"})
WALL_ROWS: Final = frozenset({"paint", "wall_finish"})
TRIM_ROWS: Final = frozenset({"baseboard", "crown_molding", "wainscoting"})
ROW_QUANTITY: Final[dict[str, str]] = {
    **dict.fromkeys(FLOOR_ROWS, "floor_area_sf"),
    **dict.fromkeys(WALL_ROWS, "wall_area_sf"),
    **dict.fromkeys(TRIM_ROWS, "perimeter_ft"),
    "countertop": "countertop_sf",
    "cabinet": "cabinet_lf",
}
KITCHEN_ONLY: Final = frozenset({"countertop_sf", "cabinet_lf"})
# The answer's quantities a room draft keeps (feet and square feet; the eval's QUANTITIES).
ANSWER_QUANTITIES: Final = (
    "floor_area_sf",
    "length_ft",
    "width_ft",
    "ceiling_height_ft",
    "perimeter_ft",
    "wall_area_sf",
    "countertop_sf",
    "cabinet_lf",
)


def _q(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    return f"{_q(value):,}"


def _dec(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        return None
    try:
        out = Decimal(str(value))
    except ArithmeticError:
        return None
    return out if out.is_finite() else None


@dataclass(frozen=True, slots=True)
class Draft:
    """A drafted row: the takeoff fields, the draft's record and (exterior) the shape."""

    label: str  # the takeoff row (``roof``, ``carpet``, ...)
    quantity: Decimal
    unit: str
    formula: str
    inputs: dict[str, str | int | bool | None]
    assumptions: tuple[str, ...]
    record: dict[str, Any]  # inputs["draft"]
    measurement: dict[str, Any] | None = None  # inputs["measurement"] (Traced.record())
    shape: traced.Traced | None = None
    factor: Decimal = Decimal(1)

    def takeoff(self, method: Literal["count", "area", "perimeter"]) -> Takeoff:
        return Takeoff(
            component=self.label,
            quantity=self.quantity,
            unit=self.unit,
            formula=self.formula,
            inputs=dict(self.inputs),
            method=method,
            assumptions=self.assumptions,
        )


def draft_record(
    *,
    drawn_by: str,
    quantity: Decimal,
    unit: str,
    error: tuple[Decimal, str],
    source: str,
    replaced: Derived | Takeoff | None,
    missing: str | None,
    factor: Decimal = Decimal(1),
    factor_reason: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The draft's record (module doc), JSON-ready; ``exposure_usd`` and ``blocks`` are set
    once the row is priced (:func:`priced`)."""
    replaces: dict[str, Any] = {}
    if missing is not None:
        replaces["missing_input"] = missing
    elif replaced is not None:
        replaces = {"formula": replaced.formula, "quantity": f"{replaced.quantity} {replaced.unit}"}
    record: dict[str, Any] = {
        "drawn_by": drawn_by,
        "verified": False,
        "quantity": format(_q(quantity), "f"),
        "unit": unit,
        "expected_error_pct": format(error[0], "f"),
        "error_source": error[1],
        "source": source,
        "replaces": replaces,
    }
    if factor != 1:
        record["factor"] = format(factor, "f")
        record["factor_reason"] = factor_reason
    if extra:
        record.update(extra)
    return record


# --------------------------------------------------------------------------- exterior


@dataclass(frozen=True, slots=True)
class ShellSource:
    """Which gather's shell the exterior drafts came from (written on the record)."""

    gather_id: str
    project: str | None
    year: int | None
    shell: Mapping[str, Any]

    @property
    def text(self) -> str:
        when = f" ({self.year})" if self.year is not None else ""
        project = f" {self.project}" if self.project else ""
        return f"USGS 3DEP lidar{project}{when}, public records gather {self.gather_id}"


def _point(value: Any) -> list[float] | None:
    if (
        not isinstance(value, list | tuple)
        or len(value) != 2
        or not all(
            isinstance(c, int | float) and not isinstance(c, bool) and math.isfinite(c)
            for c in value
        )
    ):
        return None
    return [float(value[0]), float(value[1])]


def _ring(value: Any) -> list[list[float]] | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    out: list[list[float]] = []
    for item in value:
        point = _point(item)
        if point is None:
            return None
        out.append(point)
    return out


def _image(source: ShellSource, what: str) -> dict[str, Any]:
    return {"source": f"{source.text}: {what}", "pixel_size_m": LIDAR_CELL_M}


def _shape_body(
    unit: str, shapes: list[dict[str, Any]], image: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    return {
        "unit": unit,
        "space": "lonlat",
        "shapes": shapes,
        "image": image,
        **extra,
        "drawn_by": SHELL_DRAWN_BY,
        "verified": False,
    }


def _measured(body: dict[str, Any]) -> traced.Traced | None:
    try:
        return traced.measure(body)
    except traced.MeasurementError:
        return None


def exterior_drafts(
    source: ShellSource,
    replaced: Mapping[str, Derived],
    missing: Mapping[str, str],
) -> tuple[dict[str, Draft], dict[str, str]]:
    """``(drafts by label, why each label was not drafted)`` from a stored shell. ``replaced``:
    today's derived quantity per label; ``missing``: the missing input of a label today's
    formulas cannot size. A part the shell refused, or a shell stored before the roof outline
    and the edges' ends were kept, drafts nothing for that label."""
    shell = source.shell
    drafts: dict[str, Draft] = {}
    skipped: dict[str, str] = {}

    def done(
        label: str, shape: traced.Traced, factor: Decimal, reason: str | None, what: str
    ) -> None:
        quantity = _q(shape.quantity * factor)
        error = EXTERIOR_ERROR_PCT[label]
        old = replaced.get(label)
        record = draft_record(
            drawn_by=SHELL_DRAWN_BY,
            quantity=quantity,
            unit=shape.unit,
            error=error,
            source=source.text,
            replaced=old,
            missing=None if old is not None else missing.get(label),
            factor=factor,
            factor_reason=reason,
        )
        tail = f" x {factor} ({reason})" if factor != 1 else ""
        formula = (
            f"{_fmt(quantity)} {shape.unit} drafted by Basis from the lidar shell: {what}{tail}; "
            f"not verified; expected error about {error[0]} % ({error[1]})"
        )
        inputs: dict[str, str | int | bool | None] = {
            "component": label,
            "room_area_sf": None,
            "quantity_source": QUANTITY_SOURCE,
            "drawn_by": SHELL_DRAWN_BY,
            "shape_quantity": f"{shape.quantity} {shape.unit}",
            "draft_factor": format(factor, "f"),
            "draft_expected_error_pct": format(error[0], "f"),
            "gather_id": source.gather_id,
        }
        if old is not None:
            inputs["replaced_formula"] = old.formula
            inputs["replaced_quantity"] = f"{old.quantity} {old.unit}"
        elif label in missing:
            inputs["replaced_missing_input"] = missing[label]
        drafts[label] = Draft(
            label=label,
            quantity=quantity,
            unit=shape.unit,
            formula=formula,
            inputs=inputs,
            assumptions=_assumptions(label),
            record=record,
            measurement=shape.record(),
            shape=shape,
            factor=factor,
        )

    refused = {
        str(r.get("part")): str(r.get("code"))
        for r in shell.get("refusals") or []
        if isinstance(r, Mapping)
    }
    if shell.get("code") is not None and "building" in refused:
        reason = f"the shell was refused ({refused['building']})"
        return {}, dict.fromkeys(EXTERIOR_ERROR_PCT, reason)

    walls = _ring(shell.get("wall_outline"))
    eave = _dec(shell.get("eave_main_m"))
    if walls is None:
        skipped["siding"] = f"no walls drafted ({refused.get('walls', 'none stored')})"
    elif eave is None or eave <= 0:
        skipped["siding"] = "no main eave height (no roof plane)"
    else:
        height = float(_q(eave * FT_PER_M))
        body = _shape_body(
            "SF",
            [{"kind": "line", "path": [*walls, walls[0]]}],
            _image(source, "walls 18 in inside the roof's edge, main eave height"),
            height_ft=height,
        )
        shape = _measured(body)
        if shape is None:
            skipped["siding"] = "the walls do not make a valid measurement"
        else:
            factor = Decimal(1) - WALL_OPENINGS_SHARE
            assert shape.length_ft is not None
            done(
                "siding",
                shape,
                factor,
                f"less {_q(WALL_OPENINGS_SHARE * 100).normalize()} % openings",
                f"{_fmt(shape.length_ft)} ft of wall x {height:g} ft main eave",
            )

    roof_ring = _ring(shell.get("roof_outline"))
    area_m2, plan_m2 = _dec(shell.get("roof_area_m2")), _dec(shell.get("roof_plan_m2"))
    if roof_ring is None:
        skipped["roof"] = "no roof outline stored with the shell"
    elif area_m2 is None or plan_m2 is None or plan_m2 <= 0 or area_m2 < plan_m2:
        skipped["roof"] = f"no roof planes ({refused.get('roof', 'none stored')})"
    else:
        shape = _measured(
            _shape_body(
                "SF",
                [{"kind": "area", "ring": roof_ring}],
                _image(source, "the roof's outline as flown, eaves included"),
            )
        )
        if shape is None:
            skipped["roof"] = "the roof outline does not make a valid measurement"
        else:
            factor = (area_m2 / plan_m2).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            pitch = _dec(shell.get("pitch_deg"))
            reason = "the roof planes' slope" + (
                f", {pitch.quantize(Decimal('0.1'))} degrees on average"
                if pitch is not None
                else ""
            )
            assert shape.area_sf is not None
            done("roof", shape, factor, reason, f"{_fmt(shape.area_sf)} SF of roof in plan")

    eaves: list[dict[str, Any]] = []
    unstored = False
    for edge in shell.get("edges") or []:
        if not isinstance(edge, Mapping) or edge.get("kind") != "eave":
            continue
        start, end = _point(edge.get("start")), _point(edge.get("end"))
        if start is None or end is None:
            unstored = True
            break
        eaves.append({"kind": "line", "path": [start, end]})
    if unstored:
        skipped["gutter"] = "the eaves' ends are not stored with the shell"
    elif not eaves:
        skipped["gutter"] = f"no eave edge ({refused.get('roof', 'no plane slopes to an edge')})"
    else:
        shape = _measured(_shape_body("LF", eaves, _image(source, "the roof outline's eave edges")))
        if shape is None:
            skipped["gutter"] = "the eaves do not make a valid measurement"
        else:
            done("gutter", shape, Decimal(1), None, f"{len(eaves)} eave edges of the roof outline")
    return drafts, skipped


def _assumptions(label: str) -> tuple[str, ...]:
    if label == "siding":
        return (
            "walls: the lidar roof outline less an 18 in overhang (EVAL_REPORT 105)",
            "wall height: the main eave above grade; 15% openings; gable ends not added",
        )
    if label == "roof":
        return ("roof surface: the outline's plan area x the lidar planes' slope factor",)
    return ("gutters on the eave edges the lidar planes slope down to",)


def shell_factor(record: Mapping[str, Any] | None) -> Decimal:
    """The factor a draft's record applies to its shape (1 when it has none)."""
    if not isinstance(record, Mapping):
        return Decimal(1)
    value = _dec(record.get("factor"))
    return value if value is not None and value > 0 else Decimal(1)


def descends_from_draft(shape: traced.Traced) -> bool:
    """Whether an engineer's measurement is a model draft (verified or not) or an edit of one."""
    return shape.by_model or (shape.original is not None and shape.original.by_model)


# --------------------------------------------------------------------------- interior


@dataclass(frozen=True, slots=True)
class RoomDraft:
    """One room's drafted quantities (the activity's answer, in feet and square feet)."""

    room_type: str
    quantities: Mapping[str, Decimal]
    model: str
    photos: tuple[str, ...]
    notes: str | None = None
    confidence: str | None = None
    room_fully_seen: bool | None = None

    @property
    def drawn_by(self) -> str:
        return f"model:{self.model}@{INTERIOR_PROMPT}-{INTERIOR_PROMPT_VERSION}"


def answer_quantities(values: Mapping[str, Any], room_type: str) -> dict[str, Decimal]:
    """The usable quantities of an answer: positive finite numbers; a kitchen's counters and
    cabinets only in a kitchen (the prompt asks for null elsewhere)."""
    out: dict[str, Decimal] = {}
    for key in ANSWER_QUANTITIES:
        value = _dec(values.get(key))
        if value is None or value <= 0:
            continue
        if key in KITCHEN_ONLY and room_type != "kitchen":
            continue
        out[key] = value
    return out


def room_takeoff(key: str, room: RoomDraft, today: Takeoff, missing: str | None) -> Draft | None:
    """The row ``key`` of a drafted room, or None when the draft does not size it (a counted
    row, a row no answer quantity sizes, or the answer gave no number for it). ``today``: the
    row as today's formulas write it (its formula is kept as what the draft replaced);
    ``missing``: its missing input when today's row is unpriced."""
    name = ROW_QUANTITY.get(key)
    spec = QUANTITY_ESTIMATES.get(key)
    if name is None or spec is None or spec.method == "count":
        return None
    value = room.quantities.get(name)
    if value is None:
        return None
    error = INTERIOR_ERROR_PCT[name]
    if key in FLOOR_ROWS:
        quantity = value * spec.per_sf * spec.waste_factor
        unit = spec.unit
        what = (
            f"{_fmt(value)} SF of floor / 9 x {spec.waste_factor} waste"
            if unit == "SY"
            else f"{_fmt(value)} SF of floor x {spec.waste_factor} waste"
        )
    elif key in WALL_ROWS:
        quantity, unit, what = value, "SF", f"{_fmt(value)} SF of wall"
    elif key == "wainscoting":
        quantity, unit = value * spec.per_sf, "SF"
        what = f"{_fmt(value)} ft of wall x {spec.per_sf} ft height"
    elif key in TRIM_ROWS:
        quantity, unit, what = value, "LF", f"{_fmt(value)} ft of wall"
    elif key == "countertop":
        quantity, unit, what = value, "SF", f"{_fmt(value)} SF of counter top"
    else:  # cabinet
        quantity, unit, what = value, "LF", f"{_fmt(value)} ft of base cabinet run"
    quantity = _q(quantity)
    record = draft_record(
        drawn_by=room.drawn_by,
        quantity=quantity,
        unit=unit,
        error=error,
        source=f"{len(room.photos)} photos of the room",
        replaced=None if missing is not None else today,
        missing=missing,
        extra={
            "photos": list(room.photos),
            "answer": {k: format(v, "f") for k, v in room.quantities.items()},
            **({"notes": room.notes} if room.notes else {}),
            **({"confidence": room.confidence} if room.confidence else {}),
            **(
                {"room_fully_seen": room.room_fully_seen}
                if room.room_fully_seen is not None
                else {}
            ),
        },
    )
    floor = room.quantities.get("floor_area_sf")
    inputs: dict[str, str | int | bool | None] = {
        **{k: v for k, v in today.inputs.items() if k not in ("missing_input",)},
        "room_area_sf": None if floor is None else format(floor.normalize(), "f"),
        "replaced_room_area_sf": today.inputs.get("room_area_sf"),
        "quantity_source": QUANTITY_SOURCE,
        "drawn_by": room.drawn_by,
        "room_area_source": "room_photos_draft",
        "draft_expected_error_pct": format(error[0], "f"),
        "draft_quantity": name,
    }
    if missing is None:
        inputs["replaced_formula"] = today.formula
        inputs["replaced_quantity"] = f"{today.quantity} {today.unit}"
    else:
        inputs["replaced_missing_input"] = missing
    formula = (
        f"{_fmt(quantity)} {unit} drafted by Basis from the room's photos: {what}; not verified; "
        f"expected error about {error[0]} % ({error[1]})"
    )
    note = "measured from ordinary room photos against objects of known size (EVAL_REPORT 107)"
    extra_note = (f"Legacy note replaced: {spec.note}",) if key in ("cabinet", "countertop") else ()
    return Draft(
        label=key,
        quantity=quantity,
        unit=unit,
        formula=formula,
        inputs=inputs,
        assumptions=(note, *extra_note),
        record=record,
    )


# --------------------------------------------------------------------------- pricing rule (a)


def exposure_usd(record: Mapping[str, Any], total: Decimal | None) -> Decimal | None:
    """The dollars an unverified draft could move: its expected error times its priced total
    (EVAL_REPORT 110.4 chose it); None when the row is not priced."""
    error = _dec(record.get("expected_error_pct"))
    if total is None or error is None:
        return None
    return _q(abs(total) * error / 100)


def priced(record: Mapping[str, Any], total: Decimal | None, threshold: Decimal) -> dict[str, Any]:
    """The record with its exposure and whether it holds the report: a pending draft whose
    exposure is above ``threshold``. A row the cost table cannot price moves no dollars (its
    own ``unpriced`` blocker holds the report until it is priced, and the next pricing
    decides)."""
    exposure = exposure_usd(record, total)
    out = dict(record)
    out["exposure_usd"] = None if exposure is None else format(exposure, "f")
    out["verify_above_usd"] = format(threshold, "f")
    out["blocks"] = pending(out) and exposure is not None and exposure > threshold
    return out


def pending(record: Any) -> bool:
    """Whether a row's quantity still stands on a model's draft nobody verified."""
    return (
        isinstance(record, Mapping)
        and record.get("verified") is not True
        and record.get("replaced_by") is None
    )


def blocking_threshold(inputs: Any) -> Decimal | None:
    """The threshold an unverified draft's exposure was above when the row was priced, when a
    takeoff's inputs hold the report for it; None otherwise."""
    record = inputs.get(DRAFT) if isinstance(inputs, Mapping) else None
    if not pending(record) or not isinstance(record, Mapping) or record.get("blocks") is not True:
        return None
    threshold = _dec(record.get("verify_above_usd"))
    return Decimal(0) if threshold is None else threshold


def resolved(
    record: Mapping[str, Any], *, verified: bool, by: str, quantity: str
) -> dict[str, Any]:
    """The draft's record once the engineer acted: ``verified`` (the untouched draft marked
    verified), or replaced by an edit or a typed quantity (``replaced_by``); the model's own
    numbers stay as they were."""
    out = {
        k: v for k, v in record.items() if k not in ("blocks", "exposure_usd", "verify_above_usd")
    }
    if verified:
        out["verified"] = True
    else:
        out["replaced_by"] = by
        out["engineer_quantity"] = quantity
    return out


def blocker_message(count: int, threshold: Decimal) -> str:
    noun = "measurement" if count == 1 else "measurements"
    return (
        f"{count} {noun} drafted by Basis {'is' if count == 1 else 'are'} not verified and "
        f"could each move more than ${_fmt(threshold)} (its expected error times its price)"
    )


@dataclass(frozen=True, slots=True)
class Sweep:
    """One threshold of the sweep (EVAL_REPORT 110.4): the drafts it holds and their share."""

    threshold: Decimal
    held: int
    drafts: int
    held_usd: Decimal
    drafted_usd: Decimal
    exposure_held_usd: Decimal
    exposure_usd: Decimal
    extra: dict[str, Any] = field(default_factory=dict)


def sweep(
    rows: Sequence[tuple[Decimal | None, Decimal | None]], thresholds: Sequence[Decimal]
) -> list[Sweep]:
    """For each threshold: of the drafted rows ``(priced total, expected error %)``, how many
    it holds, the dollars they carry, and the share of the drafts' exposure it covers."""
    out = []
    exposures = [
        (total, None if total is None or err is None else _q(abs(total) * err / 100))
        for total, err in rows
    ]
    for t in thresholds:
        held = [(tot, e) for tot, e in exposures if e is not None and e > t]
        out.append(
            Sweep(
                threshold=t,
                held=len(held),
                drafts=len(rows),
                held_usd=sum((tot or Decimal(0) for tot, _ in held), Decimal(0)),
                drafted_usd=sum((tot or Decimal(0) for tot, _ in exposures), Decimal(0)),
                exposure_held_usd=sum((e or Decimal(0) for _, e in held), Decimal(0)),
                exposure_usd=sum((e or Decimal(0) for _, e in exposures), Decimal(0)),
            )
        )
    return out


__all__ = [
    "BLOCKER",
    "BLOCKER_FIX",
    "DRAFT",
    "DRAFT_UNVERIFIED",
    "EXTERIOR_ERROR_PCT",
    "INTERIOR_ERROR_PCT",
    "INTERIOR_ROOM_TYPES",
    "QUANTITY_SOURCE",
    "ROW_QUANTITY",
    "SHELL_DRAWN_BY",
    "Draft",
    "RoomDraft",
    "ShellSource",
    "Sweep",
    "answer_quantities",
    "blocker_message",
    "blocking_threshold",
    "descends_from_draft",
    "draft_record",
    "exposure_usd",
    "exterior_drafts",
    "pending",
    "priced",
    "resolved",
    "room_takeoff",
    "shell_factor",
    "sweep",
]
