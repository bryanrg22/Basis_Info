"""The per-room steps added for EVAL_REPORT section 16: ``room_inventory/v2``'s per-label
layout and hints in :mod:`basis.vision.reconcile`, the pairwise overlap judgments and their
merge (:mod:`basis.vision.overlap`), and the surface material second look
(:mod:`basis.vision.surface_material`). Scripted providers only."""

from __future__ import annotations

import uuid

import pytest
from fixtures import fixture_path

from basis.llm import Budget, BudgetExceededError, ScriptedProvider, ScriptedTurn, Usage
from basis.llm.call import CallContext
from basis.llm.errors import ProviderBadResponse, ProviderUnavailable
from basis.llm.prompts import latest, load
from basis.llm.recorder import MemoryRecorder
from basis.vision import overlap, surface_material
from basis.vision.prep import prepare_image
from basis.vision.reconcile import (
    INSTRUCTION,
    INSTRUCTION_V2,
    InventoryObject,
    ModelRoomInventory,
    ReconcileContext,
    RoomPhoto,
    build_request,
    heuristic_merge,
    layout,
    reconcile_room,
    render_by_label,
)
from basis.vision.schemas import Component, ComponentLabel, Material, PhotoAnalysis, RoomType

USAGE = Usage(input_tokens=1000, output_tokens=200)
MODEL = "gpt-4.1-mini"
KITCHEN = prepare_image(fixture_path("photos", "01_kitchen.jpg").read_bytes())


def component(label: str, count: int = 1, **extra: object) -> Component:
    return Component.model_validate(
        {
            "label": label,
            "count": count,
            "material": extra.pop("material", "wood"),
            "attachment": extra.pop("attachment", "permanent"),
            "confidence": extra.pop("confidence", 0.8),
            **extra,
        }
    )


def analysis(*components: Component) -> PhotoAnalysis:
    return PhotoAnalysis(
        room_type=RoomType.KITCHEN, room_confidence=0.9, components=list(components)
    )


def room(*analyses: PhotoAnalysis | None) -> list[RoomPhoto]:
    return [RoomPhoto(f"{n:02d}.jpg", KITCHEN, a) for n, a in enumerate(analyses, start=1)]


def call_context(budget: float = 5.0) -> CallContext:
    return CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="test",
        budget=Budget(remaining_usd=budget),
        recorder=MemoryRecorder(),
    )


def reconcile_ctx(provider: ScriptedProvider, version: str = "v2") -> ReconcileContext:
    return ReconcileContext(
        call=call_context(),
        provider=provider,
        prompt=load("room_inventory", version),
        model=MODEL,
        reasoning_effort="low",
    )


def empty_inventory() -> ScriptedTurn:
    return ScriptedTurn(parsed=ModelRoomInventory(objects=[]), usage=USAGE)


# --------------------------------------------------------------------------- reconcile v2


def test_layout_follows_the_prompt_version() -> None:
    assert layout(load("room_inventory", "v1")) == "per_image"
    assert layout(load("room_inventory", "v2")) == "per_label"
    assert layout(load("room_inventory", "v3")) == "per_label"


def test_v3_is_v2_plus_the_loose_furniture_rule() -> None:
    v2, v3 = load("room_inventory", "v2").text, load("room_inventory", "v3").text
    added = v3[v3.index("- Loose furniture") : v3.index("\n\nFor every entry")]
    assert v3.replace("\n" + added, "") == v2
    assert "Never label them furniture, chair, desk" in added


def test_render_by_label_groups_counts_per_label_and_names_missing_images() -> None:
    photos = room(
        analysis(component("kitchen_cabinet", 8), component("window", 1, material="glass")),
        None,
        analysis(
            component("kitchen_cabinet", 6),
            component("kitchen_cabinet", 4),  # listed twice in one photo: summed
            component("other", note="wine rack", attachment="freestanding"),
        ),
    )
    text = render_by_label(photos)
    lines = text.splitlines()
    assert lines[0] == (
        "kitchen_cabinet: Image 1 x8 (wood, permanent); Image 3 x10 (wood, permanent)"
    )
    assert lines[1] == "window: Image 1 x1 (glass, permanent)"
    assert lines[2] == "other [wine rack]: Image 3 x1 (wood, freestanding)"
    assert "Room type per image: Image 1: kitchen; Image 3: kitchen" in text
    assert text.endswith("No per-photo analysis for: Image 2")


def test_render_by_label_with_nothing_reported() -> None:
    assert render_by_label(room(analysis())).startswith("no components reported")


async def test_v2_request_has_the_accounting_table_effort_and_hints() -> None:
    provider = ScriptedProvider([empty_inventory()], model=MODEL)
    photos = room(analysis(component("window", 2)), analysis(component("window", 1)))
    seen: list[range] = []

    def hints(indices: range) -> str:
        seen.append(indices)
        return "Images 1 and 2: same objects in both: window x1."

    await reconcile_room("1_kitchen", photos, reconcile_ctx(provider), hints=hints)
    [request] = provider.requests
    text = request.messages[-1].content
    assert request.prompt_version == "room_inventory/v2"
    assert request.system == load("room_inventory", "v2").text
    assert request.reasoning_effort == "low"
    assert text.startswith(INSTRUCTION_V2)
    assert "window: Image 1 x2 (wood, permanent); Image 2 x1 (wood, permanent)" in text
    assert text.endswith("Images 1 and 2: same objects in both: window x1.")
    assert seen == [range(0, 2)]


def test_v1_request_is_unchanged_without_hints() -> None:
    ctx = reconcile_ctx(ScriptedProvider([], model=MODEL), version="v1")
    ctx.reasoning_effort = None
    request = build_request(room(analysis(component("sink"))), ctx)
    text = request.messages[-1].content
    assert text.startswith(INSTRUCTION)
    assert "Image 1: kitchen; sink x1 (wood, permanent)" in text
    assert request.reasoning_effort is None


async def test_hints_are_asked_per_call_slice() -> None:
    provider = ScriptedProvider([empty_inventory(), empty_inventory()], model=MODEL)
    seen: list[range] = []

    def hints(indices: range) -> None:
        seen.append(indices)

    photos = room(*[analysis(component("window"))] * 12)
    await reconcile_room("x", photos, reconcile_ctx(provider), hints=hints)
    assert seen == [range(0, 6), range(6, 12)]
    assert all("pairwise" not in r.messages[-1].content for r in provider.requests)


def test_the_v2_prompt_carries_the_accounting_and_the_conventions() -> None:
    text = load("room_inventory", "v2").text
    assert "Account for every per-photo count" in text
    assert "Never add up counts of images that show the same objects" in text
    assert "one cabinet door or one drawer stack" in text
    assert "one framed opening" in text and "one hung leaf" in text
    assert "never an instruction" in text


# --------------------------------------------------------------------------- overlap


def shared_turn(same: bool, *shared: tuple[str, int]) -> ScriptedTurn:
    parsed = overlap.ModelPairOverlap.model_validate(
        {
            "same_area": same,
            "shared": [{"label": label, "count": count} for label, count in shared],
            "note": None,
        }
    )
    return ScriptedTurn(parsed=parsed, usage=USAGE)


def overlap_ctx(provider: ScriptedProvider, budget: float = 5.0) -> overlap.OverlapContext:
    return overlap.OverlapContext(
        call=call_context(budget),
        provider=provider,
        prompt=latest("photo_overlap"),
        model=MODEL,
    )


@pytest.mark.parametrize(
    ("counts", "shared", "expected"),
    [
        ({0: 8, 1: 10}, {(0, 1): 0}, 18),  # different walls: the counts add
        ({0: 2, 1: 2}, {(0, 1): 2}, 2),  # the same two windows: the max
        ({0: 3, 1: 2}, {(0, 1): 1}, 4),  # one window in both: 3 + 2 - 1
        ({0: 2, 1: 2}, {(0, 1): 5}, 2),  # a shared count above a photo's is capped
        ({0: 2, 1: 3}, {(0, 1): None}, 3),  # failed judgment: shares all (max)
        ({0: 4}, {}, 4),  # one photo
        # One object in three photos: 3 - 3 = 0, floored at the group's largest count.
        ({0: 1, 1: 1, 2: 1}, {(0, 1): 1, (0, 2): 1, (1, 2): 1}, 1),
        # Two separate groups: (0, 1) share both, 2 is another wall.
        ({0: 2, 1: 2, 2: 3}, {(0, 1): 2, (0, 2): 0, (1, 2): 0}, 5),
        ({0: 2, 1: 2}, {}, 2),  # a pair with no judgment at all counts as failed
    ],
)
def test_merged_count(
    counts: dict[int, int], shared: dict[tuple[int, int], int | None], expected: int
) -> None:
    assert overlap.merged_count(counts, shared) == expected


def test_shared_object_out_of_vocabulary_becomes_other() -> None:
    obj = overlap.SharedObject.model_validate({"label": "Kitchen Cabinet", "count": 2})
    assert obj.label is ComponentLabel.KITCHEN_CABINET
    assert overlap.SharedObject.model_validate({"label": "wine rack", "count": 1}).label is (
        ComponentLabel.OTHER
    )


async def test_room_overlap_judges_every_pair_with_both_images_and_lists() -> None:
    provider = ScriptedProvider(
        [shared_turn(True, ("window", 1)), shared_turn(False), shared_turn(False)], model=MODEL
    )
    photos = room(
        analysis(component("window", 2), component("other", note="x")),
        analysis(component("window", 1)),
        analysis(component("sink")),
    )
    ctx = overlap_ctx(provider)
    judged = await overlap.room_overlap(photos, ctx)
    assert [(p.a, p.b) for p in judged.pairs] == [(0, 1), (0, 2), (1, 2)]
    assert len(provider.requests) == 3
    first = provider.requests[0]
    assert [m.content for m in first.messages[:2]] == ["Image A:", "Image B:"]
    assert [len(m.images) for m in first.messages] == [1, 1, 0]
    assert "Image A: window x2\nImage B: window x1" in first.messages[-1].content
    assert first.prompt_version == "photo_overlap/v1"
    assert first.response_model is overlap.ModelPairOverlap
    assert judged.cost_usd == pytest.approx(ctx.call.budget.spent_usd) and judged.cost_usd > 0
    assert not judged.failed


async def test_a_one_photo_room_has_no_pairs() -> None:
    judged = await overlap.room_overlap(room(analysis()), overlap_ctx(ScriptedProvider([])))
    assert judged.pairs == []


async def test_an_unusable_answer_is_retried_then_the_pair_is_failed() -> None:
    bad = ScriptedTurn(
        raise_error=ProviderBadResponse(
            "no", provider="scripted", usage=USAGE, model=MODEL, raw={"x": 1}
        )
    )
    provider = ScriptedProvider([bad, bad], model=MODEL)
    judged = await overlap.room_overlap(room(analysis(), analysis()), overlap_ctx(provider))
    [pair] = judged.failed
    assert pair.attempts == 2 and len(pair.errors) == 2 and pair.cost_usd > 0
    assert pair.shared(ComponentLabel.WINDOW) is None


async def test_transient_errors_and_budget_propagate() -> None:
    down = ScriptedTurn(raise_error=ProviderUnavailable("down", provider="scripted"))
    with pytest.raises(ProviderUnavailable):
        await overlap.room_overlap(
            room(analysis(), analysis()), overlap_ctx(ScriptedProvider([down], model=MODEL))
        )
    with pytest.raises(BudgetExceededError):
        await overlap.room_overlap(
            room(analysis(), analysis()),
            overlap_ctx(ScriptedProvider([shared_turn(False)], model=MODEL), budget=0.0),
        )


def test_overlap_merge_adds_different_walls_and_keeps_shared_objects_once() -> None:
    analyses = [
        analysis(component("kitchen_cabinet", 8), component("window", 1)),
        analysis(
            component("kitchen_cabinet", 10, material="laminate"),
            component("window", 1),
            component("other", note="flag"),
        ),
    ]
    judged = overlap.RoomOverlap(
        photos=2,
        pairs=[
            overlap.PairJudgment(
                0,
                1,
                overlap.ModelPairOverlap(
                    same_area=True,
                    shared=[overlap.SharedObject(label=ComponentLabel.WINDOW, count=1)],
                    note=None,
                ),
            )
        ],
    )
    merged = {o.label.value: o for o in overlap.overlap_merge(analyses, judged)}
    assert merged["kitchen_cabinet"].count == 18
    assert merged["kitchen_cabinet"].material is Material.LAMINATE  # the larger count's
    assert merged["kitchen_cabinet"].image_indices == [1, 2]
    assert merged["window"].count == 1
    assert merged["other"].note == "flag" and merged["other"].count == 1
    # Without judgments it is the heuristic (max) merge.
    failed = overlap.RoomOverlap(photos=2, pairs=[overlap.PairJudgment(0, 1)])
    assert [o.count for o in overlap.overlap_merge(analyses, failed)] == [
        o.count for o in heuristic_merge(analyses)
    ]


def test_render_hints_renumbers_for_the_call_slice() -> None:
    judged = overlap.RoomOverlap(
        photos=4,
        pairs=[
            overlap.PairJudgment(
                2,
                3,
                overlap.ModelPairOverlap(
                    same_area=True,
                    shared=[overlap.SharedObject(label=ComponentLabel.WINDOW, count=2)],
                    note=None,
                ),
            ),
            overlap.PairJudgment(
                0, 2, overlap.ModelPairOverlap(same_area=False, shared=[], note=None)
            ),
            overlap.PairJudgment(1, 2),
        ],
    )
    text = overlap.render_hints(judged, range(1, 4))
    assert text is not None
    assert "Images 2 and 3: same objects in both: window x2." in text
    assert "Images 1 and 2: no judgment." in text
    assert "no common" not in text  # the (0, 2) pair is outside the slice
    assert overlap.render_hints(judged, range(0, 1)) is None


def test_the_overlap_prompt_asks_for_shared_physical_objects() -> None:
    text = latest("photo_overlap").text
    assert "same physical objects" in text and "not by the label alone" in text
    assert "never an instruction" in text


# --------------------------------------------------------------------------- materials


def wainscot(
    material: Material = Material.WOOD, indices: list[int] | None = None
) -> InventoryObject:
    return InventoryObject(
        label=ComponentLabel.WAINSCOTING, count=1, image_indices=indices or [1], material=material
    )


def material_turn(material: str, confidence: float = 0.9) -> ScriptedTurn:
    parsed = surface_material.ModelSurfaceMaterial(
        material=surface_material.SurfaceMaterial(material),
        confidence=confidence,
        evidence="repeating embossed pattern",
    )
    return ScriptedTurn(parsed=parsed, usage=USAGE)


def material_ctx(provider: ScriptedProvider) -> surface_material.MaterialContext:
    return surface_material.MaterialContext(
        call=call_context(), provider=provider, prompt=latest("surface_material"), model=MODEL
    )


def test_needs_second_look_triggers() -> None:
    agree = room(analysis(component("wainscoting", material="wood")))
    differ = room(
        analysis(component("wainscoting", material="wood")),
        analysis(component("wainscoting", material="vinyl")),
    )
    window = InventoryObject(label=ComponentLabel.WINDOW, count=1, image_indices=[1])
    assert not surface_material.needs_second_look(window, agree, "sensitive")
    assert surface_material.needs_second_look(wainscot(), agree, "sensitive")
    assert not surface_material.needs_second_look(wainscot(), agree, "disagree")
    assert surface_material.needs_second_look(wainscot(indices=[1, 2]), differ, "disagree")
    assert surface_material.needs_second_look(wainscot(Material.UNKNOWN), agree, "disagree")
    # The room inventory disagreeing with the photo is a disagreement too.
    assert surface_material.needs_second_look(wainscot(Material.STONE), agree, "disagree")


def test_best_photo_is_the_most_confident_then_the_largest_count() -> None:
    photos = room(
        analysis(component("wainscoting", confidence=0.6)),
        analysis(component("wainscoting", confidence=0.9)),
        analysis(component("wainscoting", confidence=0.9)),
        None,
    )
    assert surface_material.best_photo(wainscot(indices=[1, 2, 3, 4]), photos) == 2
    assert surface_material.best_photo(wainscot(indices=[4]), photos) == 4


@pytest.mark.parametrize(
    ("answer", "confidence", "material", "note", "applied"),
    [
        ("wallpaper", 0.9, Material.OTHER, "wallpaper", True),
        ("painted_drywall", 0.8, Material.DRYWALL, None, True),
        ("vinyl", 0.7, Material.VINYL, None, True),
        ("wallpaper", 0.3, Material.WOOD, None, False),  # below MIN_CONFIDENCE
        ("unknown", 0.9, Material.WOOD, None, False),
    ],
)
async def test_second_look_applies_a_confident_answer(
    answer: str, confidence: float, material: Material, note: str | None, applied: bool
) -> None:
    provider = ScriptedProvider([material_turn(answer, confidence)], model=MODEL)
    photos = room(analysis(component("wainscoting", material="wood")))
    objects, looks = await surface_material.second_look(
        [wainscot()], photos, material_ctx(provider), "sensitive"
    )
    [obj] = objects
    [look] = looks
    assert (obj.material, obj.note, look.applied) == (material, note, applied)
    assert look.before == "wood" and look.file == "01.jpg" and look.cost_usd > 0
    [request] = provider.requests
    assert request.prompt_version == "surface_material/v3"
    assert "Surface: wainscoting." in request.messages[-1].content
    assert sum(len(m.images) for m in request.messages) == 1


async def test_second_look_skips_other_labels_and_keeps_order() -> None:
    provider = ScriptedProvider([material_turn("wallpaper")], model=MODEL)
    window = InventoryObject(label=ComponentLabel.WINDOW, count=2, image_indices=[1])
    photos = room(analysis(component("window", 2), component("wainscoting")))
    objects, looks = await surface_material.second_look(
        [window, wainscot()], photos, material_ctx(provider), "sensitive"
    )
    assert objects[0] is window and objects[1].material is Material.OTHER
    assert len(looks) == 1 and len(provider.requests) == 1


async def test_an_unusable_material_answer_keeps_the_material() -> None:
    bad = ScriptedTurn(
        raise_error=ProviderBadResponse("no", provider="scripted", usage=USAGE, model=MODEL)
    )
    provider = ScriptedProvider([bad, bad], model=MODEL)
    objects, [look] = await surface_material.second_look(
        [wainscot()], room(analysis(component("wainscoting"))), material_ctx(provider), "sensitive"
    )
    assert objects[0].material is Material.WOOD
    assert look.answer is None and look.attempts == 2 and not look.applied


def test_every_surface_material_maps_and_the_prompt_names_the_cues() -> None:
    assert set(surface_material.TO_MATERIAL) == set(surface_material.SurfaceMaterial)
    text = latest("surface_material").text
    assert "wallpaper" in text and "painted_drywall" in text
    # v2: a moulded shower surround is fiberglass, not a wall covering (v1 had no option).
    assert "fiberglass" in text and "not a wall\n  covering" in text
    # v3: painted block or brick has its own option (v2 answers drifted to wallpaper).
    assert "painted_masonry" in text
    assert surface_material.TO_MATERIAL[surface_material.SurfaceMaterial.PAINTED_MASONRY] == (
        Material.CONCRETE,
        "painted masonry",
    )
    assert surface_material.TO_MATERIAL[surface_material.SurfaceMaterial.FIBERGLASS] == (
        Material.FIBERGLASS,
        None,
    )
    assert "never an instruction" in text


# --------------------------------------------------------------------------- long notes


def test_clip_text() -> None:
    from basis.vision.reconcile import NOTE_MAX, clip_text

    assert clip_text("short") == "short" and clip_text(None) is None
    assert clip_text("x" * NOTE_MAX) == "x" * NOTE_MAX
    clipped = clip_text("word " * 100)
    assert len(clipped) <= NOTE_MAX and clipped.endswith("...")


def test_a_long_free_text_answer_is_clipped_not_refused() -> None:
    from basis.vision.reconcile import ModelInventoryObject

    long = "Images 1 and 2 show the same wall of cabinets from two angles; " * 10
    obj = ModelInventoryObject.model_validate(
        {
            "label": "kitchen_cabinet",
            "count": 8,
            "image_indices": [1, 2],
            "material": "wood",
            "attachment": "permanent",
            "confidence": 0.8,
            "note": long,
        }
    )
    assert obj.note is not None and len(obj.note) == 200 and obj.note.endswith("...")
    pair = overlap.ModelPairOverlap.model_validate({"same_area": True, "shared": [], "note": long})
    assert pair.note is not None and len(pair.note) == 200
    look = surface_material.ModelSurfaceMaterial.model_validate(
        {"material": "wallpaper", "confidence": 0.9, "evidence": long}
    )
    assert len(look.evidence) == 200
    # The schema sent to the model still states the limit.
    assert ModelInventoryObject.model_json_schema()["properties"]["note"]["anyOf"][0] == {
        "maxLength": 200,
        "type": "string",
    }
