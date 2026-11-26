"""The exterior drafts from the lidar shell (EVAL_REPORT 110; basis.domain.measure_drafts).

The shell of a synthetic gable house whose walls, eave, pitch and gutters are known
(``test_shell.cloud``: walls 16 x 10 m, a 0.5 m overhang, the eave 3 m up, 30 degree planes,
gutters on the two 17 m eaves), stored as the gather stores it (``shell.as_record``), drafts
the siding, roof and gutter rows: each quantity is its measurement record's (the geometry the
measure tool draws) times the draft's factor, and equals the shell's own numbers; every part the
shell refused, and a shell stored before the roof outline and the edges' ends were kept, drafts
nothing for that row and says why."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import pytest

from basis.domain import measure_drafts as md
from basis.domain import traced
from basis.domain.measurements import Derived
from basis.errors.codes import E
from basis.geo import shell

from .test_shell import cloud, derive

FT = 1 / 0.3048


def record(**kw: Any) -> dict[str, Any]:
    return shell.as_record(derive(cloud(), overhang_m=0.5, **kw))


def drafts(rec: dict[str, Any], **kw: Any) -> tuple[dict[str, md.Draft], dict[str, str]]:
    return md.exterior_drafts(md.ShellSource("g-1", "IA_Test_2020", 2020, rec), **kw)


def test_the_record_keeps_the_roof_outline_and_each_edges_ends_in_lonlat() -> None:
    s = derive(cloud(), overhang_m=0.5)
    rec = shell.as_record(s)
    assert len(rec["roof_outline"]) == len(s.roof_outline_m) == 4
    for edge, kept in zip(s.edges, rec["edges"], strict=True):
        length = traced.lonlat_path_length_ft([tuple(kept["start"]), tuple(kept["end"])])
        # The shell's metres are Web Mercator's sphere scaled by cos(latitude); the lon/lat
        # record is measured on the WGS84 ellipsoid (EVAL_REPORT 97): at 41.6 N they differ by
        # the radii of curvature, 0.15 % east-west and 0.22 % north-south (EVAL_REPORT 110.2).
        assert length / FT == pytest.approx(edge.length_m, rel=0.003)
    # The fields stored before are unchanged.
    assert rec["wall_outline"] == [[lon, lat] for lon, lat in s.wall_outline_lonlat]
    assert rec["gutter_m"] == s.gutter_m


def test_siding_roof_and_gutter_are_drafted_with_their_records() -> None:
    s = derive(cloud(), overhang_m=0.5)
    got, skipped = drafts(shell.as_record(s), replaced={}, missing={})
    assert skipped == {} and set(got) == {"siding", "roof", "gutter"}
    siding, roof, gutter = got["siding"], got["roof"], got["gutter"]

    # Siding: the walls' closed line (52 m) x the main eave, less 15 % openings.
    assert siding.shape is not None and siding.shape.length_ft is not None
    assert float(siding.shape.length_ft) / FT == pytest.approx(52, rel=0.01)
    assert siding.factor == Decimal("0.85")
    assert siding.quantity == (siding.shape.quantity * Decimal("0.85")).quantize(Decimal("0.01"))
    assert float(siding.quantity) == pytest.approx(
        s.wall_face_m2 * FT * FT * 0.85, rel=0.005
    )  # the shell's own wall face, to the height's rounding to 0.01 ft
    assert siding.measurement is not None and siding.measurement["height_ft"] == pytest.approx(
        s.eave_main_m * FT, abs=0.005
    )

    # Roof: the outline as flown (17 x 11 m) times the planes' slope factor (1 / cos 30).
    assert roof.shape is not None and roof.shape.area_sf is not None
    assert float(roof.shape.area_sf) / (FT * FT) == pytest.approx(17 * 11, rel=0.01)
    assert float(roof.factor) == pytest.approx(1 / math.cos(math.radians(30)), rel=0.02)
    assert float(roof.quantity) == pytest.approx(s.roof_area_m2 * FT * FT, rel=0.01)

    # Gutter: the two 17 m eaves as two lines.
    assert gutter.shape is not None and gutter.shape.shape_count == 2
    assert float(gutter.quantity) / FT == pytest.approx(34, rel=0.01)
    assert gutter.factor == 1 and "factor" not in gutter.record

    for label, draft in got.items():
        assert draft.measurement is not None
        assert draft.measurement["drawn_by"] == md.SHELL_DRAWN_BY == "model:lidar-shell@v1"
        assert draft.measurement["verified"] is False
        assert draft.measurement["space"] == "lonlat"
        assert draft.measurement["image"]["pixel_size_m"] == md.LIDAR_CELL_M
        # The record the tool sends back computes the same shape quantity.
        body = md_body(draft.measurement)
        assert traced.measure(body).quantity == draft.shape.quantity  # type: ignore[union-attr]
        assert draft.record["drawn_by"] == md.SHELL_DRAWN_BY
        assert draft.record["verified"] is False
        assert draft.record["expected_error_pct"] == format(md.EXTERIOR_ERROR_PCT[label][0], "f")
        assert draft.inputs["quantity_source"] == "draft"
        assert "drafted by Basis" in draft.formula and "lidar" in draft.formula
        assert "not verified" in draft.formula


def md_body(rec: dict[str, Any]) -> dict[str, Any]:
    """A stored measurement record as a body (the computed fields dropped), as the measure tool
    sends an untouched draft back (``PM.bodyOfRecord``)."""
    keep = {"unit", "space", "shapes", "scale", "image", "height_ft", "drawn_by", "verified"}
    return {k: v for k, v in rec.items() if k in keep}


def test_what_the_draft_replaced_is_kept() -> None:
    old = Derived(Decimal("2018.36"), "SF", "today's roof formula", {})
    got, _ = drafts(record(), replaced={"roof": old}, missing={"siding": "stories"})
    assert got["roof"].record["replaces"] == {
        "formula": "today's roof formula",
        "quantity": "2018.36 SF",
    }
    assert got["roof"].inputs["replaced_quantity"] == "2018.36 SF"
    assert got["siding"].record["replaces"] == {"missing_input": "stories"}
    assert got["siding"].inputs["replaced_missing_input"] == "stories"
    assert got["gutter"].record["replaces"] == {}


def test_a_shell_without_a_building_drafts_nothing() -> None:
    rec = shell.as_record(derive(cloud(roof_class=lidar_ground()), overhang_m=0.5))
    got, skipped = drafts(rec, replaced={}, missing={})
    assert got == {}
    assert set(skipped) == {"siding", "roof", "gutter"}
    assert all(E.GEO_SHELL_NO_BUILDING in why for why in skipped.values())


def lidar_ground() -> int:
    from basis.geo import lidar

    return lidar.GROUND


def test_no_roof_plane_drafts_the_walls_only_when_there_is_a_main_eave() -> None:
    rec = record()
    rec["roof_area_m2"] = None
    rec["refusals"] = [{"part": "roof", "code": E.GEO_SHELL_NO_PLANES, "reason": "x"}]
    rec["edges"] = []
    got, skipped = drafts(rec, replaced={}, missing={})
    assert set(got) == {"siding"}
    assert E.GEO_SHELL_NO_PLANES in skipped["roof"] and E.GEO_SHELL_NO_PLANES in skipped["gutter"]
    rec["eave_main_m"] = None  # no planes, no eave edges: the 5th percentile is not a wall height
    got, skipped = drafts(rec, replaced={}, missing={})
    assert got == {} and "main eave" in skipped["siding"]


def test_walls_refused_at_the_edge_of_the_area_read_draft_no_siding() -> None:
    rec = record()
    rec["wall_outline"] = []
    rec["refusals"] = [{"part": "walls", "code": E.GEO_LIDAR_FOOTPRINT_UNCERTAIN, "reason": "x"}]
    got, skipped = drafts(rec, replaced={}, missing={})
    assert "siding" not in got and E.GEO_LIDAR_FOOTPRINT_UNCERTAIN in skipped["siding"]
    assert {"roof", "gutter"} <= set(got)


def test_a_shell_stored_before_the_roof_outline_and_edge_ends_drafts_the_siding_only() -> None:
    rec = record()
    del rec["roof_outline"]
    for edge in rec["edges"]:
        edge.pop("start")
        edge.pop("end")
    got, skipped = drafts(rec, replaced={}, missing={})
    assert set(got) == {"siding"}
    assert skipped["roof"] == "no roof outline stored with the shell"
    assert skipped["gutter"] == "the eaves' ends are not stored with the shell"


@pytest.mark.parametrize("bad", [None, "x", [[1.0, 2.0]], [[1.0, float("nan")], [0, 0], [1, 1]]])
def test_a_malformed_outline_is_not_drafted(bad: Any) -> None:
    rec = record()
    rec["wall_outline"] = bad
    rec["roof_outline"] = bad
    got, _ = drafts(rec, replaced={}, missing={})
    assert "siding" not in got and "roof" not in got
