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
from basis.db.models import (
    ActivityResult,
    Asset,
    Component,
    Document,
    LlmCall,
    PhotoRoom,
    PhotoRoomAnswer,
    Room,
    StudyEvent,
)
from basis.db.repositories.activity_result import COMPLETED
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.llm import ScriptedTurn, Usage
from basis.llm.errors import ProviderBadResponse, ProviderUnavailable
from basis.vision.reconcile import ModelInventoryObject, ModelRoomInventory
from basis.vision.rooms import ROOM_INVENTORY_SOURCE, RoomAnswer, assign_room, write_photo
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

# Production defaults (EVAL_REPORT 31): luna reconciles, Sonnet does the material second look.
MODEL = "gpt-6-luna"
MATERIALS_MODEL = "claude-sonnet-5-5"
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
            (MATERIALS_MODEL, "surface_material"): [
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
# EVAL_REPORT section 27, item 1, and section 48: a room grouped by file name gets its type and
# floor resolved from its name and its photos (resolve_room) before the plan, and is planned
# even when every photo answered ambiguous. "Suite" says no room type, so the photos vote.


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
    names = ("Suite photo 1.jpg", "Suite photo 2.jpg", "Suite photo 3.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    await seed_run(deps.sessionmaker, study.study_id)
    # Photo 1 is the bedroom scene (floor 2) and creates the room; 2 and 3 the kitchen.
    for photo in study.photo_ids[1:]:
        await put_scene(deps, photo, "01_kitchen.jpg")
    await analyze_all(deps, study, activity_env)
    [before] = await rows(deps, Room, study.study_id)
    assert (before.group_key, before.room_type, before.floor) == ("x_suite", "bedroom", "2")
    async with session_scope(deps.sessionmaker) as session:
        plan = await plan_rooms(session, study.study_id, 1)
    assert plan.rooms == 1 and len(plan.todo) == 1
    [after] = await rows(deps, Room, study.study_id)
    assert (after.room_type, after.floor, after.name) == ("kitchen", "1", "Suite")


async def test_a_floor_from_the_file_name_wins_and_manual_rooms_are_kept(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    names = ("2nd Floor Suite photo 1.jpg", "2nd Floor Suite photo 2.jpg", "Office.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    await seed_run(deps.sessionmaker, study.study_id)
    for photo in study.photo_ids[:2]:
        await put_scene(deps, photo, "01_kitchen.jpg")  # the kitchen scene says floor 1
    await analyze_all(deps, study, activity_env)
    async with session_scope(deps.sessionmaker) as session:
        office = (await session.scalars(select(Room).where(Room.group_key == "x_office"))).one()
        office.source, office.room_type = "manual", "storage"
    async with session_scope(deps.sessionmaker) as session:
        await plan_rooms(session, study.study_id, 1)
    by_key = {r.group_key: r for r in await rows(deps, Room, study.study_id)}
    assert (by_key["2_suite"].room_type, by_key["2_suite"].floor) == ("kitchen", "2")
    assert by_key["2_suite"].name == "Suite"  # the floor is in its own field
    assert by_key["x_office"].room_type == "storage"  # the reviewer's answer stands


def test_vote_room_needs_a_file_name_room_and_never_renames() -> None:
    room = Room(name="Suite", room_type="kitchen", floor="1", source="vision", group_key=None)
    answers = [RoomAnswer(RoomType.BEDROOM, 2)]
    assert vote_room(room, ["Suite.jpg"], answers) is None  # keyed by the model's identity
    room.group_key = "x_suite"
    assert vote_room(room, ["Suite.jpg"], []) is None  # no answer: the room's own stands
    change = vote_room(room, ["Suite.jpg"], answers)
    assert change == {
        "before": {"room_type": "kitchen", "floor": "1"},
        "after": {"room_type": "bedroom", "floor": "2"},
        "sources": {"name": "file_name", "room_type": "model", "floor": "model"},
    }
    assert vote_room(room, ["Suite.jpg"], answers) is None  # unchanged the second time
    room.source = "manual"
    assert vote_room(room, ["Suite.jpg"], [RoomAnswer(RoomType.GARAGE)]) is None
    # A room grouped before the floor left the name keeps its name; the name still types it.
    legacy = Room(
        name="2nd Floor Bed 2", room_type="storage", floor="2", source="vision", group_key="2_bed_2"
    )
    assert vote_room(legacy, ["29_2nd Floor Bed 2 .JPG"], [RoomAnswer(RoomType.STORAGE)])
    assert (legacy.name, legacy.room_type, legacy.floor) == ("2nd Floor Bed 2", "bedroom", "2")
    # Legacy rows with no answer: the name and file names still decide, nothing else moves.
    mech = Room(
        name="Mechanicals", room_type="ambiguous", floor="0", source="vision", group_key="x_m"
    )
    assert vote_room(mech, ["Mechanicals.jpg"], []) is not None
    assert (mech.room_type, mech.floor) == ("mechanical", "0")


async def test_a_file_name_room_of_ambiguous_photos_is_still_planned(
    deps: Deps, storage: Any
) -> None:
    """Photos answered ``ambiguous`` but listed objects (a close-up): by name they are one
    room, so the room is planned; an ambiguous room keyed by the model's identity (the
    Unassigned room, mixed photos) is not."""
    names = ("Suite photo 1.jpg", "Suite photo 2.jpg", "IMG_0001.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    async with session_scope(deps.sessionmaker) as session:
        repo = RoomRepository(session)
        den = await repo.create(
            study_id=study.study_id,
            revision=1,
            name="Suite",
            room_type="ambiguous",
            source="vision",
            group_key="x_suite",
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


# --------------------------------------------------------------------------- vote only
# Patch components-gate-v1: the photo stage and a rooms rejection re-vote the file-name
# rooms so the rooms review shows the voted rooms; the room step waits for the approval.


async def test_vote_only_votes_the_rooms_and_builds_nothing(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    names = ("Suite photo 1.jpg", "Suite photo 2.jpg", "Suite photo 3.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    await seed_run(deps.sessionmaker, study.study_id)
    for photo in study.photo_ids[1:]:
        await put_scene(deps, photo, "01_kitchen.jpg")
    await analyze_all(deps, study, activity_env)
    voting = with_llm(room_settings(deps), None)  # no model call is possible
    vote = reconcile(study).model_copy(update={"vote_only": True})
    output = await activity_env.run(ReconcileRooms(voting).run, vote)
    assert (output.enabled, output.rooms, output.built, output.cost_usd) == (True, 1, 0, 0.0)
    [room] = await rows(deps, Room, study.study_id)
    assert (room.room_type, room.floor) == ("kitchen", "1")  # voted and committed
    assert await inventory(deps, study.study_id) == []
    assert await llm_calls(deps, study.study_id) == 0


async def test_vote_only_with_the_room_step_off_still_votes(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    """The vote is the grouping's: ROOM_RECONCILE=false builds no inventory but the Rooms
    review still shows the voted rooms (before, the rooms kept the first photo's answer)."""
    names = ("Suite photo 1.jpg", "Suite photo 2.jpg", "Suite photo 3.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    await seed_run(deps.sessionmaker, study.study_id)
    for photo in study.photo_ids[1:]:
        await put_scene(deps, photo, "01_kitchen.jpg")
    await analyze_all(deps, study, activity_env)
    vote = reconcile(study).model_copy(update={"vote_only": True})
    output = await activity_env.run(ReconcileRooms(deps).run, vote)  # the suite pins it off
    assert output.enabled is False
    [after] = await rows(deps, Room, study.study_id)
    assert (after.room_type, after.floor) == ("kitchen", "1")
    assert await inventory(deps, study.study_id) == []


# --------------------------------------------------------------------------- room metadata
# EVAL_REPORT section 48: every analyzed photo votes (its answer row, components or not), a
# change is on the timeline, and a reviewer's name, type and floor survive moves, merges,
# re-analysis and a second vote.


async def events(deps: Deps, study_id: uuid.UUID, event_type: str) -> list[StudyEvent]:
    async with session_scope(deps.sessionmaker) as session:
        stmt = (
            select(StudyEvent)
            .where(StudyEvent.study_id == study_id, StudyEvent.event_type == event_type)
            .order_by(StudyEvent.seq)
        )
        return list((await session.scalars(stmt)).all())


async def test_analyze_photo_records_the_answer_and_names_without_the_floor(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    names = ("2nd Floor Bed 2 photo 1.jpg", "Basement Bath.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    await seed_run(deps.sessionmaker, study.study_id)
    await analyze_all(deps, study, activity_env)
    answers = await rows(deps, PhotoRoomAnswer, study.study_id)
    assert {a.document_id for a in answers} == set(study.photo_ids)
    assert all(a.room_type == "bedroom" and a.floor == 2 for a in answers)  # the bedroom scene
    by_key = {r.group_key: r for r in await rows(deps, Room, study.study_id)}
    assert (by_key["2_bed_2"].name, by_key["2_bed_2"].room_type, by_key["2_bed_2"].floor) == (
        "Bed 2",
        "bedroom",
        "2",
    )
    # The name says bathroom and the file name the basement: the model's bedroom/2 lose.
    assert (by_key["b_bath"].name, by_key["b_bath"].room_type, by_key["b_bath"].floor) == (
        "Bath",
        "bathroom",
        "0",
    )


async def test_a_photo_with_no_component_votes_through_its_answer(deps: Deps, storage: Any) -> None:
    names = ("Suite photo 1.jpg", "Suite photo 2.jpg", "Suite photo 3.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    sid = study.study_id
    async with session_scope(deps.sessionmaker) as session:
        repo = RoomRepository(session)
        suite = await repo.create(
            study_id=sid,
            revision=1,
            name="Suite",
            room_type="ambiguous",
            source="vision",
            group_key="x_suite",
        )
        for index, photo in enumerate(study.photo_ids):
            await repo.assign_photo(study_id=sid, document_id=photo, room_id=suite.id, revision=1)
            answer = ("ambiguous", None) if index == 0 else ("office", 3)
            await repo.record_answer(
                study_id=sid,
                document_id=photo,
                revision=1,
                room_type=answer[0],
                room_confidence=0.8,
                floor=answer[1],
                room_ordinal=1,
                analysis_uri=None,
            )
        # Only the first (ambiguous) photo listed a component; before, it alone voted.
        await repo.add_component(
            study_id=sid,
            revision=1,
            document_id=study.photo_ids[0],
            room_id=suite.id,
            label="electrical_outlet",
            material=None,
            attachment=None,
            confidence=0.9,
            raw={"source": "vision", "count": 1, "room_type": "ambiguous", "floor": None},
        )
    async with session_scope(deps.sessionmaker) as session:
        await plan_rooms(session, sid, 1)
    [room] = await rows(deps, Room, sid)
    assert (room.name, room.room_type, room.floor) == ("Suite", "office", "3")
    [event] = await events(deps, sid, "room.voted")
    assert (event.actor_type, event.entity_id, event.revision) == ("model", room.id, 1)
    assert event.before == {"room_type": "ambiguous", "floor": None}
    assert event.after == {"room_type": "office", "floor": "3"}
    assert event.detail["sources"] == {"name": "file_name", "room_type": "model", "floor": "model"}
    assert event.detail["answers"] == 3
    # A retry (the same vote again) changes nothing and records nothing.
    async with session_scope(deps.sessionmaker) as session:
        await plan_rooms(session, sid, 1)
    assert len(await events(deps, sid, "room.voted")) == 1


async def test_a_carried_photo_answers_from_its_last_analysis(deps: Deps, storage: Any) -> None:
    names = ("Suite photo 1.jpg",)
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    sid, [photo] = study.study_id, study.photo_ids
    async with session_scope(deps.sessionmaker) as session:
        repo = RoomRepository(session)
        await repo.record_answer(
            study_id=sid,
            document_id=photo,
            revision=1,
            room_type="laundry",
            room_confidence=0.9,
            floor=0,
            room_ordinal=1,
            analysis_uri=None,
        )
        # Revision 2 carried the photo without re-analyzing it (no row at 2) into a room
        # whose type the copy still had from somewhere else.
        room = await repo.create(
            study_id=sid,
            revision=2,
            name="Suite",
            room_type="kitchen",
            source="vision",
            group_key="x_suite",
        )
        await repo.assign_photo(study_id=sid, document_id=photo, room_id=room.id, revision=2)
        assert (await repo.answers(sid, 1))[photo].revision == 1
        assert await repo.answers(sid, 0) == {}
    async with session_scope(deps.sessionmaker) as session:
        await plan_rooms(session, sid, 2)
    [voted] = await rows(deps, Room, sid, 2)
    assert (voted.room_type, voted.floor) == ("laundry", "0")  # the basement's 0 is kept


async def test_reviewer_edits_survive_moves_reanalysis_and_the_next_vote(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    """A rooms rejection renames and refloors a room, moves a photo into it and flags
    another for re-analysis; the next revision's vote leaves the corrected room as the
    engineer left it (name, type, floor, key) and re-votes only the model's room."""
    names = ("1st Floor Suite photo 1.jpg", "1st Floor Suite photo 2.jpg", "Bed 2.jpg")
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    sid = study.study_id
    await seed_run(deps.sessionmaker, sid)
    await analyze_all(deps, study, activity_env)
    async with session_scope(deps.sessionmaker) as session:
        await plan_rooms(session, sid, 1)
    one = {r.group_key: r for r in await rows(deps, Room, sid)}
    assert (one["1_suite"].name, one["1_suite"].room_type, one["1_suite"].floor) == (
        "Suite",
        "bedroom",
        "1",
    )
    assert (one["x_bed_2"].name, one["x_bed_2"].room_type, one["x_bed_2"].floor) == (
        "Bed 2",
        "bedroom",
        "2",
    )
    moved, flagged = study.photo_ids[1], study.photo_ids[0]
    # The rooms review (components gate): a rename and refloor, and a move into that room.
    await activity_env.run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(sid),
            stage="rooms_review",
            from_revision=1,
            to_revision=2,
            components_gate=True,
            corrections=(
                CorrectionInput(
                    entity_type="room",
                    entity_id=str(one["x_bed_2"].id),
                    after={"name": "Guest Bedroom", "floor": "3"},
                ),
                CorrectionInput(
                    entity_type="photo_room",
                    entity_id=str(moved),
                    after={"room_id": str(one["x_bed_2"].id)},
                ),
            ),
        ),
    )
    async with session_scope(deps.sessionmaker) as session:
        await plan_rooms(session, sid, 2)  # the rooms rejection's vote
    # The components review flags the other Suite photo; it is re-analyzed in its room.
    await activity_env.with_info(activity_id="apply-r2").run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(sid),
            stage="components_review",
            from_revision=2,
            to_revision=3,
            components_gate=True,
            corrections=(CorrectionInput(entity_type="photo", entity_id=str(flagged), after={}),),
        ),
    )
    await put_scene(deps, flagged, "01_kitchen.jpg")
    await activity_env.run(
        AnalyzePhoto(deps).run,
        AnalyzePhotoInput(
            study_id=str(sid),
            document_id=str(flagged),
            revision=3,
            idempotency_key="k-again-r3",
            budget_remaining_usd=1.0,
            keep_room=True,
        ),
    )
    for _ in range(2):  # the room step's vote, then a retry of it
        async with session_scope(deps.sessionmaker) as session:
            await plan_rooms(session, sid, 3)
    two = {r.group_key: r for r in await rows(deps, Room, sid, 2)}
    three = {r.group_key: r for r in await rows(deps, Room, sid, 3)}
    guest = three["x_bed_2"]
    assert (guest.name, guest.room_type, guest.floor, guest.source) == (
        "Guest Bedroom",
        "bedroom",
        "3",
        "manual",
    )
    assert guest.carried_from == two["x_bed_2"].id
    assert two["x_bed_2"].carried_from == one["x_bed_2"].id
    # The re-analyzed photo answered kitchen at revision 3; its room is the model's.
    suite = three["1_suite"]
    assert (suite.name, suite.room_type, suite.floor) == ("Suite", "kitchen", "1")
    assignments = {a.document_id: a.room_id for a in await rows(deps, PhotoRoom, sid, 3)}
    assert assignments == {flagged: suite.id, moved: guest.id, study.photo_ids[2]: guest.id}
    corrected = {e.entity_id for e in await events(deps, sid, "room.voted")}
    assert not corrected & {guest.id, two["x_bed_2"].id}  # a corrected room is never voted
    assert [e.entity_id for e in await events(deps, sid, "room.voted") if e.revision == 3] == [
        suite.id
    ]
