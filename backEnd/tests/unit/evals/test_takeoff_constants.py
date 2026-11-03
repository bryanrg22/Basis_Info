"""``evals.takeoff_constants``: the constants registry, the override, the takeoff replay on
a fabricated export (hand-computed quantities), the dump conversion, the sensitivity
ranking and the table; no private data, no scorer."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from basis.domain import measurements as geometry
from basis.domain import takeoffs
from basis.domain.rooms import ROOM_WEIGHTS_SF
from evals import takeoff_constants as tc
from evals.structure_sweep import Cell

D = Decimal


def _asset(
    asset_id: str,
    label: str,
    room_id: str | None,
    room_type: str | None,
    quantity: str,
    unit: str,
    unit_cost: str,
    inputs: dict[str, Any],
    bucket: str = "y5",
) -> dict[str, Any]:
    total = (D(quantity) * D(unit_cost)).quantize(D("0.01"))
    return {
        "id": asset_id,
        "label": label,
        "room_id": room_id,
        "room_type": room_type,
        "bucket": bucket,
        "quantity": quantity,
        "unit": unit,
        "formula": "stored",
        "unit_cost": unit_cost,
        "total": str(total),
        "needs_review": False,
        "review_reason": None,
        "takeoff_inputs": {"component": label, "count": 1, **inputs},
    }


def _field(section: str, name: str, value: Any) -> dict[str, Any]:
    return {"section": section, "field": name, "value_num": value, "source": "llm"}


def _export() -> dict[str, Any]:
    """A 1,000 SF one-story house with a kitchen and a bedroom (study weight 300 = program
    weight 300: each room is 500 SF), no garage, on a 10,000 SF site. Quantities as the
    takeoff writes them today (hand-computed in the tests)."""
    fields = [
        _field("improvements", "gla_sqft", 1000),
        _field("improvements", "stories", 1),
        _field("improvements", "total_rooms", 2),
        _field("improvements", "bedrooms", 1),
        _field("site", "area_sqft", 10000),
    ]
    rooms = [
        {"id": "k", "room_type": "kitchen", "floor": "1"},
        {"id": "b", "room_type": "bedroom", "floor": "1"},
        {"id": "x", "room_type": "exterior", "floor": None},
    ]
    assets = [
        # 500 SF / 9 x 1.1 waste = 61.11 SY
        _asset(
            "carpet",
            "carpet",
            "b",
            "bedroom",
            "61.1100",
            "SY",
            "80.00",
            {"method": "area", "room_area_source": "gla_share", "room_area_sf": "500"},
        ),
        # 4 x sqrt(500) = 89.44 LF
        _asset(
            "baseboard",
            "baseboard",
            "k",
            "kitchen",
            "89.4400",
            "LF",
            "3.00",
            {"method": "perimeter", "room_area_source": "gla_share", "room_area_sf": "500"},
        ),
        # a second carpet in the bedroom: measured once, stays 0
        _asset(
            "carpet2",
            "carpet",
            "b",
            "bedroom",
            "0.0000",
            "SY",
            "80.00",
            {
                "method": "area",
                "room_area_source": "gla_share",
                "measured_with_asset": "carpet",
            },
        ),
        # counted: untouched
        _asset(
            "window",
            "window",
            "b",
            "bedroom",
            "2.0000",
            "EA",
            "300.00",
            {"method": "count", "room_area_source": "gla_share", "count": 2},
            bucket="y39",
        ),
        # (1000 + 129.10 x 1 ft) x 1.118 = 1262.33 SF
        _asset(
            "roof",
            "roof",
            None,
            None,
            "1262.3300",
            "SF",
            "4.00",
            {"method": "area", "quantity_source": "appraisal_derived"},
            bucket="y39",
        ),
        # 2 x (38.73 + 2 x 1) = 81.46 LF
        _asset(
            "gutter",
            "gutter",
            None,
            None,
            "81.4600",
            "LF",
            "6.00",
            {"method": "perimeter", "quantity_source": "appraisal_derived"},
            bucket="y39",
        ),
        # an exterior room on a typical area: untouched
        _asset(
            "exterior_tile",
            "tile",
            "x",
            "exterior",
            "172.5000",
            "SF",
            "9.00",
            {"method": "area", "room_area_source": "default_150_sf", "room_area_sf": "150"},
        ),
        # a concealed row: untouched
        {
            "id": "foundation",
            "label": "foundation",
            "bucket": "y39",
            "quantity": "1000",
            "unit": "SF",
            "unit_cost": "10.00",
            "total": "10000.00",
            "takeoff_inputs": {"concealed": True, "component": "foundation"},
        },
    ]
    return {
        "format": "basis-export/2",
        "study_id": "s",
        "revision": 1,
        "rooms": rooms,
        "extraction": [{"fields": fields}],
        "assets": assets,
        "summary": {"takeoff": {"property_class": "residential_rental", "cost_year": 2020}},
    }


def _quantities(document: dict[str, Any]) -> dict[str, str]:
    return {a["id"]: a["quantity"] for a in document["assets"]}


# ------------------------------------------------------------------ the registry


def test_every_constant_names_a_real_target_at_todays_value() -> None:
    names = [c.name for c in tc.CONSTANTS]
    assert len(set(names)) == len(names)
    for constant in tc.CONSTANTS:
        assert constant.targets, constant.name
        for target in constant.targets:
            if target.kind == "geometry":
                assert D(str(getattr(geometry, target.key))) == constant.today, constant.name
            elif target.kind == "weight":
                assert D(ROOM_WEIGHTS_SF[target.key]) == constant.today, constant.name
            elif target.kind == "perimeter":
                spec = takeoffs.QUANTITY_ESTIMATES[target.key]
                assert spec.method == "perimeter", target.key
                assert constant.today == tc.SQUARE_PERIMETER
            else:
                assert target.attr is not None
                spec = takeoffs.QUANTITY_ESTIMATES[target.key]
                assert getattr(spec, target.attr) == constant.today, constant.name
        if constant.linear:
            assert constant.slope_pair is None
        else:
            assert constant.slope_pair is not None
    # the program constant and the weight of one room type are one assumption
    kitchen = tc.BY_NAME["kitchen_sf"]
    assert {t.kind for t in kitchen.targets} == {"weight", "geometry"}
    assert tc.BY_NAME["half_bath_sf"].targets == (tc.Target("geometry", "HALF_BATH_SF"),)
    # the program's "other room" (200) and the "other" room type's weight (150) are two
    # constants, not one
    assert tc.BY_NAME["other_room_sf"].targets == (tc.Target("geometry", "OTHER_ROOM_SF"),)
    assert tc.BY_NAME["other_weight_sf"].targets == (tc.Target("weight", "other"),)
    # every perimeter spec is scaled by the room perimeter coefficient
    perimeter = {k for k, spec in takeoffs.QUANTITY_ESTIMATES.items() if spec.method == "perimeter"}
    assert {t.key for t in tc.BY_NAME["room_perimeter_coefficient"].targets} == perimeter


def test_values_cover_the_stated_range_and_the_published_value_once() -> None:
    overhang = tc.BY_NAME["eave_overhang_ft"]
    assert [v for _, v in overhang.values()] == [D("0.5"), D("0.75"), D("1.25"), D("1.5"), D(2)]
    # the cost book's 1 ft is the published value (today's: no cell); ResStock's 2 ft is an
    # extra cell and stays out of the published set
    assert overhang.values()[-1][0] == "eave_overhang_ft ResStock 2 ft"
    assert tc.published_set()["eave_overhang_ft"] == D(1)
    wainscot = tc.BY_NAME["wainscot_height_ft"]
    # today is the priced line's 2 ft (EVAL_REPORT 89); x1.5 is the 3 ft of before, the trim
    # guides' published value, cell once (the extra cell of the same value is not repeated)
    assert [v for _, v in wainscot.values()] == [D(1), D("1.5"), D("2.5"), D(3)]
    assert tc.published_set()["wainscot_height_ft"] == D(3)
    # a typical area: the range, then the survey value
    assert [v for _, v in tc.BY_NAME["kitchen_sf"].values()] == [
        D(75),
        D("112.50"),
        D("187.50"),
        D(225),
        D(288),
    ]
    # a published value already in the range is not a second cell; one equal to today is none
    assert all(v != D(9) for _, v in tc.BY_NAME["story_wall_height_ft"].values())
    assert len(tc.BY_NAME["story_wall_height_ft"].values()) == 4
    pitch = tc.BY_NAME["roof_pitch_factor"]
    assert [n for n, _ in pitch.values()] == [
        "roof_pitch_factor 4/12",
        "roof_pitch_factor 8/12",
        "roof_pitch_factor 12/12",
    ]
    names = [s.name for s in tc.setups()]
    assert names[0] == "today" and names[-1] == "all published"
    assert len(names) == len(set(names))
    assert set(tc.published_set()) >= {"eave_overhang_ft", "kitchen_sf", "wall_openings_share"}
    assert "hallway_sf" not in tc.published_set()
    groups = {s.name: s.values for s in tc.setups() if s.name.startswith("published ")}
    assert list(groups) == ["published geometry", "published typical_area", "published formula"]
    assert sum(len(v) for v in groups.values()) == len(tc.published_set())
    assert groups["published typical_area"]["kitchen_sf"] == D(288)
    assert "office_sf" in tc.published_set()


def test_override_sets_every_target_and_restores_it() -> None:
    before = (
        geometry.EAVE_OVERHANG_FT,
        geometry.KITCHEN_SF,
        ROOM_WEIGHTS_SF["kitchen"],
        takeoffs.QUANTITY_ESTIMATES["carpet"],
    )
    with tc.override({"eave_overhang_ft": D(2), "kitchen_sf": D(300), "carpet_waste": D("1.2")}):
        assert geometry.EAVE_OVERHANG_FT == D(2)
        assert geometry.KITCHEN_SF == D(300) and ROOM_WEIGHTS_SF["kitchen"] == D(300)
        assert takeoffs.QUANTITY_ESTIMATES["carpet"].waste_factor == D("1.2")
        assert takeoffs.QUANTITY_ESTIMATES["carpet"].unit == "SY"
    assert (
        geometry.EAVE_OVERHANG_FT,
        geometry.KITCHEN_SF,
        ROOM_WEIGHTS_SF["kitchen"],
        takeoffs.QUANTITY_ESTIMATES["carpet"],
    ) == before
    with pytest.raises(KeyError):
        with tc.override({"no_such_constant": D(1)}):
            pass
    assert geometry.EAVE_OVERHANG_FT == before[0]


# ------------------------------------------------------------------ the takeoff replay


def test_todays_constants_reproduce_the_stored_quantities() -> None:
    document = _export()
    taken, changes = tc.retakeoff(document)
    assert changes["changed"] == []
    assert changes["counts"] == {
        "room_measured": 2,
        "property_measured": 2,
        "measured_elsewhere": 1,
        "kept": 3,
        "now_unpriced": 0,
    }
    assert _quantities(taken) == _quantities(document)
    assert [a["total"] for a in taken["assets"]] == [a["total"] for a in document["assets"]]
    # the formulas and inputs are rewritten from the takeoff, the stored ones left behind
    roof = next(a for a in taken["assets"] if a["id"] == "roof")
    assert "1.118 pitch" in roof["formula"] and roof["takeoff_inputs"]["overhang_ft"] == "1"
    assert document["assets"][4]["formula"] == "stored"  # the input document is untouched


def test_a_varied_constant_moves_only_the_rows_that_depend_on_it() -> None:
    document = _export()
    with tc.override({"eave_overhang_ft": D(2)}):
        taken, changes = tc.retakeoff(document)
    moved = {c["id"]: c["new"] for c in changes["changed"]}
    # (1000 + 129.10 x 2) x 1.118 = 1406.67; 2 x (38.73 + 4) = 85.46
    assert moved == {"roof": "1406.6700", "gutter": "85.4600"}
    roof = next(a for a in taken["assets"] if a["id"] == "roof")
    assert roof["total"] == "5626.68"  # 4.00 x 1406.67: the pricing is linear
    with tc.override({"kitchen_sf": D(300)}):
        _, changes = tc.retakeoff(document)
    moved = {c["id"]: c["new"] for c in changes["changed"]}
    # kitchen 1000 x 300 / 450 = 666.67 SF (program weight 450 too); bedroom 333.33 SF
    # carpet 333.33 / 9 x 1.1 = 40.74 SY; baseboard 4 x sqrt(666.67) = 103.28 LF
    assert moved == {"carpet": "40.7400", "baseboard": "103.2800"}
    with tc.override({"gutter_eaves": D(4)}):
        _, changes = tc.retakeoff(document)
    assert {c["id"]: c["new"] for c in changes["changed"]} == {"gutter": "162.9200"}
    with tc.override({"carpet_waste": D("1.0")}):
        _, changes = tc.retakeoff(document)
    assert {c["id"]: c["new"] for c in changes["changed"]} == {"carpet": "55.5600"}
    # the program constant alone cannot move a room while the study weight is the larger
    with tc.override({"half_bath_sf": D(400)}):
        _, changes = tc.retakeoff(document)
    assert changes["changed"] == []


def test_a_room_todays_rules_cannot_size_is_written_unpriced() -> None:
    document = _export()
    document["rooms"][1]["room_type"] = "ambiguous"
    taken, changes = tc.retakeoff(document)
    carpet = next(a for a in taken["assets"] if a["id"] == "carpet")
    assert carpet["total"] is None and carpet["unit_cost"] is None
    assert carpet["review_reason"] == "input:room_type" and carpet["needs_review"] is True
    assert carpet["takeoff_inputs"]["missing_input"] == "room_type"
    assert changes["counts"]["now_unpriced"] == 1
    summary = tc.priced_summary(taken["assets"])
    # the concealed row and the carpet are out: baseboard 268.32 + window 600 + roof 5049.32
    # + gutter 488.76 + exterior tile 1552.50; the second carpet (measured once, at 0.00) is
    # counted priced, as section 35.2 counted the rows measured once per property
    assert summary["priced"] == 6 and summary["total"] == "7958.90"
    assert summary["shares"]["y39"] == pytest.approx(77.12, abs=0.01)


def test_an_export_without_fields_is_refused() -> None:
    document = _export()
    document["extraction"] = []
    with pytest.raises(tc.ReplayError, match="no extraction fields"):
        tc.retakeoff(document)


# ------------------------------------------------------------------ section 35.2's dump


def test_the_dump_becomes_an_export_the_replay_reads() -> None:
    rows: list[dict[str, Any]] = [
        {
            "asset_id": "a",
            "room_name": "Kitchen",
            "room_type": "kitchen",
            "floor": "1",
            "component_label": "baseboard",
            "quantity": "89.44",
            "unit": "LF",
            "unit_cost": "3.00",
            "total": "268.32",
            "bucket": "y27_5",
            "review_reason": "cross:COST_ROW_MISSING",
            "needs_review": True,
            "formula": "4 x sqrt(500 SF)",
            "inputs": {
                "component": "baseboard",
                "count": 1,
                "method": "perimeter",
                "room_area_source": "gla_share",
            },
        },
        {
            "asset_id": "b",
            "room_name": "Kitchen",
            "room_type": "kitchen",
            "floor": "1",
            "component_label": "driveway",
            "quantity": "0.00",
            "unit": "SF",
            "unit_cost": None,
            "total": None,
            "bucket": "y15",
            "review_reason": "cross:COST_NEEDS_QUANTITY",
            "needs_review": True,
            "formula": "",
            "inputs": {"component": "driveway", "method": "area", "room_area_source": "gla_share"},
        },
    ]
    fields = [
        {**_field("improvements", "gla_sqft", 1000), "revision": 3},
        {**_field("improvements", "stories", 1), "revision": 3},
        {**_field("improvements", "gla_sqft", 999), "revision": 2},
    ]
    document = tc.document_from_dump(rows, fields, revision=3, property_class="residential_rental")
    assert [r["id"] for r in document["rooms"]] == ["Kitchen"]
    assert [f["value_num"] for f in document["extraction"][0]["fields"]] == [1000, 1]
    summary = tc.dump_summary(document)
    assert summary == {
        "priced": 1,
        "total": "268.32",
        "shares": {"y5": 0.0, "y7": 0.0, "y15": 0.0, "y39": 100.0},
        "missing_measurement": 1,
        "no_cost_row": 1,
        "assets": 2,
    }
    # one kitchen of 150 SF weight takes the whole GLA: 4 x sqrt(1000) = 126.49 LF
    taken, changes = tc.retakeoff(document)
    assert {c["id"]: c["new"] for c in changes["changed"]} == {"a": "126.4900"}
    assert tc.dump_summary(taken)["total"] == "379.47"


# ------------------------------------------------------------------ the sensitivity table


def _cell(error: float) -> Cell:
    return Cell(
        export="A",
        variant="v",
        sourced=True,
        note="",
        runtime_ms=1.0,
        status="reconciled",
        gap_share_pct=2.0,
        mean_bucket_error_pct=error,
        bucket_share_pct={b: 25.0 for b in ("y5", "y7", "y15", "y39")},
        bucket_error_pct={b: error for b in ("y5", "y7", "y15", "y39")},
        tvd_pct=20.0,
    )


def _scored(export: str, setup: tc.Setup, error: float, ratio: float, moved: int) -> tc.Scored:
    return tc.Scored(
        export=export,
        setup=setup.name,
        constant=setup.constant,
        value=None if setup.value is None else str(setup.value),
        factor=setup.factor,
        runtime_ms=1.0,
        recomputed={},
        changed_quantities=moved,
        quantities_equal_stored=moved == 0,
        priced=10,
        priced_total="1000.00",
        priced_total_ratio=ratio,
        itemized_shares={b: 25.0 for b in ("y5", "y7", "y15", "y39")},
        itemized_error=error / 2,
        itemized_worst=error,
        cell=_cell(error),
    )


def test_sensitivity_ranks_by_the_central_slope_and_reports_unexercised_constants() -> None:
    by_name = {s.name: s for s in tc.setups()}
    cells = [_scored("A", by_name["today"], 1.0, 1.0, 0)]
    # the overhang: error 0.9 at x0.75, 1.3 at x1.25 -> slope 0.8 points per +100 %
    for name, error, ratio in (
        ("eave_overhang_ft x0.5", 0.8, 0.98),
        ("eave_overhang_ft x0.75", 0.9, 0.99),
        ("eave_overhang_ft x1.25", 1.3, 1.01),
        ("eave_overhang_ft x1.5", 1.6, 1.02),
        ("eave_overhang_ft ResStock 2 ft", 2.0, 1.04),
    ):
        cells.append(_scored("A", by_name[name], error, ratio, 2))
    # the gutters: hip at 1.2 -> slope (1.2 - 1.0) / 1.0 = 0.2
    cells.append(_scored("A", by_name["gutter_eaves hip (4 sides)"], 1.2, 1.001, 1))
    # the garage: never moves a quantity
    for factor in ("x0.5", "x0.75", "x1.25", "x1.5"):
        cells.append(_scored("A", by_name[f"garage_sf_per_car {factor}"], 1.0, 1.0, 0))
    ranking = tc.sensitivity(cells)
    rows = {s.constant: s for s in ranking}
    assert [s.constant for s in ranking[:2]] == ["eave_overhang_ft", "gutter_eaves"]
    overhang = rows["eave_overhang_ft"]
    assert overhang.slope_points_per_100pct == pytest.approx(0.8)
    assert overhang.max_abs_error_move == pytest.approx(1.0)  # the published cell
    assert overhang.max_abs_total_move_pct == pytest.approx(4.0)
    assert overhang.exercised is True and overhang.range_tested.endswith("ResStock 2 ft (2)")
    assert overhang.itemized_slope_per_100pct == pytest.approx(0.4)  # half the scored error
    assert overhang.slope_by_export == {"A": pytest.approx(0.8)}
    assert overhang.dump_slope_per_100pct is None  # no dump cells
    assert rows["gutter_eaves"].slope_points_per_100pct == pytest.approx(0.2)
    garage = rows["garage_sf_per_car"]
    assert garage.exercised is False and garage.slope_points_per_100pct == 0.0
    # a constant with no cells at all has no slope and ranks last
    assert rows["tile_waste"].slope_points_per_100pct is None
    assert ranking[-1].slope_points_per_100pct is None
    text = tc.render_markdown(cells, ranking, {"y5": 14.4, "y39": 80.2}, {"stored": {"x": 1}})
    assert "| 1 | eave_overhang_ft | geometry | 1 |" in text
    assert "every export unchanged" in text and "Section 35.2's dump" in text
    moved = tc.render_markdown([_scored("A", by_name["today"], 1.0, 1.0, 3)], [], {}, None)
    assert "MOVED on A (3)" in moved


# ------------------------------------------------------------------ the added axes


def test_the_room_perimeter_coefficient_scales_every_perimeter_row_after_the_spec() -> None:
    before = dict(takeoffs.QUANTITY_ESTIMATES)
    with tc.override({"room_perimeter_coefficient": D("4.08"), "wainscot_height_ft": D(2)}):
        assert takeoffs.QUANTITY_ESTIMATES["baseboard"].per_sf == D("1.02")
        # the wainscot height is set first, then scaled: 2 x 4.08 / 4
        assert takeoffs.QUANTITY_ESTIMATES["wainscoting"].per_sf == D("2.04")
        assert takeoffs.QUANTITY_ESTIMATES["carpet"] == before["carpet"]
    assert takeoffs.QUANTITY_ESTIMATES == before
    document = _export()
    with tc.override({"room_perimeter_coefficient": D(5)}):
        _, changes = tc.retakeoff(document)
    # the kitchen's baseboard: 5 x sqrt(500) = 111.80 LF; nothing else is on a perimeter
    assert {c["id"]: c["new"] for c in changes["changed"]} == {"baseboard": "111.8000"}


def test_a_site_item_on_a_measured_room_is_never_given_the_room_area() -> None:
    document = _export()
    document["assets"].append(
        _asset(
            "drive",
            "driveway",
            "k",
            "kitchen",
            "0.00",
            "SF",
            "0",
            {"method": "area", "room_area_source": "gla_share", "room_area_sf": "500"},
            bucket="y15",
        )
    )
    taken, changes = tc.retakeoff(document)
    assert changes["changed"] == []
    assert next(a for a in taken["assets"] if a["id"] == "drive")["quantity"] == "0.00"


def test_share_error_and_the_engineers_shares() -> None:
    mean, worst = tc.share_error(
        {"y5": 12.0, "y7": 1.0, "y15": 4.0, "y39": 83.0},
        {"y5": 14.0, "y7": 0.5, "y15": 5.0, "y39": 80.5},
    )
    assert mean == pytest.approx((2.0 + 0.5 + 1.0 + 2.5) / 4) and worst == pytest.approx(2.5)

    class Report:
        bucket_totals = {"y5": D(20), "y15": D(10), "y27_5": D(30), "y39": D(40)}

    shares = tc.engineer_shares(Report())  # type: ignore[arg-type]
    assert shares == {"y5": 20.0, "y15": 10.0, "y39": 70.0}


def test_the_dump_is_swept_on_its_stored_unit_costs() -> None:
    document = _export()
    engineer = {"y5": 50.0, "y7": 0.0, "y15": 0.0, "y39": 50.0}
    by_name = {s.name: s for s in tc.setups()}
    today = tc.run_dump_setup(document, by_name["today"], engineer=engineer, today_total=None)
    assert today.cell is None and today.quantities_equal_stored
    assert today.priced_total_ratio == 1.0
    low = tc.run_dump_setup(
        document,
        by_name["carpet_waste x0.5"],
        engineer=engineer,
        today_total=D(today.priced_total),
    )
    # the carpet alone moves: 500 / 9 x 0.55 = 30.56 SY (from 61.11) at its stored 80.00
    assert low.changed_quantities == 1 and low.priced_total_ratio < 1.0
    assert D(low.priced_total) == D(today.priced_total) - (D("61.11") - D("30.56")) * 80
    assert low.itemized_shares["y5"] < today.itemized_shares["y5"]
    ranking = tc.sensitivity([], [today, low])
    carpet = next(s for s in ranking if s.constant == "carpet_waste")
    assert carpet.exercised is True and carpet.slope_points_per_100pct is None
    text = tc.render_markdown([], ranking, engineer, None, [today, low])
    assert "## Section 35.2's dump (itemized only" in text and "| carpet_waste x0.5 |" in text
