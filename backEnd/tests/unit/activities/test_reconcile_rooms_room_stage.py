"""``reconcile_rooms`` with the two measured-but-off options (EVAL_REPORT 26.10), on real rows
(Postgres container) and storage, with fake providers and a fake segmenter.

* ``ROOM_STEP_MODE=room_call``: interior rooms get one ``room_photos_inventory/v2`` call on
  Opus 5.5 at high effort, exterior rooms keep the reconcile path, an unusable answer falls
  back to it, the path and cost are recorded, and the budget guard and the account-error
  stop of PR 53 still apply.
* ``MATERIALS_SECOND_LOOK=surface_crops``: the crops' answer replaces the surface materials,
  and every failure (segmenter, unusable answer, provider outage, no segmenter) falls back to
  the per-photo material with a flag instead of failing the room; an account error still
  stops the activity.
* Regression: at the defaults the rows and the stored record carry exactly the fields they
  had before these options existed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from lifecycletest_support import SeededStudy, make_deps
from llm_fakes import PHOTO_COST_USD, FakeSegmenter, FixtureVisionProvider, ModelKeyedProvider
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.process_assets import ProcessAssets
from basis.activities.reconcile_rooms import ReconcileRooms, room_uri
from basis.db.models import Asset, Component
from basis.llm import ScriptedTurn, Usage
from basis.llm.errors import ProviderAccountError, ProviderBadResponse, ProviderUnavailable
from basis.llm.provider import ChatRequest, ChatResponse
from basis.vision.surface_crops import CropAnswer, CropMaterial, LowerFinish
from basis.workflow.activity_options import PROVIDER_ACCOUNT_ERROR
from basis.workflow.contracts import AnalyzePhotoInput

from .conftest import Env
from .test_reconcile_rooms import (
    assets_input,
    hall_inventory,
    inventory,
    llm_calls,
    place_hall,
    reconcile,
    rooms_by_name,
    rows,
)

pytestmark = pytest.mark.integration

# Production defaults (EVAL_REPORT 31): luna reconciles, Sonnet does the material second look.
RECONCILER = "gpt-6-luna"
LOOK_MODEL = "claude-sonnet-5-5"
CALL_MODEL = "claude-opus-5-5"
CROP_MODEL = "claude-sonnet-5-5"
USAGE = Usage(input_tokens=1000, output_tokens=200)
# The fields a room inventory row's raw had before ROOM_STEP_MODE and MATERIALS_SECOND_LOOK
# (basis/activities/reconcile_rooms.py at 35db766), "second_look" only on a looked-at row.
RAW_BEFORE = {
    "source",
    "count",
    "note",
    "material",
    "attachment",
    "photos",
    "seen_in",
    "step",
    "prompt_version",
    "materials_prompt_version",
    "model",
    "room_needs_review",
    "room_review_reason",
    "record_uri",
}
RECORD_BEFORE = {
    "room_id",
    "revision",
    "photos",
    "step",
    "prompt_version",
    "materials",
    "materials_prompt_version",
    "model",
    "reasoning_effort",
    "needs_review",
    "review_reason",
    "objects",
    "dropped",
    "calls",
    "second_looks",
}


def stage_deps(deps: Deps, llm: Any, *, segmenter: Any = None, **changes: Any) -> Deps:
    settings = deps.settings.model_copy(update={"room_reconcile": True, **changes})
    return make_deps(deps.sessionmaker, deps.storage, settings, llm=llm, segmenter=segmenter)


def room_call(deps: Deps, llm: Any, **changes: Any) -> Deps:
    return stage_deps(deps, llm, room_step_mode="room_call", **changes)


def crops_on(deps: Deps, llm: Any, segmenter: Any, **changes: Any) -> Deps:
    return stage_deps(
        deps,
        llm,
        segmenter=segmenter,
        materials_second_look="surface_crops",
        room_materials="none",
        **changes,
    )


def crop(material: CropMaterial, lower: LowerFinish = LowerFinish.NONE) -> ScriptedTurn:
    return ScriptedTurn(
        parsed=CropAnswer(material=material, lower_finish=lower, confidence=0.9, evidence="x"),
        usage=USAGE,
    )


def wall_turns() -> list[ScriptedTurn]:
    """Two hall photos, three surfaces each (floor, wall, ceiling): the wall's lower band is
    wallpaper."""
    one = [
        crop(CropMaterial.HARDWOOD),
        crop(CropMaterial.PAINTED_DRYWALL, LowerFinish.WALLPAPER),
        crop(CropMaterial.PAINTED_DRYWALL),
    ]
    return one + [
        crop(CropMaterial.HARDWOOD),
        crop(CropMaterial.PAINTED_DRYWALL, LowerFinish.WALLPAPER),
        crop(CropMaterial.PAINTED_DRYWALL),
    ]


async def record_of(step: Deps, seeded: SeededStudy, room_name: str) -> dict[str, Any]:
    room = (await rooms_by_name(step, seeded.study_id))[room_name]
    return json.loads(await step.storage.read(room_uri(step.bucket, seeded.study_id, room.id, 1)))


@pytest.fixture
async def revision_one(deps: Deps, seeded: SeededStudy, activity_env: Env) -> SeededStudy:
    """The seeded photos analyzed (as ``test_reconcile_rooms``): a two-photo Kitchen and a
    one-photo Bathroom."""
    for photo in seeded.photo_ids:
        await activity_env.run(
            AnalyzePhoto(deps).run,
            AnalyzePhotoInput(
                study_id=str(seeded.study_id),
                document_id=str(photo),
                revision=1,
                idempotency_key=f"k-p-{photo}",
                budget_remaining_usd=1.0,
            ),
        )
    return seeded


# --------------------------------------------------------------------------- room call


async def test_room_call_mode_counts_each_interior_room_in_one_call(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    fixture = FixtureVisionProvider()
    step = room_call(deps, fixture)
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    assert (output.built, output.room_calls, output.room_call_fallbacks) == (2, 2, 0)
    assert (output.surface_crop_rooms, output.surface_crop_fallbacks) == (0, 0)
    # Kitchen (two photos) and Bathroom (one photo: the room call too, as 26.4 measured).
    assert len(fixture.requests) == 2
    for request in fixture.requests:
        assert request.prompt_version == "room_photos_inventory/v2"
        assert (request.model, request.reasoning_effort, request.max_tokens) == (
            CALL_MODEL,
            "high",
            6000,
        )
    assert sorted(sum(1 for m in r.messages if m.images) for r in fixture.requests) == [1, 2]
    assert output.cost_usd == pytest.approx(2 * PHOTO_COST_USD)
    assert await llm_calls(step, sid) == 2
    rows_now = await inventory(step, sid)
    assert {c.raw["step"] for c in rows_now} == {"room_call"}
    assert {c.raw["path"] for c in rows_now} == {"room_call"}
    assert {c.raw["room_call_fallback"] for c in rows_now} == {None}
    assert {c.raw["prompt_version"] for c in rows_now} == {"room_photos_inventory/v2"}
    assert {c.raw["materials_prompt_version"] for c in rows_now} == {None}
    cabinet = next(c for c in rows_now if c.label == "kitchen_cabinet")
    assert cabinet.raw["count"] == 6 and len(cabinet.raw["seen_in"]) == 2
    record = await record_of(step, revision_one, "Kitchen")
    assert (record["path"], record["room_call_fallback"]) == ("room_call", None)
    assert (record["room_call_model"], record["room_call_reasoning_effort"]) == (
        CALL_MODEL,
        "high",
    )
    assert record["room_call_prompt_version"] == "room_photos_inventory/v2"
    assert record["cost_usd"] == pytest.approx(PHOTO_COST_USD)
    assert record["calls"][0]["response"]["objects"]
    # The assets are built from the room call's rows.
    await activity_env.run(ProcessAssets(step).run, assets_input(revision_one))
    components = {c.id: c for c in await rows(step, Component, sid)}
    used = [components[a.component_id] for a in await rows(step, Asset, sid)]
    assert used and all(c.raw["step"] == "room_call" for c in used)


async def test_exterior_rooms_keep_the_reconcile_path_in_room_call_mode(
    deps: Deps, sessionmaker: Any, storage: Any, activity_env: Env
) -> None:
    from lifecycletest_support import seed_run, seed_study

    seeded = await seed_study(
        sessionmaker,
        storage=storage,
        photos=[
            "21_1st Floor Kitchen photo 1.jpg",
            "22_1st Floor Kitchen photo 2.jpg",
            "05_Exterior photo 1.jpg",
            "06_Exterior photo 2.jpg",
        ],
    )
    await seed_run(sessionmaker, seeded.study_id)
    for photo in seeded.photo_ids:
        await activity_env.run(
            AnalyzePhoto(deps).run,
            AnalyzePhotoInput(
                study_id=str(seeded.study_id),
                document_id=str(photo),
                revision=1,
                idempotency_key=f"k-p-{photo}",
                budget_remaining_usd=1.0,
            ),
        )
    fixture = FixtureVisionProvider()
    step = room_call(deps, fixture)
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    families = sorted(r.prompt_version for r in fixture.requests)
    assert families == ["room_inventory/v3", "room_photos_inventory/v2"]
    assert (output.room_calls, output.room_call_fallbacks) == (1, 0)
    rooms = await rooms_by_name(step, seeded.study_id)
    assert {r.group_key: r.room_type for r in rooms.values()} == {
        "1_kitchen": "kitchen",
        "x_exterior": "exterior",
    }
    by_room = {
        next(n for n, r in rooms.items() if r.id == c.room_id): c.raw
        for c in await inventory(step, seeded.study_id)
    }
    kitchen, exterior_raw = by_room["Kitchen"], by_room["Exterior"]
    assert (kitchen["path"], kitchen["step"]) == ("room_call", "room_call")
    assert (exterior_raw["path"], exterior_raw["step"]) == ("reconcile", "model")
    assert exterior_raw["prompt_version"] == "room_inventory/v3"
    exterior = await record_of(step, seeded, "Exterior")
    assert (exterior["path"], exterior["room_call_fallback"]) == ("reconcile", None)


class _BadRoomCalls:
    """The fixture provider, except that every room call is unusable."""

    name = "bad-room-calls"

    def __init__(self) -> None:
        self.fixture = FixtureVisionProvider()
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if request.prompt_version.startswith("room_photos_inventory/"):
            raise ProviderBadResponse("no", provider="x", usage=USAGE, model=CALL_MODEL)
        return await self.fixture.complete(request)


async def test_an_unusable_room_call_falls_back_to_the_reconcile_path(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    llm = _BadRoomCalls()
    step = room_call(deps, llm)
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    assert (output.built, output.room_calls, output.room_call_fallbacks) == (2, 0, 2)
    # Each room call tried twice; then the Kitchen's reconciliation call (the Bathroom is one
    # photo: its analysis, no call).
    assert [r.prompt_version.split("/")[0] for r in llm.requests].count(
        "room_photos_inventory"
    ) == 4
    assert [r.prompt_version for r in llm.requests].count("room_inventory/v3") == 1
    rows_now = await inventory(step, revision_one.study_id)
    assert {c.raw["path"] for c in rows_now} == {"room_call"}
    assert {c.raw["room_call_fallback"] for c in rows_now} == {"invalid_model_output"}
    assert {c.raw["step"] for c in rows_now} == {"model", "per_photo"}
    record = await record_of(step, revision_one, "Kitchen")
    assert record["room_call_fallback"] == "invalid_model_output"
    failed = record["calls"][0]
    assert failed["attempts"] == 2 and failed["cost_usd"] > 0 and failed["response"] is None


async def test_an_account_error_in_a_room_call_stops_the_activity(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    refused = ScriptedTurn(
        raise_error=ProviderAccountError(
            "Your credit balance is too low", provider="anthropic", status=400
        )
    )
    llm = ModelKeyedProvider(turns={(CALL_MODEL, "room_photos_inventory"): [refused, refused]})
    step = room_call(deps, llm)
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    assert raised.value.non_retryable and raised.value.type == PROVIDER_ACCOUNT_ERROR
    assert await inventory(step, revision_one.study_id) == []


async def test_an_exhausted_budget_stops_the_room_call(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    fixture = FixtureVisionProvider()
    step = room_call(deps, fixture)
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one, budget=0.0))
    assert raised.value.non_retryable and raised.value.type == "BudgetExceededError"
    assert fixture.requests == []


# --------------------------------------------------------------------------- surface crops


async def test_surface_crops_set_the_rooms_surface_materials(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await place_hall(deps, seeded)
    llm = ModelKeyedProvider(
        turns={
            (RECONCILER, "room_inventory"): [hall_inventory()],
            (CROP_MODEL, "surface_crop_material"): wall_turns(),
        }
    )
    segmenter = FakeSegmenter()
    step = crops_on(deps, llm, segmenter)
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    assert (output.surface_crop_rooms, output.surface_crop_fallbacks) == (1, 0)
    assert segmenter.calls == 2
    crops_asked = [r for r in llm.requests if r.prompt_version == "surface_crop_material/v1"]
    assert len(crops_asked) == 6
    assert {(r.model, r.reasoning_effort) for r in crops_asked} == {(CROP_MODEL, "low")}
    assert await llm_calls(step, seeded.study_id) == 7
    rows_now = {c.label: c for c in await inventory(step, seeded.study_id)}
    wainscot, light = rows_now["wainscoting"], rows_now["light_fixture"]
    assert wainscot.material == "wallpaper" and wainscot.raw["material_before_crops"] == "wood"
    assert light.material == "metal" and "material_before_crops" not in light.raw
    for row in rows_now.values():
        assert row.raw["materials_second_look"] == "surface_crops"
        assert row.raw["surface_crops_flags"] == []
        assert "path" not in row.raw  # the room call is off
    record = await record_of(step, seeded, "Hallway")
    looked = record["surface_crops"]
    assert looked["segmenter"] == "fake-segmenter" and looked["flags"] == []
    [change] = looked["changes"]
    assert (change["label"], change["before"], change["after"], change["class"]) == (
        "wainscoting",
        "wood",
        "wallpaper",
        "wallpaper",
    )
    assert [len(p["crops"]) for p in looked["photos"]] == [3, 3]
    assert record["cost_usd"] == pytest.approx(output.cost_usd)


async def test_a_segmenter_failure_keeps_the_per_photo_materials_flagged(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await place_hall(deps, seeded)
    llm = ModelKeyedProvider(turns={(RECONCILER, "room_inventory"): [hall_inventory()]})
    step = crops_on(deps, llm, FakeSegmenter(fail=True))
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    assert (output.built, output.surface_crop_rooms, output.surface_crop_fallbacks) == (1, 1, 1)
    rows_now = {c.label: c for c in await inventory(step, seeded.study_id)}
    assert rows_now["wainscoting"].material == "wood"
    assert {tuple(c.raw["surface_crops_flags"]) for c in rows_now.values()} == {("segmenter",)}
    assert len(llm.requests) == 1  # no crop was asked


async def test_no_segmenter_keeps_the_per_photo_materials_flagged(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await place_hall(deps, seeded)
    llm = ModelKeyedProvider(turns={(RECONCILER, "room_inventory"): [hall_inventory()]})
    step = crops_on(deps, llm, None)
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    assert output.surface_crop_fallbacks == 1
    [flags] = {tuple(c.raw["surface_crops_flags"]) for c in await inventory(step, seeded.study_id)}
    assert flags == ("segmenter",)


@pytest.mark.parametrize(
    ("failure", "flag"),
    [
        (
            lambda: ScriptedTurn(
                raise_error=ProviderBadResponse("no", provider="x", usage=USAGE, model=CROP_MODEL)
            ),
            "invalid_model_output",
        ),
        (
            lambda: ScriptedTurn(raise_error=ProviderUnavailable("down", provider="x", status=503)),
            "provider_unavailable",
        ),
    ],
)
async def test_a_failed_crop_falls_back_without_failing_the_room(
    deps: Deps, seeded: SeededStudy, activity_env: Env, failure: Any, flag: str
) -> None:
    """The first photo's wall crop fails (twice when unusable, once when the provider is
    down); the second photo's wall crop still answers, so the wainscot is set from it."""
    await place_hall(deps, seeded)
    turns = wall_turns()
    retries = 2 if flag == "invalid_model_output" else 1
    turns[1:2] = [failure() for _ in range(retries)]
    llm = ModelKeyedProvider(
        turns={
            (RECONCILER, "room_inventory"): [hall_inventory()],
            (CROP_MODEL, "surface_crop_material"): turns,
        }
    )
    step = crops_on(deps, llm, FakeSegmenter())
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    assert (output.built, output.surface_crop_fallbacks) == (1, 1)
    rows_now = {c.label: c for c in await inventory(step, seeded.study_id)}
    assert rows_now["wainscoting"].material == "wallpaper"
    assert rows_now["wainscoting"].raw["surface_crops_flags"] == [flag]


async def test_an_account_error_in_a_crop_stops_the_activity(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await place_hall(deps, seeded)
    refused = ScriptedTurn(
        raise_error=ProviderAccountError("invalid x-api-key", provider="anthropic", status=401)
    )
    llm = ModelKeyedProvider(
        turns={
            (RECONCILER, "room_inventory"): [hall_inventory()],
            (CROP_MODEL, "surface_crop_material"): [refused],
        }
    )
    step = crops_on(deps, llm, FakeSegmenter())
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    assert raised.value.non_retryable and raised.value.type == PROVIDER_ACCOUNT_ERROR
    assert await inventory(step, seeded.study_id) == []


async def test_room_call_and_crops_together(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await place_hall(deps, seeded)
    llm = ModelKeyedProvider(
        turns={
            (CALL_MODEL, "room_photos_inventory"): [hall_inventory()],
            (CROP_MODEL, "surface_crop_material"): wall_turns(),
        }
    )
    step = crops_on(deps, llm, FakeSegmenter(), room_step_mode="room_call")
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    assert (output.room_calls, output.surface_crop_rooms) == (1, 1)
    rows_now = {c.label: c for c in await inventory(step, seeded.study_id)}
    assert rows_now["wainscoting"].material == "wallpaper"
    assert rows_now["wainscoting"].raw["step"] == "room_call"
    record = await record_of(step, seeded, "Hallway")
    assert record["cost_usd"] == pytest.approx(output.cost_usd)


# --------------------------------------------------------------------------- regression


async def test_at_the_defaults_rows_and_records_carry_only_the_fields_they_had(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    """Regression: ROOM_STEP_MODE=reconcile and MATERIALS_SECOND_LOOK=per_photo write
    exactly the row and record fields of the room step before them."""
    step = stage_deps(deps, FixtureVisionProvider())
    assert (step.settings.room_step_mode, step.settings.materials_second_look) == (
        "reconcile",
        "per_photo",
    )
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    assert (output.room_calls, output.room_call_fallbacks) == (0, 0)
    assert (output.surface_crop_rooms, output.surface_crop_fallbacks) == (0, 0)
    for row in await inventory(step, revision_one.study_id):
        assert set(row.raw) - {"second_look"} == RAW_BEFORE
    for name in ("Kitchen", "Bathroom"):
        assert set(await record_of(step, revision_one, name)) == RECORD_BEFORE


# --------------------------------------------------------------------------- second-look model


async def test_the_reconciler_and_the_second_look_can_run_on_different_models(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    """EVAL_REPORT 28.2's recommendation, set by settings: the room reconciled on gpt-6-luna,
    the material second look left on its default, Sonnet 5.5 at low effort."""
    from basis.vision.surface_material import ModelSurfaceMaterial, SurfaceMaterial

    await place_hall(deps, seeded)
    llm = ModelKeyedProvider(
        turns={
            ("gpt-6-luna", "room_inventory"): [hall_inventory()],
            (LOOK_MODEL, "surface_material"): [
                ScriptedTurn(
                    parsed=ModelSurfaceMaterial(
                        material=SurfaceMaterial.WALLPAPER, confidence=0.9, evidence="seams"
                    ),
                    usage=USAGE,
                )
            ],
        }
    )
    step = stage_deps(deps, llm, room_model="gpt-6-luna", room_reasoning_effort="low")
    assert (step.settings.room_materials_model, step.settings.room_materials_reasoning_effort) == (
        LOOK_MODEL,
        "low",
    )
    await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    assert [(r.model, r.prompt_version, r.reasoning_effort) for r in llm.requests] == [
        ("gpt-6-luna", "room_inventory/v3", "low"),
        (LOOK_MODEL, "surface_material/v3", "low"),
    ]
    rows_now = {c.label: c for c in await inventory(step, seeded.study_id)}
    wainscot = rows_now["wainscoting"]
    assert wainscot.material == "wallpaper"
    assert wainscot.raw["model"] == "claude-sonnet-5-5+gpt-6-luna"
    assert [look["model"] for look in wainscot.raw["second_look"]] == [LOOK_MODEL]
    assert set(wainscot.raw) - {"second_look"} == RAW_BEFORE  # no new row fields
