"""What a priced row and a signed report may stand on (EVAL_REPORT 78).

The rule (Bryan, 2026-11-02): a value substituted because an input is missing, unreadable,
or because a model call failed never reaches dollars. Before EVAL_REPORT 78 the pipeline
priced on typical room areas, a 1-story default, a count of 1, a 100 % finished basement,
the six legacy placeholder rows and a default depreciation class, and a report could be
signed on them. Now:

* A row whose quantity would stand on a substitute is written **unpriced**: quantity 0, no
  cost, the review reason ``input:<code>`` and the missing input named on its cost row
  (:data:`INPUTS` says what is missing and where the engineer supplies it).
* The study's result withholds the allocation, so no report is rendered and none can be
  signed, while any **blocker** remains (:func:`row_blockers` plus the study-level checks in
  :mod:`basis.evidence.result`): an unpriced row, an unclassified row, a location factor or
  cost year the study's inputs do not give, a building model on defaulted inputs. Each
  blocker says what is missing and where it is fixed.

What is not a substitute, and is priced with the assumption written on the row: a published
method applied to inputs the study has (RSMeans' square-foot model, a garage of N cars at
240 SF per car, the GLA shared over the rooms by typical weights, a wall area from a floor
area at an 8-foot ceiling), and an input no appraisal carries that every cost segregation
study estimates (a buried utility lateral). Those are documented methods, not missing data.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from basis.domain.takeoffs import Takeoff

REASON_PREFIX: Final = "input:"
# The takeoff input naming the missing input of a row written unpriced.
MISSING_INPUT: Final = "missing_input"
# Measured labels whose quantity the takeoff can only guess (the room's perimeter taken as
# the cabinet run, and the countertop on it): never priced on the guess.
GUESSED_RUNS: Final = frozenset({"cabinet", "countertop"})


@dataclass(frozen=True, slots=True)
class MissingInput:
    """One input a row can be missing: what it is, in the reviewer's words, and where the
    engineer supplies it."""

    code: str
    what: str
    fix: str

    @property
    def reason(self) -> str:
        return f"{REASON_PREFIX}{self.code}"


def _input(code: str, what: str, fix: str) -> tuple[str, MissingInput]:
    return code, MissingInput(code, what, fix)


APPRAISAL_FIX: Final = "add or correct the field at the appraisal review ({fields})"
MEASURE_FIX: Final = "enter the measured quantity at the assets review"

INPUTS: Final[dict[str, MissingInput]] = dict(
    (
        _input(
            "gla",
            "the gross living area (GLA) is not on the appraisal, so no room has a floor area",
            APPRAISAL_FIX.format(fields="improvements.gla_sqft"),
        ),
        _input(
            "stories",
            "the number of stories is not on the appraisal, so the footprint (roof, siding, "
            "gutters, landscaping), the smoke alarms and the building model's type cannot be "
            "derived",
            APPRAISAL_FIX.format(fields="improvements.stories")
            + " (the building type can also be set with structure_model at the assets review)",
        ),
        _input(
            "garage_area",
            "the garage area is not on the appraisal (no garage square feet and no car count)",
            APPRAISAL_FIX.format(fields="cost_approach.garage_sqft or improvements.garage_cars"),
        ),
        _input(
            "basement_area",
            "the basement area is not on the appraisal, so a basement room has no floor area",
            APPRAISAL_FIX.format(fields="improvements.basement_area_sqft"),
        ),
        _input(
            "basement_finish",
            "the finished share of the basement is not on the appraisal",
            APPRAISAL_FIX.format(fields="improvements.basement_finish_percent"),
        ),
        _input(
            "site_area",
            "the site area is not on the appraisal, so landscaping cannot be derived",
            APPRAISAL_FIX.format(fields="site.area_sqft or site.area_acres"),
        ),
        _input(
            "exterior_area",
            "an exterior room has no floor area to measure this item by",
            MEASURE_FIX,
        ),
        _input(
            "room_type",
            "the room's type is not known (its photos were ambiguous), so its share of the "
            "living area cannot be sized",
            "set the room's type at the rooms review",
        ),
        _input(
            "room",
            "the component is in no room, so it has no area to measure by",
            "assign it to a room at the components review, or " + MEASURE_FIX,
        ),
        _input(
            "count",
            "the component has no count",
            "set its count at the components review",
        ),
        _input(
            "cabinet_run",
            "the cabinet run is not measured (the room's perimeter is not a cabinet run)",
            MEASURE_FIX,
        ),
        _input(
            "model_class",
            "the quality rating is not on the appraisal, so the RSMeans class of construction "
            "the building model splits the cost by would be assumed",
            APPRAISAL_FIX.format(fields="improvements.quality")
            + ", or confirm the class (structure_model) at the assets review",
        ),
        _input(
            "cost_data_date",
            "the cost approach prints no cost data date, so the appraiser's cost new cannot be "
            "brought to the cost year",
            APPRAISAL_FIX.format(fields="cost_approach.effective_date_of_cost_data"),
        ),
        _input(
            "location",
            "neither the intake nor the appraisal gives a ZIP or state with an RSMeans location "
            "factor, so the setting's factor would apply",
            APPRAISAL_FIX.format(fields="subject.zip or subject.state"),
        ),
        _input(
            "cost_year",
            "neither the placed-in-service date nor the appraisal's effective date gives a cost "
            "year within the cost index, so the setting's year would apply",
            APPRAISAL_FIX.format(fields="reconciliation.effective_date_of_appraisal"),
        ),
        _input(
            "takeoff_areas_typical",
            "TAKEOFF_AREAS=typical measures nothing (typical room areas are not a measurement)",
            "set TAKEOFF_AREAS=appraisal",
        ),
    )
)


def with_missing_input(takeoff: Takeoff, code: str) -> Takeoff:
    """``takeoff`` as a row that is not priced because ``code``'s input is missing: quantity
    0 (no guessed quantity is recorded as if measured), the formula saying what is missing,
    the code in ``inputs[MISSING_INPUT]``."""
    found = INPUTS[code]
    return Takeoff(
        component=takeoff.component,
        quantity=Decimal("0.00"),
        unit=takeoff.unit,
        formula=f"not measured: {found.what}",
        inputs={**takeoff.inputs, MISSING_INPUT: code},
        method=takeoff.method,
        assumptions=(),
    )


def missing(code: str) -> MissingInput:
    """The input ``code`` names; ``KeyError`` for an unknown code (a programming error)."""
    return INPUTS[code]


def codes_in(review_reason: str | None) -> list[str]:
    """The ``input:<code>`` reasons of an asset's ``review_reason`` (``"; "``-joined), in
    order, unknown codes kept as they are."""
    if not review_reason:
        return []
    return [
        part[len(REASON_PREFIX) :]
        for part in review_reason.split("; ")
        if part.startswith(REASON_PREFIX)
    ]


@dataclass(frozen=True, slots=True)
class Blocker:
    """One reason the study's report is withheld: a stable ``code``, the reviewer's
    ``message``, how many assets it holds (0 for a study-level blocker) and the ``fix``."""

    code: str
    message: str
    assets: int
    fix: str


def row_blockers(reasons: Iterable[str]) -> list[Blocker]:
    """One blocker per missing input among the rows' ``input:<code>`` reasons, with the
    number of rows it leaves unpriced, in :data:`INPUTS` order (unknown codes last)."""
    counts = Counter(reasons)
    order = {code: i for i, code in enumerate(INPUTS)}
    out = []
    for code in sorted(counts, key=lambda c: (order.get(c, len(order)), c)):
        found = INPUTS.get(code)
        what = found.what if found is not None else f"input {code!r} is missing"
        fix = found.fix if found is not None else "see the asset's review reason"
        out.append(Blocker(f"input:{code}", what, counts[code], fix))
    return out


__all__ = [
    "GUESSED_RUNS",
    "INPUTS",
    "MISSING_INPUT",
    "REASON_PREFIX",
    "Blocker",
    "MissingInput",
    "codes_in",
    "missing",
    "row_blockers",
    "with_missing_input",
]
