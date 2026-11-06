"""The engineer's unit price (EVAL_REPORT 90, :mod:`basis.domain.price_overrides`) and its
cost trail (``engineer_price``, :mod:`basis.domain.cost_trail`).

Pure: which ``after`` values the decision route and the activities accept, the record kept
on the component row, which override prices a row (the asset's beats the component's), the
estimate's arithmetic (the price as entered, no factor, total half up to the cent), and the
trail: the engineer's price with the table's own pricing kept as ``replaced``, both checked,
read back by the API unchanged.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from basis.api.schemas.outputs import CostTrailOut
from basis.domain import price_overrides as po
from basis.domain.cost_trail import (
    ENGINEER_PRICE,
    REPLACED_KEYS,
    TrailContext,
    check,
    engineer_trail,
    priced_trail,
    quantity_block,
)
from basis.domain.costs import year_factor_index
from basis.domain.measurements import CostIndex
from basis.domain.takeoffs import Takeoff

from .test_cost_trail import TABLE, takeoff, trail_of, written

D = Decimal
WHO = uuid.UUID("00000000-0000-4000-8000-000000000090")
AT = datetime(2026, 11, 5, 18, 30, tzinfo=UTC)
CONTEXT = TrailContext(3, "residential_rental", {"cost_basis": "bare"})
GOOD = {"unit_cost": 512.4, "unit_cost_source": "Acme Cabinets quote 2024-118"}


def index() -> CostIndex:
    return CostIndex(2026, None, D("1.010"), None, year_factor_index(2026))


def record(after: dict[str, Any] = GOOD, level: po.Level = "component") -> dict[str, Any]:
    made = po.record(
        after,
        "  Contractor quote on file  ",
        level=level,
        by=WHO,
        by_name="Erin Engineer",
        at=AT,
        correction_id=None,
        revision=2,
    )
    assert made is not None
    return made


# --------------------------------------------------------------------------- the values


def test_a_price_with_a_source_and_a_reason_is_accepted() -> None:
    assert po.problems(GOOD, "quote") == []
    assert po.problems({"unit_cost": 1, "unit_cost_source": "x"}, "r") == []
    assert po.problems({"unit_cost": 10_000_000, "unit_cost_source": "x" * 300}, "r") == []
    assert po.problems({"unit_cost": 0.01, "unit_cost_source": "x"}, "r") == []
    # Clearing takes no source (absent or null).
    assert po.problems({"unit_cost": None}, "back to the table") == []
    assert po.problems({"unit_cost": None, "unit_cost_source": None}, "r") == []


def test_a_correction_without_a_price_has_no_price_problems() -> None:
    assert po.problems({"category": "Carpet"}, None) == []
    assert not po.has_price({"count": 3})


@pytest.mark.parametrize(
    ("after", "fragment"),
    [
        ({"unit_cost": 0, "unit_cost_source": "x"}, "above 0"),
        ({"unit_cost": -5, "unit_cost_source": "x"}, "above 0"),
        ({"unit_cost": 10_000_000.01, "unit_cost_source": "x"}, "at most"),
        ({"unit_cost": "12.50", "unit_cost_source": "x"}, "must be a number"),
        ({"unit_cost": True, "unit_cost_source": "x"}, "must be a number"),
        ({"unit_cost": [1], "unit_cost_source": "x"}, "must be a number"),
        ({"unit_cost": float("inf"), "unit_cost_source": "x"}, "must be a number"),
        ({"unit_cost": float("nan"), "unit_cost_source": "x"}, "must be a number"),
        ({"unit_cost": 12.345, "unit_cost_source": "x"}, "to the cent"),
        ({"unit_cost": 12.5}, "where the price comes from"),
        ({"unit_cost": 12.5, "unit_cost_source": "   "}, "where the price comes from"),
        ({"unit_cost": 12.5, "unit_cost_source": "x" * 301}, "where the price comes from"),
        ({"unit_cost": 12.5, "unit_cost_source": 7}, "where the price comes from"),
        ({"unit_cost_source": "a quote"}, "goes with a unit_cost"),
        ({"unit_cost": None, "unit_cost_source": "a quote"}, "clearing takes none"),
    ],
)
def test_a_bad_price_is_refused_with_the_reason(after: dict[str, Any], fragment: str) -> None:
    found = po.problems(after, "a reason")
    assert found and any(fragment in p for p in found), found


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_a_price_needs_the_corrections_reason(reason: str | None) -> None:
    assert po.problems(GOOD, reason) == ["a unit price override needs the correction's reason"]
    # Clearing needs one too: undoing an engineer's price is a decision as well.
    assert po.problems({"unit_cost": None}, reason)


def test_split_separates_the_price_from_the_rest() -> None:
    rest, price = po.split({"bucket": "y5", **GOOD})
    assert rest == {"bucket": "y5"} and price == GOOD


# --------------------------------------------------------------------------- the record


def test_the_record_keeps_the_price_as_entered_and_who_when_why() -> None:
    assert record() == {
        "unit_cost": "512.4",
        "source": "Acme Cabinets quote 2024-118",
        "reason": "Contractor quote on file",
        "by": str(WHO),
        "by_name": "Erin Engineer",
        "at": "2026-11-05T18:30:00+00:00",
        "level": "component",
        "correction_id": None,
        "revision": 2,
    }
    assert po.record({"unit_cost": None}, "r", level="asset", by=None, by_name=None, at=AT,
                     correction_id=None, revision=2) is None  # fmt: skip


def test_the_assets_own_price_wins_over_its_components() -> None:
    component, asset = (
        record(level="component"),
        record({"unit_cost": 600, "unit_cost_source": "invoice 77"}, level="asset"),
    )
    assert po.active({po.COMPONENT_KEY: component}) == component
    assert po.active({po.COMPONENT_KEY: component, po.ASSET_KEY: asset}) == asset
    assert po.active({po.ASSET_KEY: asset}) == asset
    assert po.active({"count": 3}) is None
    assert po.active(None) is None and po.active([1]) is None
    assert po.active({po.ASSET_KEY: {"unit_cost": None}, po.COMPONENT_KEY: component}) == component


# --------------------------------------------------------------------------- the estimate


def test_the_estimate_is_the_price_times_the_quantity_with_no_factor() -> None:
    t = takeoff("kitchen_cabinet", "18", "EA")
    estimate = po.estimate(t, record(), cost_year=2026, replaced="synthetic[kitchen_cabinet] ...")
    assert (estimate.unit_cost, estimate.total) == (D("512.4"), D("9223.20"))
    assert (estimate.year_factor, estimate.location_factor) == (D(1), D(1))
    assert estimate.row is None and estimate.year is None and estimate.year_assumption is None
    assert estimate.source_row.startswith(
        "engineer's unit price 512.4 per EA: Acme Cabinets quote 2024-118 (set by Erin "
        "Engineer on 2026-11-05; reason: Contractor quote on file); replaces: synthetic"
    )


def test_the_total_rounds_half_up_to_the_cent() -> None:
    t = takeoff("carpet", "3.3333", "SY")
    priced = po.estimate(
        t, record({"unit_cost": 1.5, "unit_cost_source": "x"}), cost_year=2026, replaced="-"
    )
    assert priced.total == D("5.00")  # 4.99995 -> 5.00
    zero = po.estimate(
        takeoff("carpet", "0", "SY"), record(), cost_year=2026, replaced="measured elsewhere"
    )
    assert zero.total == D("0.00")


# --------------------------------------------------------------------------- the trail


def engineer_of(t: Takeoff, override: dict[str, Any]) -> dict[str, Any]:
    table_estimate, table_trail = trail_of(t, index())
    estimate = po.estimate(t, override, cost_year=2026, replaced=table_estimate.source_row)
    return engineer_trail(
        quantity_block(t, written(t)),
        estimate,
        index(),
        context=CONTEXT,
        override=override,
        replaced=table_trail,
    )


def test_the_trail_keeps_the_engineers_price_and_the_tables() -> None:
    t = takeoff("kitchen_cabinet", "18", "EA")
    _, table = trail_of(t, index())
    trail = engineer_of(t, record())
    assert trail["kind"] == ENGINEER_PRICE and trail["base"] is None
    assert (trail["unit_cost"], trail["total"]) == ("512.4", "9223.20")
    assert trail["override"] == record()
    assert trail["replaced"] == {key: table[key] for key in REPLACED_KEYS}
    assert trail["replaced"]["kind"] == "unit_price" and trail["replaced"]["unit_cost"] != "512.4"
    # The study's index, recorded as an unpriced row records it (not applied).
    assert (trail["cost_year"], trail["location_factor"]) == (2026, "1.010")
    assert trail["year"] == table["year"] and trail["location"] == table["location"]
    assert check(trail) == []
    stored = json.loads(json.dumps(trail))
    sent = CostTrailOut.model_validate(stored).model_dump(mode="json", by_alias=True)
    assert sent["override"]["unit_cost"] == "512.4" and sent["replaced"] == stored["replaced"]


def test_an_unpriced_rows_table_pricing_is_kept_as_why() -> None:
    t = takeoff("driveway", "1", "EA")
    table = priced_trail(
        quantity_block(t, written(t)),
        None,
        index(),
        context=CONTEXT,
        missing="missing: no cost row for 'driveway' per EA",
    )
    estimate = po.estimate(t, record(), cost_year=2026, replaced="missing: no cost row")
    trail = engineer_trail(
        quantity_block(t, written(t)),
        estimate,
        index(),
        context=CONTEXT,
        override=record(),
        replaced=table,
    )
    assert trail["replaced"]["kind"] == "unpriced" and trail["replaced"]["unit_cost"] is None
    assert trail["replaced"]["missing"] == "missing: no cost row for 'driveway' per EA"
    assert check(trail) == []


@pytest.mark.parametrize(
    ("tamper", "found"),
    [
        (lambda t: t.update(unit_cost="600.00"), "is not the override's"),
        (lambda t: t.update(total="9999.99"), "unit_cost x quantity"),
        (lambda t: t["replaced"].update(unit_cost="1.00"), "replaced:"),
        (lambda t: t.pop("override"), "needs its override"),
        (lambda t: t.pop("replaced"), "keeps the pricing it replaced"),
    ],
)
def test_check_finds_a_tampered_engineer_trail(tamper: Any, found: str) -> None:
    trail = engineer_of(takeoff("kitchen_cabinet", "18", "EA"), record())
    tamper(trail)
    problems = check(trail)
    assert problems and any(found in p for p in problems), problems


def test_the_table_row_is_untouched_by_the_feature() -> None:
    """Regression: a row with no override is priced and trailed exactly as before."""
    t = takeoff("kitchen_cabinet", "18", "EA")
    estimate, trail = trail_of(t, index())
    assert trail["kind"] == "unit_price" and "override" not in trail and "replaced" not in trail
    assert TABLE.row("kitchen_cabinet") is not None and check(trail) == []
    assert estimate.unit_cost == D(trail["unit_cost"])
