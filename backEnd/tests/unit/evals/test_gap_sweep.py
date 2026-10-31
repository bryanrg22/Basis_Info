"""``evals.gap_sweep`` and the pricing options of ``evals.reprice_export`` (EVAL_REPORT 49,
STRUCTURE_MODEL.md section 9), on a fabricated export and the synthetic cost table."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fixtures import fixture_path

from basis.domain.cost_table import load_cost_table
from evals import gap_sweep as gs
from evals.reprice_export import reprice
from evals.structure_replay import Variant


def test_the_grid_is_every_pricing_and_structure_variant() -> None:
    grid = gs.grid()
    # 3 pricings x (square-foot: modifications x residual x small systems = 8; the
    # appraisal's cost new: residual x small systems = 4)
    assert len(grid) == 3 * (8 + 4)
    names = [gs.cell_name(p, v) for p, v in grid]
    assert len(set(names)) == len(names)
    assert gs.BEFORE in names and gs.PRODUCTION in names
    assert not any(v.modifications for _, v in grid if v.rcn_source == "appraisal_total")
    assert all(v.cost_basis == p.cost_basis and v.overhead == p.overhead for p, v in grid)
    assert [p.name for p in gs.PRICINGS] == ["bare", "installed", "installed+gc"]


def test_the_production_variant_is_every_change_on() -> None:
    production = Variant()
    assert production.gap_name == ("mods=on+spec=building+small=carved+basis=installed+gc=pro_rata")
    assert gs.cell_name(gs.PRICINGS[2], production) == gs.PRODUCTION


def _asset(label: str, quantity: str, unit: str, unit_cost: str) -> dict[str, Any]:
    total = (Decimal(quantity) * Decimal(unit_cost)).quantize(Decimal("0.01"))
    return {
        "id": label,
        "label": label,
        "bucket": "y5",
        "quantity": quantity,
        "unit": unit,
        "formula": "",
        "unit_cost": unit_cost,
        "total": str(total),
        "cost_source": "synthetic (base 2020, x1.0000 for 2020 = HCI; x1.0 location)",
        "takeoff_inputs": {"component": label},
        "needs_review": False,
        "review_reason": None,
    }


def _export() -> dict[str, Any]:
    fields = [
        {"section": "improvements", "field": "gla_sqft", "value_num": 2400, "source": "llm"},
        {"section": "improvements", "field": "stories", "value_num": 2, "source": "llm"},
        {"section": "improvements", "field": "quality", "value_text": "Q4", "source": "llm"},
    ]
    return {
        "format": "basis-export/2",
        "study_id": "s",
        "revision": 1,
        "extraction": [{"fields": fields}],
        "assets": [_asset("window", "1", "EA", "230.00"), _asset("patio", "100", "SF", "3.50")],
        "summary": {
            "takeoff": {
                "cost_year": 2020,
                "location_factor": "1.0",
                "cost_year_source": "fabricated",
                "location_source": "state IL (intake): fabricated",
            }
        },
    }


def test_reprice_prices_on_the_basis_and_marks_up_the_rows_inside_the_model() -> None:
    table = load_cost_table(str(fixture_path("cost_table.json")))
    common: dict[str, Any] = {
        "cost_table": table,
        "state": None,
        "zip_code": None,
        "supplement": None,
    }
    bare, changes = reprice(_export(), **common)
    totals = {a["label"]: a["total"] for a in bare["assets"]}
    # IL is the mean of its cities (0.959): bare 230, and 3.50 per SF (3.36 to the cent) x 100
    assert totals == {"window": "220.57", "patio": "336.00"}
    assert changes["counts"] == {"repriced": 2} and changes["cost_basis"] == "bare"
    installed, changes = reprice(_export(), cost_basis="installed", overhead="pro_rata", **common)
    totals = {a["label"]: a["total"] for a in installed["assets"]}
    # the window is inside the model (Exterior Walls): Total Incl O&P 280 x the Average 2
    # Story general contractor's O&P 1.169835 x 0.959 = 314.12; the patio is outside it:
    # 4.50 x 0.959 = 4.32 per SF x 100 = 432.00, no markup
    assert totals == {"window": "314.12", "patio": "432.00"}
    assert changes["overhead_rate"] == "0.169835"
    # the stored dollars are checked on the bare basis either way: no base mismatch
    assert "base_mismatch" not in changes["counts"]
    sources = {a["label"]: a["cost_source"] for a in installed["assets"]}
    assert (
        "general contractor" in sources["window"] and "general contractor" not in (sources["patio"])
    )
