"""``evals.balance_tolerance`` (EVAL_REPORT 81): the fixed, scaled and two-band rules at
their boundaries, the summaries on fabricated cells, the two 10 % checks against
``basis.domain.basis.allocate``, and one variant replayed and scored end to end on a
fabricated export whose price is set to put the gap at 6 %; no private data."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path

from basis.domain.basis import RcnRow, allocate
from basis.domain.cost_table import load_cost_table
from basis.vision.schemas import COMPONENT_LABELS
from evals import balance_tolerance as bt
from evals import structure_sweep as sw
from evals.e2e_report import E2ERows, EngineerReport
from evals.record import EvalRun
from evals.structure_replay import Variant, rebuild_intake, replay

D = Decimal


def cell(name: str = "v", export: str = "A", **overrides: Any) -> bt.Cell:
    values: dict[str, Any] = {
        "export": export,
        "grid": bt.GRID_GAP,
        "name": name,
        "sourced": True,
        "note": "",
        "runtime_ms": 5.0,
        "status": "reconciled",
        "gap_share": "0.020000",
        "gap_pct": 2.0,
        "model_share_pct": 60.0,
        "mean_bucket_error_pct": 1.0,
        "max_bucket_error_pct": 2.0,
        "visible_evidenced": 35,
        "visible_right": 35,
        "regression_ok": True,
    }
    values.update(overrides)
    return bt.Cell(**values)


def at(gap: str, **overrides: Any) -> bt.Cell:
    return cell(gap_share=gap, gap_pct=float(D(gap) * 100), **overrides)


# ------------------------------------------------------------------ the rules


def test_balances_is_the_reconciliations_inclusive_test_on_six_places() -> None:
    assert bt.balances("0.100000", 10) is True and bt.balances("-0.100000", 10) is True
    assert bt.balances("0.100001", 10) is False and bt.balances("-0.100001", 10) is False
    assert bt.balances("0.025000", 2.5) is True and bt.balances("0.025001", 2.5) is False
    assert bt.balances(None, 10) is None
    assert bt.fixed_verdict(at("0.07"), 7.5) == "balances"
    assert bt.fixed_verdict(at("0.07"), 5) == "withheld"
    assert bt.fixed_verdict(cell(status="not_checked", gap_share=None), 10) == "not_checked"


def test_the_scaled_tolerance_follows_the_model_share_and_collapses_to_fixed() -> None:
    assert bt.scaled_tolerance_pct(0) == 5.0 and bt.scaled_tolerance_pct(100) == 10.0
    assert bt.scaled_tolerance_pct(60) == 8.0  # 10 x 0.6 + 5 x 0.4
    assert bt.scaled_tolerance_pct(150) == 10.0 and bt.scaled_tolerance_pct(-5) == 5.0
    assert bt.scaled_tolerance_pct(37, item_pct=10.0) == 10.0  # equal parts: the fixed rule
    assert bt.scaled_verdict(at("0.079")) == ("balances", 8.0)
    assert bt.scaled_verdict(at("0.081")) == ("withheld", 8.0)
    # no model share: the model's accuracy alone
    assert bt.scaled_verdict(at("0.081", model_share_pct=None)) == ("balances", None)


def test_the_two_band_rule_allocates_flags_and_withholds() -> None:
    assert bt.two_band_verdict(at("0.050000")) == "allocate"
    assert bt.two_band_verdict(at("-0.050001")) == "allocate_flagged"
    assert bt.two_band_verdict(at("0.100000")) == "allocate_flagged"
    assert bt.two_band_verdict(at("0.100001")) == "withhold"
    assert bt.two_band_verdict(cell(status="not_checked", gap_share=None)) == "not_checked"
    # equal bands: the fixed rule, with no flag band
    assert bt.two_band_verdict(at("0.07"), inner_pct=10, outer_pct=10) == "allocate"
    with pytest.raises(ValueError, match="inner band"):
        bt.two_band_verdict(at("0.07"), inner_pct=12, outer_pct=10)


def test_defects_are_read_from_the_cell_name_and_mods_count_only_on_the_model() -> None:
    assert bt.defects_of(bt.GAP_PRODUCTION, bt.GRID_GAP) == ()
    assert bt.defects_of(bt.GAP_RUNNER_UP, bt.GRID_GAP) == ()  # the appraisal has no step 2
    name = "bare|square_foot|mods=off|spec=report|small=added"
    assert bt.defects_of(name, bt.GRID_GAP) == ("mods=off", "spec=report", "small=added", "bare")
    assert bt.defects_of("square_foot+rsmeans+top_down+none", bt.GRID_STRUCTURE) == (
        "top_down",
        "none",
    )
    assert bt.defects_of(bt.STRUCTURE_PRODUCTION, bt.GRID_STRUCTURE) == ()


# ------------------------------------------------------------------ the two 10 % checks


def test_a_gap_above_one_eleventh_reconciles_but_the_factor_check_reads_outside() -> None:
    """``|gap_share| <= 10 %`` on B and ``|T / RCNLD - 1| <= 10 %`` are different tests: at a
    9.5 % gap the reconciliation allocates and ``allocate``'s own check warns."""
    b, rcnld = D("100000.00"), D("90500.00")
    gap_share = (b - rcnld) / b
    assert bt.balances(gap_share, bt.PRODUCTION_TOLERANCE_PCT) is True
    result = allocate([RcnRow("r", "y39", D("120000.00"), rcnld=rcnld)], b, D(0), depreciated=True)
    check = result.checks[0]
    assert check.code == "rcnld_factor" and check.severity.value == "warning"
    assert bt.factor_check_within(result.factor) is False
    # at 9.0 % both agree; capitalized costs push the factor further out
    ok = allocate([RcnRow("r", "y39", D("1"), rcnld=D("91000.00"))], b, D(0), depreciated=True)
    assert ok.checks[0].severity.value == "ok" and bt.factor_check_within(ok.factor) is True
    with_cc = allocate(
        [RcnRow("r", "y39", D("1"), rcnld=D("91000.00"))], b, D("1000.00"), depreciated=True
    )
    assert bt.factor_check_within(with_cc.factor) is False


def test_the_implied_factor_is_b_plus_capitalized_costs_over_rcnld() -> None:
    changes: dict[str, Any] = {
        "allocation": None,
        "reconciliation": {"rcnld_total": "90000.00", "building_basis": "99000.00"},
    }
    replayed = {
        "summary": {
            "basis": {"worksheet": {"building_basis": "99000.00", "capitalized_costs": "0"}}
        }
    }
    assert bt.allocation_factor_of(replayed, changes) == (D("1.100000000"), "implied")
    replayed["summary"]["basis"]["worksheet"]["capitalized_costs"] = "900.00"
    assert bt.allocation_factor_of(replayed, changes) == (D("1.110000000"), "implied")
    changes["allocation"] = {"factor": "1.050000000"}
    assert bt.allocation_factor_of(replayed, changes) == (D("1.050000000"), "allocation")
    assert bt.allocation_factor_of({}, {"reconciliation": {"rcnld_total": None}}) == (None, None)
    flagged = [cell(factor_within=False), cell(status="unreconciled", factor_within=False)]
    assert bt.factor_disagreements(flagged) == flagged[:1]


# ------------------------------------------------------------------ the summaries


def _grid() -> list[bt.Cell]:
    """Four variants on two exports: the production cell (clean, 2 %), a clean one at
    -4 %, one with the book's step 2 skipped at 9 % / 8 % and a less accurate one with the
    residual not added back at 3 %."""
    out = []
    for export, shift in (("A", 0.0), ("B", -0.01)):
        for name, gap, err in (
            (bt.GAP_PRODUCTION, 0.02, 1.3),
            (bt.GAP_RUNNER_UP, -0.04, 1.6),
            ("installed+gc|square_foot|mods=off|spec=building|small=carved", 0.09, 0.6),
            ("installed+gc|square_foot|mods=on|spec=report|small=carved", 0.03, 2.5),
        ):
            g = D(str(gap + shift)).quantize(D("0.000001"))
            out.append(
                at(
                    str(g),
                    name=name,
                    export=export,
                    defects=bt.defects_of(name, bt.GRID_GAP),
                    mean_bucket_error_pct=err,
                    max_bucket_error_pct=err * 2,
                )
            )
    return out


def test_tolerance_rows_count_what_each_tolerance_admits() -> None:
    rows = {r.tolerance_pct: r for r in bt.tolerance_rows(_grid(), (2.5, 5.0, 10.0))}
    # 2.5 %: the production cell only (2 % / 1 %); the clean runner-up (-4 % / -5 %) is refused
    assert (rows[2.5].admitted, rows[2.5].clean_refused, rows[2.5].defects_refused) == (1, 1, 2)
    # 5 %: both clean variants in, the residual omission in (3 % / 2 %), step 2 out
    r5 = rows[5.0]
    assert (r5.admitted, r5.clean_refused, r5.defects_refused) == (3, 0, 1)
    assert r5.admitted_defect_names == ("spec=report",)
    assert r5.production_admitted and r5.runner_up_admitted
    assert r5.production_gaps_pct == (2.0, 1.0)
    assert r5.worst_admitted is not None and r5.worst_admitted[1] == 2.5
    assert r5.best_refused is not None and r5.best_refused[1] == 0.6
    # 10 %: everything in, including the variant that skips step 2
    assert rows[10.0].admitted == 4 and rows[10.0].admitted_with_defects == 2
    assert rows[10.0].admitted_defect_names == ("mods=off", "spec=report")


def test_separation_and_the_tightest_tolerance() -> None:
    cells = _grid()
    sep = bt.separation(cells, bt.GRID_GAP)
    assert sep["clean"] == pytest.approx(5.0) and sep["mods=off"] == pytest.approx(8.0)
    assert sep["spec=report"] == pytest.approx(2.0) and sep["top_down"] is None
    groups = bt.by_variant(cells, bt.GRID_GAP)
    assert bt.tightest_tolerance(groups[bt.GAP_PRODUCTION]) == 2.5
    assert bt.tightest_tolerance(groups[bt.GAP_RUNNER_UP]) == 5.0
    assert bt.tightest_tolerance([at("0.3")]) is None
    rules = {r["name"]: r for r in bt.rule_rows(cells)}
    step2 = rules["installed+gc|square_foot|mods=off|spec=building|small=carved"]
    assert step2["two_band"] == ["allocate_flagged", "allocate_flagged"]
    assert step2["scaled_verdict"] == ["withheld", "balances"]  # 9 % > 8 %, 8 % <= 8 %


def test_the_markdown_and_the_regression_cases(tmp_path: Path) -> None:
    cells = [*_grid(), cell(name="ms", sourced=False, note="licensed", status=None)]
    rows = bt.tolerance_rows(cells)
    rules = bt.rule_rows(cells)
    text = bt.render_markdown(cells, rows, rules, item_pct=5.0, inner_pct=5.0, outer_pct=10.0)
    assert "## Fixed tolerances" in text and "not sourced: licensed" in text
    assert "every cell unchanged" in text and "The two 10 % checks" in text
    moved = bt.render_markdown(
        [cell(regression_ok=False)], [], [], item_pct=5.0, inner_pct=5.0, outer_pct=10.0
    )
    assert "MOVED on A v" in moved
    run = EvalRun.begin(tmp_path / "run", harness="test", component=bt.COMPONENT)
    reference = {
        ("A", bt.GAP_PRODUCTION): {"status": "reconciled", "gap_share_pct": 2.0},
        ("B", bt.GAP_PRODUCTION): {"status": "reconciled", "gap_share_pct": 1.5},
    }
    bt.record_cases(run, cells, reference)
    manifest = run.finish({})
    cases = manifest["results"]
    # 8 sourced cells x 2 cases + 2 reference cases + 1 unsourced case
    assert cases["total"] == 19 and cases["failed"] == 1  # B's gap is 1.0, not 1.5


# ------------------------------------------------------------------ end to end


def _field(section: str, name: str, value: Any) -> dict[str, Any]:
    key = "value_text" if isinstance(value, str) else "value_num"
    return {"section": section, "field": name, key: value, "source": "llm"}


def _export(price: str, land: str) -> dict[str, Any]:
    """A 2,000 SF two-story Q4 house whose appraisal prices it at 300,000 new with 25 %
    physical depreciation, and two itemized rows inside the model."""
    fields = [
        _field("improvements", "gla_sqft", 2000),
        _field("improvements", "stories", 2),
        _field("improvements", "quality", "Q4"),
        _field("improvements", "bedrooms", 3),
        _field("improvements", "total_rooms", 7),
        _field("improvements", "effective_age_years", 15),
        _field("cost_approach", "total_cost_new", 300000),
        _field("cost_approach", "dwelling_cost", 300000),
        _field("cost_approach", "depreciation_physical", 75000),
        _field("cost_approach", "depreciated_cost_improvements", 225000),
        _field("cost_approach", "remaining_economic_life", 45),
        _field("cost_approach", "site_value", 20000),
    ]

    def asset(label: str, bucket: str, total: str) -> dict[str, Any]:
        return {
            "id": label,
            "label": label,
            "room_type": "kitchen",
            "category": "Interior finishes" if bucket == "y5" else "Exterior walls",
            "bucket": bucket,
            "quantity": "1",
            "unit": "EA",
            "formula": "",
            "unit_cost": total,
            "total": total,
            "takeoff_inputs": {"component": label, "count": 1},
            "needs_review": False,
            "review_reason": None,
        }

    return {
        "format": "basis-export/2",
        "study_id": "s",
        "revision": 1,
        "extraction": [{"fields": fields}],
        "assets": [asset("carpet", "y5", "5000.00"), asset("window", "y39", "15000.00")],
        "summary": {
            "takeoff": {
                "cost_year": 2020,
                "location_factor": "1.0",
                "cost_year_source": "fabricated",
                "location_source": "fabricated",
            },
            "basis": {
                "worksheet": {
                    "purchase_price": price,
                    "land": land,
                    "land_source": "fabricated",
                    "acquisition_costs": [],
                    "property_use": "nonresidential",
                    "placed_in_service_date": "2020-06-01",
                    "state": "IL",
                    "zip": None,
                }
            },
        },
    }


def _labels() -> tuple[E2ERows, EngineerReport]:
    assign = [
        {"id": "r1", "system": "Carpet", "kind": "B", "assign": [{"label": "carpet"}]},
        {"id": "r2", "system": "Windows", "kind": "A", "assign": [{"label": "window"}]},
    ]
    used = {"carpet", "window"}
    rows = E2ERows.model_validate(
        {
            "rows": assign,
            "unassigned": [
                {"label": label, "reason": "not in this test"}
                for label in COMPONENT_LABELS
                if label not in used
            ],
        }
    )
    report = EngineerReport.model_validate(
        {
            "building_basis": "200000.00",
            "closing_costs": "0.00",
            "rows": [
                {"id": "r1", "system": "Carpet", "bucket": "y5", "cost": "20000.00"},
                {"id": "r2", "system": "Windows", "bucket": "y39", "cost": "180000.00"},
            ],
        }
    )
    return rows, report


def test_one_variant_end_to_end_at_a_six_percent_gap() -> None:
    """Replay the appraisal-total variant once at a price that leaves a 6 % gap, then set the
    price from its own RCNLD: the cell carries the replay's status and gap, and the rules
    give 6 %'s verdicts (withheld at 5, balances at 7.5 and 10, flagged by the two bands)."""
    table = load_cost_table(str(fixture_path("cost_table.json")))
    variant = Variant(rcn_source="appraisal_total")
    probe = _export("400000.00", "40000.00")
    _, changes = replay(
        probe, cost_table=table, intake=rebuild_intake(probe, None), variant=variant
    )
    rcnld = D(changes["reconciliation"]["rcnld_total"])
    # B = price - land with land at 10 % of the price; B = RCNLD / 0.94 puts the gap at +6 %
    b = (rcnld / D("0.94")).quantize(D("0.01"))
    price = (b / D("0.9")).quantize(D("0.01"))
    document = _export(str(price), str((price * D("0.1")).quantize(D("0.01"))))
    rows, report = _labels()
    baseline = sw.baseline_scores(document, "S", rows, report)
    got = bt.measure(
        "S",
        document,
        variant,
        grid=bt.GRID_STRUCTURE,
        name=variant.name,
        cost_table=table,
        rows=rows,
        report=report,
        baseline=baseline,
    )
    assert got.sourced and got.status == "reconciled" and got.defects == ()
    assert got.gap_pct == pytest.approx(6.0, abs=0.001)
    assert got.regression_ok is True
    assert got.model_share_pct is not None and 0 < got.model_share_pct < 100
    assert got.factor_source in ("allocation", "implied")
    assert got.allocation_factor is not None
    assert D(got.allocation_factor) == pytest.approx(D(1) / D("0.94"), abs=D("0.0001"))
    assert [bt.fixed_verdict(got, t) for t in (5, 7.5, 10)] == [
        "withheld",
        "balances",
        "balances",
    ]
    assert bt.two_band_verdict(got) == "allocate_flagged"
    assert set(got.bucket_error_pct or {}) == set(bt.BUCKETS)
