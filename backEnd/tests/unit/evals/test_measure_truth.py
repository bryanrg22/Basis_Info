"""``evals.measure_truth`` (EVAL_REPORT 103): the dimension parser, the schema's own checks, the
chain arithmetic (closure, perimeter, area, extent), the derived truths, and every committed
truth file: it validates, its chain closes or says why, and it records two passes."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from evals import measure_eval as me
from evals import measure_truth as mt


@pytest.mark.parametrize(
    ("text", "feet"),
    [
        ("32'-4\"", 32 + 4 / 12),
        ("22'-5 3/8\"", 22 + 5.375 / 12),
        ("0'-0\"", 0.0),
        ('8"', 8 / 12),
        ("28'-0 5/8\"", 28 + 0.625 / 12),
        ("96'", 96.0),
        ("-6'-8\"", -(6 + 8 / 12)),
        ('1 1/2"', 1.5 / 12),
        ("10'-10 1/4\"", 10 + 10.25 / 12),
        ("27′-7″", 27 + 7 / 12),
    ],
)
def test_parse_ft_reads_architects_dimensions(text: str, feet: float) -> None:
    assert mt.parse_ft(text) == pytest.approx(feet, abs=1e-9)


@pytest.mark.parametrize("text", ["", "abc", "5", '5/0"', "12'-x\""])
def test_parse_ft_refuses_what_is_not_a_dimension(text: str) -> None:
    with pytest.raises(ValueError):
        mt.parse_ft(text)


def length(ft: float, **kw: Any) -> dict[str, Any]:
    whole = int(ft)
    inches = round((ft - whole) * 12, 4)
    return {"ft": ft, "text": f"{whole}'-{inches:g}\"", "sheet": "1", **kw}


def edge(d: str, ft: float) -> dict[str, Any]:
    return {"dir": d, "length": length(ft)}


def house_doc(chain: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    doc = me.synthetic_house().model_dump(by_alias=True, exclude_none=True)
    doc["plan"]["chain"] = chain
    doc["plan"].pop("overall", None)
    doc.update(over)
    return doc


L_SHAPE = [edge("E", 40), edge("S", 20), edge("W", 15), edge("S", 10), edge("W", 25), edge("N", 30)]


def test_a_printed_length_must_match_its_text() -> None:
    mt.Length.model_validate({"ft": 32 + 4 / 12, "text": "32'-4\""})
    with pytest.raises(ValidationError, match="not 32.5"):
        mt.Length.model_validate({"ft": 32.5, "text": "32'-4\""})
    with pytest.raises(ValidationError, match="printed text"):
        mt.Length.model_validate({"ft": 3.0})


def test_scaled_and_derived_lengths_say_how() -> None:
    with pytest.raises(ValidationError, match="scale"):
        mt.Length.model_validate({"ft": 3.0, "how": "scaled"})
    mt.Length.model_validate({"ft": 3.0, "how": "scaled", "scale": '1/8" = 1\'-0"'})
    with pytest.raises(ValidationError, match="note"):
        mt.Length.model_validate({"ft": 3.0, "how": "derived"})


def test_an_edge_has_exactly_one_direction() -> None:
    with pytest.raises(ValidationError):
        mt.Edge.model_validate({"length": length(4)})
    with pytest.raises(ValidationError):
        mt.Edge.model_validate({"dir": "N", "bearing_deg": 0, "length": length(4)})
    assert mt.Edge.model_validate({"bearing_deg": 45, "length": length(4)}).bearing == 45


def test_a_printed_height_is_its_level_less_the_grade() -> None:
    mt.Height.model_validate(
        {"above_grade_ft": 20 + 2 / 12, "text": "22'-9\"", "datum_text": "2'-7\""}
    )
    with pytest.raises(ValidationError, match="not 22.75"):
        mt.Height.model_validate(
            {"above_grade_ft": 22.75, "text": "22'-9\"", "datum_text": "2'-7\""}
        )


def test_unknown_members_are_refused() -> None:
    doc = house_doc(L_SHAPE)
    doc["colour"] = "red"
    with pytest.raises(ValidationError):
        mt.House.model_validate(doc)


def test_the_l_shape_closes_and_measures() -> None:
    house = mt.House.model_validate(house_doc(L_SHAPE))
    chain = house.plan.chain
    assert mt.closure_ft(chain) == pytest.approx(0, abs=1e-9)
    assert mt.perimeter_ft(chain) == pytest.approx(140)
    assert mt.area_sf(chain) == pytest.approx(40 * 30 - 15 * 10)
    assert mt.extent_ft(chain) == pytest.approx((40, 30))
    assert mt.convex_minus_concave(chain) == 4


def test_the_area_does_not_depend_on_the_walking_direction() -> None:
    reverse = {"N": "S", "S": "N", "E": "W", "W": "E"}
    back = [edge(reverse[e["dir"]], e["length"]["ft"]) for e in reversed(L_SHAPE)]
    a = mt.House.model_validate(house_doc(L_SHAPE)).plan.chain
    b = mt.House.model_validate(house_doc(back)).plan.chain
    assert mt.area_sf(a) == pytest.approx(mt.area_sf(b))


def test_a_bay_at_45_degrees_closes() -> None:
    d = 4 / math.sqrt(2)
    chain = [
        edge("E", 10),
        {"bearing_deg": 135, "length": {"ft": 4.0, "how": "scaled", "scale": '1/4" = 1\'-0"'}},
        edge("S", 6),
        {"bearing_deg": 225, "length": {"ft": 4.0, "how": "scaled", "scale": '1/4" = 1\'-0"'}},
        {"dir": "W", "length": {"ft": 10.0, "text": "10'-0\""}},
        {"dir": "N", "length": {"ft": 6 + 2 * d, "how": "derived", "note": "bay closes the wall"}},
    ]
    house = mt.House.model_validate(house_doc(chain))
    assert mt.closure_ft(house.plan.chain) == pytest.approx(0, abs=1e-9)
    assert mt.area_sf(house.plan.chain) == pytest.approx(10 * (6 + 2 * d) + d * (6 + 2 * d) - d * d)
    assert not mt.is_rectilinear(house.plan.chain)


def test_an_open_chain_and_a_wrong_overall_are_findings_until_declared() -> None:
    chain = [*L_SHAPE[:-1], edge("N", 29)]
    doc = house_doc(chain)
    doc["plan"]["overall"] = {"ew": length(41), "ns": length(30)}
    house = mt.House.model_validate(doc)
    codes = {f.code for f in mt.check(house)}
    assert codes == {"chain_open", "overall_mismatch"}
    assert {f.code for f in mt.undeclared(house)} == codes
    doc["flags"] = [
        {"code": "chain_open", "note": "the drawing's chain is 1 ft short"},
        {"code": "overall_mismatch", "note": "the overall string reads 41'"},
    ]
    assert mt.undeclared(mt.House.model_validate(doc)) == []


def test_a_clean_house_has_no_findings() -> None:
    assert mt.check(me.synthetic_house()) == []


def test_the_derived_truths_have_their_closed_forms() -> None:
    t = mt.truths(me.synthetic_house())  # 40 x 30, two floors, eave 20, ridge 28, overhang 1.5
    assert t["footprint_sf"].value == pytest.approx(1200)
    assert t["perimeter_ft"].value == pytest.approx(140)
    assert t["gla_sf"].value == pytest.approx(2400)
    assert t["wall_area_gross_sf"].value == pytest.approx(140 * 20)
    assert t["roof_plan_sf"].value == pytest.approx(43 * 33)
    run, rise = 15 + 1.5, 8
    assert t["roof_area_sf"].value == pytest.approx(43 * 33 * math.hypot(run, rise) / run)
    assert t["gutter_ft"].value == pytest.approx(2 * (43 + 33))
    assert {k: v.tier for k, v in t.items()}["roof_area_sf"] == "derived"


def test_gable_eaves_take_the_named_sides() -> None:
    doc = me.synthetic_house().model_dump(by_alias=True, exclude_none=True)
    doc["roof"]["eaves"] = ["N", "S"]
    t = mt.truths(mt.House.model_validate(doc))
    assert t["gutter_ft"].value == pytest.approx(2 * (40 + 3))


def test_no_overhang_no_roof_truth() -> None:
    doc = me.synthetic_house().model_dump(by_alias=True, exclude_none=True)
    doc["roof"].pop("overhang")
    t = mt.truths(mt.House.model_validate(doc))
    assert "roof_area_sf" not in t and "gutter_ft" not in t and "roof_plan_sf" not in t


def test_roof_truths_need_the_explicit_opt_in() -> None:
    doc = me.synthetic_house().model_dump(by_alias=True, exclude_none=True)
    doc["roof"]["derive"] = False
    t = mt.truths(mt.House.model_validate(doc))
    assert not {"roof_plan_sf", "roof_area_sf", "gutter_ft"} & set(t)


def test_a_printed_roof_outline_is_the_roof_plan() -> None:
    doc = me.synthetic_house().model_dump(by_alias=True, exclude_none=True)
    doc["roof"]["outline"] = [edge("E", 50), edge("S", 34), edge("W", 50), edge("N", 34)]
    doc["roof"]["derive"] = False
    t = mt.truths(mt.House.model_validate(doc))
    assert t["roof_plan_sf"].value == pytest.approx(50 * 34)
    assert t["roof_plan_sf"].tier == "printed"
    assert "roof_area_sf" not in t  # the pitch assumption still needs the opt-in
    doc["roof"]["derive"] = True
    t = mt.truths(mt.House.model_validate(doc))
    assert t["gutter_ft"].value == pytest.approx(2 * (50 + 34))
    run, rise = 34 / 2, 8  # the outline's own half-width; no overhang added to a roof edge
    assert t["roof_area_sf"].value == pytest.approx(50 * 34 * math.hypot(run, rise) / run)


def test_a_sum_of_printed_strings_is_printed_tier_and_a_scaled_edge_is_not() -> None:
    chain = [
        edge("E", 40),
        {"dir": "S", "length": {"ft": 30.0, "how": "derived", "note": "10'-0\" + 20'-0\""}},
        edge("W", 40),
        edge("N", 30),
    ]
    assert mt.truths(mt.House.model_validate(house_doc(chain)))["footprint_sf"].tier == "printed"
    chain[3] = {"dir": "N", "length": {"ft": 30.0, "how": "scaled", "scale": '1/4" = 1\'-0"'}}
    assert mt.truths(mt.House.model_validate(house_doc(chain)))["footprint_sf"].tier == "scaled"


# ------------------------------------------------------------------ the committed truth files

FILES = sorted(mt.FIXTURES.glob("*.json"))


def test_there_are_ten_houses() -> None:
    assert len(FILES) == 10


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_each_truth_file_validates_and_declares_every_finding(path: Path) -> None:
    house = mt.load(path)
    assert house.id == path.stem
    assert house.loc_url.startswith("https://www.loc.gov/")
    assert mt.undeclared(house) == [], [f.detail for f in mt.check(house)]
    assert house.passes.a and house.passes.b and house.passes.a != house.passes.b
    flagged = {f.code for f in house.flags}
    unknown = flagged - set(mt.CHECK_CODES) - {"note"}
    # A flag names a check that failed, or is a free note ("note"); never a typo.
    assert not unknown, unknown
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema"] == mt.SCHEMA
