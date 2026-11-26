"""AI-drafted site-item outlines (EVAL_REPORT 106): a mask to a simplified ring, the two
georeferences, the measurement body ``traced`` accepts, every refusal, a scripted SAM 3 and a
scripted vision model's proposal. No network, no paid call."""

from __future__ import annotations

import asyncio
import io
import json
import math
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from basis.config import Settings
from basis.domain import traced
from basis.errors.codes import E
from basis.geo.geometry import lonlat_to_pixel, view_box
from basis.vision import localize as loc
from basis.vision import site_items as si
from basis.vision.localize import Box, Instance, LocalizeCall, LocalizerUnavailable, Point, Rle
from basis.vision.prep import PreparedImage
from basis.vision.schemas import ComponentLabel

SQFT_PER_M2 = 10.763910416709722


def rect_mask(w: int, h: int, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    m = np.zeros((h, w), dtype=bool)
    m[y1:y2, x1:x2] = True
    return m


def png(w: int, h: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), "grey").save(out, format="PNG")
    return out.getvalue()


def view(
    w: int = 200,
    h: int = 160,
    pixel: float | None = 0.1,
    lat: float = 38.87,
    lon: float = -77.43,
    captured_on: date | None = date(2023, 4, 1),
) -> si.TopDownView:
    return si.TopDownView(
        data=png(w, h),
        width=w,
        height=h,
        pixel_size_m=pixel,
        georef=si.CentredGeoref(lat, lon, w, h, pixel or 0.1),
        kind="capture",
        source="Google Earth straight-down view",
        label="aerial-close",
        captured_on=captured_on,
    )


def prepared(w: int, h: int) -> PreparedImage:
    return PreparedImage(b"", w, h, w, h, "PNG")


def instance(mask: np.ndarray, score: float = 0.9, label: str = "driveway") -> Instance:
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    box = Box.from_pixels(xs.min(), ys.min(), xs.max() + 1, ys.max() + 1, w, h)
    return Instance(
        label=ComponentLabel(label),
        phrase=label,
        score=score,
        box=box,
        mask=Rle.encode(mask.astype(np.uint8)),
        area=float(mask.mean()),
    )


class ScriptedSam:
    """A segmenter that answers every prompt with the instances scripted for it, in order."""

    def __init__(self, answers: Sequence[Sequence[Instance]], fail: Exception | None = None):
        self.answers = [list(a) for a in answers]
        self.fail = fail
        self.calls: list[list[loc.VisualPrompt]] = []

    async def run_prompts(
        self, image: PreparedImage, prompts: Sequence[loc.VisualPrompt], *, threshold: float = 0.5
    ) -> tuple[list[list[Instance]], LocalizeCall]:
        self.calls.append(list(prompts))
        if self.fail is not None:
            raise self.fail
        call = LocalizeCall(
            prompts=tuple(p.name for p in prompts),
            predict_seconds=2.0,
            cost_usd=0.00195,
            status="succeeded",
        )
        return [self.answers[k] for k in range(len(prompts))], call


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def same_size(data: bytes) -> PreparedImage:
    with Image.open(io.BytesIO(data)) as im:
        return PreparedImage(data, im.width, im.height, im.width, im.height, "PNG")


# ------------------------------------------------------------------ mask to ring


def shoelace(ring: Sequence[tuple[float, float]]) -> float:
    return abs(traced.shoelace(list(ring)))


def test_a_rectangle_is_four_corners_with_its_pixel_area() -> None:
    m = rect_mask(50, 40, 5, 7, 35, 27)
    ring = si.mask_to_ring(m)
    assert len(ring) == 4
    assert shoelace(ring) == m.sum() == 600
    assert sorted(ring) == [(5.0, 7.0), (5.0, 27.0), (35.0, 7.0), (35.0, 27.0)]


def test_an_l_keeps_its_six_corners() -> None:
    m = rect_mask(60, 60, 10, 10, 50, 50)
    m[10:30, 30:50] = False
    ring = si.mask_to_ring(m)
    assert len(ring) == 6
    assert shoelace(ring) == m.sum()


def test_a_turned_rectangle_simplifies_to_four_corners_close_to_its_area() -> None:
    from PIL import ImageDraw

    img = Image.new("L", (200, 200), 0)
    c, a = (100.0, 100.0), math.radians(25)
    pts = []
    for dx, dy in ((-60, -25), (60, -25), (60, 25), (-60, 25)):
        pts.append(
            (c[0] + dx * math.cos(a) - dy * math.sin(a), c[1] + dx * math.sin(a) + dy * math.cos(a))
        )
    ImageDraw.Draw(img).polygon(pts, fill=1)
    m = np.asarray(img, dtype=bool)
    traced_ring = si.mask_to_ring(m, 0)
    simple = si.mask_to_ring(m, 1.5)
    assert len(traced_ring) > 50  # the stair steps
    assert 4 <= len(simple) <= 8
    # Against the mask's own pixels (PIL fills the boundary pixels too, so the mask is a little
    # larger than the drawn 120 x 50 rectangle).
    assert shoelace(traced_ring) == m.sum()
    assert shoelace(simple) == pytest.approx(m.sum(), rel=0.02)
    assert m.sum() == pytest.approx(120 * 50, rel=0.04)
    assert traced.ring_problem(simple) is None


def test_the_largest_part_is_kept_and_its_holes_filled() -> None:
    m = rect_mask(80, 80, 5, 5, 45, 45)
    m[20:30, 20:30] = False  # a hole (a tree over the driveway)
    m[60:70, 60:70] = True  # a separate smaller part
    part = si.largest_part(m)
    assert part[25, 25] and not part[65, 65]
    ring = si.mask_to_ring(m)
    assert shoelace(ring) == 40 * 40


def test_a_tie_between_parts_keeps_the_first_labelled() -> None:
    m = rect_mask(40, 40, 0, 0, 10, 10) | rect_mask(40, 40, 20, 20, 30, 30)
    part = si.largest_part(m)
    assert part[5, 5] and not part[25, 25]


def test_an_empty_mask_has_no_ring() -> None:
    assert si.mask_to_ring(np.zeros((10, 10), dtype=bool)) == []
    assert not si.largest_part(np.zeros((10, 10), dtype=bool)).any()


def test_a_ragged_blob_stays_simple_and_under_the_corner_cap() -> None:
    rng = np.random.default_rng(7)
    for _ in range(5):
        yy, xx = np.mgrid[0:400, 0:400]
        r = 150 + rng.normal(0, 6, size=(400, 400))
        m = (yy - 200) ** 2 + (xx - 200) ** 2 < r**2
        for tolerance in (0, 0.5, 1.0, 2.0):
            ring = si.mask_to_ring(m, tolerance)
            assert 3 <= len(ring) <= traced.MAX_RING
            assert traced.ring_problem(ring) is None


def test_touching_the_edge() -> None:
    assert si.touches_edge(rect_mask(10, 10, 0, 3, 4, 6))
    assert si.touches_edge(rect_mask(10, 10, 3, 3, 10, 6))
    assert not si.touches_edge(rect_mask(10, 10, 1, 1, 9, 9))


# ------------------------------------------------------------------ georeference


def test_the_mercator_georef_is_lonlat_to_pixel_inverted() -> None:
    box = view_box(38.87, -77.43, extent_m=60, pixel_m=0.0762, max_px=1008)
    g = si.MercatorGeoref(box.bbox, box.width, box.height)
    for x, y in ((0, 0), (100.5, 400.25), (box.width, box.height)):
        lon, lat = g.to_lonlat(x, y)
        px, py = lonlat_to_pixel(box.bbox, box.width, box.height, lon, lat)
        assert (px, py) == pytest.approx((x, y), abs=1e-6)


def test_the_centred_georef_puts_the_centre_on_the_pin_and_metres_on_pixels() -> None:
    g = si.CentredGeoref(38.87, -77.43, 1440, 900, 0.0841)
    assert g.to_lonlat(720, 450) == pytest.approx((-77.43, 38.87), abs=1e-12)
    east = traced.lonlat_path_length_ft([g.to_lonlat(720, 450), g.to_lonlat(820, 450)])
    north = traced.lonlat_path_length_ft([g.to_lonlat(720, 450), g.to_lonlat(720, 350)])
    assert east * 0.3048 == pytest.approx(8.41, rel=1e-3)
    assert north * 0.3048 == pytest.approx(8.41, rel=1e-3)
    lon, lat = g.to_lonlat(720, 350)
    assert lat > 38.87  # y down is south


def test_the_capture_scale_stretches_every_length() -> None:
    plain = si.CentredGeoref(38.87, -77.43, 1440, 900, 0.0841)
    scaled = si.CentredGeoref(38.87, -77.43, 1440, 900, 0.0841, scale=1.025)
    a = traced.lonlat_path_length_ft([plain.to_lonlat(700, 450), plain.to_lonlat(900, 450)])
    b = traced.lonlat_path_length_ft([scaled.to_lonlat(700, 450), scaled.to_lonlat(900, 450)])
    assert b / a == pytest.approx(1.025, rel=1e-6)


def test_a_crop_offsets_its_pixels_and_refuses_a_sliver() -> None:
    v = view(200, 160)
    part = si.crop(v, (50, 40, 150, 120))
    assert (part.width, part.height) == (100, 80)
    assert part.georef.to_lonlat(0, 0) == v.georef.to_lonlat(50, 40)
    clamped = si.crop(v, (-20, -20, 60, 60))
    assert (clamped.width, clamped.height) == (60, 60)
    with pytest.raises(ValueError, match="too little"):
        si.crop(v, (0, 0, 10, 10))


# ------------------------------------------------------------------ the body


def test_the_body_is_a_model_draft_traced_accepts() -> None:
    v = view()
    ring = [v.georef.to_lonlat(x, y) for x, y in ((10, 10), (110, 10), (110, 60), (10, 60))]
    body = si.measurement_body(v, ring, "SF", si.DraftSettings().drawn_by)
    result = traced.measure(body)
    assert body["drawn_by"] == f"model:sam3@{si.PIPELINE_VERSION}"
    assert body["verified"] is False
    assert result.by_model and result.verified is False
    assert body["image"]["captured_on"] == "2023-04-01"
    assert result.view == {"kind": "capture", "width": 200, "height": 160, "label": "aerial-close"}
    assert float(result.quantity) == pytest.approx(10 * 5 * SQFT_PER_M2, rel=1e-4)


def test_an_undated_view_without_a_name_still_has_a_label() -> None:
    v = si.TopDownView(
        data=b"",
        width=100,
        height=100,
        pixel_size_m=0.15,
        georef=si.CentredGeoref(40.0, -90.0, 100, 100, 0.15),
        kind="public",
        source="Fairfax County 2023",
    )
    ring = [v.georef.to_lonlat(x, y) for x, y in ((10, 10), (50, 10), (50, 50))]
    body = si.measurement_body(v, ring, "SF", "model:sam3@site-v1")
    assert "captured_on" not in body["image"]
    assert body["view"]["label"] == "Fairfax County 2023"
    traced.measure(body)


# ------------------------------------------------------------------ drafting


def test_a_box_drafts_the_instance_it_overlaps_with_its_area() -> None:
    v = view(200, 160, pixel=0.1)
    m = rect_mask(200, 160, 40, 30, 140, 80)  # 100 x 50 px = 10 x 5 m
    neighbour = rect_mask(200, 160, 150, 100, 190, 150)
    sam = ScriptedSam([[instance(neighbour, 0.95), instance(m, 0.8)]])
    prompt = si.SitePrompt("driveway", box=Box(x1=0.19, y1=0.17, x2=0.71, y2=0.52))
    drafted = run(si.draft_site_items(v, [prompt], sam, prepare=same_size))
    (d,) = drafted.results
    assert isinstance(d, si.Draft)
    assert float(d.quantity) == pytest.approx(50 * SQFT_PER_M2, rel=1e-3)
    assert d.unit == "SF" and d.mask_pixels == 5000
    assert d.measurement["drawn_by"].startswith("model:sam3@")
    assert d.measurement["verified"] is False
    assert drafted.call is not None and drafted.call.cost_usd == 0.00195
    # The box went alone: no phrase.
    (sent,) = sam.calls[0]
    assert sent.text is None and sent.boxes == (prompt.box,) and not sent.points
    assert sent.body() == {"positive_boxes": [pytest.approx([0.45, 0.345, 0.52, 0.35])]}


def test_a_point_drafts_the_instance_under_it() -> None:
    v = view(200, 160)
    left = rect_mask(200, 160, 10, 10, 60, 60)
    right = rect_mask(200, 160, 100, 10, 150, 60)
    sam = ScriptedSam([[instance(left, 0.99), instance(right, 0.6)]])
    prompt = si.SitePrompt("patio", point=Point(x=0.6, y=0.2))
    (d,) = run(si.draft_site_items(v, [prompt], sam, prepare=same_size)).results
    assert isinstance(d, si.Draft) and d.mask_pixels == 2500
    assert min(x for x, _ in d.ring_px) == 100
    assert sam.calls[0][0].points == (prompt.point,)


def test_the_expected_error_is_never_below_the_methods_measured_error() -> None:
    v = view(400, 400, pixel=0.05)
    m = rect_mask(400, 400, 50, 50, 350, 350)  # big: the one-pixel bound is small
    sam = ScriptedSam([[instance(m)]])
    prompt = si.SitePrompt("driveway", box=Box(x1=0.1, y1=0.1, x2=0.9, y2=0.9))
    (d,) = run(si.draft_site_items(v, [prompt], sam, prepare=same_size)).results
    assert isinstance(d, si.Draft)
    assert (
        d.one_pixel_error_pct is not None
        and d.one_pixel_error_pct < si.METHOD_ERROR_PCT["driveway"]
    )
    assert d.expected_error_pct == si.METHOD_ERROR_PCT["driveway"]
    custom = si.DraftSettings(method_error_pct={"driveway": Decimal("0")})
    (e,) = run(
        si.draft_site_items(v, [prompt], ScriptedSam([[instance(m)]]), custom, prepare=same_size)
    ).results
    assert isinstance(e, si.Draft) and e.expected_error_pct == e.one_pixel_error_pct


def test_a_family_not_measured_carries_the_largest_measured_error() -> None:
    v = view(400, 400, pixel=0.05)
    m = rect_mask(400, 400, 50, 50, 350, 350)
    prompt = si.SitePrompt("walk", box=Box(x1=0.1, y1=0.1, x2=0.9, y2=0.9))
    (d,) = run(
        si.draft_site_items(v, [prompt], ScriptedSam([[instance(m)]]), prepare=same_size)
    ).results
    assert isinstance(d, si.Draft)
    assert "walk" not in si.METHOD_ERROR_PCT
    assert d.expected_error_pct == si.UNMEASURED_ERROR_PCT == max(si.METHOD_ERROR_PCT.values())


def test_masks_are_mapped_from_the_prepared_size_to_the_view() -> None:
    v = view(400, 320, pixel=0.05)
    m = rect_mask(200, 160, 40, 30, 140, 80)  # at half size
    sam = ScriptedSam([[instance(m)]])

    def half(data: bytes) -> PreparedImage:
        return PreparedImage(b"", 200, 160, 400, 320, "PNG")

    prompt = si.SitePrompt("driveway", box=Box(x1=0.19, y1=0.17, x2=0.71, y2=0.52))
    (d,) = run(si.draft_site_items(v, [prompt], sam, prepare=half)).results
    assert isinstance(d, si.Draft)
    assert max(x for x, _ in d.ring_px) == 280
    assert float(d.quantity) == pytest.approx(200 * 100 * 0.05**2 * SQFT_PER_M2, rel=1e-3)


@pytest.mark.parametrize(
    ("pixel", "code"),
    [(None, E.SITE_NOT_TOP_DOWN), (0.6, E.SITE_VIEW_TOO_COARSE)],
)
def test_a_view_that_cannot_be_measured_is_refused_without_a_call(pixel: Any, code: str) -> None:
    v = view(pixel=pixel)
    sam = ScriptedSam([[]])
    prompts = [si.SitePrompt("driveway", point=Point(x=0.5, y=0.5))]
    (r,) = run(si.draft_site_items(v, prompts, sam, prepare=same_size)).results
    assert isinstance(r, si.Refused) and r.code == code
    assert sam.calls == []


def test_nothing_at_the_prompt_is_refused() -> None:
    v = view()
    far = rect_mask(200, 160, 150, 120, 190, 150)
    sam = ScriptedSam([[instance(far)], []])
    prompts = [
        si.SitePrompt("driveway", box=Box(x1=0.0, y1=0.0, x2=0.3, y2=0.3)),
        si.SitePrompt("patio", point=Point(x=0.1, y=0.1)),
    ]
    results = run(si.draft_site_items(v, prompts, sam, prepare=same_size)).results
    assert [r.code for r in results if isinstance(r, si.Refused)] == [
        E.SITE_NOTHING_FOUND,
        E.SITE_NOTHING_FOUND,
    ]


def test_an_outline_at_the_pictures_edge_is_refused() -> None:
    v = view()
    m = rect_mask(200, 160, 0, 40, 80, 100)
    sam = ScriptedSam([[instance(m)]])
    prompt = si.SitePrompt("driveway", box=Box(x1=0.0, y1=0.25, x2=0.4, y2=0.625))
    (r,) = run(si.draft_site_items(v, [prompt], sam, prepare=same_size)).results
    assert isinstance(r, si.Refused) and r.code == E.SITE_AT_EDGE


def test_a_tiny_outline_is_refused() -> None:
    v = view()
    m = rect_mask(200, 160, 50, 50, 60, 60)  # 100 px at 0.1 m: 1 m2, under the 3 m2 floor
    sam = ScriptedSam([[instance(m)]])
    prompt = si.SitePrompt("walk", point=Point(x=0.275, y=0.344))
    (r,) = run(si.draft_site_items(v, [prompt], sam, prepare=same_size)).results
    assert isinstance(r, si.Refused) and r.code == E.SITE_TOO_SMALL


def test_a_shape_traced_refuses_is_refused() -> None:
    # A view at the edge of what traced measures: the outline's corners fall north of 85
    # degrees, which traced.measure refuses.
    v = view(pixel=0.1, lat=84.99995)
    m = rect_mask(200, 160, 40, 10, 140, 60)
    sam = ScriptedSam([[instance(m)]])
    prompt = si.SitePrompt("driveway", box=Box(x1=0.19, y1=0.05, x2=0.71, y2=0.4))
    (r,) = run(si.draft_site_items(v, [prompt], sam, prepare=same_size)).results
    assert isinstance(r, si.Refused) and r.code == E.SITE_SHAPE_INVALID
    assert "latitude" in r.reason


def test_a_failed_call_refuses_every_prompt() -> None:
    v = view()
    sam = ScriptedSam([], fail=LocalizerUnavailable("replicate 503 after retries"))
    prompts = [
        si.SitePrompt("driveway", point=Point(x=0.5, y=0.5)),
        si.SitePrompt("patio", point=Point(x=0.2, y=0.2)),
    ]
    drafted = run(si.draft_site_items(v, prompts, sam, prepare=same_size))
    assert [r.code for r in drafted.results if isinstance(r, si.Refused)] == [
        E.SITE_SEGMENTER_FAILED
    ] * 2
    assert drafted.call is None


def test_a_fence_is_not_drafted_and_the_rest_are() -> None:
    v = view()
    m = rect_mask(200, 160, 40, 30, 140, 80)
    sam = ScriptedSam([[instance(m)]])
    prompts = [
        si.SitePrompt("fence", box=Box(x1=0.0, y1=0.9, x2=1.0, y2=0.95)),
        si.SitePrompt("driveway", box=Box(x1=0.19, y1=0.17, x2=0.71, y2=0.52)),
    ]
    fence, drive = run(si.draft_site_items(v, prompts, sam, prepare=same_size)).results
    assert isinstance(fence, si.Refused) and fence.code == E.SITE_NOT_DRAFTED
    assert isinstance(drive, si.Draft)
    assert len(sam.calls[0]) == 1  # the fence was never sent


def test_only_fences_make_no_call() -> None:
    sam = ScriptedSam([])
    prompts = [si.SitePrompt("fence", box=Box(x1=0.0, y1=0.9, x2=1.0, y2=0.95))]
    drafted = run(si.draft_site_items(view(), prompts, sam, prepare=same_size))
    assert drafted.call is None and sam.calls == []


def test_an_unknown_family_and_an_empty_prompt_are_errors() -> None:
    with pytest.raises(ValueError, match="unknown site family"):
        run(
            si.draft_site_items(
                view(), [si.SitePrompt("gazebo", point=Point(x=0.5, y=0.5))], ScriptedSam([])
            )
        )
    with pytest.raises(ValueError, match="box or a point"):
        si.sam_prompt(si.SitePrompt("driveway"))


def test_a_view_prepared_from_real_bytes_drafts_end_to_end() -> None:
    v = view(240, 180)
    m = rect_mask(240, 180, 60, 40, 180, 120)
    sam = ScriptedSam([[instance(m)]])
    prompt = si.SitePrompt("patio", box=Box(x1=0.24, y1=0.21, x2=0.76, y2=0.68))
    (d,) = run(si.draft_site_items(v, [prompt], sam)).results  # the production prepare
    assert isinstance(d, si.Draft)


# ------------------------------------------------------------------ the setting


def test_the_setting_is_off_by_default_and_drafts_nothing() -> None:
    s = Settings(_env_file=None)
    assert s.site_drafts == "off"
    sam = ScriptedSam([[instance(rect_mask(200, 160, 40, 30, 140, 80))]])
    prompts = [si.SitePrompt("driveway", box=Box(x1=0.19, y1=0.17, x2=0.71, y2=0.52))]
    assert run(si.draft_if_enabled(s, view(), prompts, sam)) is None
    assert sam.calls == []
    on = Settings(_env_file=None, site_drafts="on", site_draft_max_pixel_m=0.2)
    drafted = run(si.draft_if_enabled(on, view(), prompts, sam, prepare=same_size))
    assert drafted is not None and isinstance(drafted.results[0], si.Draft)
    coarse = run(si.draft_if_enabled(on, view(pixel=0.25), prompts, sam))
    assert coarse is not None and coarse.results[0].code == E.SITE_VIEW_TOO_COARSE  # type: ignore[union-attr]


def test_the_capture_scale_setting_reaches_a_capture_view() -> None:
    on = Settings(_env_file=None, site_drafts="on", site_draft_capture_scale=1.02)
    settings = si.DraftSettings.from_settings(on)
    assert settings.capture_scale == 1.02
    v = view(200, 160, pixel=0.1)
    scaled = si.apply_capture_scale(v, settings.capture_scale)
    m = rect_mask(200, 160, 40, 30, 140, 80)
    prompt = si.SitePrompt("driveway", box=Box(x1=0.19, y1=0.17, x2=0.71, y2=0.52))
    (d,) = run(
        si.draft_site_items(
            scaled, [prompt], ScriptedSam([[instance(m)]]), settings, prepare=same_size
        )
    ).results
    assert isinstance(d, si.Draft)
    assert float(d.quantity) == pytest.approx(50 * 1.02**2 * SQFT_PER_M2, rel=1e-3)
    public = si.TopDownView(
        data=b"", width=10, height=10, pixel_size_m=0.1, georef=v.georef, kind="public", source="x"
    )
    assert si.apply_capture_scale(public, 1.02) is public


# ------------------------------------------------------------------ the vision model's proposal


def test_a_proposal_becomes_box_prompts_and_bad_boxes_are_dropped() -> None:
    answer = si.Proposal.model_validate_json(
        json.dumps(
            {
                "items": [
                    {"family": "driveway", "box": [580, 720, 650, 790], "confidence": "high"},
                    {"family": "patio", "box": [-20, 100, 1200, 300], "confidence": "medium"},
                    {"family": "deck", "box": [500, 500, 400, 600], "confidence": "low"},
                    {"family": "walk", "box": [1, 2, 3], "confidence": "high"},
                ]
            }
        )
    )
    prompts = si.proposal_prompts(answer, by="model:gpt-4o-mini")
    assert [p.family for p in prompts] == ["driveway", "patio"]
    assert prompts[0].box == Box(x1=0.58, y1=0.72, x2=0.65, y2=0.79)
    assert prompts[1].box == Box(x1=0.0, y1=0.1, x2=1.0, y2=0.3)
    assert prompts[0].by == "model:gpt-4o-mini"


def test_a_proposal_with_an_unknown_family_is_refused_by_its_schema() -> None:
    with pytest.raises(ValidationError):
        si.Proposal.model_validate(
            {"items": [{"family": "gazebo", "box": [0, 0, 1, 1], "confidence": "high"}]}
        )


# ------------------------------------------------------------------ the visual prompt


def test_a_visual_prompt_needs_a_phrase_a_box_or_a_point() -> None:
    with pytest.raises(ValidationError):
        loc.VisualPrompt(label=ComponentLabel.DRIVEWAY)
    p = loc.VisualPrompt(
        label=ComponentLabel.DRIVEWAY,
        text="driveway",
        boxes=(Box(x1=0.1, y1=0.2, x2=0.3, y2=0.6),),
        points=(Point(x=0.2, y=0.4),),
        negative_points=(Point(x=0.9, y=0.9),),
    )
    assert p.body() == {
        "text": "driveway",
        "positive_boxes": [
            [pytest.approx(0.2), pytest.approx(0.4), pytest.approx(0.2), pytest.approx(0.4)]
        ],
        "positive_points": [[0.2, 0.4]],
        "negative_points": [[0.9, 0.9]],
    }
    assert p.name == "driveway"
    assert (
        loc.VisualPrompt(label=ComponentLabel.PATIO, points=(Point(x=0.1, y=0.1),)).name
        == "patio (point)"
    )


def test_run_prompts_sends_one_prediction_and_parses_each_prompts_result() -> None:
    sent: dict[str, Any] = {}
    mask = [[0, 1, 1, 0], [0, 1, 1, 0], [0, 0, 0, 0]]

    async def fake_run(input_: dict[str, Any], names: tuple[str, ...]) -> Any:
        sent["input"], sent["names"] = input_, names
        result = {"boxes": [[1, 0, 3, 2]], "scores": [0.8], "masks": [[mask]]}
        empty = {"boxes": [], "scores": [], "masks": []}
        call = LocalizeCall(prompts=names, predict_seconds=1.5, cost_usd=0.001, status="succeeded")
        return [result, empty], call

    image = PreparedImage(b"\xff\xd8", 4, 3, 4, 3, "JPEG")
    prompts = [
        loc.VisualPrompt(
            label=ComponentLabel.DRIVEWAY, boxes=(Box(x1=0.25, y1=0.0, x2=0.75, y2=0.67),)
        ),
        loc.VisualPrompt(label=ComponentLabel.PATIO, text="patio"),
    ]
    found, call = run(loc._run_prompts(fake_run, image, prompts, 0.5, True))
    assert [json.loads(p) for p in sent["input"]["prompts"]] == [p.body() for p in prompts]
    assert sent["names"] == ("driveway (box)", "patio")
    assert sent["input"]["offset_masks"] is True
    assert len(found[0]) == 1 and found[1] == []
    assert found[0][0].label == ComponentLabel.DRIVEWAY and found[0][0].mask is not None
    assert found[0][0].mask.area() == 4
    assert call.cost_usd == 0.001
    with pytest.raises(ValueError, match="no prompts"):
        run(loc._run_prompts(fake_run, image, [], 0.5, True))
