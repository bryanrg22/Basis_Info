"""The site-item evaluation (EVAL_REPORT 106): the county truth's selection and geometry, the
image registration that reads Google's pixel size, the scorer (prompted and finding methods,
matching, misses and false items), the fence probe's arithmetic, and the committed truth files.
No network, no paid call."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from basis.errors.codes import E
from basis.geo.geometry import lonlat_to_pixel, view_box
from basis.vision.localize import Box, Instance, Point, Rle
from basis.vision.schemas import ComponentLabel
from evals import site_fence as sf
from evals import site_register as sr
from evals import site_score as ss
from evals import site_truth as st

LAT, LON = 38.87, -77.43
M_PER_DEG_LAT = 111_320.0


def lonlat(east_m: float, north_m: float) -> tuple[float, float]:
    return (
        LON + east_m / (M_PER_DEG_LAT * math.cos(math.radians(LAT))),
        LAT + north_m / M_PER_DEG_LAT,
    )


def square(e: float, n: float, w: float, h: float) -> list[tuple[float, float]]:
    return [lonlat(e, n), lonlat(e + w, n), lonlat(e + w, n + h), lonlat(e, n + h)]


# ------------------------------------------------------------------ the truth


def test_area_and_perimeter_are_on_the_ellipsoid_not_mercator() -> None:
    ring = square(0, 0, 10, 20)
    assert st.ring_area_sf(ring) == pytest.approx(200 * 10.7639, rel=0.002)
    assert st.ring_perimeter_ft(ring) == pytest.approx(60 / 0.3048, rel=0.002)


def test_inside_centroid_and_share_inside() -> None:
    parcel = square(0, 0, 30, 30)
    assert st.inside(lonlat(15, 15), parcel) and not st.inside(lonlat(40, 15), parcel)
    c = st.centroid(square(10, 10, 4, 8))
    assert c == pytest.approx(lonlat(12, 14), abs=1e-9)
    half_out = square(20, 10, 20, 5)
    assert st.share_inside(half_out, parcel) == pytest.approx(0.5, abs=0.04)


def feature(geometry: list[tuple[float, float]], **props: Any) -> dict[str, Any]:
    ring = [list(p) for p in [*geometry, geometry[0]]]
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def test_selection_keeps_one_driveway_lots_and_their_items() -> None:
    lot_a = square(0, 0, 30, 40)
    lot_b = square(40, 0, 30, 40)
    condo = square(80, 0, 30, 40)
    imp = [
        feature(
            square(5, -5, 5, 20),
            IMPERVIOUS_TYPE="Driveway or Parking Lot",
            IMPERVIOUS_SUBTYPE="Paved Driveway",
        ),
        feature(
            square(15, 25, 6, 6), IMPERVIOUS_TYPE="Building Addition", IMPERVIOUS_SUBTYPE="Patio"
        ),
        feature(square(-50, 10, 200, 2), IMPERVIOUS_TYPE="Sidewalk", IMPERVIOUS_SUBTYPE=None),
        # lot b has two driveways: a shared lot, refused
        feature(
            square(45, 5, 5, 10),
            IMPERVIOUS_TYPE="Driveway or Parking Lot",
            IMPERVIOUS_SUBTYPE="Paved Driveway",
        ),
        feature(
            square(60, 5, 5, 10),
            IMPERVIOUS_TYPE="Driveway or Parking Lot",
            IMPERVIOUS_SUBTYPE="Paved Driveway",
        ),
        feature(
            square(85, 5, 5, 10),
            IMPERVIOUS_TYPE="Driveway or Parking Lot",
            IMPERVIOUS_SUBTYPE="Paved Driveway",
        ),
        feature(square(10, 10, 12, 10), IMPERVIOUS_TYPE="Building", IMPERVIOUS_SUBTYPE=None),
    ]
    parcels = [
        feature(lot_a, PIN="A", PARCEL_TYPE="ORDINARY"),
        feature(lot_b, PIN="B", PARCEL_TYPE="ORDINARY"),
        feature(condo, PIN="C", PARCEL_TYPE="CONDO"),
    ]
    picked = st.select("t", imp, parcels, [], 8, "2026-11-25")
    assert [p["pin"] for p in picked] == ["A"]
    families = [i["family"] for i in picked[0]["items"]]
    assert families == ["driveway", "patio"]  # the long public sidewalk is not the lot's
    assert len(picked[0]["buildings"]) == 1
    doc = {**picked[0], "id": "zz01"}
    st.Parcel.model_validate(doc)


def test_the_committed_truth_files_load_and_hold_no_owner() -> None:
    parcels = st.load_all()
    assert len(parcels) >= 30
    for p in parcels:
        assert p.items[0].family == "driveway"
        text = json.dumps(p.model_dump(by_alias=True)).lower()
        assert "owner" not in text and "name" not in text
    dropped = [p.id for p in parcels if any(c.status == "dropped" for c in p.checks.values())]
    assert dropped  # the visual check is recorded in the files


# ------------------------------------------------------------------ registration


def test_registration_recovers_a_known_scale_and_shift() -> None:
    rng = np.random.default_rng(3)
    from scipy import ndimage

    ground = ndimage.gaussian_filter(rng.random((600, 600)), 3) * 255
    county_px, google_px, k_true = 0.08, 0.0841, 1.03
    # Google's picture of the same ground: its real pixel is google_px * k_true.
    f = county_px / (google_px * k_true)
    google = ndimage.zoom(ground, f, order=1)
    template = ground[150:350, 180:380]
    reg = sr.register(template, google, county_px=county_px, google_px=google_px)
    assert reg.k == pytest.approx(k_true, abs=0.006)
    gx, gy = reg.county_to_google(100, 100)  # template pixel (100, 100) = ground (280, 250)
    assert gx == pytest.approx(280 * f, abs=1.5) and gy == pytest.approx(250 * f, abs=1.5)
    assert reg.score > 0.9


def test_a_template_larger_than_the_picture_is_an_error() -> None:
    with pytest.raises(ValueError, match="smaller"):
        sr.register(np.ones((50, 50)), np.ones((20, 20)), county_px=0.08, google_px=0.08)


# ------------------------------------------------------------------ scoring


def rle(mask: np.ndarray) -> dict[str, Any]:
    return Rle.encode(mask.astype(np.uint8)).model_dump(mode="json")


def inst(mask: np.ndarray, score: float = 0.9) -> dict[str, Any]:
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    return Instance(
        label=ComponentLabel.DRIVEWAY,
        phrase="driveway",
        score=score,
        box=Box.from_pixels(xs.min(), ys.min(), xs.max() + 1, ys.max() + 1, w, h),
        mask=Rle.encode(mask.astype(np.uint8)),
        area=float(mask.mean()),
    ).model_dump(mode="json")


def rect(w: int, h: int, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    m = np.zeros((h, w), dtype=bool)
    m[y1:y2, x1:x2] = True
    return m


@pytest.fixture
def run_dir(tmp_path: Path) -> tuple[Path, dict[str, st.Parcel]]:
    box = view_box(LAT, LON, extent_m=40, pixel_m=0.1, max_px=400)
    w = h = box.width
    to_px = lambda lo, la: lonlat_to_pixel(box.bbox, w, h, lo, la)  # noqa: E731
    drive = square(-10, -10, 10, 5)  # 100 x 50 px
    patio = square(5, 5, 6, 6)
    parcel = square(-18, -18, 36, 36)
    truth = st.Parcel.model_validate(
        {
            "schema": st.SCHEMA,
            "id": "zz01",
            "area": "t",
            "pin": "Z",
            "centre": (LON, LAT),
            "parcel": parcel,
            "parcel_area_m2": 1296.0,
            "items": [
                {
                    "id": "driveway-1",
                    "family": "driveway",
                    "subtype": None,
                    "ring": drive,
                    "area_sf": st.ring_area_sf(drive),
                    "perimeter_ft": st.ring_perimeter_ft(drive),
                },
                {
                    "id": "patio-2",
                    "family": "patio",
                    "subtype": None,
                    "ring": patio,
                    "area_sf": st.ring_area_sf(patio),
                    "perimeter_ft": st.ring_perimeter_ft(patio),
                },
            ],
            "layers": {},
            "revisions": {},
            "fetched_on": "2026-11-25",
        }
    )
    dpx = [to_px(*p) for p in drive]
    x1, y1 = round(min(x for x, _ in dpx)), round(min(y for _, y in dpx))
    good = rect(w, h, x1, y1, x1 + 100, y1 + 50)
    big = rect(w, h, x1, y1, x1 + 110, y1 + 50)  # 10 % over
    stray = rect(w, h, 2, 2, 14, 14)  # off the parcel: neither a match nor a false item
    meta = {
        "parcel": "zz01",
        "view": "county",
        "width": w,
        "height": h,
        "prepared": [w, h],
        "pixel_size_m": box.pixel_size_m,
        "source": "test",
        "georef": {"type": "mercator", "bbox": list(box.bbox), "width": w, "height": h},
        "truth_px": {i.id: [list(to_px(*p)) for p in i.ring] for i in truth.items},
        "parcel_px": [list(to_px(*p)) for p in parcel],
    }
    bx = Box.from_pixels(x1 - 5, y1 - 5, x1 + 105, y1 + 55, w, h).model_dump()
    m2 = {
        "prompts": [
            {
                "mode": "box",
                "item": "driveway-1",
                "family": "driveway",
                "prompt": {"boxes": [bx], "points": []},
            },
            {
                "mode": "point",
                "item": "driveway-1",
                "family": "driveway",
                "prompt": {"boxes": [], "points": [Point(x=0.99, y=0.99).model_dump()]},
            },
            {"mode": "text", "item": None, "family": "driveway", "prompt": {"text": "driveway"}},
        ],
        "instances": [
            [inst(big)],
            [inst(good)],
            [inst(good, 0.9), inst(big, 0.8), inst(stray, 0.7)],
        ],
        "call": {"prompts": [], "predict_seconds": 2.0, "cost_usd": 0.00195, "status": "succeeded"},
    }
    folder = tmp_path / "run" / "zz01" / "county"
    folder.mkdir(parents=True)
    (folder / "view.json").write_text(json.dumps(meta))
    (folder / "m2.json").write_text(json.dumps(m2))
    from PIL import Image

    Image.new("RGB", (w, h), "grey").save(folder / "prepared.jpg")
    return tmp_path / "run", {"zz01": truth}


def test_prompted_methods_score_area_iou_and_refusals(
    run_dir: tuple[Path, dict[str, st.Parcel]],
) -> None:
    root, truth = run_dir
    scored = ss.score_all(root, truth)
    box = next(s for s in scored if s.method == "box")
    assert box.signed_pct == pytest.approx(10.0, abs=0.5)
    # 100 / 110 less the rasterization's half-pixel edges
    assert box.iou == pytest.approx(100 / 110, abs=0.04)
    point = next(s for s in scored if s.method == "point")
    assert point.draft_sf is None and point.code == E.SITE_NOTHING_FOUND  # the click missed


def test_finding_methods_match_dedupe_and_count_misses_and_false_items(
    run_dir: tuple[Path, dict[str, st.Parcel]],
) -> None:
    root, truth = run_dir
    text = [s for s in ss.score_all(root, truth) if s.method == "text"]
    matched = [s for s in text if s.item == "driveway-1"]
    assert len(matched) == 1 and matched[0].signed_pct == pytest.approx(0, abs=0.5)
    # The 10 % larger copy overlaps the kept one (IoU 0.91): one draft, not a false item.
    assert [s.item for s in text if s.item is None] == []
    assert [s.item for s in text if s.draft_sf is None] == ["patio-2"]  # a miss
    rows = {(r["method"], r["family"]): r for r in ss.summarize(text)}
    assert rows[("text", "all")]["misses"] == 1 and rows[("text", "all")]["false_items"] == 0


def test_summary_rows_and_costs(run_dir: tuple[Path, dict[str, st.Parcel]]) -> None:
    root, truth = run_dir
    rows = ss.summarize(ss.score_all(root, truth), ["driveway", "patio"])
    box_all = next(r for r in rows if r["method"] == "box" and r["family"] == "all")
    assert box_all["items"] == 1 and box_all["drafted"] == 1
    assert box_all["hand_bound_pct"] == pytest.approx(
        ss.hand_bound(truth["zz01"].items[0], 0.1), rel=0.01
    )
    (cost,) = ss.costs(root)
    assert cost["usd"] == pytest.approx(0.00195) and cost["properties"] == 1


def test_scale_corrections_touch_google_only() -> None:
    a = ss.Scored("p", "google", "box", "driveway", "d", 100.0, 100.0, 0.9)
    b = ss.Scored("p", "county", "box", "driveway", "d", 100.0, 100.0, 0.9)
    out = ss.with_parcel_scale([a, b], {"p": 1.02})
    assert out[0].draft_sf == pytest.approx(104.04) and out[1].draft_sf == 100.0
    assert ss.leave_one_out_k(
        [{"parcel": "x", "k": 1.0}, {"parcel": "y", "k": 1.04}, {"parcel": "z", "k": 1.02}]
    ) == {"x": 1.03, "y": 1.01, "z": 1.02}


def test_hand_bound_is_one_pixel_on_every_edge() -> None:
    ring = square(0, 0, 10, 5)
    item = st.Item(
        id="d",
        family="driveway",
        subtype=None,
        ring=tuple(ring),
        area_sf=st.ring_area_sf(ring),
        perimeter_ft=st.ring_perimeter_ft(ring),
    )
    # 30 m of edge x 0.084 m over 50 m2: 5.04 %
    assert ss.hand_bound(item, 0.084) == pytest.approx(5.04, abs=0.05)


# ------------------------------------------------------------------ fence probe


def test_lot_line_points_and_confirmed_length() -> None:
    ring = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
    pts = sf.lot_line_points(ring, 10.0)
    assert len(pts) <= sf.MAX_POINTS and pts[0] == (0.0, 0.0)
    fence = np.zeros((60, 120), dtype=bool)
    fence[0:2, 0:100] = True  # along the south edge
    total, near = sf.confirmed_length_m(ring, fence, 1.0, 0.1)
    assert total == pytest.approx(30.0, abs=0.2)  # 300 px of lot line at 0.1 m
    # 10 m of the south edge, and the half metre of each side edge within 1 px of it
    assert near == pytest.approx(10.5, abs=0.3)
    _, none = sf.confirmed_length_m(ring, np.zeros((60, 120), dtype=bool), 1.0, 0.1)
    assert none == 0.0


def test_measure_eval_renders_the_site_tables(
    run_dir: tuple[Path, dict[str, st.Parcel]], tmp_path: Path
) -> None:
    from evals import measure_eval as me

    root, truth = run_dir
    truth_dir = tmp_path / "truth"
    truth_dir.mkdir()
    for p in truth.values():
        (truth_dir / f"{p.id}.json").write_text(p.model_dump_json(by_alias=True))
    text, result = me.site_results([root], truth_dir, None)
    assert "## Site items (EVAL_REPORT 106)" in text
    assert "Douglas-Peucker tolerance" in text and "Cost, tokens and time" in text
    assert result["parcels"] == ["zz01"] and result["dropped"] == {}
    assert {r["method"] for r in result["summary"]} == {"box", "point", "text"}
    assert [r["tolerance_m"] for r in result["tolerance_sweep"]] == list(me.SITE_TOLERANCES)
