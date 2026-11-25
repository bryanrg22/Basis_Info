"""Renovations in the study result (EVAL_REPORT 102, ``basis.evidence.renovations`` through
``compute_result``): a row's own age; a project's schedule apportioning the capitalized amount
with its own factor; qualified improvement property at each exclusion; the dispositions with
their depreciation allowed or allowable, computed by hand; every check and blocker; the
before-service variant; and a study without renovations answering exactly as before.
Fabricated values."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from basis.db.enums import Bucket
from basis.domain.basis import PropertyUse
from basis.domain.component_age import ComponentAge
from basis.domain.renovations import Removal, StudyRenovations
from basis.evidence.renovations import (
    AcquisitionLine,
    ProjectRow,
    compute,
    qip,
)
from basis.evidence.result import ScheduleRow, StudyResultOut, compute_result
from tests.unit.evidence.test_result import PRODUCED, _id, clean, intake

D = Decimal
KEY = "kitchen-2025"
TWENTY = D(20)
BASE = [r for r in PRODUCED if r.rcn is not None]
ORIGINAL = [
    ScheduleRow(_id(21), "Kitchen cabinets", "y5", D("12000.00"), label="kitchen_cabinet"),
    ScheduleRow(_id(22), "Countertops", "y5", D("4000.00"), label="countertop"),
    ScheduleRow(_id(23), "Hardwood floor", "y27_5", D("6000.00"), label="hardwood_floor"),
]
NEW = [
    ScheduleRow(_id(31), "Kitchen cabinets", "y5", D("15000.00"), label="kitchen_cabinet"),
    ScheduleRow(_id(32), "Countertops", "y5", D("6000.00"), label="countertop"),
    ScheduleRow(_id(33), "Tile floor", "y27_5", D("9000.00"), label="tile"),
]


def residential(**over: object) -> Any:
    values: dict[str, object] = {
        "purchase_price": "205000.00",
        "property_use": "residential_rental",
        "transient_lodging": False,
        "dwelling_unit_income_share": "1",
        "acquisition_date": date(2023, 3, 10),
        "placed_in_service_date": date(2023, 4, 1),
    }
    values.update(over)
    return intake(**values)


def project(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "key": KEY,
        "kind": "after_service",
        "scope": "Kitchen remodel",
        "contract_date": "2025-02-10",
        "work_start_date": "2025-03-03",
        "placed_in_service_date": "2025-05-30",
        "invoice_total": "38500.00",
        "capitalized_amount": "38500.00",
        "capitalized_confirmed_by": "A. Preparer, CPA",
    }
    base.update(over)
    return base


def document(*projects: dict[str, Any], **over: Any) -> StudyRenovations:
    return StudyRenovations.model_validate({"projects": list(projects or [project()]), **over})


def removed(row: ScheduleRow, when: date = date(2025, 3, 3), key: str = KEY) -> ScheduleRow:
    return replace(row, removal=Removal(key, when, D(1), "demolition photographs"))


def built(rows: list[ScheduleRow], version: int = 1) -> list[ScheduleRow]:
    return [replace(r, renovations_version=version) for r in rows]


def kitchen(
    *,
    doc: StudyRenovations | None = None,
    the_intake: Any = None,
    original: list[ScheduleRow] | None = None,
    new: list[ScheduleRow] | None = None,
) -> StudyResultOut:
    rows = built(
        BASE
        + (original if original is not None else [removed(r) for r in ORIGINAL])
        + [replace(r, project=KEY) for r in (new if new is not None else NEW)]
    )
    return compute_result(
        rows,
        the_intake or residential(),
        1,
        clean(),
        renovations=doc or document(),
        renovations_version=1,
    )


def codes(result: StudyResultOut) -> list[str]:
    return [b.code for b in result.blockers]


# ------------------------------------------------------------------ no renovations


def test_a_study_without_renovations_adds_no_member() -> None:
    result = compute_result(BASE + ORIGINAL, residential(), 1, clean())
    dumped = result.model_dump(mode="json")
    assert "renovations" not in dumped and "component_ages" not in dumped
    assert result.allocation is not None and result.blockers == []


# ------------------------------------------------------------------ the seller's renovation


def aged(
    year: int | None = 2021, age: Decimal | None = None, life: Decimal = TWENTY
) -> list[ScheduleRow]:
    return [
        replace(
            r,
            age=ComponentAge(
                age, year, life, "appraisal_update_status", "Fully Updated 1-5 years", "r"
            ),
        )
        if r.label == "kitchen_cabinet"
        else r
        for r in ORIGINAL
    ]


def test_a_rows_own_age_moves_its_depreciation_and_the_allocation() -> None:
    before = compute_result(BASE + ORIGINAL, residential(), 1, clean())
    after = compute_result(BASE + aged(), residential(), 1, clean())
    assert before.reconciliation is not None and after.reconciliation is not None
    # 2023 - 2021 = 2 years of 20: 10 %, not the appraiser's 25 %.
    ages = after.component_ages
    assert ages is not None and len(ages) == 1
    assert ages[0].effective_age == D(2) and ages[0].rate == D("0.100000")
    assert ages[0].replaced_rate == D("0.250000") and ages[0].rcnld == D("10800.00")
    assert before.reconciliation.gap_share == D("0.018919")
    assert after.reconciliation.gap_share == D("0.009189")
    assert after.reconciliation.status == "reconciled"
    share = {str(a.label): a.allocated for a in after.allocation.assets}  # type: ignore[union-attr]
    old = {str(a.label): a.allocated for a in before.allocation.assets}  # type: ignore[union-attr]
    assert share["kitchen_cabinet"] > old["kitchen_cabinet"]
    assert share["countertop"] < old["countertop"]  # the rest gives way
    assert after.allocation.rows_sum == before.allocation.rows_sum  # type: ignore[union-attr]
    # The by-row depreciation still foots to the cent.
    assert after.schedule.rcnld_total == sum(
        (a.rcnld for a in after.allocation.assets if a.rcnld is not None),
        D(0),  # type: ignore[union-attr]
    )


def test_an_effective_age_is_used_as_given() -> None:
    result = compute_result(BASE + aged(None, D("5")), residential(), 1, clean())
    assert result.component_ages is not None and result.component_ages[0].rate == D("0.250000")


@pytest.mark.parametrize(
    ("year", "age", "the_intake"),
    [
        (2024, None, residential()),  # installed after the building was placed in service
        (2000, None, residential()),  # 23 years of a 20-year life
    ],
)
def test_an_age_that_cannot_be_used_blocks(year: int, age: Decimal | None, the_intake: Any) -> None:
    result = compute_result(BASE + aged(year, age), the_intake, 1, clean())
    assert "component_age" in codes(result) and result.allocation is None
    assert result.component_ages is not None and result.component_ages[0].rate is None


# ------------------------------------------------------------------ the buyer's renovation


def test_the_project_schedule_apportions_the_capitalized_amount_with_its_own_factor() -> None:
    result = kitchen()
    assert result.blockers == [], codes(result)
    baseline = compute_result(BASE + ORIGINAL, residential(), 1, clean())
    # The acquisition is exactly what it was without the project's rows.
    assert result.allocation == baseline.allocation
    assert result.reconciliation == baseline.reconciliation
    assert result.renovations is not None
    (p,) = result.renovations.projects
    assert p.estimate_total == D("30000.00") and p.factor == D("1.283333333")
    assert p.rows_sum == p.capitalized_amount == D("38500.00")
    assert [(r.label, r.allocated) for r in p.rows] == [
        ("kitchen_cabinet", D("19250.00")),
        ("countertop", D("7700.00")),
        ("tile", D("11550.00")),
    ]
    assert [(c.schedule_class, c.allocated) for c in p.by_class] == [
        ("y5", D("26950.00")),
        ("y27_5", D("11550.00")),
    ]
    # Contract 2025-02-10, after 2025-01-19: 100 % (Notice 2026-11).
    assert p.bonus is not None and p.bonus.rate == D("1.00") and "Notice 2026-11" in p.bonus.source
    assert {c.code for c in result.renovations.checks} == {
        "renovation_invoices",  # no invoice documents attached
        "renovation_estimate_gap",  # 22.1 % between the estimates and the actual cost
    }


def test_the_dispositions_by_hand() -> None:
    """The answer key of EVAL_REPORT 102 (ii): the acquisition allocated the cabinets
    9,173.55, the countertops 3,057.85 and the hardwood floor 4,586.78, placed in service
    2023-04-01 with the 2023 phase-down bonus (80 %), removed 2025-03-03."""
    result = kitchen()
    assert result.renovations is not None
    got = {d.label: d for d in result.renovations.dispositions}
    cab, top, floor = got["kitchen_cabinet"], got["countertop"], got["hardwood_floor"]
    # 9,173.55 x 0.80 = 7,338.84; 1,834.71 x (20 % + 32 % + 19.20 %/2) = 366.94 + 587.11 + 176.13
    assert (cab.unadjusted_basis, cab.bonus, cab.allowed_or_allowable, cab.adjusted_basis) == (
        D("9173.55"), D("7338.84"), D("8469.02"), D("704.53"),
    )  # fmt: skip
    assert [y.amount for y in cab.depreciation_by_year] == [D("366.94"), D("587.11"), D("176.13")]
    assert (top.bonus, top.allowed_or_allowable, top.adjusted_basis) == (
        D("2446.28"), D("2823.00"), D("234.85"),
    )  # fmt: skip
    # 27.5-year, April: 2.576 % + 3.636 % + 3.636 % x 2.5/12 = 118.16 + 166.78 + 34.74
    assert (floor.bonus, floor.allowed_or_allowable, floor.adjusted_basis) == (
        D("0.00"), D("319.68"), D("4267.10"),
    )  # fmt: skip
    assert (cab.treatment, cab.parent) == ("whole_asset", "own_asset")
    assert (floor.treatment, floor.parent) == ("election_required", "building")
    (year,) = result.renovations.dispositions_by_year
    assert (year.year, year.lines, year.election_lines) == (2025, 3, 1)
    assert year.adjusted_basis == D("5206.48")


def test_the_cpas_bonus_rate_replaces_the_statutory_default() -> None:
    result = kitchen(doc=document(acquisition_bonus_rate="0"))
    got = {d.label: d for d in result.renovations.dispositions}  # type: ignore[union-attr]
    # 9,173.55 x (20 % + 32 % + 9.60 %) = 1,834.71 + 2,935.54 + 880.66
    assert got["kitchen_cabinet"].bonus == D("0.00")
    assert got["kitchen_cabinet"].allowed_or_allowable == D("5650.91")


def test_a_multi_year_project_splits_its_dispositions_by_year() -> None:
    original = [
        removed(ORIGINAL[0], date(2025, 3, 3)),
        ORIGINAL[1],
        removed(ORIGINAL[2], date(2026, 1, 15)),
    ]
    long = project(placed_in_service_date="2026-02-01")
    result = kitchen(doc=document(long), original=original)
    assert result.blockers == [], codes(result)
    years = [(y.year, y.lines) for y in result.renovations.dispositions_by_year]  # type: ignore[union-attr]
    assert years == [(2025, 1), (2026, 1)]
    assert "dispositions_by_year" in {c.code for c in result.renovations.checks}  # type: ignore[union-attr]


BEFORE_SERVICE = project(
    key="other",
    kind="before_service",
    placed_in_service_date=None,
    work_start_date="2023-03-20",
    contract_date=None,
)
UNCONFIRMED = project(capitalized_amount=None, capitalized_confirmed_by=None)


@pytest.mark.parametrize(
    ("doc", "original", "code"),
    [
        (document(UNCONFIRMED), None, "renovation_capitalized"),
        (document(project(addition=True)), None, "renovation_removal"),
        (
            document(project(), BEFORE_SERVICE),
            [removed(ORIGINAL[0], key="other")],
            "renovation_removal",
        ),
        (document(), [removed(ORIGINAL[0], key="unknown")], "renovation_removal"),
        (document(), [removed(ORIGINAL[0], date(2023, 3, 1))], "renovation_removal"),
    ],
)
def test_each_renovation_blocker_withholds_the_allocation(
    doc: StudyRenovations, original: list[ScheduleRow] | None, code: str
) -> None:
    result = kitchen(doc=doc, original=original)
    assert code in codes(result)
    assert result.allocation is None and "blockers" in result.missing


def test_an_addition_with_stated_removals_is_disposed_of() -> None:
    result = kitchen(doc=document(project(addition=True, removals_stated=True)))
    assert result.blockers == [] and len(result.renovations.dispositions) == 3  # type: ignore[union-attr]


def test_a_project_without_priced_rows_blocks() -> None:
    result = kitchen(new=[])
    assert codes(result) == ["renovation_rows"]


def test_an_unpriced_project_row_is_the_unpriced_blocker() -> None:
    result = kitchen(new=[*NEW, ScheduleRow(_id(34), "Range hood", "y5", None, label="range_hood")])
    assert "unpriced" in codes(result)


def test_rows_of_a_project_no_longer_listed_block() -> None:
    other = document(project(key="bath-2026"))
    result = kitchen(doc=other, original=list(ORIGINAL))
    assert "renovation_unknown_project" in codes(result)


def test_a_removal_on_a_project_row_blocks() -> None:
    result = kitchen(new=[removed(NEW[0]), *NEW[1:]])
    assert "renovation_removal" in codes(result)


def test_renovations_changed_since_the_assets_were_built_block() -> None:
    rows = built(BASE + ORIGINAL, version=1)
    result = compute_result(
        rows, residential(), 1, clean(), renovations=document(), renovations_version=2
    )
    assert "renovations_changed" in codes(result)
    # Built with renovations, which were then cleared: still stale.
    result = compute_result(rows, residential(), 1, clean())
    assert "renovations_changed" in codes(result)


def test_project_dates_before_the_building_block() -> None:
    early = project(
        work_start_date="2023-02-01", contract_date=None, placed_in_service_date="2023-03-01"
    )
    assert "renovation_dates" in codes(kitchen(doc=document(early), original=list(ORIGINAL)))


def test_the_aerial_bracket_flags_a_date_outside_it() -> None:
    bracket = {
        "last_without": {"date": "2025-06-15", "reference": "view 7"},
        "first_with": {"date": "2025-09-01", "reference": "view 8"},
    }
    result = kitchen(doc=document(project(aerial=bracket)))
    assert result.blockers == []
    assert "aerial_bracket" in {c.code for c in result.renovations.checks}  # type: ignore[union-attr]
    roof = {"label": "kitchen_cabinet", "bracket": {
        "last_without": {"date": "2018-01-01", "reference": "a"},
        "first_with": {"date": "2019-01-01", "reference": "b"}}}  # fmt: skip
    rows = built(BASE + aged(2021), version=1)
    result = compute_result(
        rows,
        residential(),
        1,
        clean(),
        renovations=document(exterior_brackets=[roof]),
        renovations_version=1,
    )
    messages = [c.message for c in result.renovations.checks if c.code == "aerial_bracket"]  # type: ignore[union-attr]
    assert messages and "installed in 2021" in messages[0]


# ------------------------------------------------------------------ before service


def pre_service(capitalized: str) -> dict[str, Any]:
    return project(
        kind="before_service",
        placed_in_service_date=None,
        work_start_date="2023-03-15",
        contract_date=None,
        invoice_total="20000.00",
        capitalized_amount=capitalized,
    )


def test_a_before_service_project_is_part_of_the_acquisition() -> None:
    line = {"category": "improvements_before_service", "amount": "20000.00"}
    the_intake = residential(acquisition_costs=[line], purchase_price="185000.00")
    pre = pre_service("20000.00")
    rows = built(BASE + ORIGINAL, version=1)
    result = compute_result(
        rows, the_intake, 1, clean(), renovations=document(pre), renovations_version=1
    )
    assert result.blockers == [], codes(result)
    assert result.worksheet is not None and result.worksheet.capitalized_costs == D("20000.00")
    # The reconciliation's target is the price less land plus the improvements.
    assert result.reconciliation is not None
    assert result.reconciliation.building_basis == result.worksheet.building_basis + D("20000.00")
    assert result.allocation is not None and result.allocation.total == result.worksheet.total_basis
    (p,) = result.renovations.projects  # type: ignore[union-attr]
    assert p.kind == "before_service" and p.rows == [] and result.renovations.dispositions == []  # type: ignore[union-attr]
    # The intake line and the projects must agree.
    other = pre_service("15000.00")
    result = compute_result(
        rows, the_intake, 1, clean(), renovations=document(other), renovations_version=1
    )
    assert "renovation_before_service" in codes(result)


# ------------------------------------------------------------------ qualified improvement property


def p(**over: Any) -> Any:
    return document(project(**over)).projects[0]


@pytest.mark.parametrize(
    ("proj", "use", "bucket", "label", "expected", "fragment"),
    [
        (p(), PropertyUse.NONRESIDENTIAL, "y39", "tile", True, "168(e)(6)"),
        (p(), PropertyUse.NONRESIDENTIAL, "y39", "light_fixture", True, "168(e)(6)"),
        (p(), PropertyUse.NONRESIDENTIAL, "y39", "interior_partition", True, "168(e)(6)"),
        (p(), PropertyUse.RESIDENTIAL_RENTAL, "y27_5", "tile", False, "only to nonresidential"),
        (p(), PropertyUse.NONRESIDENTIAL, "y5", "carpet", False, "kept as classified"),
        (p(addition=True), PropertyUse.NONRESIDENTIAL, "y39", "tile", False, "enlargement"),
        (p(), PropertyUse.NONRESIDENTIAL, "y39", "passenger_elevator", False, "elevator"),
        (p(), PropertyUse.NONRESIDENTIAL, "y39", "escalator", False, "elevator or escalator"),
        (
            p(),
            PropertyUse.NONRESIDENTIAL,
            "y39",
            "structural_framing",
            False,
            "structural framework",
        ),
        (p(), PropertyUse.NONRESIDENTIAL, "y39", "foundation", False, "structural framework"),
        (p(), PropertyUse.NONRESIDENTIAL, "y39", "window", False, "interior portion"),
        (p(), PropertyUse.NONRESIDENTIAL, "y39", "roof", False, "interior portion"),
        (p(), PropertyUse.NONRESIDENTIAL, "y39", "ac_condenser", False, "interior portion"),
        (p(qip="off"), PropertyUse.NONRESIDENTIAL, "y39", "tile", False, "turned off"),
    ],
)
def test_qip_at_each_exclusion(
    proj: Any, use: PropertyUse, bucket: str, label: str, expected: bool, fragment: str
) -> None:
    got, why = qip(proj, use, bucket, label)
    assert got is expected and fragment in why


def test_a_nonresidential_projects_interior_rows_print_as_qip() -> None:
    # Nonresidential (transient lodging), placed in service 2023-04-01.
    the_intake = intake(purchase_price="180000.00", placed_in_service_date=date(2023, 4, 1))
    new = [replace(NEW[0], bucket="y5"), replace(NEW[2], bucket="y39"),
           ScheduleRow(_id(35), "Window", "y39", D("3000.00"), label="window")]  # fmt: skip
    rows = built(BASE + [replace(r, project=KEY) for r in new])
    result = compute_result(
        rows,
        the_intake,
        1,
        clean(),
        renovations=document(project(placed_in_service_date="2025-06-30")),
        renovations_version=1,
    )
    assert result.blockers == [], codes(result)
    classes = {r.label: r.schedule_class for r in result.renovations.projects[0].rows}  # type: ignore[union-attr]
    assert classes == {"kitchen_cabinet": "y5", "tile": "qip", "window": "y39"}


# ------------------------------------------------------------------ compute directly


def line(n: int, bucket: str, allocated: str, key: str | None = KEY) -> AcquisitionLine:
    return AcquisitionLine(_id(n), "x", "X", bucket, D(allocated), key, date(2025, 3, 3), D(1), "r")


def test_the_disposed_basis_never_exceeds_the_buildings_remaining_basis() -> None:
    """A guard: the same building line twice (a corrupt input) cannot dispose of more than
    the building's basis."""
    lines = [line(1, "y27_5", "100.00"), line(1, "y27_5", "100.00")]
    out, blockers = compute(
        document(), 1, 1, intake=residential(), acquisition=lines, project_rows=[]
    )
    assert out is not None and len(out.dispositions) == 1
    removal = [b for b in blockers if b.code == "renovation_removal"]
    assert len(removal) == 1 and "remaining basis" in removal[0].message


def test_without_a_known_acquisition_bonus_the_disposition_waits() -> None:
    lines = [line(1, "y5", "100.00")]
    out, blockers = compute(
        document(),
        1,
        1,
        intake=residential(acquisition_date=None),
        acquisition=lines,
        project_rows=[],
    )
    assert "renovation_bonus" in [b.code for b in blockers]
    out, blockers = compute(
        document(acquisition_bonus_rate="0.6"), 1, 1, intake=residential(acquisition_date=None),
        acquisition=lines, project_rows=[],
    )  # fmt: skip
    assert "renovation_bonus" not in [b.code for b in blockers]
    assert out is not None and out.dispositions[0].bonus == D("60.00")


def test_nothing_at_all_gives_nothing() -> None:
    assert compute(None, 0, None, intake=residential(), acquisition=[], project_rows=[]) == (
        None,
        [],
    )


def test_a_project_row_has_its_bucket() -> None:
    row = ProjectRow(_id(9), "tile", "Tile", "y39", D("1.00"), KEY)
    out, _ = compute(document(), 1, 1, intake=intake(), acquisition=None, project_rows=[row])
    assert out is not None and out.projects[0].rows[0].bucket is Bucket.Y39
