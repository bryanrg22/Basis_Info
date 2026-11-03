"""``evals.location_factor``: the five resolutions of the location factor, the nearby-prefix
rule, the state spread and the caller-chosen factor of ``evals.reprice_export``, on a tiny
synthetic factor table and a fabricated export."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fixtures import fixture_path

from basis.domain.cost_indices import CityFactor, LocationTable, location_table_from
from basis.domain.cost_table import load_cost_table
from evals import location_factor as lf
from evals.reprice_export import LocationChoice, reprice
from evals.structure_sweep import Cell


def table() -> LocationTable:
    """Two states: XX with three cities around a mean of exactly 1.000 (a gap at 102 and
    104), YY with one city."""
    return location_table_from(
        [
            CityFactor("XX", "100-101", "Alpha", Decimal("0.90"), 1),
            CityFactor("XX", "103", "Beta", Decimal("1.10"), 1),
            CityFactor("XX", "105", "Gamma", Decimal("1.00"), 1),
            CityFactor("YY", "200", "Delta", Decimal("1.20"), 2),
        ],
        year=2020,
        origin="synthetic",
        source="Synthetic Location Factors",
        pages="1-2",
    )


def test_state_spread_counts_cities_around_the_mean() -> None:
    s = lf.state_spread("xx", table())
    assert (s.cities, s.mean, s.median, s.low, s.high) == (
        3,
        Decimal("1.000"),
        Decimal("1.000"),
        Decimal("0.90"),
        Decimal("1.10"),
    )
    assert (s.above_mean, s.below_mean, s.at_mean) == (1, 1, 1)
    assert s.spread_points == Decimal("20.00")
    with pytest.raises(lf.LocationFactorError):
        lf.state_spread("ZZ", table())


def test_nearest_listed_prefix_is_the_prefix_itself_when_listed_else_the_nearest_by_number() -> (
    None
):
    t = table()
    assert lf.nearest_listed_prefix("103", "XX", t) == ("103", t.by_prefix["103"], False)
    # 102 is one away from 101 and 103: the tie goes to the lower prefix
    assert lf.nearest_listed_prefix("102", "XX", t) == ("101", t.by_prefix["101"], True)
    assert lf.nearest_listed_prefix("104", "XX", t) == ("103", t.by_prefix["103"], True)
    assert lf.nearest_listed_prefix("109", "XX", t) == ("105", t.by_prefix["105"], True)
    # the search stays in the state asked for
    assert lf.nearest_listed_prefix("200", "XX", t) == ("105", t.by_prefix["105"], True)
    assert lf.nearest_listed_prefix("200", "ZZ", t) is None


def test_prefix_of_takes_the_first_three_digits() -> None:
    assert lf.prefix_of("10399") == "103"
    assert lf.prefix_of(" 103-45 ") == "103"
    assert lf.prefix_of("10") is None
    assert lf.prefix_of(None) is None


def _keys(chosen: list[lf.Setup]) -> list[str]:
    return [s.key for s in chosen]


def test_setups_with_a_listed_prefix() -> None:
    chosen = lf.setups("10399", "XX", zip_origin="intake", table=table())
    assert _keys(chosen) == ["state_mean", "zip_city", "nearest_prefix", "state_median", "national"]
    by_key = {s.key: s for s in chosen}
    assert by_key["state_mean"].factor == Decimal("1.000")
    assert "mean of 3 XX cities (0.90-1.10)" in by_key["state_mean"].source
    assert by_key["zip_city"].factor == Decimal("1.10")
    assert by_key["zip_city"].row == "Beta 1.10" and by_key["zip_city"].detail == "prefix listed"
    assert (
        by_key["zip_city"].source == "zip (intake): 103 Beta 1.10, Synthetic Location Factors p.1"
    )
    assert by_key["nearest_prefix"].factor == Decimal("1.10")
    assert by_key["nearest_prefix"].detail.startswith("prefix listed: the rule is not needed")
    assert by_key["state_median"].factor == Decimal("1.000")
    assert "median of 3 XX cities" in by_key["state_median"].source
    assert by_key["national"].factor == Decimal("1.000")
    # a ZIP never appears in what the table prints
    for s in chosen:
        assert "10399" not in s.source and "10399" not in s.detail and "10399" not in s.title


def test_setups_with_an_absent_prefix_fall_back_as_the_code_does_and_name_the_nearest() -> None:
    by_key = {s.key: s for s in lf.setups("10250", "XX", table=table())}
    # the code's own fallback for a prefix the table lacks: the state mean
    assert by_key["zip_city"].factor == Decimal("1.000") and by_key["zip_city"].row is None
    assert by_key["zip_city"].detail.startswith("prefix absent from the table")
    assert by_key["nearest_prefix"].factor == Decimal("0.90")
    assert by_key["nearest_prefix"].row == "Alpha 0.90"
    assert by_key["nearest_prefix"].detail.startswith("prefix not among XX's listed prefixes")
    assert "nearest listed prefix 101 of XX" in by_key["nearest_prefix"].source


def test_fallback_check_leaves_one_row_out_at_a_time() -> None:
    # Alpha out: mean of the others 1.050 (err 15 points), nearest 103 Beta 1.10 (20);
    # Beta out: mean 0.950 (15), 101 and 105 tie, the lower wins: Alpha 0.90 (20);
    # Gamma out: mean 1.000 (0), nearest 103 Beta 1.10 (10)
    check = lf.fallback_check("XX", table())
    assert check == {
        "state": "XX",
        "rows": 3,
        "state_mean_mae_points": Decimal("10.00"),
        "state_mean_worst_points": Decimal("15.00"),
        "nearest_prefix_mae_points": Decimal("16.67"),
        "nearest_prefix_worst_points": Decimal("20.00"),
    }
    assert lf.fallback_check("YY", table())["rows"] == 0
    # over the table: YY (one row) has nothing to check and is left out
    assert lf.fallback_check_all(table()) == {
        "states": 1,
        "rows": 3,
        "state_mean_mae_points": Decimal("10.00"),
        "nearest_prefix_mae_points": Decimal("16.67"),
        "states_nearest_closer": 0,
        "states_mean_closer": 1,
    }


def test_setups_name_a_prefix_listed_under_another_state() -> None:
    by_key = {s.key: s for s in lf.setups("20011", "XX", zip_origin="appraisal", table=table())}
    # the ZIP's row is in YY: the lookup applies it anyway, the setup says so
    assert by_key["zip_city"].factor == Decimal("1.20") and by_key["zip_city"].row == "Delta 1.20"
    assert by_key["zip_city"].detail == "prefix listed under YY, not XX: no cross-check in the code"
    assert by_key["nearest_prefix"].factor == Decimal("1.00")
    assert by_key["nearest_prefix"].detail.endswith("(the ZIP's own row is in YY)")


def test_setups_without_a_zip_and_with_extra_zips() -> None:
    chosen = lf.setups(None, "XX", extra_zips={"city": "10500"}, table=table())
    by_key = {s.key: s for s in chosen}
    assert by_key["zip_city"].factor == Decimal("1.000")
    assert by_key["zip_city"].detail.startswith("no ZIP on the intake or the appraisal")
    assert by_key["nearest_prefix"].detail.startswith("no usable prefix")
    assert by_key["zip_city:city"].factor == Decimal("1.00")
    assert by_key["zip_city:city"].row == "Gamma 1.00"
    with pytest.raises(lf.LocationFactorError):
        lf.setups(None, "XX", extra_zips={"bad": "99999"}, table=table())
    with pytest.raises(lf.LocationFactorError):
        lf.setups(None, "ZZ", table=table())


# ------------------------------------------------------------------ the repricing


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
        {"section": "subject", "field": "state", "value_text": "IL", "source": "llm"},
        {"section": "subject", "field": "zip", "value_text": "60601", "source": "llm"},
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


def test_reprice_prices_on_the_callers_factor_and_writes_its_source() -> None:
    cost_table = load_cost_table(str(fixture_path("cost_table.json")))
    choice = LocationChoice(Decimal("0.900"), "zip (intake): 103 Beta 0.90, Synthetic p.1")
    repriced, changes = reprice(
        _export(),
        cost_table=cost_table,
        state=None,
        zip_code=None,
        supplement=None,
        location_choice=choice,
    )
    totals = {a["label"]: a["total"] for a in repriced["assets"]}
    # bare 230 x 0.9 = 207; 3.50 x 0.9 = 3.15 per SF x 100
    assert totals == {"window": "207.00", "patio": "315.00"}
    assert changes["location_factor"] == {"old": "1.0", "new": "0.900"}
    assert changes["location_source"]["new"] == choice.source
    assert repriced["summary"]["takeoff"]["location_factor"] == "0.900"
    assert repriced["summary"]["takeoff"]["location_source"] == choice.source
    for asset in repriced["assets"]:
        assert asset["location_factor"] == "0.900"
        assert asset["takeoff_inputs"]["location_source"] == choice.source
        assert choice.source in asset["cost_source"]
    # without a choice the export's state is looked up as before (IL: the mean 0.959)
    _, plain = reprice(_export(), cost_table=cost_table, state=None, zip_code=None, supplement=None)
    assert plain["location_factor"]["new"] == "0.959"


def test_export_location_reads_the_intake_first_then_the_appraisal() -> None:
    document = _export()
    assert lf.export_location(document) == (
        "IL",
        "60601",
        "export takeoff",
        "appraisal subject.zip (llm)",
    )
    document["summary"]["basis"] = {"worksheet": {"state": "CA", "zip": "90001"}}
    assert lf.export_location(document) == ("CA", "90001", "intake", "intake")


# ------------------------------------------------------------------ the record and the table


def _cell(**overrides: object) -> Cell:
    values: dict[str, object] = {
        "export": "A",
        "variant": "state_mean",
        "sourced": True,
        "note": "",
        "runtime_ms": 8.0,
        "status": "reconciled",
        "gap_share_pct": 2.0096,
        "implied_uniform_factor": 0.80298,
        "allocation_factor": 1.02199,
        "kind_c_produced": 8,
        "kind_c_right": 7,
        "kind_c_wrong": ("Access Control (y5 vs y39)",),
        "bucket_share_pct": {"y5": 14.09, "y7": 0.94, "y15": 4.21, "y39": 80.75},
        "bucket_error_pct": {"y5": 0.26, "y7": 0.58, "y15": 0.84, "y39": 0.52},
        "mean_bucket_error_pct": 0.55,
        "tvd_pct": 23.06,
        "mean_row_error_pct": 1.0,
        "visible_evidenced": 35,
        "visible_right": 35,
        "regression_ok": True,
    }
    values.update(overrides)
    return Cell(**values)  # type: ignore[arg-type]


def test_compare_cell_reports_only_the_fields_that_differ() -> None:
    cell = _cell()
    stored = {**cell.__dict__, "variant": "production", "runtime_ms": 99.0}
    assert lf.compare_cell(cell, stored) == []
    stored["gap_share_pct"] = 2.1
    stored["bucket_share_pct"] = {"y5": 14.0, "y7": 0.94, "y15": 4.21, "y39": 80.75}
    diffs = lf.compare_cell(cell, stored)
    assert len(diffs) == 2 and diffs[0].startswith("gap_share_pct: 2.0096 != 2.1")


def test_the_table_has_the_house_style_columns_and_no_zip() -> None:
    chosen = lf.setups("10399", "XX", table=table())
    results = [
        lf.Result(
            "A", s, _cell(variant=s.key), Decimal("1000"), Decimal("1.1"), {"repriced": 2}, 9.0
        )
        for s in chosen
    ]
    text = lf.render_markdown(
        results,
        {"XX": lf.state_spread("XX", table())},
        {"y5": 14.4, "y7": 0.4, "y15": 5.1, "y39": 80.2},
        {"XX": lf.fallback_check("XX", table())},
        lf.fallback_check_all(table()),
    )
    assert "**How to read this table.**" in text
    assert "| Tokens (input / output) |" in text and "| None |" in text
    assert "| No AI model |" in text
    assert "| 1.10 (Beta 1.10) |" in text
    assert "| +2.0 reconciled |" in text and "| 0.55 / 0.8 |" in text
    assert "| 14.1 / 0.9 / 4.2 / 80.8 |" in text and "| 35 / 35 |" in text
    assert "XX: 3 listed cities, mean 1.000, median 1.000, range 0.90-1.10 (20 points)" in text
    assert "the mean of the others is off by 10.00 points on average (worst 15.00)" in text
    assert "the nearest prefix is closer in 0 states, the mean in 1." in text
    assert "10399" not in text


def test_also_zip_takes_label_equals_zip() -> None:
    assert lf._parse_extra(["city=10500", " other = 10399 "]) == {"city": "10500", "other": "10399"}
    with pytest.raises(lf.LocationFactorError):
        lf._parse_extra(["nolabel"])
