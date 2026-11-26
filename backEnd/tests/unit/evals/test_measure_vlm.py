"""``evals.measure_vlm`` and M5 in ``evals.measure_eval`` (EVAL_REPORT 108), with a scripted
model: the answer's parsing and schema, the pictures (which view, the crop, the cross and the
scale bar, the houses not run and why), the message per prompt, the corners measured by code,
the judgments' truths, and the cost accounting from the ledger (unusable answers and retries
included). No network, no paid call."""

from __future__ import annotations

import asyncio
import io
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

import anthropic
import pytest
from PIL import Image
from pydantic import ValidationError

from basis.config import Settings
from basis.llm import prompts
from basis.llm.cost import cost_decimal
from basis.llm.errors import ProviderBadResponse
from basis.llm.provider import Budget, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.providers import strict_json_schema
from basis.llm.recorder import MemoryRecorder
from evals import measure_eval as me
from evals import measure_truth as mt
from evals import measure_vlm as mv

SIZE = (1440, 900)
PIXEL_M = 0.0840796770343956


def answer(**overrides: Any) -> mv.ExteriorAnswer:
    fields: dict[str, Any] = {
        "subject_found": True,
        "footprint_sf": 1200.0,
        "perimeter_ft": 140.0,
        "long_side_ft": 40.0,
        "eave_height_ft": 20.0,
        "ridge_height_ft": 28.0,
        "overhang_ft": 1.5,
        "roof_plan_sf": 1420.0,
        "roof_area_sf": 1600.0,
        "gutter_ft": 152.0,
        "outline_shape": "rectangle",
        "eaves_all_round": True,
        "rectangle_understates_perimeter": False,
        "footprint_exceeds_gla_per_storey": False,
        "confidence": "medium",
        "notes": "measured on the straight-down picture",
    }
    fields.update(overrides)
    return mv.ExteriorAnswer(**fields)


def pixel_answer(corners: list[tuple[float, float]], **overrides: Any) -> mv.PixelAnswer:
    base = answer(**overrides).model_dump()
    return mv.PixelAnswer(**base, roof_outline_px=[{"x": x, "y": y} for x, y in corners])


# ------------------------------------------------------------------ the answer


def test_quantities_come_from_the_answer_and_the_wall_face_from_perimeter_and_eave() -> None:
    q = mv.answer_quantities(answer())
    assert q["perimeter_ft"] == 140 and q["eave_height_ft"] == 20
    assert q["wall_area_gross_sf"] == pytest.approx(140 * 20)
    assert "overhang_ft" not in q  # not a scored quantity
    assert set(q) == set(mv.ANSWER_QUANTITIES) | {"wall_area_gross_sf"}


def test_a_null_quantity_is_left_out_and_no_wall_face_without_an_eave() -> None:
    q = mv.answer_quantities(answer(eave_height_ft=None, gutter_ft=None))
    assert "eave_height_ft" not in q and "gutter_ft" not in q
    assert "wall_area_gross_sf" not in q


def test_the_schema_refuses_a_negative_length_an_unknown_field_and_a_bad_shape() -> None:
    with pytest.raises(ValidationError):
        answer(perimeter_ft=-1.0)
    with pytest.raises(ValidationError):
        answer(outline_shape="circle")
    with pytest.raises(ValidationError):
        mv.ExteriorAnswer(**answer().model_dump(), surprise=1)
    with pytest.raises(ValidationError):  # the pixel prompt's answer needs its corners
        mv.PixelAnswer(**answer().model_dump())


def test_long_notes_are_clipped_not_refused() -> None:
    a = answer(notes="x" * 5000)
    assert len(a.notes) == mv.NOTE_MAX


@pytest.mark.parametrize("model", [mv.ExteriorAnswer, mv.PixelAnswer])
def test_both_providers_accept_the_schemas(model: type[mv.ExteriorAnswer]) -> None:
    """OpenAI's strict structured output and Claude's ``output_config.format`` (the SDK's
    transform) both take the answer models; every field is required (strict mode)."""
    strict = strict_json_schema(model)
    assert set(strict["required"]) == set(model.model_fields)
    assert strict["additionalProperties"] is False
    assert anthropic.transform_schema(model)["type"] == "object"


# ------------------------------------------------------------------ the setups and prompts


def test_setups_name_model_and_prompt_pairs(settings: Settings) -> None:
    models = mv.models_from_settings(settings)
    assert models["sol"].model == settings.vision_model
    assert models["opus"].effort == settings.vision_fusion_reasoning_effort
    every = mv.parse_setups("*", models)
    assert len(every) == len(models) * len(mv.PROMPTS)
    two = mv.parse_setups("opus:plain,sol:*", models)
    assert [s.id for s in two] == [
        "M5.opus.plain",
        "M5.sol.plain",
        "M5.sol.pixels",
        "M5.sol.reference",
    ]
    for bad in ("gemini:plain", "sol:shout", ""):
        with pytest.raises(ValueError):
            mv.parse_setups(bad, models)


def test_every_prompt_is_released_and_pinned_by_its_hash() -> None:
    for name in mv.PROMPTS.values():
        prompt = prompts.load(name, mv.PROMPT_VERSION)
        assert prompt.sha256 == prompts.manifest()[name][mv.PROMPT_VERSION]
        assert "red cross" in prompt.text and "JSON" in prompt.text
    assert "roof_outline_px" in prompts.load("measure_exterior_pixels", "v1").text
    assert "80 in" in prompts.load("measure_exterior_reference", "v1").text


# ------------------------------------------------------------------ the pictures


def png(size: tuple[int, int], colour: tuple[int, int, int] = (90, 120, 90)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


def write_capture(
    directory: Path,
    lat: float,
    lon: float,
    *,
    labels: tuple[str, ...] = (
        "aerial-close",
        "aerial-wide",
        "side-south",
        "side-west",
        "side-north",
        "side-east",
        "street-front",
    ),
) -> None:
    """A capture as ``evals.measure_capture`` leaves it: ``result.json`` and the PNGs."""
    views = []
    for label in labels:
        uri = f"gs://bucket/studies/s/captures/c/{label}.png"
        path = directory / "objects" / uri.removeprefix("gs://")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png(SIZE))
        straight = label.startswith("aerial")
        views.append(
            {
                "label": label,
                "uri": uri,
                "width": SIZE[0],
                "height": SIZE[1],
                "pixel_size_m": (PIXEL_M if label == "aerial-close" else 0.2452)
                if straight
                else None,
                "lat": lat,
                "lon": lon,
                "imagery_on": "2024-04-06",
            }
        )
    (directory / "result.json").write_text(json.dumps({"views": views}))


def test_the_house_lands_on_its_pixel_north_up() -> None:
    view = mv.View("aerial-close", Path("x"), 1440, 900, PIXEL_M, 39.0, -94.0, None)
    assert mv.subject_pixel(view, 39.0, -94.0) == pytest.approx((720, 450))
    east = 10 / (mv.EARTH_RADIUS_M * math.cos(math.radians(39.0))) * 180 / math.pi
    north = 10 / mv.EARTH_RADIUS_M * 180 / math.pi
    x, y = mv.subject_pixel(view, 39.0 + north, -94.0 + east)
    assert x == pytest.approx(720 + 10 / PIXEL_M, abs=0.01)
    assert y == pytest.approx(450 - 10 / PIXEL_M, abs=0.01)


def test_the_close_view_is_used_unless_the_house_is_near_its_edge() -> None:
    close = mv.View("aerial-close", Path("c"), 1440, 900, PIXEL_M, 39.0, -94.0, None)
    wide = mv.View("aerial-wide", Path("w"), 1440, 900, 0.2452, 39.0, -94.0, None)
    assert mv.choose_view([wide, close], 39.0, -94.0)[0].label == "aerial-close"
    # 30 m south: 357 px below the close view's centre, 12 px from its edge.
    south = 39.0 - 30 / mv.EARTH_RADIUS_M * 180 / math.pi
    assert mv.choose_view([wide, close], south, -94.0)[0].label == "aerial-wide"
    # 100 m south: 17.6 m from the wide view's edge, under the 20 m margin.
    far = 39.0 - 100 / mv.EARTH_RADIUS_M * 180 / math.pi
    with pytest.raises(LookupError):
        mv.choose_view([wide, close], far, -94.0)


def test_the_crop_stays_inside_the_picture() -> None:
    assert mv.crop_box(1440, 900, 720, 450) == (336, 66, 1104, 834)
    assert mv.crop_box(1440, 900, 10, 890) == (0, 132, 768, 900)
    assert mv.crop_box(500, 400, 250, 200) == (0, 0, 500, 400)


def test_the_crop_carries_the_cross_on_the_house_and_a_true_scale_bar() -> None:
    image = Image.new("RGB", SIZE, (90, 120, 90))
    data, (cx, cy), bar_ft = mv.draw_straight_down(image, 1000.0, 700.0, PIXEL_M)
    crop = Image.open(io.BytesIO(data))
    assert crop.size == (768, 768)
    assert crop.getpixel((cx + 6, cy)) == (255, 0, 0)  # an arm of the cross
    assert crop.getpixel((cx, cy)) == (90, 120, 90)  # the house's centre left visible
    assert bar_ft == 30  # 109 px at 0.084 m
    bar_px = round(30 * 0.3048 / PIXEL_M)
    y0 = crop.height - 16
    assert crop.getpixel((12 + bar_px // 2, y0)) == (0, 0, 0)
    assert crop.getpixel((12 + bar_px + 8, y0)) != (0, 0, 0)
    assert mv.scale_bar_ft(0.2452) == 100


def test_pictures_and_message_for_a_house(tmp_path: Path) -> None:
    house = me.synthetic_house()
    write_capture(tmp_path / house.id, house.location.lat, house.location.lon + 0.0002)
    pics = mv.load_pictures(house, tmp_path)
    assert pics.view == "aerial-close" and pics.count == 6
    assert [label for label, _ in pics.obliques] == list(mv.OBLIQUES)
    plain = mv.message_text("plain", house, pics)
    pixels = mv.message_text("pixels", house, pics)
    assert "GLA 2,400 SF; 2 storeys." in plain
    assert "on the ground" not in plain and "0.0841 m" in pixels
    # The capture's point is 17 m east of the house: the obliques say so.
    assert "about 17 m east of the house" in plain
    request = mv.build_request(
        mv.Setup(mv.models_from_settings(Settings())["sol"], "pixels"),
        house,
        pics,
        study_id="s",
        activity="a",
    )
    images = request.messages[0].images
    assert len(images) == 6 and images[0].media_type == "image/png"
    assert {i.detail for i in images} == {"high"}
    assert request.response_model is mv.PixelAnswer
    assert request.prompt_version == "measure_exterior_pixels/v1"


def test_houses_without_pictures_or_not_to_measure_from_above_are_not_run(
    tmp_path: Path,
) -> None:
    house = me.synthetic_house()
    assert mv.skip_reason(house, tmp_path) == "no pictures"
    write_capture(tmp_path / house.id, house.location.lat, house.location.lon)
    assert mv.skip_reason(house, tmp_path) is None
    changed = house.model_copy(update={"standing": mt.Standing(status="changed", source="ortho")})
    assert "changed" in (mv.skip_reason(changed, tmp_path) or "")
    lost = house.model_copy(
        update={"location": mt.Location(lat=40.0, lon=-90.0, how="APPROXIMATE, the barns")}
    )
    assert "not located" in (mv.skip_reason(lost, tmp_path) or "")


def test_a_house_in_a_neighbours_capture_reads_that_capture(tmp_path: Path) -> None:
    assert mv.capture_dir(tmp_path, "mo1223") == tmp_path / "mo1222"
    assert mv.capture_dir(tmp_path, "il0491") == tmp_path / "il0491"


# ------------------------------------------------------------------ corners measured by code


def test_corners_give_the_roof_plan_and_the_walls_less_the_overhang() -> None:
    ft = PIXEL_M * mv.M_TO_FT
    w, h = 40 / ft, 30 / ft  # a 40 x 30 ft roof edge
    a = pixel_answer([(100, 100), (100 + w, 100), (100 + w, 100 + h), (100, 100 + h)])
    q = mv.corner_quantities(a, PIXEL_M)
    assert q["roof_plan_sf"] == pytest.approx(1200, rel=1e-9)
    d = 1.5
    assert q["perimeter_ft"] == pytest.approx(140 - 8 * d)
    assert q["footprint_sf"] == pytest.approx((40 - 2 * d) * (30 - 2 * d))
    assert q["long_side_ft"] == pytest.approx(40 - 2 * d)
    assert q["wall_area_gross_sf"] == pytest.approx((140 - 8 * d) * 20)


def test_the_long_side_follows_a_rotated_outline() -> None:
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    rect = [(0, 0), (100, 0), (100, 40), (0, 40)]
    turned = [(x * c - y * s, x * s + y * c) for x, y in rect]
    assert mv.long_extent(turned) == pytest.approx(100)
    assert mv.shoelace(turned) == pytest.approx(4000)
    assert mv.ring_length(turned) == pytest.approx(280)


def test_under_three_corners_nothing_is_measured() -> None:
    assert mv.corner_quantities(pixel_answer([(1, 1), (5, 5)]), PIXEL_M) == {}
    assert mv.shoelace([(0, 0), (1, 1)]) == 0.0


# ------------------------------------------------------------------ the judgments


def test_judgment_truths_on_the_synthetic_box() -> None:
    house = me.synthetic_house()  # 40 x 30, two equal floors, hip, eaves all round
    m0 = me.run_m0(house).quantities["perimeter_ft"]
    t = mv.judgment_truths(house, m0)
    assert t == {
        "outline_is_rectangle": True,
        "rectangle_understates_perimeter": False,  # M0's 141.4 against 140
        "footprint_exceeds_gla_per_storey": False,
        "eaves_all_round": True,
    }
    assert mv.judgment_truths(house, 0.85 * 140)["rectangle_understates_perimeter"]
    assert mv.answer_judgments(answer(outline_shape="L"))["outline_is_rectangle"] is False


def test_the_committed_houses_judgment_truths() -> None:
    houses = {h.id: h for h in mt.load_all()}
    m0 = {k: me.run_m0(h).quantities["perimeter_ft"] for k, h in houses.items()}
    t = {k: mv.judgment_truths(h, m0[k]) for k, h in houses.items()}
    assert t["mo1222"]["outline_is_rectangle"] and not t["mo1223"]["outline_is_rectangle"]
    assert t["mo1224"]["rectangle_understates_perimeter"]  # M0 25.4 % low (EVAL 103.3)
    assert not t["il0491"]["rectangle_understates_perimeter"]  # 5.5 % low
    assert t["va1914"]["footprint_exceeds_gla_per_storey"]
    assert "eaves_all_round" not in t["md1464"]  # not recorded


# ------------------------------------------------------------------ the money


def scripted_jobs(
    tmp_path: Path, turns: list[ScriptedTurn]
) -> tuple[list[mv.Outcome], MemoryRecorder, Budget]:
    house = me.synthetic_house()
    write_capture(tmp_path / house.id, house.location.lat, house.location.lon)
    pics = mv.load_pictures(house, tmp_path)
    setup = mv.Setup(mv.models_from_settings(Settings())["sol"], "plain")
    provider = ScriptedProvider(turns, model="gpt-6.1-sol")
    recorder = MemoryRecorder()
    budget = Budget(remaining_usd=1.0)
    ctx = mv.new_context(budget, recorder)
    jobs = [(setup, house, pics, 1), (setup, house, pics, 2)]
    outcomes = asyncio.run(mv.run_jobs(provider, ctx, recorder.records, jobs, concurrency=1))
    return outcomes, recorder, budget


def test_each_house_call_costs_its_own_ledger_rows(tmp_path: Path) -> None:
    u1 = Usage(input_tokens=6000, output_tokens=700, reasoning_output_tokens=400)
    u2 = Usage(input_tokens=6100, output_tokens=900)
    outcomes, recorder, budget = scripted_jobs(
        tmp_path,
        [ScriptedTurn(parsed=answer(), usage=u1), ScriptedTurn(parsed=answer(), usage=u2)],
    )
    first, second = sorted(outcomes, key=lambda o: o.repeat)
    assert first.cost_usd == pytest.approx(float(cost_decimal("gpt-6.1-sol", u1)))
    assert second.cost_usd == pytest.approx(float(cost_decimal("gpt-6.1-sol", u2)))
    assert (first.input_tokens, first.output_tokens, first.reasoning_tokens) == (6000, 700, 400)
    assert first.calls == second.calls == 1 and first.models == ["gpt-6.1-sol"]
    assert sum(r.cost_usd for r in recorder.records) == Decimal(str(first.cost_usd)) + Decimal(
        str(second.cost_usd)
    )
    assert budget.spent_usd == pytest.approx(first.cost_usd + second.cost_usd)
    row = recorder.records[0].entry
    assert row.prompt_version == "measure_exterior_plain/v1" and row.prompt_sha256
    assert row.entity_type == "measure_house"
    assert first.quantities["perimeter_ft"] == 140


def test_an_unusable_answer_is_retried_once_and_both_attempts_are_paid(tmp_path: Path) -> None:
    """A reply the schema refuses reaches the ledger as an error row with its usage; the
    second attempt's refusal too; the house gets no quantities and pays for both."""
    bad = Usage(input_tokens=6000, output_tokens=500)

    def refusal() -> ProviderBadResponse:
        return ProviderBadResponse(
            "the answer does not match the schema", usage=bad, model="gpt-6.1-sol"
        )

    outcomes, recorder, budget = scripted_jobs(
        tmp_path,
        [
            ScriptedTurn(raise_error=refusal()),
            ScriptedTurn(raise_error=refusal()),
            ScriptedTurn(parsed=answer(), usage=bad),
        ],
    )
    first, second = sorted(outcomes, key=lambda o: o.repeat)
    assert first.answer is None and first.quantities == {} and first.calls == 2
    assert len(first.errors) == 2
    assert first.cost_usd == pytest.approx(2 * float(cost_decimal("gpt-6.1-sol", bad)))
    assert second.answer is not None and second.calls == 1
    assert [r.entry.status for r in recorder.records] == ["error", "error", "ok"]
    assert budget.spent_usd == pytest.approx(first.cost_usd + second.cost_usd)


# ------------------------------------------------------------------ M5 in the harness


def test_the_harness_runs_m5_scores_it_and_replays_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    house = me.synthetic_house()
    captures = tmp_path / "captures"
    write_capture(captures / house.id, house.location.lat, house.location.lon)
    provider = ScriptedProvider(
        [
            ScriptedTurn(parsed=answer(perimeter_ft=154.0)),  # 10 % over the 140 ft truth
            ScriptedTurn(
                parsed=pixel_answer(
                    [(300, 300), (450, 300), (450, 410), (300, 410)], perimeter_ft=126.0
                )
            ),
        ],
        model="gpt-6.1-sol",
    )
    monkeypatch.setattr(me, "m5_provider", lambda settings: provider)
    out = tmp_path / "run"
    argv = ["--dry-run", "--out", str(out), "--captures", str(captures)]
    m5 = ["--m5", "sol:plain,sol:pixels", "--budget-usd", "1", "--m5-concurrency", "1"]
    assert me.main([*argv, *m5]) == 0
    body = json.loads((out / "measure_eval.json").read_text())
    perim = {
        s["method"]: s["signed_pct"] for s in body["scores"] if s["quantity"] == "perimeter_ft"
    }
    assert perim["M5.sol.plain"] == pytest.approx(10.0)
    assert perim["M5.sol.pixels"] == pytest.approx(-10.0)
    assert "M5.sol.pixels.corners" in perim  # the corners measured by code
    paid = [c for c in body["costs"] if c["method"].startswith("M5") and c["cost_usd"] > 0]
    assert {c["method"] for c in paid} == {"M5.sol.plain", "M5.sol.pixels"}
    manifest = json.loads((out / "manifest.json").read_text())
    sha = prompts.load("measure_exterior_pixels", "v1").sha256
    assert sha in json.dumps(manifest)  # the prompts called, by their hash
    text = (out / "measure_eval.md").read_text()
    assert "## M5: a vision model asked" in text and "corners measured by code" in text
    # Rescoring the recorded outcomes calls nothing.
    monkeypatch.setattr(me, "m5_provider", lambda settings: ScriptedProvider([]))
    again = tmp_path / "again"
    replay = ["--m5", "sol:*", "--m5-replay", str(out)]
    assert me.main(["--dry-run", "--out", str(again), *replay]) == 0
    rescored = json.loads((again / "measure_eval.json").read_text())
    assert rescored["scores"] == body["scores"]


def test_the_estimate_calls_nothing_and_a_house_without_pictures_is_not_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    monkeypatch.setattr(me, "m5_provider", lambda settings: ScriptedProvider([]))
    captures = tmp_path / "captures"
    captures.mkdir()
    base = ["--dry-run", "--captures", str(captures), "--m5", "opus:plain"]
    assert me.main([*base, "--out", str(tmp_path / "est"), "--m5-estimate"]) == 0
    assert me.main([*base, "--out", str(tmp_path / "run")]) == 0  # no pictures: no call
    body = json.loads((tmp_path / "run" / "measure_eval.json").read_text())
    assert body["m5"][0]["skipped"] == "no pictures"
    assert not [s for s in body["scores"] if s["method"].startswith("M5")]
    assert "| zz0001 | no | no pictures |" in (tmp_path / "run" / "measure_eval.md").read_text()


def test_the_budget_stops_the_run_before_a_call_it_cannot_afford(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    house = me.synthetic_house()
    captures = tmp_path / "captures"
    write_capture(captures / house.id, house.location.lat, house.location.lon)
    provider = ScriptedProvider([ScriptedTurn(parsed=answer())])
    monkeypatch.setattr(me, "m5_provider", lambda settings: provider)
    argv = ["--dry-run", "--out", str(tmp_path / "run"), "--captures", str(captures)]
    assert me.main([*argv, "--m5", "opus:plain", "--budget-usd", "0.001"]) == 2
    assert provider.requests == []  # refused before anything was sent
