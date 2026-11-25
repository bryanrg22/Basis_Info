"""``evals.measure_capture`` and ``evals.measure_gather`` (EVAL_REPORT 103): the page-load
ledger refuses past the budget and past a house's share, the hour-room wait, and the gather's
point parsing. The network paths themselves are exercised live (EVAL_REPORT 103), not here."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evals import measure_capture as mc
from evals import measure_gather as mg


def test_the_ledger_refuses_past_the_budget_and_the_house_share(tmp_path: Path) -> None:
    ledger = mc.Ledger(tmp_path / "loads.jsonl", budget=3, per_house=2)
    ledger.record("a", "u1")
    ledger.record("a", "u2")
    with pytest.raises(mc.Refused, match="a: 2 loads"):
        ledger.record("a", "u3")
    ledger.record("b", "u4")
    with pytest.raises(mc.Refused, match="budget 3"):
        ledger.record("c", "u5")
    rows = ledger.rows()
    assert [r["n"] for r in rows] == [1, 2, 3] and [r["house"] for r in rows] == ["a", "a", "b"]


def test_hour_room_waits_until_the_hour_holds_the_house(tmp_path: Path) -> None:
    ledger = mc.Ledger(tmp_path / "loads.jsonl", budget=30, per_house=2)
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    assert mc.hour_room(ledger, cap=6, need=2, now=now) == timedelta(0)
    lines = [
        json.dumps(
            {"at": (now - timedelta(minutes=m)).isoformat(), "n": i, "house": "x", "url": "u"}
        )
        for i, m in enumerate((50, 40, 30, 20, 10), start=1)
    ]
    (tmp_path / "loads.jsonl").write_text("\n".join(lines) + "\n")
    # Five loads in the hour, two needed, six allowed: wait until the oldest is an hour old.
    wait = mc.hour_room(ledger, cap=6, need=2, now=now)
    assert wait == timedelta(minutes=10, seconds=30)
    assert mc.hour_room(ledger, cap=6, need=1, now=now) == timedelta(0)


def test_the_gather_reads_points_or_the_truth_files() -> None:
    ns = argparse.Namespace(point=["x1:39.5,-94.25"], truth=None, only=None)
    assert mg.points(ns) == [("x1", 39.5, -94.25)]
    ns = argparse.Namespace(point=None, truth=mg.mt.FIXTURES, only="mo1222")
    [(hid, lat, lon)] = mg.points(ns)
    assert hid == "mo1222" and 39 < lat < 40 and -95 < lon < -94
