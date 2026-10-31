"""Room name, type and floor through the API (EVAL_REPORT section 48): what the grouping
writes is what ``GET /rooms`` shows, and a reviewer's ``room`` corrections posted to
``POST /decisions`` come back from the next revision as the engineer left them, with their
provenance, through the next vote.

The grouping is the production one (``assign_room_by_name``, the answer row and
``reconcile_rooms``' vote, as ``analyze_photo`` writes them, without the model call); the
rejection is applied by the real ``apply_corrections`` activity with the corrections the
decision put in the outbox, as the workflow would.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from apitest_support import Api, Seed
from lifecycletest_support import make_deps, seed_run
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.testing import ActivityEnvironment

from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.reconcile_rooms import plan_rooms
from basis.config import load_settings
from basis.db.enums import DocumentKind, Stage
from basis.db.models import OutboxMessage
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.storage import LocalStorage
from basis.vision.rooms import PhotoInput, assign_room_by_name, initial_rooms
from basis.vision.schemas import PhotoAnalysis, RoomType
from basis.workflow.contracts import ApplyCorrectionsInput, CorrectionInput

pytestmark = pytest.mark.integration

# file name -> what the per-photo model answered (type, floor)
PHOTOS: dict[str, tuple[RoomType, int | None]] = {
    "29_2nd Floor Bed 2 .JPG": (RoomType.STORAGE, None),
    "35_2nd Floor Bed 3 .JPG": (RoomType.BEDROOM, 2),
    "45_Basement Bath.JPG": (RoomType.BATHROOM, 1),
    "49_Mechanicals.JPG": (RoomType.AMBIGUOUS, None),
    "04_Property Front.JPG": (RoomType.EXTERIOR, 1),
    "Suite.jpg": (RoomType.OFFICE, 3),
}


async def group(sessionmaker: async_sessionmaker[AsyncSession], sid: uuid.UUID) -> None:
    """The photo stage for ``PHOTOS`` at revision 1: documents, rooms, answers, the vote."""
    async with session_scope(sessionmaker) as session:
        documents = DocumentRepository(session)
        rooms = RoomRepository(session)
        for filename, (room_type, floor) in PHOTOS.items():
            document_id = uuid.uuid4()
            await documents.create(
                study_id=sid,
                kind=DocumentKind.PHOTO,
                filename=filename,
                content_type="image/jpeg",
                size_bytes=10,
                sha256=uuid.uuid4().hex * 2,
                gcs_uri=f"gs://basis-local/studies/{sid}/{document_id}/file",
                uploaded_by=None,
                document_id=document_id,
            )
            analysis = PhotoAnalysis(room_type=room_type, room_confidence=0.7, floor=floor)
            room_id, _ = await assign_room_by_name(
                session,
                study_id=sid,
                revision=1,
                filename=filename,
                photo_files=list(PHOTOS),
                aliases={},
                analysis=analysis,
            )
            await rooms.assign_photo(
                study_id=sid, document_id=document_id, room_id=room_id, revision=1
            )
            await rooms.record_answer(
                study_id=sid,
                document_id=document_id,
                revision=1,
                room_type=room_type.value,
                room_confidence=0.7,
                floor=floor,
                room_ordinal=1,
                analysis_uri=None,
            )
        await plan_rooms(session, sid, 1)


async def rooms(api: Api, sid: uuid.UUID, revision: int) -> dict[str, dict[str, Any]]:
    response = await api.get("alice", f"/v1/studies/{sid}/rooms", params={"revision": revision})
    assert response.status_code == 200, response.text
    return {room["group_key"]: room for room in response.json()["items"]}


async def test_room_metadata_round_trips_through_the_api(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    study = await api.create_study("alice")
    sid = uuid.UUID(str(study["id"]))
    await seed.stage(sid, Stage.ROOMS_REVIEW, 1, components_gate=True)
    await seed_run(sessionmaker, sid)  # the execution the activities record under
    await group(sessionmaker, sid)

    # ---- what the Rooms review opens on
    first = await rooms(api, sid, 1)
    shown = {k: (r["name"], r["room_type"], r["floor"]) for k, r in first.items()}
    assert shown == {
        "2_bed_2": ("Bed 2", "bedroom", "2"),  # the name says bedroom, the file name floor 2
        "2_bed_3": ("Bed 3", "bedroom", "2"),  # two bedrooms on one floor stay two rooms
        "b_bath": ("Bath", "bathroom", "0"),  # the basement is 0, whatever the model said
        "x_mechanicals": ("Mechanicals", "mechanical", None),  # unknown floor stays null
        "x_property_front": ("Property Front", "exterior", None),  # no model floor outside
        "x_suite": ("Suite", "office", "3"),  # the name says nothing: the model's answer
    }
    assert all(r["source"] == "vision" for r in first.values())
    assert all(r["provenance"]["origin"] == "model" for r in first.values())

    # ---- the engineer's edits, posted as a rooms rejection
    edits = [
        {
            "entity_type": "room",
            "entity_id": first["2_bed_2"]["id"],
            "before": {"name": "Bed 2", "floor": "2"},
            "after": {"name": "Guest Bedroom", "floor": None},
        },
        {
            "entity_type": "room",
            "entity_id": first["x_mechanicals"]["id"],
            "after": {"floor": 0},  # a number: stored as the text "0"
        },
        {
            "entity_type": "room",
            "entity_id": first["x_suite"]["id"],
            "after": {"room_type": "bedroom"},
        },
        {
            "entity_type": "photo_room",
            "entity_id": first["2_bed_3"]["photos"][0]["document_id"],
            "after": {"room_id": first["2_bed_2"]["id"]},
        },
    ]
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        {"stage": "rooms_review", "revision": 1, "decision": "reject", "corrections": edits},
    )
    assert response.status_code == 202, response.text
    async with sessionmaker() as session:
        (signal,) = (await session.scalars(select(OutboxMessage))).all()
    corrections = tuple(CorrectionInput.model_validate(c) for c in signal.payload["corrections"])
    deps = make_deps(sessionmaker, LocalStorage(tmp_path), load_settings(), llm=None)
    await ActivityEnvironment().run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(sid),
            stage="rooms_review",
            from_revision=1,
            to_revision=2,
            components_gate=True,
            corrections=corrections,
        ),
    )
    async with session_scope(sessionmaker) as session:
        await plan_rooms(session, sid, 2)  # the rejection's vote

    # ---- the next revision shows the engineer's values, and only theirs changed
    second = await rooms(api, sid, 2)
    shown = {k: (r["name"], r["room_type"], r["floor"]) for k, r in second.items()}
    assert shown == {
        "2_bed_2": ("Guest Bedroom", "bedroom", None),
        "b_bath": ("Bath", "bathroom", "0"),
        "x_mechanicals": ("Mechanicals", "mechanical", "0"),
        "x_property_front": ("Property Front", "exterior", None),
        "x_suite": ("Suite", "bedroom", "3"),
    }  # Bed 3 lost its only photo to the move, so it is not carried
    guest = second["2_bed_2"]
    assert guest["source"] == "manual"
    assert guest["provenance"]["origin"] == "engineer_modified"
    assert guest["provenance"]["changed"] == ["floor", "name"]
    assert guest["provenance"]["before"] == {"name": "Bed 2", "floor": "2"}
    assert sorted(p["filename"] for p in guest["photos"]) == [
        "29_2nd Floor Bed 2 .JPG",
        "35_2nd Floor Bed 3 .JPG",
    ]
    assert second["x_suite"]["provenance"]["changed"] == ["room_type"]
    assert second["b_bath"]["provenance"]["origin"] == "model"


@pytest.mark.parametrize(
    "after",
    [
        {"name": ""},
        {"name": "   "},
        {"name": 7},
        {"room_type": None},
        {"floor": True},
        {"floor": 2.5},
        {"floor": ""},
        {"floor": 999},
        {"floor": ["2"]},
    ],
)
async def test_a_room_value_the_activity_could_not_store_is_422(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], after: dict[str, Any]
) -> None:
    study = await api.create_study("alice")
    sid = uuid.UUID(str(study["id"]))
    await seed.stage(sid, Stage.ROOMS_REVIEW, 1, components_gate=True)
    targets = await seed.outputs(sid, revision=1)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        {
            "stage": "rooms_review",
            "revision": 1,
            "decision": "reject",
            "corrections": [{"entity_type": "room", "entity_id": targets["room"], "after": after}],
        },
    )
    assert response.status_code == 422, response.text
    async with sessionmaker() as session:
        assert (await session.scalars(select(OutboxMessage))).all() == []


async def test_the_database_holds_what_initial_rooms_scores(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """``evals.room_metadata`` scores :func:`initial_rooms`; the rows the activities write
    (assignment, answers, vote) are the same rooms with the same name, type and floor."""
    study = await api.create_study("alice")
    sid = uuid.UUID(str(study["id"]))
    await group(sessionmaker, sid)
    want = initial_rooms(
        [
            PhotoInput(f, PhotoAnalysis(room_type=t, room_confidence=0.7, floor=floor))
            for f, (t, floor) in PHOTOS.items()
        ]
    )
    got = await rooms(api, sid, 1)
    assert {r.key: (r.name, r.room_type, r.floor, sorted(r.photos)) for r in want} == {
        k: (r["name"], r["room_type"], r["floor"], sorted(p["filename"] for p in r["photos"]))
        for k, r in got.items()
    }
