"""Facade truth for M3 (EVAL_REPORT 109): one wall of a HABS house, its openings and heights as
its measured elevation drawing shows them, and the photographs of that wall.

One JSON per facade in ``evals/fixtures/measure_facades/`` (schema ``measure_facade/v1``), read
twice from the elevations like the house truths of :mod:`evals.measure_truth` (EVAL_REPORT
103.1: two independent readings, every disagreement recorded in ``passes.disagreements``).

**What a facade is.** One wall plane of the enclosed building, as an elevation drawing projects
it: between the two outside corners the file names (``wall.length``), from grade to the eave
(``eave``, the same eave as the house's truth file). Its *gross face* is length x eave height
(EVAL_REPORT 103.1's wall face, one side; no gable triangle). An *opening* is a window or door in
that rectangle, measured as the bounding box of its exterior trim (casing, head and sill): the
area the siding does not cover (Hover's reports list openings by width, height and the tops,
sills and sides of their trim; :mod:`basis.geo.facade`). Openings behind a porch are in it
(``behind_porch``); shutters and vents above the eave are not. The *net face* (the siding,
foundation included) is gross less openings; the *trim* is the openings' perimeters.

**Photos** (``photos``): the photographs of this wall kept under
``$BASIS_EVAL_DATA_DIR/measure_truth/photos/`` (never in the repository), each with its source,
licence, the camera's horizontal field of view where it is known (EXIF focal length, the street
picture's recorded field of view) and which wall in the picture is this one.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA: Final = "measure_facade/v1"
FIXTURES: Final = Path(__file__).parent / "fixtures" / "measure_facades"
How = Literal["printed", "derived", "scaled"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Value(_Model):
    ft: float = Field(gt=0)
    how: How
    text: str | None = None  # the printed string, where printed
    sheet: str
    note: str | None = None


class Opening(_Model):
    id: str
    kind: Literal["window", "door", "garage"]
    storey: Literal["basement", "first", "second", "attic"]
    width_ft: float = Field(gt=0)
    height_ft: float = Field(gt=0)
    left_ft: float  # the trim's left edge from the facade's left corner, as the elevation shows it
    sill_ft: float  # the trim's bottom above grade (at the facade's mean grade)
    sheet_box: tuple[float, float, float, float] | None = None  # sheet px x0, y0, x1, y1
    behind_porch: bool = False
    how: How = "scaled"
    note: str | None = None


class Disagreement(_Model):
    what: str
    a: str
    b: str
    kept: str


class Passes(_Model):
    a: str
    b: str
    disagreements: tuple[Disagreement, ...] = ()


class Photo(_Model):
    id: str  # the file stem under photos/
    path: str  # relative to $BASIS_EVAL_DATA_DIR/measure_truth/photos/
    source: str  # the page it came from
    licence: str
    taken: str | None = None
    hfov_deg: float | None = None  # horizontal field of view of the image as stored
    hfov_from: str | None = None
    which: str  # which wall in the picture is this facade
    width_px: int
    height_px: int


class Facade(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_: Literal["measure_facade/v1"] = Field(alias="schema")
    id: str
    house: str
    side: str  # the drawing's name for the side (north, east ...)
    description: str  # the wall, its corners, its eave, in words the photos can be read by
    sheet: str
    scale: str
    px_per_ft: float = Field(gt=0)
    length: Value
    eave: Value  # grade to eave on this wall
    openings: tuple[Opening, ...]
    porch: str | None = None  # what stands in front of the wall
    passes: Passes
    photos: tuple[Photo, ...] = ()
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Truth:
    value: float
    tier: How


def truths(f: Facade) -> dict[str, Truth]:
    """The scored quantities of a facade. The tier is the weakest input's."""
    order = {"printed": 0, "derived": 1, "scaled": 2}
    gross_tier: How = max((f.length.how, f.eave.how), key=order.__getitem__)
    openings_sf = sum(o.width_ft * o.height_ft for o in f.openings)
    gross = f.length.ft * f.eave.ft
    out = {
        "eave_height_ft": Truth(f.eave.ft, f.eave.how),
        "gross_sf": Truth(gross, gross_tier),
        "openings_sf": Truth(openings_sf, "scaled"),
        "net_sf": Truth(gross - openings_sf, gross_tier if gross_tier == "scaled" else "derived"),
        "trim_lf": Truth(sum(2 * (o.width_ft + o.height_ft) for o in f.openings), "scaled"),
        "windows": Truth(sum(1 for o in f.openings if o.kind == "window"), "printed"),
        "doors": Truth(sum(1 for o in f.openings if o.kind in ("door", "garage")), "printed"),
    }
    return {k: v for k, v in out.items() if v.value > 0 or k in ("windows", "doors")}


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    detail: str


def check(f: Facade) -> list[Finding]:
    """Every opening inside the wall's rectangle, ids unique, sizes plausible."""
    found = []
    ids = [o.id for o in f.openings]
    if len(set(ids)) != len(ids):
        found.append(Finding("duplicate_id", ", ".join(ids)))
    for o in f.openings:
        if o.left_ft < -0.5 or o.left_ft + o.width_ft > f.length.ft + 0.5:
            found.append(Finding("outside", f"{o.id} runs past the wall's ends"))
        if o.sill_ft < -0.5 or o.sill_ft + o.height_ft > f.eave.ft + 0.5:
            found.append(Finding("outside", f"{o.id} runs past grade or the eave"))
        if not (0.8 <= o.width_ft <= 20 and 0.8 <= o.height_ft <= 12):
            found.append(
                Finding("implausible", f"{o.id} is {o.width_ft:.2f} x {o.height_ft:.2f} ft")
            )
        if o.sheet_box is not None:
            x0, y0, x1, y1 = o.sheet_box
            w, h = (x1 - x0) / f.px_per_ft, (y1 - y0) / f.px_per_ft
            if not (
                math.isclose(w, o.width_ft, abs_tol=0.02)
                and math.isclose(h, o.height_ft, abs_tol=0.02)
            ):
                found.append(Finding("box_mismatch", f"{o.id}: box {w:.2f} x {h:.2f} ft"))
    return found


def load(path: Path) -> Facade:
    return Facade.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_all(directory: Path = FIXTURES) -> list[Facade]:
    return [load(p) for p in sorted(directory.glob("*.json"))]


def ids(facades: Iterable[Facade]) -> list[str]:
    return [f.id for f in facades]


__all__ = [
    "FIXTURES",
    "SCHEMA",
    "Facade",
    "Finding",
    "Opening",
    "Photo",
    "Truth",
    "Value",
    "check",
    "ids",
    "load",
    "load_all",
    "truths",
]
