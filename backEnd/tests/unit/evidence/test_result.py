"""The study result (``basis.evidence.result.compute_result``): the schedule with and without
an intake, land and unpriced assets kept out of the allocation, the not-produced list, the
regrouping by system only when the categories are report rows. Fabricated values."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from basis.domain.intake import StudyIntakeIn, with_location
from basis.evidence.result import NOT_PRODUCED, ScheduleRow, compute_result

D = Decimal


def _id(n: int) -> str:
    return str(uuid.UUID(int=n))


def intake(**overrides: object) -> object:
    values: dict[str, object] = {
        "purchase_price": "100000.00",
        "acquisition_costs": [],
        "land_method": "appraisal_site_value",
        "appraisal_site_value": "20000.00",
        "property_use": "nonresidential",
        "transient_lodging": True,
        "placed_in_service_date": date(2025, 5, 1),
    }
    values.update(overrides)
    return with_location(
        StudyIntakeIn.model_validate(values), appraisal_state="CA", appraisal_zip="90001"
    )


ROWS = [
    ScheduleRow(_id(3), "Toilet", "y39", None),
    ScheduleRow(_id(1), "Carpet", "y5", D("1368.52")),
    ScheduleRow(_id(2), "Kitchen cabinets", "y5", D("15108.48")),
    ScheduleRow(_id(4), "Site", "land", D("5.00")),
]


def test_without_an_intake_the_schedule_alone_is_returned() -> None:
    result = compute_result(ROWS, None, 2)
    assert (result.revision, result.intake, result.missing) == (2, False, ["intake"])
    assert result.worksheet is None and result.allocation is None
    schedule = result.schedule
    assert (schedule.assets, schedule.priced, schedule.rcn_total) == (3, 2, D("16477.00"))
    assert [(b.bucket.value, b.assets, b.rcn) for b in schedule.by_bucket] == [
        ("y5", 2, D("16477.00")),
        ("y39", 1, D("0.00")),
    ]
    assert schedule.unpriced == [uuid.UUID(int=3)]
    assert schedule.land == [uuid.UUID(int=4)]
    assert [n.system for n in result.not_produced] == [s for s, _ in NOT_PRODUCED]
    assert all("STUDY_REPORT_SPEC 5.3" in n.reason for n in result.not_produced)


def test_with_an_intake_the_schedule_is_allocated_to_the_basis() -> None:
    result = compute_result(ROWS, intake(), 1)  # type: ignore[arg-type]
    assert result.missing == []
    ws = result.worksheet
    assert ws is not None
    assert (ws.building_basis, ws.capitalized_costs, ws.total_basis) == (
        D("80000.00"),
        D("0.00"),
        D("80000.00"),
    )
    assert (ws.state, ws.zip, ws.location_source) == ("CA", "90001", "appraisal")
    assert ws.recovery_period == D("39")
    a = result.allocation
    assert a is not None
    # f = 80000 / 16477: 1368.52 × f = 6644.5105… → 6644.51; the cabinets take the rest.
    assert [(r.asset_id, r.allocated) for r in a.assets] == [
        (uuid.UUID(int=1), D("6644.51")),
        (uuid.UUID(int=2), D("73355.49")),
    ]
    assert a.rows_sum == a.total == D("80000.00")
    assert [
        (b.bucket.value, b.allocated, b.percent, b.building, b.closing_costs) for b in a.by_bucket
    ] == [("y5", D("80000.00"), D("100.0"), D("80000.00"), D("0.00"))]
    assert a.by_system is None and "2 of 2 rows are not mapped" in (a.by_system_reason or "")
    # The schedule covers a fifth of the basis: scaling up is the ATG's rejected step-up.
    step_up = [c for c in a.checks if c.code == "uniform_factor"]
    assert step_up[0].severity == "error" and "step-up" in step_up[0].message
    assert a.factor == D("4.855252777")  # 4.8552527765… to nine places


def test_report_row_categories_regroup_by_system() -> None:
    rows = [
        ScheduleRow(_id(1), "Interior Half Walls", "y5", D("100.00")),
        ScheduleRow(_id(2), "Painting", "y39", D("300.00")),
    ]
    result = compute_result(rows, intake(purchase_price="420.00", appraisal_site_value="20.00"), 1)  # type: ignore[arg-type]
    a = result.allocation
    assert a is not None and a.by_system is not None
    assert [(s.system, s.allocated) for s in a.by_system] == [
        ("Interior Framing / Partitions", D("80.00")),
        ("Painting", D("320.00")),
    ]


def test_an_intake_with_nothing_priced_has_no_allocation() -> None:
    rows = [ScheduleRow(_id(3), "Toilet", "y39", None), ScheduleRow(_id(4), "Site", "land", D("5"))]
    result = compute_result(rows, intake(), 1)  # type: ignore[arg-type]
    assert result.missing == ["allocation"]
    assert result.worksheet is not None and result.allocation is None
    assert compute_result([], intake(), 1).missing == ["allocation"]  # type: ignore[arg-type]
    assert compute_result([], None, 1).missing == ["intake"]


def test_rows_are_taken_in_asset_id_order_and_the_document_is_json() -> None:
    result = compute_result(ROWS, intake(), 3)  # type: ignore[arg-type]
    document = result.model_dump(mode="json")
    assert document["revision"] == 3
    assert document["worksheet"]["total_basis"] == "80000.00"
    assert [r["asset_id"] for r in document["allocation"]["assets"]] == [_id(1), _id(2)]
    assert document["allocation"]["assets"][0]["allocated"] == "6644.51"
    assert document["schedule"]["unpriced"] == [_id(3)]
