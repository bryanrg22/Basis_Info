"""``evals.report_math``: the deterministic tail of a cost segregation schedule, to the cent."""

from __future__ import annotations

from decimal import Decimal

import pytest

from evals.report_math import (
    ScheduleError,
    build_schedule,
    cents,
    check_reproduction,
    implied_rcn,
    ordered_buckets,
    systems_page,
)

D = Decimal

# Six fabricated rows: RCNs chosen so the hand arithmetic below is checkable.
RCN = {
    "r1": D("1000.00"),  # y5
    "r2": D("250.50"),  # y5
    "r3": D("300.00"),  # y7
    "r4": D("2000.00"),  # y15
    "r5": D("6000.00"),  # y39
    "r6": D("449.50"),  # y39
}
BUCKETS = {"r1": "y5", "r2": "y5", "r3": "y7", "r4": "y15", "r5": "y39", "r6": "y39"}
B = D("7000.00")
CC = D("100.00")
# sum(RCN) = 10000.00; T = 7100.00; f = 0.71 exactly, so every row is RCN x 0.71 to the cent.


def test_factor_rows_buckets_and_percentages_by_hand() -> None:
    s = build_schedule(RCN, BUCKETS, B, CC)
    assert s.total == D("7100.00")
    assert s.rcn_total == D("10000.00")
    assert s.factor == D("0.71")
    assert s.rows == {
        "r1": D("710.00"),
        "r2": D("177.86"),  # 250.50 x 0.71 = 177.855 -> half up
        "r3": D("213.00"),
        "r4": D("1420.00"),
        "r5": D("4260.00"),
        "r6": D("319.15"),  # 449.50 x 0.71 = 319.145 -> half up
    }
    assert s.residual == D("-0.01")  # the two half-ups add a cent: rows sum to 7100.01
    assert s.absorbed_by is None
    assert s.bucket_totals == {
        "y5": D("887.86"),
        "y7": D("213.00"),
        "y15": D("1420.00"),
        "y39": D("4579.15"),
    }
    assert s.bucket_order == ["y5", "y7", "y15", "y39"]
    # 887.86 / 7100 = 12.505% -> 12.5 (half up at tenths: 12.5056 -> 12.5)
    assert s.bucket_percent == {"y5": D("12.5"), "y7": D("3.0"), "y15": D("20.0"), "y39": D("64.5")}


def test_residual_absorbed_by_the_largest_row() -> None:
    s = build_schedule(RCN, BUCKETS, B, CC, absorb_residual=True)
    assert s.absorbed_by == "r5"
    assert s.rows["r5"] == D("4259.99")
    assert sum(s.rows.values(), D(0)) == s.total
    assert s.residual == D("-0.01")  # reported as it was before absorbing


def test_allocation_block_is_pro_rata_and_each_column_foots() -> None:
    s = build_schedule(RCN, BUCKETS, B, CC)
    # building_b = 7000 x bucket / 7100, cents: 875.3527 -> 875.35; 210.00; 1400.00;
    # 4514.6472 -> 4514.65 (the column already foots: no adjustment)
    assert s.allocation_building == {
        "y5": D("875.35"),
        "y7": D("210.00"),
        "y15": D("1400.00"),
        "y39": D("4514.65"),
    }
    assert sum(s.allocation_building.values(), D(0)) == B
    # closing_b = 100 x bucket / 7100: 12.5051 -> 12.51; 3.00; 20.00; 64.4951 -> 64.50
    # (column sums to 100.01: the largest bucket gives back the cent -> 64.49)
    assert s.allocation_closing == {
        "y5": D("12.51"),
        "y7": D("3.00"),
        "y15": D("20.00"),
        "y39": D("64.49"),
    }
    assert sum(s.allocation_closing.values(), D(0)) == CC
    assert s.allocation_adjustment == {"building": D("0.00"), "closing_costs": D("-0.01")}
    # Each bucket's pair against its bucket total: off only where the footing cent landed.
    assert s.allocation_total == {
        "y5": D("887.86"),
        "y7": D("213.00"),
        "y15": D("1420.00"),
        "y39": D("4579.14"),
    }
    assert s.allocation_residuals == {
        "y5": D("0.00"),
        "y7": D("0.00"),
        "y15": D("0.00"),
        "y39": D("0.01"),
    }


def test_systems_page_sums_rows_by_share() -> None:
    s = build_schedule(RCN, BUCKETS, B, CC)
    systems = {
        "Finishes": {"r1": D(1), "r2": D("0.8")},
        "Painting": {"r2": D("0.2"), "r3": D(1)},
        "Structure": {"r4": D(1), "r5": D(1), "r6": D(1)},
    }
    lines = systems_page(s, RCN, systems)
    assert [(ln.name, ln.depreciable_cost, ln.replacement_cost) for ln in lines] == [
        ("Finishes", D("852.29"), D("1200.40")),  # 710 + 0.8 x 177.86; 1000 + 0.8 x 250.50
        ("Painting", D("248.57"), D("350.10")),  # 0.2 x 177.86 + 213; 0.2 x 250.50 + 300
        ("Structure", D("5999.15"), D("8449.50")),
    ]
    assert sum((ln.replacement_cost for ln in lines), D(0)) == s.rcn_total
    with pytest.raises(ScheduleError, match="do not sum to 1"):
        systems_page(s, RCN, {"Finishes": {"r1": D(1)}})
    with pytest.raises(ScheduleError, match="unknown row"):
        systems_page(s, RCN, {"Finishes": {"r9": D(1)}})


def test_implied_rcn_inverts_the_factor_and_reproduces_to_the_cent() -> None:
    """A printed schedule foots (its rows sum to T, as the engineer's do), so the RCNs
    implied from its rows and its printed RCN total scale back to every printed number."""
    printed = build_schedule(RCN, BUCKETS, B, CC, absorb_residual=True)
    assert sum(printed.rows.values(), D(0)) == printed.total
    rcn = implied_rcn(printed.rows, printed.rcn_total, printed.total)
    assert cents(sum(rcn.values(), D(0))) == printed.rcn_total
    again = build_schedule(rcn, BUCKETS, B, CC)
    check = check_reproduction(
        again,
        printed.rows,
        printed_bucket_totals=printed.bucket_totals,
        printed_bucket_percent=printed.bucket_percent,
        printed_allocation_building=printed.allocation_building,
        printed_allocation_closing=printed.allocation_closing,
        printed_total=printed.total,
    )
    assert check.compared == 6 + 1 + 4 + 4 + 4 + 4
    assert check.exact
    assert check.max_abs_deviation == D(0)
    # Rows that do not foot (a cent of rounding left in) cannot be inverted exactly: the
    # implied RCN total is off and the largest row comes back a cent short.
    unfooted = build_schedule(RCN, BUCKETS, B, CC)
    again = build_schedule(
        implied_rcn(unfooted.rows, unfooted.rcn_total, unfooted.total), BUCKETS, B, CC
    )
    off = check_reproduction(again, unfooted.rows)
    assert [(d.item, d.difference) for d in off.deviations] == [("row r5", D("-0.01"))]


def test_reproduction_reports_every_deviation() -> None:
    s = build_schedule(RCN, BUCKETS, B, CC)
    printed = dict(s.rows)
    printed["r1"] = D("710.01")
    percent = dict(s.bucket_percent)
    percent["y5"] = D("12.6")
    check = check_reproduction(s, printed, printed_bucket_percent=percent)
    assert not check.exact
    assert [(d.item, d.difference) for d in check.deviations] == [
        ("row r1", D("-0.01")),
        ("percent y5", D("-0.1")),
    ]
    assert check.max_abs_deviation == D("0.1")


def test_inputs_are_checked() -> None:
    with pytest.raises(ScheduleError, match="differ"):
        build_schedule(RCN, {k: v for k, v in BUCKETS.items() if k != "r6"}, B, CC)
    with pytest.raises(ScheduleError, match="no rows"):
        build_schedule({}, {}, B, CC)
    with pytest.raises(ScheduleError, match="negative"):
        build_schedule({"r1": D(-1)}, {"r1": "y5"}, B, CC)
    with pytest.raises(ScheduleError, match="sum to zero"):
        build_schedule({"r1": D(0)}, {"r1": "y5"}, B, CC)
    with pytest.raises(ScheduleError, match="negative"):
        build_schedule(RCN, BUCKETS, D(-1), CC)
    with pytest.raises(ScheduleError, match="zero total"):
        implied_rcn({"r1": D(1)}, D(1), D(0))


def test_helpers() -> None:
    assert cents(D("1.005")) == D("1.01")
    assert cents(D("1.004")) == D("1.00")
    assert ordered_buckets(["y39", "zz", "y5", "y27_5", "aa"]) == ["y5", "y27_5", "y39", "aa", "zz"]
