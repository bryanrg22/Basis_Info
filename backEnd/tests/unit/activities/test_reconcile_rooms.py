"""``reconcile_rooms``: the room step on real rows (Postgres container) and storage.

The ``revision_one`` fixture analyzes the seeded photos with the offline fixture provider:
``kitchen_1.jpg`` and ``kitchen_2.jpg`` are one Kitchen (two photos, six cabinets each),
``bathroom_1.jpg`` a one-photo Bathroom. The room step then runs at its production
settings: the Kitchen gets one ``room_inventory/v3`` call (six cabinets, not twelve), the
Bathroom its photo's analysis without a call; ``process_assets`` builds the Kitchen's assets
from the inventory. Also: idempotency per room, stale inventories, the second look,
failures, carry-over across a rooms rejection, and the step switched off.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fixtures import fixture_path
from lifecycletest_support import SeededStudy, make_deps, seed_run, seed_study
from llm_fakes import PHOTO_COST_USD, FixtureVisionProvider, ModelKeyedProvider
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.process_assets import ProcessAssets
from basis.activities.reconcile_rooms import ReconcileRooms, plan_rooms, room_uri, vote_room
from basis.db.models import ActivityResult, Asset, Component, Document, LlmCall, PhotoRoom, Room
from basis.db.repositories.activity_result import COMPLETED
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.llm import ScriptedTurn, Usage
from basis.llm.errors import ProviderBadResponse, ProviderUnavailable
from basis.vision.reconcile import ModelInventoryObject, ModelRoomInventory
from basis.vision.rooms import ROOM_INVENTORY_SOURCE, assign_room, write_photo
from basis.vision.schemas import (
    Attachment,
    ComponentLabel,
    Material,
    PhotoAnalysis,
    RoomType,
)
from basis.vision.schemas import (
    Component as VisionComponent,
)
from basis.vision.surface_material import ModelSurfaceMaterial, SurfaceMaterial
from basis.workflow.contracts import (
    AnalyzePhotoInput,
    ApplyCorrectionsInput,
    CorrectionInput,
    ProcessAssetsInput,
    ReconcileRoomsInput,
)

from .conftest import Env

pytestmark = pytest.mark.integration

MODEL = "claude-sonnet-5-5"
USAGE = Usage(input_tokens=1000, output_tokens=200)


def room_settings(deps: Deps, **changes: Any) -> Deps:
    """``deps`` with the room step's production settings (the suite pins it off)."""
    settings = deps.settings.model_copy(update={"room_reconcile": True, **changes})
    return make_deps(deps.sessionmaker, deps.storage, settings, llm=deps.llm)


def with_llm(deps: Deps, llm: Any) -> Deps:
    return make_deps(deps.sessionmaker, deps.storage, deps.settings, llm=llm)


def reconcile(
    seeded: SeededStudy, revision: int = 1, key: str = "k-rooms", budget: float = 5.0
) -> ReconcileRoomsInput:
    return ReconcileRoomsInput(
        study_id=str(seeded.study_id),
        revision=revision,
        idempotency_key=f"{key}-r{revision}",
        budget_remaining_usd=budget,
    )


@pytest.fixture
async def revision_one(deps: Deps, seeded: SeededStudy, activity_env: Env) -> SeededStudy:
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


async def rows(deps: Deps, model: Any, study_id: uuid.UUID, revision: int = 1) -> list[Any]:
    async with session_scope(deps.sessionmaker) as session:
        stmt = select(model).where(model.study_id == study_id, model.revision == revision)
        return list((await session.scalars(stmt)).all())


async def rooms_by_name(deps: Deps, study_id: uuid.UUID, revision: int = 1) -> dict[str, Room]:
    return {r.name: r for r in await rows(deps, Room, study_id, revision)}


async def inventory(deps: Deps, study_id: uuid.UUID, revision: int = 1) -> list[Component]:
    return [
        c
        for c in await rows(deps, Component, study_id, revision)
        if c.document_id is None and (c.raw or {}).get("source") == ROOM_INVENTORY_SOURCE
    ]


async def llm_calls(deps: Deps, study_id: uuid.UUID, activity: str = "reconcile_rooms") -> int:
    async with session_scope(deps.sessionmaker) as session:
        stmt = select(LlmCall).where(LlmCall.study_id == study_id, LlmCall.activity == activity)
        return len((await session.scalars(stmt)).all())


def assets_input(seeded: SeededStudy, revision: int = 1) -> ProcessAssetsInput:
    return ProcessAssetsInput(
        study_id=str(seeded.study_id),
        revision=revision,
        idempotency_key=f"k-assets-{revision}",
        budget_remaining_usd=1.0,
    )


# --------------------------------------------------------------------------- the step


async def test_the_production_room_step_counts_the_kitchen_once(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    fixture = FixtureVisionProvider()
    step = with_llm(room_settings(deps), fixture)
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    assert (output.enabled, output.rooms, output.built, output.current) == (True, 2, 2, 0)
    assert output.needs_review == 0
    # One call (the Kitchen's two photos); the Bathroom has one photo: no call.
    [request] = fixture.requests
    assert request.prompt_version == "room_inventory/v3"
    assert (request.model, request.reasoning_effort) == (MODEL, "low")
    assert sum(1 for m in request.messages if m.images) == 2
    assert output.cost_usd == pytest.approx(PHOTO_COST_USD)
    assert await llm_calls(step, sid) == 1
    rooms = await rooms_by_name(step, sid)
    by_room: dict[str, dict[str, Component]] = {}
    for row in await inventory(step, sid):
        name = next(n for n, r in rooms.items() if r.id == row.room_id)
        by_room.setdefault(name, {})[row.label] = row
    assert {n: {k: v.raw["count"] for k, v in labels.items()} for n, labels in by_room.items()} == {
        "Kitchen": {"kitchen_cabinet": 6, "countertop": 1},
        "Bathroom": {"bathtub": 1, "toilet": 1},
    }
    cabinet = by_room["Kitchen"]["kitchen_cabinet"]
    photos = sorted(str(p) for p in revision_one.photo_ids[:2])
    assert sorted(cabinet.raw["photos"]) == photos and sorted(cabinet.raw["seen_in"]) == photos
    assert cabinet.raw["step"] == "model" and cabinet.raw["prompt_version"] == "room_inventory/v3"
    assert cabinet.raw["materials_prompt_version"] == "surface_material/v3"
    assert cabinet.attachment == "permanent" and cabinet.material is None
    bath = by_room["Bathroom"]["toilet"]
    assert bath.raw["step"] == "per_photo" and bath.raw["prompt_version"] is None
    record = json.loads(await step.storage.read(room_uri(step.bucket, sid, rooms["Kitchen"].id, 1)))
    assert record["step"] == "model" and len(record["calls"]) == 1
    assert record["calls"][0]["raw"] and record["calls"][0]["response"]["objects"]


async def test_assets_are_built_from_the_inventory(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    step = with_llm(room_settings(deps), FixtureVisionProvider())
    await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    output = await activity_env.run(ProcessAssets(step).run, assets_input(revision_one))
    assets = await rows(step, Asset, sid)
    components = {c.id: c for c in await rows(step, Component, sid)}
    used = [components[a.component_id] for a in assets]
    assert output.assets == len(assets) == 4
    assert all(c.document_id is None for c in used)
    assert sorted(c.label for c in used) == ["bathtub", "countertop", "kitchen_cabinet", "toilet"]


async def test_room_step_off_is_the_previous_behaviour(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    """Regression: ROOM_RECONCILE=false writes nothing and the assets are per photo."""
    sid = revision_one.study_id
    fixture = FixtureVisionProvider()
    off = with_llm(deps, fixture)
    assert off.settings.room_reconcile is False
    output = await activity_env.run(ReconcileRooms(off).run, reconcile(revision_one))
    assert output.enabled is False and output.built == 0 and not fixture.requests
    async with session_scope(off.sessionmaker) as session:
        [result] = (
            await session.scalars(
                select(ActivityResult).where(ActivityResult.idempotency_key == "k-rooms-r1")
            )
        ).all()
    assert result.status == COMPLETED  # every started activity records its key
    assert await inventory(off, sid) == []
    await activity_env.run(ProcessAssets(off).run, assets_input(revision_one))
    assets = await rows(off, Asset, sid)
    components = {c.id: c for c in await rows(off, Component, sid)}
    assert len(assets) == 6  # two kitchen photos x two labels + the bathroom's two
    assert all(components[a.component_id].document_id is not None for a in assets)


async def test_a_current_room_is_not_rebuilt(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    fixture = FixtureVisionProvider()
    step = with_llm(room_settings(deps), fixture)
    await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    again = await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one, key="k-2"))
    assert (again.built, again.current, again.cost_usd) == (0, 2, 0.0)
    assert len(fixture.requests) == 1
    assert len(await inventory(step, revision_one.study_id)) == 4


async def test_a_room_whose_photos_changed_is_rebuilt(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    sid = seeded.study_id
    fixture = FixtureVisionProvider()
    step = with_llm(room_settings(deps), fixture)
    first = seeded.photo_ids[0]
    await activity_env.run(
        AnalyzePhoto(deps).run,
        AnalyzePhotoInput(
            study_id=str(sid),
            document_id=str(first),
            revision=1,
            idempotency_key="k-first",
            budget_remaining_usd=1.0,
        ),
    )
    await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    [kitchen_before] = [c for c in await inventory(step, sid) if c.label == "kitchen_cabinet"]
    assert (
        kitchen_before.raw["photos"] == [str(first)] and kitchen_before.raw["step"] == "per_photo"
    )
    # The second kitchen photo joins the room.
    await activity_env.run(
        AnalyzePhoto(deps).run,
        AnalyzePhotoInput(
            study_id=str(sid),
            document_id=str(seeded.photo_ids[1]),
            revision=1,
            idempotency_key="k-second",
            budget_remaining_usd=1.0,
        ),
    )
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(seeded, key="k-again"))
    assert (output.built, output.current) == (1, 0)
    rows_now = [c for c in await inventory(step, sid) if c.label == "kitchen_cabinet"]
    assert len(rows_now) == 1 and rows_now[0].id != kitchen_before.id
    assert len(rows_now[0].raw["photos"]) == 2 and rows_now[0].raw["step"] == "model"


async def test_one_photo_rooms_get_the_call_when_asked(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    fixture = FixtureVisionProvider()
    step = with_llm(room_settings(deps, room_single_photo="reconcile"), fixture)
    await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    assert len(fixture.requests) == 2
    assert {c.raw["step"] for c in await inventory(step, revision_one.study_id)} == {"model"}


async def test_the_unassigned_room_and_empty_rooms_are_skipped(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    sid = seeded.study_id
    async with session_scope(deps.sessionmaker) as session:
        for index, analysis in enumerate(
            [
                PhotoAnalysis(room_type=RoomType.AMBIGUOUS, room_confidence=0.1),
                PhotoAnalysis(room_type=RoomType.GARAGE, room_confidence=0.9),
            ]
        ):
            room_id, _ = await assign_room(session, study_id=sid, revision=1, analysis=analysis)
            await write_photo(
                session,
                study_id=sid,
                document_id=seeded.photo_ids[index],
                revision=1,
                room_id=room_id,
                analysis=analysis,
                context={},
            )
    fixture = FixtureVisionProvider()
    step = with_llm(room_settings(deps), fixture)
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    assert (output.rooms, output.built) == (0, 0) and not fixture.requests


# --------------------------------------------------------------------------- second look


def comp(label: str, count: int, material: str) -> VisionComponent:
    return VisionComponent.model_validate(
        {
            "label": label,
            "count": count,
            "material": material,
            "attachment": "permanent",
            "confidence": 0.9,
        }
    )


async def place_hall(deps: Deps, seeded: SeededStudy) -> None:
    """Two photos of a hall with a wainscot every call said was wood."""
    sid = seeded.study_id
    analysis = PhotoAnalysis(
        room_type=RoomType.HALLWAY,
        room_confidence=0.9,
        components=[comp("wainscoting", 1, "wood"), comp("light_fixture", 1, "metal")],
    )
    async with session_scope(deps.sessionmaker) as session:
        for document_id in seeded.photo_ids[:2]:
            room_id, _ = await assign_room(session, study_id=sid, revision=1, analysis=analysis)
            await write_photo(
                session,
                study_id=sid,
                document_id=document_id,
                revision=1,
                room_id=room_id,
                analysis=analysis,
                context={"room_confidence": 0.9},
            )


def hall_inventory() -> ScriptedTurn:
    return ScriptedTurn(
        parsed=ModelRoomInventory(
            objects=[
                ModelInventoryObject(
                    label=ComponentLabel.WAINSCOTING,
                    count=1,
                    image_indices=[1, 2],
                    material=Material.WOOD,
                    attachment=Attachment.PERMANENT,
                    confidence=0.9,
                    note=None,
                ),
                ModelInventoryObject(
                    label=ComponentLabel.LIGHT_FIXTURE,
                    count=1,
                    image_indices=[1, 2],
                    material=Material.METAL,
                    attachment=Attachment.PERMANENT,
                    confidence=0.9,
                    note=None,
                ),
            ]
        ),
        usage=USAGE,
    )


async def test_the_second_look_corrects_a_wallpaper_wainscot(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await place_hall(deps, seeded)
    chat = ModelKeyedProvider(
        turns={
            (MODEL, "room_inventory"): [hall_inventory()],
            (MODEL, "surface_material"): [
                ScriptedTurn(
                    parsed=ModelSurfaceMaterial(
                        material=SurfaceMaterial.WALLPAPER, confidence=0.9, evidence="seams"
                    ),
                    usage=USAGE,
                )
            ],
        }
    )
    step = with_llm(room_settings(deps), chat)
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    assert output.built == 1
    rows_now = {c.label: c for c in await inventory(step, seeded.study_id)}
    wainscot = rows_now["wainscoting"]
    assert wainscot.material == "wallpaper"  # a component material of its own now
    [look] = wainscot.raw["second_look"]
    assert (look["before"], look["applied"], look["answer"]["material"]) == (
        "wood",
        True,
        "wallpaper",
    )
    assert "second_look" not in rows_now["light_fixture"].raw
    assert rows_now["light_fixture"].material == "metal"


async def test_materials_none_sends_no_second_look(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await place_hall(deps, seeded)
    chat = ModelKeyedProvider(turns={(MODEL, "room_inventory"): [hall_inventory()]})
    step = with_llm(room_settings(deps, room_materials="none"), chat)
    await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    rows_now = {c.label: c for c in await inventory(step, seeded.study_id)}
    assert rows_now["wainscoting"].material == "wood"
    assert rows_now["wainscoting"].raw["materials_prompt_version"] is None


# --------------------------------------------------------------------------- failures


async def test_an_unusable_answer_is_the_heuristic_merge_flagged(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await place_hall(deps, seeded)
    bad = ScriptedTurn(
        raise_error=ProviderBadResponse("no", provider="x", usage=USAGE, model=MODEL)
    )
    chat = ModelKeyedProvider(
        turns={(MODEL, "room_inventory"): [bad, bad]},
    )
    step = with_llm(room_settings(deps, room_materials="none"), chat)
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(seeded))
    assert (output.built, output.needs_review) == (1, 1)
    rows_now = await inventory(step, seeded.study_id)
    assert {c.raw["step"] for c in rows_now} == {"heuristic"}
    assert {c.raw["room_review_reason"] for c in rows_now} == {"invalid_model_output"}


async def test_a_transient_error_is_retryable_and_the_retry_finishes(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await place_hall(deps, seeded)
    down = ScriptedTurn(raise_error=ProviderUnavailable("down", provider="x", status=503))
    failing = with_llm(
        room_settings(deps, room_materials="none"),
        ModelKeyedProvider(turns={(MODEL, "room_inventory"): [down]}),
    )
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(ReconcileRooms(failing).run, reconcile(seeded))
    assert not raised.value.non_retryable
    assert await inventory(failing, seeded.study_id) == []
    working = with_llm(
        failing, ModelKeyedProvider(turns={(MODEL, "room_inventory"): [hall_inventory()]})
    )
    output = await activity_env.run(ReconcileRooms(working).run, reconcile(seeded))
    assert output.built == 1 and len(await inventory(working, seeded.study_id)) == 2


async def test_an_exhausted_budget_is_not_retried(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    step = with_llm(room_settings(deps), FixtureVisionProvider())
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one, budget=0.0))
    assert raised.value.non_retryable and raised.value.type == "BudgetExceededError"


async def test_no_provider_is_not_retried(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    step = with_llm(room_settings(deps), None)
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    assert raised.value.non_retryable and raised.value.type == "ProviderNotConfigured"


# --------------------------------------------------------------------------- carry-over


async def test_a_rooms_rejection_rebuilds_only_the_rooms_of_flagged_photos(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    step = with_llm(room_settings(deps), FixtureVisionProvider())
    await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    flagged = revision_one.photo_ids[0]  # a kitchen photo
    await activity_env.run(
        ApplyCorrections(step).run,
        ApplyCorrectionsInput(
            study_id=str(sid),
            stage="rooms_review",
            from_revision=1,
            to_revision=2,
            corrections=(CorrectionInput(entity_type="photo", entity_id=str(flagged), after={}),),
        ),
    )
    carried = await inventory(step, sid, 2)
    rooms = {r.id: r.name for r in await rows(step, Room, sid, 2)}
    assert {rooms[c.room_id] for c in carried} == {"Bathroom"}  # the Kitchen's is not carried
    # The flagged photo is re-analyzed into revision 2, then the room step rebuilds the Kitchen.
    await activity_env.run(
        AnalyzePhoto(step).run,
        AnalyzePhotoInput(
            study_id=str(sid),
            document_id=str(flagged),
            revision=2,
            idempotency_key="k-again-r2",
            budget_remaining_usd=1.0,
        ),
    )
    output = await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one, revision=2))
    assert (output.built, output.current) == (1, 1)
    kitchen = [c for c in await inventory(step, sid, 2) if c.label == "kitchen_cabinet"]
    assert len(kitchen) == 1 and len(kitchen[0].raw["photos"]) == 2
    assert kitchen[0].raw["count"] == 6


async def test_an_appraisal_rejection_carries_every_inventory(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    step = with_llm(room_settings(deps), FixtureVisionProvider())
    await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    await activity_env.run(
        ApplyCorrections(step).run,
        ApplyCorrectionsInput(
            study_id=str(sid),
            stage="appraisal_review",
            from_revision=1,
            to_revision=2,
            corrections=(),
        ),
    )
    assert len(await inventory(step, sid, 2)) == 4
    again = await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one, revision=2))
    assert (again.built, again.current) == (0, 2)


async def test_the_input_is_the_rows_with_corrections_applied(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    """A reviewer's component correction carried into revision 2 is what is reconciled."""
    sid = revision_one.study_id
    flagged = revision_one.photo_ids[2]  # the bathroom photo; the Kitchen is untouched
    components = await rows(deps, Component, sid)
    cabinet = next(c for c in components if c.label == "kitchen_cabinet")
    step = with_llm(room_settings(deps), FixtureVisionProvider())
    await activity_env.run(
        ApplyCorrections(step).run,
        ApplyCorrectionsInput(
            study_id=str(sid),
            stage="rooms_review",
            from_revision=1,
            to_revision=2,
            corrections=(
                CorrectionInput(entity_type="photo", entity_id=str(flagged), after={}),
                CorrectionInput(
                    entity_type="component", entity_id=str(cabinet.id), after={"material": "wood"}
                ),
            ),
        ),
    )
    chat = FixtureVisionProvider()
    await activity_env.run(
        ReconcileRooms(with_llm(step, chat)).run, reconcile(revision_one, revision=2)
    )
    [request] = chat.requests
    [line] = [
        x for x in request.messages[-1].content.splitlines() if x.startswith("kitchen_cabinet:")
    ]
    # One kitchen photo's cabinets were corrected to wood; the other's stay unknown.
    assert "x6 (wood, permanent)" in line and "x6 (unknown, permanent)" in line


async def test_documents_order_the_photos_by_file_name(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    async with session_scope(deps.sessionmaker) as session:
        names = {
            d.id: d.filename
            for d in (await session.scalars(select(Document).where(Document.study_id == sid))).all()
        }
        assignments = (
            await session.scalars(select(PhotoRoom).where(PhotoRoom.study_id == sid))
        ).all()
        kitchen = next(
            r
            for r in await RoomRepository(session).list_for_revision(sid, 1)
            if r.name == "Kitchen"
        )
    step = with_llm(room_settings(deps), FixtureVisionProvider())
    await activity_env.run(ReconcileRooms(step).run, reconcile(revision_one))
    [cabinet] = [c for c in await inventory(step, sid) if c.label == "kitchen_cabinet"]
    expected = sorted(
        (str(a.document_id) for a in assignments if a.room_id == kitchen.id),
        key=lambda d: names[uuid.UUID(d)],
    )
    assert cabinet.raw["photos"] == expected


# --------------------------------------------------------------------------- file-name rooms
# EVAL_REPORT section 26, item 1: a room grouped by file name gets its type and floor voted
# from its photos (group_photos's rules) before the plan, and is planned even when every
# photo answered ambiguous.


async def analyze_all(deps: Deps, study: SeededStudy, activity_env: Env) -> None:
    for photo in study.photo_ids:
        await activity_env.run(
            AnalyzePhoto(deps).run,
            AnalyzePhotoInput(
                study_id=str(study.study_id),
                document_id=str(photo),
                revision=1,
                idempotency_key=f"k-p-{photo}",
                budget_remaining_usd=1.0,
            ),
        )


async def put_scene(deps: Deps, document_id: uuid.UUID, scene: str) -> None:
    async with session_scope(deps.sessionmaker) as session:
        document = await session.get(Document, document_id)
        assert document is not None
        uri = document.gcs_uri
    await deps.storage.put(uri, fixture_path("photos", scene).read_bytes(), "image/jpeg")


async def test_a_file_name_room_is_voted_from_its_photos(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    names = ("Den photo 1.jpg", "Den photo 2.jpg", "Den photo 3.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    await seed_run(deps.sessionmaker, study.study_id)
    # Photo 1 is the bedroom scene (floor 2) and creates the room; 2 and 3 the kitchen.
    for photo in study.photo_ids[1:]:
        await put_scene(deps, photo, "01_kitchen.jpg")
    await analyze_all(deps, study, activity_env)
    [before] = await rows(deps, Room, study.study_id)
    assert (before.group_key, before.room_type, before.floor) == ("x_den", "bedroom", "2")
    async with session_scope(deps.sessionmaker) as session:
        plan = await plan_rooms(session, study.study_id, 1)
    assert plan.rooms == 1 and len(plan.todo) == 1
    [after] = await rows(deps, Room, study.study_id)
    assert (after.room_type, after.floor, after.name) == ("kitchen", "1", "Den")


async def test_a_floor_from_the_file_name_wins_and_manual_rooms_are_kept(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    names = ("2nd Floor Den photo 1.jpg", "2nd Floor Den photo 2.jpg", "Office.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    await seed_run(deps.sessionmaker, study.study_id)
    for photo in study.photo_ids[:2]:
        await put_scene(deps, photo, "01_kitchen.jpg")  # the kitchen scene says floor 1
    await analyze_all(deps, study, activity_env)
    async with session_scope(deps.sessionmaker) as session:
        office = (await session.scalars(select(Room).where(Room.group_key == "x_office"))).one()
        office.source, office.room_type = "manual", "office"
    async with session_scope(deps.sessionmaker) as session:
        await plan_rooms(session, study.study_id, 1)
    by_key = {r.group_key: r for r in await rows(deps, Room, study.study_id)}
    assert (by_key["2_den"].room_type, by_key["2_den"].floor) == ("kitchen", "2")
    assert by_key["x_office"].room_type == "office"  # the reviewer's answer stands


def test_vote_room_needs_a_file_name_room_with_answers() -> None:
    room = Room(name="Kitchen", room_type="kitchen", floor="1", source="vision", group_key=None)
    rows_ = [[{"raw": {"room_type": "bedroom", "floor": 2}}]]
    assert not vote_room(room, ["Kitchen.jpg"], rows_)  # keyed by the model's identity
    room.group_key = "x_kitchen"
    assert not vote_room(room, ["Kitchen.jpg"], [[]])  # no photo answered
    assert vote_room(room, ["Kitchen.jpg"], rows_)
    assert (room.room_type, room.floor) == ("bedroom", "2")
    assert not vote_room(room, ["Kitchen.jpg"], rows_)  # unchanged the second time
    room.source = "manual"
    assert not vote_room(room, ["Kitchen.jpg"], [[{"raw": {"room_type": "garage"}}]])


async def test_a_file_name_room_of_ambiguous_photos_is_still_planned(
    deps: Deps, storage: Any
) -> None:
    """Photos answered ``ambiguous`` but listed objects (a close-up): by name they are one
    room, so the room is planned; an ambiguous room keyed by the model's identity (the
    Unassigned room, mixed photos) is not."""
    names = ("Den photo 1.jpg", "Den photo 2.jpg", "IMG_0001.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    async with session_scope(deps.sessionmaker) as session:
        repo = RoomRepository(session)
        den = await repo.create(
            study_id=study.study_id,
            revision=1,
            name="Den",
            room_type="ambiguous",
            source="vision",
            group_key="x_den",
        )
        unassigned = await repo.create(
            study_id=study.study_id,
            revision=1,
            name="Unassigned",
            room_type="ambiguous",
            source="vision",
        )
        for photo, room in zip(study.photo_ids, (den, den, unassigned), strict=True):
            await repo.assign_photo(
                study_id=study.study_id, document_id=photo, room_id=room.id, revision=1
            )
            await repo.add_component(
                study_id=study.study_id,
                revision=1,
                document_id=photo,
                room_id=room.id,
                label="electrical_outlet",
                material=None,
                attachment=None,
                confidence=0.9,
                raw={"source": "vision", "count": 1, "room_type": "ambiguous", "floor": None},
            )
    async with session_scope(deps.sessionmaker) as session:
        plan = await plan_rooms(session, study.study_id, 1)
    assert [p.room.id for p in plan.todo] == [den.id]
    assert plan.rooms == 1
