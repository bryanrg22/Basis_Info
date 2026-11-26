"""The interior drafts and pricing rule (a) (EVAL_REPORT 110; basis.domain.measure_drafts).

A room's answer sizes exactly the rows it measures, with today's waste factors and heights; a
counted row, a quantity the answer left null and a kitchen-only quantity outside a kitchen keep
today's row; the record names who drew it, its expected error, what it replaced. A pending draft's
exposure (expected error x priced total) holds the report only above the threshold; an unpriced
row moves no dollars; a verified or replaced draft never holds it, and keeps the model's numbers.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from basis.domain import measure_drafts as md
from basis.domain.takeoffs import estimate_quantity

D = Decimal
ANSWER = {
    "floor_area_sf": 150.0,
    "length_ft": 15.0,
    "width_ft": 10.0,
    "ceiling_height_ft": 8.5,
    "perimeter_ft": 50.0,
    "wall_area_sf": 425.0,
    "countertop_sf": 30.0,
    "cabinet_lf": 14.0,
}


def kitchen(**over: float | None) -> md.RoomDraft:
    values = {**ANSWER, **over}
    return md.RoomDraft(
        room_type="kitchen",
        quantities=md.answer_quantities(values, "kitchen"),
        model="claude-sonnet-5-5",
        photos=("p1", "p2"),
        notes="measured against the door",
        confidence="medium",
        room_fully_seen=False,
    )


@pytest.mark.parametrize(
    ("key", "quantity", "unit", "measure"),
    [
        ("carpet", D("18.33"), "SY", "floor_area_sf"),  # 150 / 9 x 1.10
        ("tile", D("172.50"), "SF", "floor_area_sf"),  # 150 x 1.15
        ("flooring", D("150.00"), "SF", "floor_area_sf"),
        ("ceiling_finish", D("150.00"), "SF", "floor_area_sf"),
        ("concrete_floor", D("150.00"), "SF", "floor_area_sf"),
        ("paint", D("425.00"), "SF", "wall_area_sf"),  # the wall area, not 3.5 x the floor
        ("wall_finish", D("425.00"), "SF", "wall_area_sf"),
        ("baseboard", D("50.00"), "LF", "perimeter_ft"),
        ("crown_molding", D("50.00"), "LF", "perimeter_ft"),
        ("wainscoting", D("100.00"), "SF", "perimeter_ft"),  # x the 2 ft of its RSMeans line
        ("countertop", D("30.00"), "SF", "countertop_sf"),
        ("cabinet", D("14.00"), "LF", "cabinet_lf"),
    ],
)
def test_each_measured_row_is_sized_by_its_quantity(
    key: str, quantity: Decimal, unit: str, measure: str
) -> None:
    today = estimate_quantity(key, 120)
    draft = md.room_takeoff(key, kitchen(), today, None)
    assert draft is not None
    assert (draft.quantity, draft.unit) == (quantity, unit)
    assert draft.inputs["draft_quantity"] == measure
    assert draft.inputs["quantity_source"] == "draft"
    assert (
        draft.inputs["drawn_by"]
        == ("model:claude-sonnet-5-5@measure_interior_reference-v1")
        == draft.record["drawn_by"]
    )
    assert draft.inputs["replaced_formula"] == today.formula
    assert draft.inputs["room_area_sf"] == "150" and draft.inputs["replaced_room_area_sf"] == "120"
    assert draft.record["replaces"] == {
        "formula": today.formula,
        "quantity": f"{today.quantity} {today.unit}",
    }
    assert draft.record["expected_error_pct"] == format(md.INTERIOR_ERROR_PCT[measure][0], "f")
    assert draft.record["verified"] is False
    assert draft.record["photos"] == ["p1", "p2"] and draft.record["notes"]
    assert draft.measurement is None  # the model answers numbers: no shape to draw
    assert "drafted by Basis" in draft.formula and "claude" not in draft.formula


def test_a_row_held_by_a_missing_input_is_sized_and_names_it() -> None:
    today = estimate_quantity("cabinet", 150)
    draft = md.room_takeoff("cabinet", kitchen(), today, "cabinet_run")
    assert draft is not None and draft.quantity == D("14.00")
    assert draft.record["replaces"] == {"missing_input": "cabinet_run"}
    assert draft.inputs["replaced_missing_input"] == "cabinet_run"
    assert "replaced_formula" not in draft.inputs


def test_counted_rows_and_unanswered_quantities_keep_todays_row() -> None:
    assert (
        md.room_takeoff("light_fixture", kitchen(), estimate_quantity("light_fixture", 150), None)
        is None
    )
    assert (
        md.room_takeoff(
            "kitchen_cabinet", kitchen(), estimate_quantity("kitchen_cabinet", 150), None
        )
        is None
    )
    no_counter = kitchen(countertop_sf=None)
    assert (
        md.room_takeoff("countertop", no_counter, estimate_quantity("countertop", 150), None)
        is None
    )
    assert (
        md.room_takeoff(
            "flooring", kitchen(floor_area_sf=0), estimate_quantity("flooring", 150), None
        )
        is None
    )


def test_counters_and_cabinets_count_only_in_a_kitchen() -> None:
    bath = md.answer_quantities(ANSWER, "bathroom")
    assert "countertop_sf" not in bath and "cabinet_lf" not in bath
    assert bath["floor_area_sf"] == D("150.0")
    junk = md.answer_quantities(
        {"floor_area_sf": "x", "perimeter_ft": True, "wall_area_sf": -1}, "kitchen"
    )
    assert junk == {}


def test_exposure_is_the_expected_error_times_the_priced_total() -> None:
    record = md.room_takeoff("cabinet", kitchen(), estimate_quantity("cabinet", 150), "cabinet_run")
    assert record is not None
    rec = record.record  # 29.1 % expected error
    assert md.exposure_usd(rec, D("2000.00")) == D("582.00")
    assert md.exposure_usd(rec, None) is None
    held = md.priced(rec, D("2000.00"), D("500"))
    assert held["blocks"] is True and held["exposure_usd"] == "582.00"
    assert held["verify_above_usd"] == "500"
    assert md.blocking_threshold({md.DRAFT: held}) == D("500")
    free = md.priced(rec, D("1000.00"), D("500"))  # 291.00 is not above 500
    assert free["blocks"] is False and md.blocking_threshold({md.DRAFT: free}) is None
    at = md.priced(rec, D("1718.21"), D("500"))  # 500.00 exactly: not above
    assert at["exposure_usd"] == "500.00" and at["blocks"] is False
    unpriced = md.priced(rec, None, D("0"))
    assert unpriced["blocks"] is False and unpriced["exposure_usd"] is None


def test_a_verified_or_replaced_draft_never_holds_the_report_and_keeps_the_models_numbers() -> None:
    draft = md.room_takeoff("countertop", kitchen(), estimate_quantity("countertop", 150), None)
    assert draft is not None
    held = md.priced(draft.record, D("9000"), D("1"))
    assert md.pending(held) and held["blocks"] is True
    verified = md.resolved(held, verified=True, by="engineer", quantity="30.00 SF")
    assert verified["verified"] is True and not md.pending(verified)
    assert md.blocking_threshold({md.DRAFT: verified}) is None
    assert "blocks" not in verified and verified["quantity"] == "30.00"
    edited = md.resolved(held, verified=False, by="engineer", quantity="26.00 SF")
    assert edited["replaced_by"] == "engineer" and edited["engineer_quantity"] == "26.00 SF"
    assert edited["verified"] is False and edited["quantity"] == "30.00"  # the model's
    assert edited["answer"]["countertop_sf"] == "30.0"
    assert md.blocking_threshold({md.DRAFT: {**held, "blocks": True, "replaced_by": "x"}}) is None
    assert md.blocking_threshold({}) is None and md.blocking_threshold(None) is None


def test_the_sweep_counts_what_each_threshold_holds() -> None:
    rows = [(D("1000"), D("10")), (D("200"), D("20")), (None, D("10")), (D("5000"), D("5"))]
    got = md.sweep(rows, [D("0"), D("40"), D("100"), D("250")])
    assert [s.held for s in got] == [3, 2, 1, 0]  # exposures 100, 40, None, 250
    assert got[0].exposure_usd == D("390.00") and got[0].drafted_usd == D("6200")
    assert got[1].exposure_held_usd == D("350.00") and got[1].held_usd == D("6000")
    assert got[3].exposure_held_usd == 0


def test_the_blocker_message_says_how_many_and_the_threshold() -> None:
    assert md.blocker_message(1, D("100")) == (
        "1 measurement drafted by Basis is not verified and could each move more than $100.00 "
        "(its expected error times its price)"
    )
    assert md.blocker_message(3, D("100")).startswith("3 measurements drafted by Basis are")
    assert md.BLOCKER_FIX == "verify or edit this measurement in the measure tool"


def test_the_measured_sample_is_said_without_the_internal_report() -> None:
    sources = [v[1] for v in (*md.EXTERIOR_ERROR_PCT.values(), *md.INTERIOR_ERROR_PCT.values())]
    said = [md.measured_on(s) for s in sources]
    assert said == [
        "six houses",
        "two houses",
        "two houses",
        "24 rooms",
        "15 rooms",
        "24 rooms",
        "3 kitchens",
        "4 kitchens",
        "16 rooms",
    ]
    assert not any("EVAL" in s or ":" in s for s in said)
