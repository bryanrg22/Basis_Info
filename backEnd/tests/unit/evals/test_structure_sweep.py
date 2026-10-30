"""``evals.structure_sweep``: the grid, the ranking rule and the table, on fabricated cells."""

from __future__ import annotations

from evals import structure_sweep as sw
from evals.structure_replay import PRODUCTION, Variant


def cell(export: str, variant: str, **overrides: object) -> sw.Cell:
    values: dict[str, object] = {
        "export": export,
        "variant": variant,
        "sourced": True,
        "note": "",
        "runtime_ms": 8.0,
        "status": "reconciled",
        "gap_share_pct": 3.0,
        "implied_uniform_factor": 0.8,
        "allocation_factor": 1.03,
        "kind_c_produced": 8,
        "kind_c_right": 7,
        "kind_c_wrong": ("Access Control (y5 vs y39)",),
        "bucket_share_pct": {"y5": 11.0, "y7": 0.9, "y15": 4.0, "y39": 84.1},
        "bucket_error_pct": {"y5": 3.4, "y7": 0.5, "y15": 1.1, "y39": 3.9},
        "mean_bucket_error_pct": 2.225,
        "tvd_pct": 21.0,
        "mean_row_error_pct": 0.9,
        "visible_evidenced": 35,
        "visible_right": 35,
        "regression_ok": True,
    }
    values.update(overrides)
    return sw.Cell(**values)  # type: ignore[arg-type]


def test_the_grid_is_every_combination_with_the_production_point_first() -> None:
    grid = sw.grid()
    assert len(grid) == 3 * 3 * 2 * 3 and len({v.name for v in grid}) == len(grid)
    assert grid[0] == Variant(
        rcn_source="appraisal_total", split="rsmeans", carve=True, depreciation="appraisal_physical"
    )
    assert PRODUCTION in grid and PRODUCTION.name == "square_foot+rsmeans+carve+appraisal_physical"
    assert Variant(carve=False).name.endswith("+top_down+appraisal_physical")


def test_ranking_keeps_sourced_reconciled_variants_and_orders_by_error_then_tvd_then_gap() -> None:
    cells = [
        cell("A", "x", mean_bucket_error_pct=1.5, tvd_pct=20.0, gap_share_pct=5.0),
        cell("B", "x", mean_bucket_error_pct=2.5, tvd_pct=22.0, gap_share_pct=-5.0),
        cell("A", "y", mean_bucket_error_pct=2.5, tvd_pct=20.0, gap_share_pct=1.0),
        cell("B", "y", mean_bucket_error_pct=2.5, tvd_pct=21.0, gap_share_pct=1.0),
        # same error as y, a smaller TVD: ahead of y
        cell("A", "z", mean_bucket_error_pct=2.5, tvd_pct=19.0, gap_share_pct=9.0),
        cell("B", "z", mean_bucket_error_pct=2.5, tvd_pct=19.0, gap_share_pct=9.0),
        # unreconciled on one export: out
        cell("A", "w", mean_bucket_error_pct=0.1),
        cell("B", "w", mean_bucket_error_pct=0.1, status="unreconciled"),
        # not sourced: out
        cell("A", "ms", sourced=False, note="licensed"),
        cell("B", "ms", sourced=False, note="licensed"),
    ]
    ranked = sw.rank(cells)
    assert [r[0] for r in ranked] == ["x", "z", "y"]
    assert ranked[0][1:] == (2.0, 21.0, 5.0)  # averaged over the exports; the gap in absolute
    assert ranked[1][1:] == (2.5, 19.0, 9.0)
    text = sw.render_markdown(cells, ranked, {"y5": 14.4, "y7": 0.4, "y15": 5.1, "y39": 80.2})
    assert "**Pick:** `x`. **Runner-up:** `z`." in text
    assert "not sourced: licensed" in text and "Cost per variant: $0" in text
    assert "every variant unchanged" in text and PRODUCTION.name in text
    moved = sw.render_markdown([cell("A", "x", regression_ok=False)], [], {})
    assert "MOVED on A x" in moved and "**Pick:**" not in moved
