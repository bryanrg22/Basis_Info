"""The pipeline side of the surface-crop material look (``MATERIALS_SECOND_LOOK=surface_crops``,
EVAL_REPORT 26.3): :func:`basis.vision.surface_crops.photo_crops`, :func:`apply_crops` and
:func:`look_at_room`, and the segmenter backends of :mod:`basis.vision.surface_segmenter`.

Offline: scripted providers, a fake segmenter, a fake Replicate client.
"""

from __future__ import annotations

import base64
import io
import json
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fixtures import fixture_path
from llm_fakes import FakeSegmenter, ModelKeyedProvider
from PIL import Image

from basis.llm import Budget, ScriptedTurn, Usage
from basis.llm.call import CallContext
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderConfigError,
    ProviderRateLimited,
    ProviderRequestRejected,
)
from basis.llm.prompts import load as load_prompt
from basis.llm.provider import BudgetExceededError
from basis.llm.recorder import MemoryRecorder
from basis.vision import surface_crops as sc
from basis.vision import surface_segmenter as seg
from basis.vision.localize import LocalizeCall, LocalizerUnavailable
from basis.vision.reconcile import InventoryObject
from basis.vision.schemas import Attachment, ComponentLabel, Material
from evals import predict_surface_crops as ps

MODEL = "claude-sonnet-5-5"
USAGE = Usage(input_tokens=1000, output_tokens=200)
PHOTO = fixture_path("photos", "01_kitchen.jpg").read_bytes()


def ctx(budget: float = 5.0, provider: Any = None) -> sc.CropContext:
    return sc.CropContext(
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="reconcile_rooms",
            budget=Budget(remaining_usd=budget),
            recorder=MemoryRecorder(),
        ),
        provider=provider,
        prompt=load_prompt(sc.PROMPT_NAME, sc.PROMPT_VERSION),
        model=MODEL,
        reasoning_effort="low",
    )


def answer(material: sc.CropMaterial, lower: sc.LowerFinish = sc.LowerFinish.NONE) -> ScriptedTurn:
    return ScriptedTurn(
        parsed=sc.CropAnswer(material=material, lower_finish=lower, confidence=0.9, evidence="x"),
        usage=USAGE,
    )


def obj(label: str, material: str) -> InventoryObject:
    return InventoryObject(
        label=ComponentLabel(label),
        count=1,
        image_indices=[1],
        material=Material(material),
        attachment=Attachment.PERMANENT,
        confidence=0.8,
    )


def provider(*turns: ScriptedTurn) -> ModelKeyedProvider:
    return ModelKeyedProvider(turns={(MODEL, "surface_crop_material"): list(turns)})


# --------------------------------------------------------------------------- one photo


async def test_every_large_surface_is_asked_once_with_the_photo_and_its_crop() -> None:
    chat = provider(
        answer(sc.CropMaterial.CARPET),
        answer(sc.CropMaterial.PAINTED_DRYWALL, sc.LowerFinish.WOOD),
        answer(sc.CropMaterial.ACOUSTIC_TILE),
    )
    found = await sc.photo_crops("a.jpg", PHOTO, FakeSegmenter(), ctx(provider=chat))
    assert [c.surface for c in found.crops] == ["floor", "wall", "ceiling"]
    assert found.errors == []
    assert [r.prompt_version for r in chat.requests] == ["surface_crop_material/v1"] * 3
    first = chat.requests[0]
    assert (first.model, first.reasoning_effort, first.max_tokens) == (MODEL, "low", sc.MAX_TOKENS)
    assert [m.content for m in first.messages[:2]] == [
        "Image 1 (the whole photo):",
        "Image 2 (the floor, cropped and enlarged, the rest greyed out):",
    ]
    assert found.classes() == {
        "floor": {"carpet"},
        "wall": {"painted_drywall"},
        "wall_lower": {"wood"},
        "ceiling": {"acoustic_tile"},
    }
    record = found.crops[1].record()
    assert record["answer"]["lower_finish"] == "wood" and record["cost_usd"] > 0
    assert record["box"][2] > record["box"][0] and 0.4 < record["share"] < 0.6


async def test_a_surface_under_two_percent_is_not_asked() -> None:
    class Tiny(FakeSegmenter):
        async def segment(
            self, image: Any, *, budget: Any = None, calls: Any = None
        ) -> seg.SurfaceMasks:
            masks = await super().segment(image)
            small = np.zeros_like(masks["floor"])
            small[:2, :2] = True
            return {"floor": small, "wall": masks["wall"]}

    chat = provider(answer(sc.CropMaterial.PAINTED_DRYWALL))
    found = await sc.photo_crops("a.jpg", PHOTO, Tiny(), ctx(provider=chat))
    assert [c.surface for c in found.crops] == ["wall"]


async def test_the_pipeline_request_is_the_studys_request() -> None:
    """The eval runner's request is the pipeline's (section 26.3 measured it)."""
    image = Image.open(io.BytesIO(PHOTO)).convert("RGB")
    masks = await FakeSegmenter().segment(image)
    crop = sc.jpeg_bytes(sc.crop_image(image, masks["wall"]))  # type: ignore[arg-type]
    context = ctx()
    piped = sc.build_request(
        "wall",
        PHOTO,
        crop,
        model=MODEL,
        prompt=context.prompt,
        study_id=str(context.call.study_id),
        activity=context.call.activity,
        effort="low",
    )
    studied = ps.build_request("wall", PHOTO, crop, MODEL, context.prompt, context.call, "low")
    assert piped == studied


async def test_an_unusable_answer_is_asked_once_more_then_left_unanswered() -> None:
    bad = ScriptedTurn(
        raise_error=ProviderBadResponse("no", provider="x", usage=USAGE, model=MODEL)
    )
    chat = provider(
        bad, bad, answer(sc.CropMaterial.PAINTED_DRYWALL), answer(sc.CropMaterial.PLASTER)
    )
    context = ctx(provider=chat)
    found = await sc.photo_crops("a.jpg", PHOTO, FakeSegmenter(), context)
    floor = found.crops[0]
    assert (floor.answer, floor.attempts, len(floor.errors)) == (None, 2, 2)
    assert floor.cost_usd > 0 and found.errors == []  # charged, not a provider failure
    assert context.call.budget.spent_usd == pytest.approx(sum(c.cost_usd for c in found.crops))
    assert sc.RoomCrops(photos=[found]).flags == ["invalid_model_output"]


async def test_a_provider_outage_leaves_the_surface_unanswered_and_flagged() -> None:
    down = ScriptedTurn(raise_error=ProviderRateLimited("slow down", provider="x", status=429))
    chat = provider(down, answer(sc.CropMaterial.PAINTED_DRYWALL), answer(sc.CropMaterial.PLASTER))
    found = await sc.photo_crops("a.jpg", PHOTO, FakeSegmenter(), ctx(provider=chat))
    assert found.crops[0].answer is None and found.crops[0].attempts == 1
    assert found.errors == ["provider_unavailable: floor"]
    assert sc.RoomCrops(photos=[found]).flags == ["provider_unavailable"]
    assert found.crops[1].answer is not None


async def test_a_segmenter_failure_asks_nothing() -> None:
    chat = provider()
    found = await sc.photo_crops("a.jpg", PHOTO, FakeSegmenter(fail=True), ctx(provider=chat))
    assert found.crops == [] and chat.requests == []
    assert found.errors == ["segmenter: fake segmenter failure"]


async def test_an_unreadable_photo_is_a_segmenter_fallback() -> None:
    found = await sc.photo_crops(
        "a.jpg", b"not an image", FakeSegmenter(), ctx(provider=provider())
    )
    assert found.errors and found.errors[0].startswith("segmenter: unreadable photo")


async def test_a_refused_crop_is_left_unanswered_and_flagged_without_a_retry() -> None:
    refused = ScriptedTurn(
        raise_error=ProviderRequestRejected(
            "Could not process image", provider="anthropic", status=400
        )
    )
    chat = provider(
        refused, answer(sc.CropMaterial.PAINTED_DRYWALL), answer(sc.CropMaterial.PLASTER)
    )
    found = await sc.photo_crops("a.jpg", PHOTO, FakeSegmenter(), ctx(provider=chat))
    assert found.crops[0].answer is None and found.crops[0].attempts == 1
    assert found.errors == ["request_rejected: floor"]
    assert sc.RoomCrops(photos=[found]).flags == ["request_rejected"]
    assert found.crops[1].answer is not None


@pytest.mark.parametrize(
    "error",
    [
        ProviderAccountError("credit balance is too low", provider="anthropic", status=400),
        ProviderConfigError("model: x", provider="anthropic", status=404),
    ],
)
async def test_an_account_error_propagates(error: Exception) -> None:
    chat = provider(ScriptedTurn(raise_error=error))
    with pytest.raises(ProviderAccountError):
        await sc.photo_crops("a.jpg", PHOTO, FakeSegmenter(), ctx(provider=chat))


async def test_an_exhausted_budget_propagates_before_any_call() -> None:
    chat = provider(answer(sc.CropMaterial.CARPET))
    with pytest.raises(BudgetExceededError):
        await sc.photo_crops("a.jpg", PHOTO, FakeSegmenter(), ctx(budget=0.0, provider=chat))
    assert chat.requests == []


# --------------------------------------------------------------------------- the room


def crops_of(file: str, **answers: tuple[str, str]) -> sc.PhotoCrops:
    found = sc.PhotoCrops(file=file)
    for surface, (material, lower) in answers.items():
        found.crops.append(
            sc.CropRecord(
                file=file,
                surface=surface,  # type: ignore[arg-type]
                box=(0, 0, 1, 1),
                share=0.5,
                answer=sc.CropAnswer(
                    material=sc.CropMaterial(material),
                    lower_finish=sc.LowerFinish(lower),
                    confidence=0.9,
                    evidence="x",
                ),
            )
        )
    return found


def test_apply_takes_the_majority_over_the_rooms_photos_and_keeps_the_rest() -> None:
    objects = [
        obj("wall_finish", "drywall"),
        obj("wainscoting", "wood"),
        obj("tile", "unknown"),
        obj("ceiling_finish", "unknown"),
        obj("window", "glass"),
        obj("carpet", "fabric"),
    ]
    photos = [
        crops_of("a", wall=("wallpaper", "wood"), floor=("carpet", "none")),
        crops_of("b", wall=("wallpaper", "none"), floor=("ceramic_tile", "none")),
        crops_of("c", wall=("painted_drywall", "none")),
    ]
    changed, changes = sc.apply_crops(objects, photos)
    assert [o.material.value for o in changed] == [
        "wallpaper",  # 2 of 3 walls
        "wood",  # unchanged: the only lower band said wood already
        "ceramic",  # a tile takes only a tile class: carpet is not one
        "unknown",  # no ceiling crop: the room's own material stands
        "glass",
        "fabric",
    ]
    assert [(c["index"], c["label"], c["before"], c["after"]) for c in changes] == [
        (0, "wall_finish", "drywall", "wallpaper"),
        (2, "tile", "unknown", "ceramic"),
    ]
    assert changes[0]["files"] == ["a", "b"]


def test_apply_is_the_studys_apply(tmp_path: Path) -> None:
    """The pipeline and ``evals.predict_surface_crops apply`` set the same materials."""
    photos = [
        crops_of("a", wall=("wallpaper", "wood"), ceiling=("acoustic_tile", "none")),
        crops_of("b", wall=("painted_masonry", "none"), floor=("stone", "none")),
        crops_of("c", wall=("painted_masonry", "fiberglass")),
    ]
    objects = [
        obj("wall_finish", "drywall"),
        obj("wainscoting", "other"),
        obj("tile", "ceramic"),
        obj("ceiling_finish", "drywall"),
    ]
    changed, _ = sc.apply_crops(objects, photos)
    crops_dir, rooms_dir, out = tmp_path / "crops", tmp_path / "rooms", tmp_path / "out"
    crops_dir.mkdir()
    rooms_dir.mkdir()
    (crops_dir / "surface_crops.json").write_text(
        json.dumps(
            {
                p.file: {
                    c.surface: {"repeats": [c.answer.model_dump(mode="json")]} for c in p.crops
                }
                for p in photos
            }
        )
    )
    (rooms_dir / "room_raw.jsonl").write_text(
        json.dumps(
            {
                "room_id": "r",
                "repeat": 1,
                "files": ["a", "b", "c"],
                "objects": [o.model_dump(mode="json") for o in objects],
            }
        )
        + "\n"
    )
    assert (
        ps.apply(
            ps.parse_args(
                ["apply", "--rooms", str(rooms_dir), "--crops", str(crops_dir), "--out", str(out)]
            )
        )
        == 0
    )
    studied = json.loads((out / "room_raw.jsonl").read_text())["objects"]
    assert [o["material"] for o in studied] == [o.material.value for o in changed]


async def test_look_at_room_runs_every_photo_in_order() -> None:
    chat = provider(*[answer(sc.CropMaterial.PAINTED_DRYWALL, sc.LowerFinish.WALLPAPER)] * 6)
    objects, found = await sc.look_at_room(
        [("a", PHOTO), ("b", PHOTO)],
        [obj("wainscoting", "wood")],
        FakeSegmenter(),
        ctx(provider=chat),
    )
    assert [p.file for p in found.photos] == ["a", "b"] and found.segmenter == "fake-segmenter"
    assert objects[0].material is Material.WALLPAPER and found.flags == []
    assert found.cost_usd > 0 and found.record()["changes"][0]["after"] == "wallpaper"


async def test_look_at_room_without_a_segmenter_changes_nothing_and_flags() -> None:
    objects, found = await sc.look_at_room(
        [("a", PHOTO)], [obj("wainscoting", "wood")], None, ctx(provider=provider())
    )
    assert objects[0].material is Material.WOOD and found.flags == ["segmenter"]


# --------------------------------------------------------------------------- segmenters


def test_union_by_surface_keeps_only_wall_floor_and_ceiling() -> None:
    a = np.zeros((4, 4), dtype=bool)
    a[0] = True
    b = np.zeros((4, 4), dtype=bool)
    b[1] = True
    masks = seg.union_by_surface([("wall", a), ("Wall ", b), ("floor", b), ("sky", a), ("rug", a)])
    assert set(masks) == {"wall", "floor"}
    assert int(masks["wall"].sum()) == 8 and int(masks["floor"].sum()) == 4


def png_of(colours: list[list[int]], size: tuple[int, int]) -> bytes:
    """A colour-coded segmentation: one horizontal band per colour."""
    width, height = size
    pixels = np.zeros((height, width, 3), dtype=np.uint8)
    band = height // len(colours)
    for i, colour in enumerate(colours):
        pixels[i * band : (i + 1) * band] = colour
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    return buffer.getvalue()


def test_masks_from_colours_reads_the_legend_and_resizes() -> None:
    legend = [
        {"label": "ceiling", "color": [10, 20, 30]},
        {"label": "wall", "color": [40, 50, 60]},
        {"label": "floor", "color": [70, 80, 90]},
        {"label": "bad"},
    ]
    png = png_of([c["color"] for c in legend[:3]], (40, 30))  # type: ignore[misc]
    masks = seg.masks_from_colours(png, legend, (80, 60))
    assert masks["ceiling"].shape == (60, 80)
    assert masks["ceiling"][:20].all() and not masks["ceiling"][20:].any()
    assert masks["floor"][40:].all()


class FakeReplicate:
    """Stands in for the SAM 3 client's create-poll cycle."""

    usd_per_second = seg.T4_USD_PER_SECOND

    def __init__(self, output: Any, error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.inputs: list[dict[str, Any]] = []

    async def _predict(self, input_: dict[str, Any], prompts: tuple[str, ...]) -> Any:
        self.inputs.append(input_)
        if self.error is not None:
            raise self.error
        call = LocalizeCall(
            prompts=prompts, predict_seconds=2.0, cost_usd=0.00045, status="succeeded"
        )
        return {"output": self.output}, call

    async def _request(self, method: str, url: str) -> Any:
        raise AssertionError("a data URI is not fetched")


async def test_the_replicate_segmenter_decodes_the_inline_segmentation_and_charges() -> None:
    image = Image.open(io.BytesIO(PHOTO)).convert("RGB")
    legend = [{"label": "wall", "color": [1, 2, 3]}, {"label": "floor", "color": [4, 5, 6]}]
    png = png_of([[1, 2, 3], [4, 5, 6]], image.size)
    client = FakeReplicate(
        {"segment": "data:image/png;base64," + base64.b64encode(png).decode(), "objects": legend}
    )
    segmenter = seg.ReplicateMask2Former(client=client, version="95798aa39f0e1234")  # type: ignore[arg-type]
    budget = Budget(remaining_usd=1.0)
    masks = await segmenter.segment(image, budget=budget)
    assert set(masks) == {"wall", "floor"} and masks["wall"].shape == (image.height, image.width)
    assert client.inputs[0]["image"].startswith("data:image/jpeg;base64,")
    assert budget.spent_usd == pytest.approx(0.00045)
    assert segmenter.name == "replicate:oitoito/mask2former@95798aa39f0e"


@pytest.mark.parametrize(
    ("output", "error"),
    [
        ({"segment": "ftp://x/y.png", "objects": []}, None),
        ({"segment": "data:image/png,raw", "objects": []}, None),
        ({"segment": "data:image/png;base64,!!!", "objects": []}, None),
        ({"objects": []}, None),
        (None, LocalizerUnavailable("replicate down")),
    ],
)
async def test_replicate_failures_are_segmenter_errors(
    output: Any, error: Exception | None
) -> None:
    image = Image.new("RGB", (80, 80))
    segmenter = seg.ReplicateMask2Former(client=FakeReplicate(output, error), version="v")  # type: ignore[arg-type]
    with pytest.raises(seg.SegmenterError) as raised:
        await segmenter.segment(image, budget=Budget(remaining_usd=1.0))
    assert "64 px" not in str(raised.value)  # each case fails at its own step


async def test_a_photo_replicate_cannot_take_is_a_segmenter_error() -> None:
    segmenter = seg.ReplicateMask2Former(client=FakeReplicate({}), version="v")  # type: ignore[arg-type]
    with pytest.raises(seg.SegmenterError, match="at least 64 px"):
        await segmenter.segment(Image.new("RGB", (8, 8)), budget=Budget(remaining_usd=1.0))


async def test_the_replicate_segmenter_respects_the_budget() -> None:
    segmenter = seg.ReplicateMask2Former(client=FakeReplicate({}), version="v")  # type: ignore[arg-type]
    with pytest.raises(BudgetExceededError):
        await segmenter.segment(Image.new("RGB", (80, 80)), budget=Budget(remaining_usd=0.0))


async def test_a_local_model_failure_is_a_segmenter_error(monkeypatch: pytest.MonkeyPatch) -> None:
    local = seg.LocalMask2Former(checkpoint="nobody/no-such-checkpoint")

    def broken(_: Any) -> Any:
        raise OSError("no weights")

    monkeypatch.setattr(local, "segment_sync", broken)
    with pytest.raises(seg.SegmenterError, match="no weights"):
        await local.segment(Image.new("RGB", (8, 8)))
    assert local.name == "local:nobody/no-such-checkpoint"


def test_build_segmenter() -> None:
    assert isinstance(seg.build_segmenter("local"), seg.LocalMask2Former)
    assert seg.build_segmenter("local").name == f"local:{seg.LOCAL_CHECKPOINT}"
    replicate = seg.build_segmenter("replicate", token="r8_x", version="abc")
    assert isinstance(replicate, seg.ReplicateMask2Former)
    assert replicate.client.usd_per_second == seg.T4_USD_PER_SECOND
    with pytest.raises(ValueError, match="REPLICATE_API_TOKEN"):
        seg.build_segmenter("replicate", token=None)
    with pytest.raises(ValueError):
        seg.build_segmenter("oneformer")
