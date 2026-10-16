"""The configured unit-cost table (EVAL_REPORT section 25, item 6): schema, lookup, units.

The fixture is synthetic (invented numbers); the engineer's RSMeans table is licensed and
never in the repository (``COST_TABLE_PATH``).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path

from basis.db.enums import Bucket
from basis.domain.cost_table import (
    CostTableError,
    cost_table_for,
    load_cost_table,
    parse_cost_table,
)
from basis.domain.costs import MissingCostRow, NeedsQuantity, estimate_cost
from basis.domain.cross_validation import check_takeoff_cost, cross_validate
from basis.domain.takeoffs import estimate_quantity

PATH = str(fixture_path("cost_table.json"))


def table_doc() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(Path(PATH).read_text())
    return data


def test_the_table_loads_with_factors_applied() -> None:
    table = load_cost_table(PATH)
    assert (table.name, table.base_year, len(table.rows)) == ("synthetic_2020", 2020, 8)
    window = table.rows["window"]
    assert (window.unit, window.total, window.total_incl_op) == ("EA", Decimal(230), Decimal(280))
    countertop = table.rows["countertop"]  # per LF of a 24-inch top -> per SF
    assert (countertop.total, countertop.factor) == (Decimal("14.0"), Decimal("0.5"))
    roof = table.rows["roof"]  # per square -> per SF
    assert roof.total == Decimal("1.50")
    assert load_cost_table(PATH) is table  # read once per process
    assert cost_table_for(None) is None and cost_table_for("") is None
    assert cost_table_for(PATH) is table


def test_a_label_is_priced_from_the_table_in_its_unit() -> None:
    table = load_cost_table(PATH)
    cabinets = estimate_quantity("kitchen_cabinet", 150, 18)
    cost = estimate_cost(cabinets, 2025, "1.0", table=table, label="kitchen_cabinet")
    assert cost.base_unit_cost == Decimal("425.00")
    assert cost.unit_cost == Decimal("501.50")  # 425 x 1.18 (2025)
    assert cost.total == Decimal("9027.00")  # x 18 units
    assert cost.source_row.startswith("synthetic_2020[kitchen_cabinet] synthetic 12-0001, p. 2")
    assert "assumes one cabinet unit" in cost.source_row
    walls = estimate_quantity("wall_finish", 100)  # 350 SF of wall
    painted = estimate_cost(walls, 2020, "1.1", table=table, label="wall_finish")
    assert (painted.unit_cost, painted.total) == (Decimal("0.99"), Decimal("346.50"))
    top = estimate_cost(
        estimate_quantity("countertop", 144), 2020, "1", table=table, label="countertop"
    )
    assert top.base_unit_cost == Decimal("14.00") and "x0.5 from per L.F." in top.source_row


def test_the_legacy_rows_win_and_unknown_labels_stay_missing() -> None:
    table = load_cost_table(PATH)
    fixture = estimate_quantity("light_fixture", 100, 2)
    legacy = estimate_cost(fixture, 2020, "1", table=table, label="light_fixture")
    assert legacy.source_row.startswith("TYPICAL_UNIT_COSTS[light_fixture]")
    with pytest.raises(MissingCostRow):
        estimate_cost(estimate_quantity("stairs", 100), 2020, "1", table=table, label="stairs")
    with pytest.raises(MissingCostRow):  # no table configured: as before
        estimate_cost(estimate_quantity("window", 100), 2020, "1", label="window")
    # An unlabelled takeoff can still match the table by its takeoff component.
    by_component = estimate_cost(estimate_quantity("window", 100), 2020, "1", table=table)
    assert by_component.total == Decimal("230.00")


def test_a_row_in_another_unit_needs_a_measured_quantity() -> None:
    table = load_cost_table(PATH)
    roof = estimate_quantity("roof", 100)  # counted (1 EA): nothing measures a roof yet
    with pytest.raises(NeedsQuantity) as raised:
        estimate_cost(roof, 2020, "1", table=table, label="roof")
    assert raised.value.unit == "SF"
    [issue] = check_takeoff_cost(roof, None, priced_per="SF")
    assert issue.code == "COST_NEEDS_QUANTITY" and issue.needs_review
    [missing] = check_takeoff_cost(roof, None)
    assert missing.code == "COST_ROW_MISSING"
    codes = [i.code for i in cross_validate(section=None, bucket=Bucket.Y39, takeoff=roof,
                                            cost=None, priced_per="SF")]  # fmt: skip
    assert codes[-1] == "COST_NEEDS_QUANTITY"


def test_a_table_on_another_base_year_is_refused() -> None:
    doc = table_doc()
    doc["base_year"] = 2024
    table = parse_cost_table(doc)
    with pytest.raises(Exception, match="based on 2024"):
        estimate_cost(estimate_quantity("window", 100), 2025, "1", table=table, label="window")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(schema_version=2), "schema_version"),
        (lambda d: d.update(name=""), "name"),
        (lambda d: d.update(base_year="2020"), "base_year"),
        (lambda d: d.update(rows={}), "needs rows"),
        (lambda d: d["rows"].update(window=[1]), "not an object"),
        (lambda d: d["rows"]["window"].update(unit="Ea."), "unit"),
        (lambda d: d["rows"]["window"].update(material=-1), "non-negative"),
        (lambda d: d["rows"]["window"].update(labor="lots"), "not a number"),
        (lambda d: d["rows"]["window"].update(labor=True), "not a number"),
        (lambda d: d["rows"]["window"].update(labor=float("nan")), "finite"),
        (lambda d: d["rows"]["window"].update(factor=0), "factor"),
        (lambda d: d["rows"]["window"].update(source=None), "source"),
    ],
)
def test_a_table_that_breaks_the_schema_is_refused(change: Any, message: str) -> None:
    doc = table_doc()
    change(doc)
    with pytest.raises(CostTableError, match=message):
        parse_cost_table(doc)


def test_a_missing_or_unreadable_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CostTableError, match="does not exist"):
        load_cost_table(str(tmp_path / "nope.json"))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(CostTableError, match="cannot be read"):
        load_cost_table(str(broken))
