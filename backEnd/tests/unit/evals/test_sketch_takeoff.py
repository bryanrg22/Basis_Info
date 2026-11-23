"""``evals.sketch_takeoff``: the harness that replays the takeoff with the appraisal's sketch
(EVAL_REPORT 96), on a synthetic export and the fabricated sketch pages. No client data.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fixtures import sketch_fixture

from basis.domain.measurements import measurements_from_fields
from basis.extraction.sketch import read_sketch
from evals import sketch_takeoff as st
from evals import takeoff_constants as tc
from evals.structure_replay import ReplayError, field_rows

from .test_takeoff_constants import _cell, _export, _quantities

D = Decimal
PAGES: dict[str, Any] = json.loads(sketch_fixture("pages.json").read_text("utf-8"))


def pages(name: str) -> list[tuple[int, list[str]]]:
    return [(number, lines) for number, lines in PAGES[name]["pages"]]


def sketch() -> list[dict[str, Any]]:
    reading = read_sketch(ONE_STORY, "azure")
    assert reading is not None
    return st.field_dicts(reading)


# A sketch for the synthetic export's house (1,000 SF, one story): 1,000 SF first floor.
ONE_STORY: list[tuple[int, list[str]]] = [
    (
        7,
        [
            "Area Calculations Summary",
            "Living Area",
            "First Floor",
            "1000 Sq ft",
            "40 x 25 = 1000",
            "Total Living Area (Rounded):",
            "1000 Sq ft",
            "Non-living Area",
            "Patio",
            "200 Sq ft",
            "20 x 10 = 200",
        ],
    )
]


def test_azure_pages_reads_the_sdk_dump_and_the_stored_result(tmp_path: Path) -> None:
    sdk = {
        "pages": [{"pageNumber": 2, "lines": [{"content": "b"}]}, {"pageNumber": 1, "lines": []}]
    }
    ours = {"pages": [{"page_number": 1, "lines": ["a", "b"]}]}
    (tmp_path / "sdk.json").write_text(json.dumps(sdk))
    (tmp_path / "ours.json").write_text(json.dumps(ours))
    assert st.azure_pages(tmp_path / "sdk.json") == [(1, []), (2, ["b"])]
    assert st.azure_pages(tmp_path / "ours.json") == [(1, ["a", "b"])]


def test_the_sketch_fields_are_written_as_the_export_stores_fields() -> None:
    fields = sketch()
    assert [(f["section"], f["field"], f["value_num"], f["page"], f["source"]) for f in fields] == [
        ("sketch", "living_area_sqft", "1000", 7, "azure"),
        ("sketch", "ground_floor_area_sqft", "1000", 7, "azure"),
        ("sketch", "patio_area_sqft", "200", 7, "azure"),
    ]
    document = _export()
    prepared = st.with_fields(document, fields)
    assert len(prepared["extraction"][0]["fields"]) == len(document["extraction"][0]["fields"]) + 3
    assert "sketch" not in {f["section"] for f in document["extraction"][0]["fields"]}  # a copy
    rows = field_rows(prepared)
    assert [r.page for r in rows if r.section == "sketch"] == [7, 7, 7]
    corrected = st.with_fields(document, [], gla=D(1200))
    assert measurements_from_fields(field_rows(corrected)).gla_sf.value == D(1200)  # type: ignore[union-attr]
    with pytest.raises(ReplayError):
        st.with_fields({"extraction": []}, fields)


def test_outline_fields_carry_the_two_perimeters_as_manual_readings() -> None:
    fields = st.outline_fields(
        {"ground_perimeter_ft": 140.0, "upper_perimeter_ft": None, "page": 7}
    )
    assert [(f["field"], f["value_num"], f["source"], f["page"]) for f in fields] == [
        ("ground_perimeter_ft", "140.0", "manual", 7)
    ]


def test_the_setups_name_what_each_one_changes() -> None:
    names = [s.name for s in st.setups(D("0.01"), outline=False)]
    assert names == [
        "today",
        "sketch on",
        "sketch on, check widened",
        "GLA corrected, sketch off",
        "GLA corrected, sketch on",
    ]
    full = st.setups(D("0.01"), outline=True)
    assert full[-1].outline and full[-1].gla_corrected and full[-1].tolerance == D("0.01")
    assert full[0].tolerance is None and full[3].tolerance is None
    assert "tolerance 1 %" in full[1].what
    assert st.DEFAULT_TOLERANCE == D("0.01")


def test_off_the_replay_ignores_the_sketch_fields_and_on_it_moves_the_property_rows() -> None:
    document = _export()
    prepared = st.with_fields(document, sketch())
    off, _ = tc.retakeoff(prepared)
    plain, _ = tc.retakeoff(document)
    assert off["assets"] == plain["assets"]

    on, changes = tc.retakeoff(prepared, sketch_tolerance=D("0.01"))
    # The sketch's ground floor equals GLA / stories here, so no quantity moves; the rows
    # say the sketch was used.
    assert _quantities(on) == _quantities(plain) and changes["changed"] == []
    roof = next(a for a in on["assets"] if a["id"] == "roof")
    assert roof["takeoff_inputs"]["footprint_source"] == "sketch"
    assert roof["formula"].startswith("(1000 SF ground floor on the appraisal sketch + ")


def test_a_refused_sketch_moves_nothing_and_a_corrected_gla_admits_it() -> None:
    document = _export()
    two_level = st.field_dicts(read_sketch(pages("two_level"), "azure"))  # type: ignore[arg-type]
    prepared = st.with_fields(document, two_level)  # living area 2,100 against a GLA of 1,000
    plain, _ = tc.retakeoff(document)
    refused, _ = tc.retakeoff(prepared, sketch_tolerance=D("0.01"))
    assert _quantities(refused) == _quantities(plain)
    roof = next(a for a in refused["assets"] if a["id"] == "roof")
    assert roof["takeoff_inputs"]["sketch_check"].startswith("appraisal sketch not used: ")

    read = measurements_from_fields(field_rows(prepared), sketch_tolerance=D("0.01"))
    assert st.geometry(read) == st.geometry(measurements_from_fields(field_rows(document)))
    corrected = st.with_fields(document, two_level, gla=D(2100))
    admitted = measurements_from_fields(field_rows(corrected), sketch_tolerance=D("0.01"))
    shape = st.geometry(admitted)
    assert shape["dwelling_footprint_sf"] == "1200.00"
    assert shape["ground_footprint_sf"] == "1684.00" and shape["patio_sf"] == "120.00"


def test_geometry_reports_the_perimeter_the_roof_used() -> None:
    document = st.with_fields(
        _export(), [*sketch(), *st.outline_fields({"ground_perimeter_ft": "150", "page": 7})]
    )
    read = measurements_from_fields(field_rows(document), sketch_tolerance=D("0.01"))
    assert st.geometry(read)["ground_perimeter_ft"] == "150.00"
    assert st.geometry(measurements_from_fields(field_rows(_export())))["patio_sf"] is None


def test_site_items_waiting_for_a_measurement_are_counted_and_the_answered_ones_named() -> None:
    assets = [
        {"label": "patio", "takeoff_inputs": {"component": "patio", "needs_measurement": "x"}},
        {"label": "fence", "takeoff_inputs": {"component": "fence", "needs_measurement": "x"}},
        {"label": "roof", "takeoff_inputs": {"component": "roof"}},
    ]
    assert st.needs_measurement(assets, []) == {"waiting": 2, "answered_by_sketch": 0}
    assert st.needs_measurement(assets, ["roof", "patio"]) == {
        "waiting": 1,
        "answered_by_sketch": 1,
    }


def test_the_cross_check_table_row() -> None:
    row = st.sample_row("fixture", pages("two_level"), D(2100), D("0.01"))
    assert row == {
        "name": "fixture",
        "pages": 2,
        "rows": 5,
        "verified": 5,
        "apart_pct": "+0.0 %",
        "accepted": "yes",
    }
    assert st.sample_row("f", pages("two_level"), D(2400), D("0.01"))["accepted"] == "no"
    assert st.sample_row("f", pages("two_level"), D(2400), D("0.01"))["apart_pct"] == "-12.5 %"
    assert st.sample_row("f", pages("no_table"), D(2400), D("0.01"))["apart_pct"] == "no table"
    assert st.sample_row("f", pages("damaged"), D(1200), D("0.01"))["accepted"] == "no"


def _scored(setup: str, accepted: bool | None, roof: str, moved: int) -> st.Scored:
    return st.Scored(
        export="A",
        setup=setup,
        what=setup,
        runtime_ms=1.0,
        sketch_check=None if accepted is None else f"appraisal sketch {setup}",
        sketch_accepted=accepted,
        geometry={
            "dwelling_footprint_sf": "1000.00",
            "ground_footprint_sf": "1000.00",
            "ground_perimeter_ft": "129.10",
            "roof_sf": roof,
            "siding_sf": None,
            "gutter_lf": "81.46",
            "landscaping_sf": "9000.00",
            "patio_sf": None,
        },
        stored_drift=[{"label": "wainscoting"}] if setup == "today" else [],
        moved=moved,
        quantities={},
        needs_measurement={"waiting": 2, "answered_by_sketch": 0},
        priced=5,
        priced_total="100.00",
        priced_total_ratio=1.0,
        itemized_shares={b: 25.0 for b in ("y5", "y7", "y15", "y39")},
        itemized_error=1.0,
        cell=_cell(2.0),
    )


def test_the_public_table_prints_changes_and_never_a_quantity() -> None:
    cells = [
        _scored("today", None, "1262.37", 0),
        _scored("sketch on", False, "1262.37", 0),
        _scored("GLA corrected, sketch on, outline perimeters", True, "1388.61", 3),
    ]
    reading = {"source": "azure", "pages": [7], "rows": 2, "verified": 2, "fields": ["a"]}
    engineer = {b: 25.0 for b in ("y5", "y7", "y15", "y39")}
    public = st.render_markdown(cells, reading, [], engineer, private=False)
    private = st.render_markdown(cells, reading, [], engineer, private=True)
    assert "| roof (SF) | baseline | +0.0 % | +10.0 % |" in public
    assert "1262.37" not in public and "1388.61" not in public and "129.10" not in public
    assert "| roof (SF) | 1262.37 | 1262.37 (+0.0 %) | 1388.61 (+10.0 %) |" in private
    # The last setup is the drawing: the others are shown against it.
    assert "| roof (SF) | -9.1 % | -9.1 % |" in public
    assert "A / sketch on: 0 rows moved." in public
    assert "Stored drift on A: 1 rows (wainscoting)" in public
    assert "- sketch on: appraisal sketch sketch on" in private
    assert "appraisal sketch sketch on" not in public


def test_pct_change() -> None:
    assert st.pct_change("110", "100") == "+10.0 %"
    assert st.pct_change(None, "100") == "-" and st.pct_change("1", "0") == "-"
