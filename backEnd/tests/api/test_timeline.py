"""``GET /v1/studies/{id}/timeline``, ``/timeline/steps``, ``/audit`` and the ``provenance``
member of the output rows, on seeded rows (docs/api/TIMELINE_AND_AUDIT.md); the ``code``
member of problem responses (docs/ERROR_CODES.md). Real Postgres; synthetic rows.

The seeded study has two revisions: revision 1 as the pipeline wrote it, revision 2 as
``apply_corrections`` would copy it after a rejection with one correction of each kind
(a renamed room, a merged room, a moved photo, a component with a new material and count,
an added component, a corrected extraction field, an asset with a new bucket), each copy
naming its parent the way the activity does (``carried_from``). The corrections are
recorded through the decision route, so the timeline has the decision and its corrections,
and the copies' events are written the way the activities write them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from apitest_support import COMPLETE_INTAKE, Api, Seed, api_settings, build_api_app
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from workflowtest_support import FakeWorkflowService

from basis.db.enums import Bucket, ClassificationMethod, DocumentKind, Stage, StudyStatus
from basis.db.models import (
    Asset,
    Component,
    Correction,
    Extraction,
    ExtractionField,
    PhotoRoom,
    Room,
    Study,
)
from basis.db.repositories import DocumentRepository
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.vision.rooms import ROOM_MANUAL_SOURCE

pytestmark = pytest.mark.integration


async def _document(
    session: AsyncSession, sid: uuid.UUID, name: str, kind: DocumentKind
) -> uuid.UUID:
    document_id = uuid.uuid4()
    await DocumentRepository(session).create(
        study_id=sid,
        kind=kind,
        filename=name,
        content_type="application/octet-stream",
        size_bytes=10,
        sha256=uuid.uuid4().hex * 2,
        gcs_uri=f"gs://basis-local/studies/{sid}/{document_id}/x",
        uploaded_by=None,
        document_id=document_id,
    )
    return document_id


async def seed_revision_one(
    api: Api, seed: Seed, sm: async_sessionmaker[AsyncSession]
) -> dict[str, Any]:
    """Revision 1 at the rooms review: two rooms, two photos, two components, two assets, one
    extraction field."""
    study = await api.create_study("alice")
    sid = uuid.UUID(study["id"])
    await seed.stage(sid, Stage.ROOMS_REVIEW, revision=1)
    ids: dict[str, Any] = {"study_id": sid}
    async with session_scope(sm) as session:
        # The workflow's components gate (record_run_start writes it): the rooms review takes
        # the grouping (room, photo_room) and the components review the rest.
        await session.execute(
            Study.__table__.update().where(Study.id == sid).values(components_gate=True)
        )
        ids["appraisal"] = await _document(session, sid, "appraisal.pdf", DocumentKind.APPRAISAL)
        ids["photo_a"] = await _document(session, sid, "Kitchen photo 1.jpg", DocumentKind.PHOTO)
        ids["photo_b"] = await _document(session, sid, "Bath.jpg", DocumentKind.PHOTO)
        extraction = Extraction(
            study_id=sid, document_id=ids["appraisal"], revision=1, method="azure_di", raw={}
        )
        kitchen = Room(
            study_id=sid, revision=1, name="Kitchen", room_type="kitchen", source="vision"
        )
        bath = Room(study_id=sid, revision=1, name="Bath", room_type="bathroom", source="vision")
        session.add_all([extraction, kitchen, bath])
        await session.flush()
        field = ExtractionField(
            extraction_id=extraction.id,
            study_id=sid,
            section="improvements",
            field="year_built",
            value_text="1905",
            value_num=Decimal(1905),
            source="azure",
        )
        cabinet = Component(
            study_id=sid,
            revision=1,
            document_id=ids["photo_a"],
            room_id=kitchen.id,
            label="kitchen_cabinet",
            material="wood",
            raw={"count": 6},
        )
        tub = Component(
            study_id=sid,
            revision=1,
            document_id=ids["photo_b"],
            room_id=bath.id,
            label="bathtub",
            material="acrylic",
            raw={"count": 1},
        )
        session.add_all(
            [
                field,
                cabinet,
                tub,
                PhotoRoom(study_id=sid, document_id=ids["photo_a"], room_id=kitchen.id, revision=1),
                PhotoRoom(study_id=sid, document_id=ids["photo_b"], room_id=bath.id, revision=1),
            ]
        )
        await session.flush()
        cabinet_asset = Asset(
            study_id=sid,
            revision=1,
            component_id=cabinet.id,
            category="cabinetry",
            bucket=Bucket.Y5,
            recovery_period=Decimal("5"),
            irs_section="1245",
            method=ClassificationMethod.RULE,
        )
        session.add(cabinet_asset)
        await session.flush()
        ids.update(
            extraction=extraction.id,
            field=field.id,
            kitchen=kitchen.id,
            bath=bath.id,
            cabinet=cabinet.id,
            tub=tub.id,
            cabinet_asset=cabinet_asset.id,
        )
    return ids


async def seed_revision_two(sm: async_sessionmaker[AsyncSession], ids: dict[str, Any]) -> None:
    """What ``apply_corrections``, ``extract_fields`` and ``process_assets`` write after the
    rejection: copies naming their parents, with the corrections applied."""
    sid = ids["study_id"]
    async with session_scope(sm) as session:
        corrections = {
            (c.entity_type, c.entity_id): c
            for c in (
                await session.scalars(select(Correction).where(Correction.study_id == sid))
            ).all()
        }
        extraction = Extraction(
            study_id=sid,
            document_id=ids["appraisal"],
            revision=2,
            method="azure_di",
            raw={"carried_from": str(ids["extraction"])},
        )
        kitchen = Room(
            study_id=sid,
            revision=2,
            name="Main Kitchen",
            room_type="kitchen",
            source="manual",
            carried_from=ids["kitchen"],
        )
        session.add_all([extraction, kitchen])
        await session.flush()
        field = ExtractionField(
            extraction_id=extraction.id,
            study_id=sid,
            section="improvements",
            field="year_built",
            value_text="1925",
            value_num=Decimal(1925),
            source="manual",
        )
        cabinet = Component(
            study_id=sid,
            revision=2,
            document_id=ids["photo_a"],
            room_id=kitchen.id,
            label="kitchen_cabinet",
            material="laminate",
            raw={
                "count": 8,
                "count_before": 6,
                "carried_from": str(ids["cabinet"]),
                "corrected": ["count", "material"],
            },
        )
        tub = Component(  # the bath's row, moved with its photo into the merged kitchen
            study_id=sid,
            revision=2,
            document_id=ids["photo_b"],
            room_id=kitchen.id,
            label="bathtub",
            material="acrylic",
            raw={"count": 1, "carried_from": str(ids["tub"])},
        )
        lamp = Component(
            study_id=sid,
            revision=2,
            document_id=None,
            room_id=kitchen.id,
            label="light_fixture",
            material="metal",
            raw={"source": ROOM_MANUAL_SOURCE, "count": 3, "room_from": str(ids["kitchen"])},
        )
        session.add_all(
            [
                field,
                cabinet,
                tub,
                lamp,
                PhotoRoom(study_id=sid, document_id=ids["photo_a"], room_id=kitchen.id, revision=2),
                PhotoRoom(study_id=sid, document_id=ids["photo_b"], room_id=kitchen.id, revision=2),
            ]
        )
        await session.flush()
        cabinet_asset = Asset(
            study_id=sid,
            revision=2,
            component_id=cabinet.id,
            category="cabinetry",
            bucket=Bucket.Y7,
            recovery_period=Decimal("7"),
            irs_section="1245",
            method=ClassificationMethod.MANUAL,
        )
        lamp_asset = Asset(
            study_id=sid,
            revision=2,
            component_id=lamp.id,
            category="lighting",
            bucket=Bucket.Y5,
            recovery_period=Decimal("5"),
            irs_section="1245",
            method=ClassificationMethod.RULE,
        )
        session.add_all([cabinet_asset, lamp_asset])
        await session.flush()
        # The forward links the activities record on the timeline.
        events = StudyEventRepository(session)
        rename = corrections[("room", ids["kitchen"])]
        await events.record(
            sid,
            "entity.corrected",
            actor="engineer",
            actor_id=rename.reviewer_id,
            revision=2,
            entity_type="room",
            entity_id=kitchen.id,
            before={"name": "Kitchen"},
            after=rename.after,
            detail={"carried_from": str(ids["kitchen"]), "correction_ids": [str(rename.id)]},
        )
        ids.update(
            kitchen_2=kitchen.id,
            field_2=field.id,
            cabinet_2=cabinet.id,
            tub_2=tub.id,
            lamp_2=lamp.id,
            cabinet_asset_2=cabinet_asset.id,
            lamp_asset_2=lamp_asset.id,
        )
        await session.execute(
            Study.__table__.update()
            .where(Study.id == sid)
            .values(
                current_stage=Stage.ASSETS_REVIEW, revision=2, status=StudyStatus.WAITING_REVIEW
            )
        )


@pytest.fixture
def workflows() -> FakeWorkflowService:
    return FakeWorkflowService()


@pytest.fixture
def api_app(database_url, sessionmaker, storage, verifier, workflows):  # type: ignore[no-untyped-def]
    return build_api_app(api_settings(database_url), sessionmaker, storage, verifier, workflows)


async def reject_with_corrections(api: Api, ids: dict[str, Any]) -> dict[str, Any]:
    sid = ids["study_id"]
    corrections = [
        {
            "entity_type": "room",
            "entity_id": str(ids["kitchen"]),
            "before": {"name": "Kitchen"},
            "after": {"name": "Main Kitchen"},
            "reason": "the listing calls it the main kitchen",
        },
        {
            "entity_type": "room",
            "entity_id": str(ids["bath"]),
            "after": {"merge_into": str(ids["kitchen"])},
        },
        {
            "entity_type": "photo_room",
            "entity_id": str(ids["photo_b"]),
            "after": {"room_id": str(ids["kitchen"])},
        },
    ]
    # The rooms review takes the grouping; the other kinds are recorded at their own gates
    # below, directly, since the API refuses them at the rooms review (correction_type).
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        {"stage": "rooms_review", "revision": 1, "decision": "reject", "corrections": corrections},
    )
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


async def record_other_corrections(
    sm: async_sessionmaker[AsyncSession], ids: dict[str, Any], reviewer: uuid.UUID
) -> None:
    """The component, added-component, field and asset corrections of the same rejection
    (one rejection per revision in this synthetic study), written as the repository does."""
    async with session_scope(sm) as session:
        session.add_all(
            [
                Correction(
                    study_id=ids["study_id"],
                    entity_type="component",
                    entity_id=ids["cabinet"],
                    revision=1,
                    before={"material": "wood"},
                    after={"material": "laminate", "count": 8},
                    reason="laminate doors, eight units",
                    reviewer_id=reviewer,
                ),
                Correction(
                    study_id=ids["study_id"],
                    entity_type="room_component",
                    entity_id=ids["kitchen"],
                    revision=1,
                    after={"label": "light_fixture", "count": 3, "material": "metal"},
                    reviewer_id=reviewer,
                ),
                Correction(
                    study_id=ids["study_id"],
                    entity_type="extraction_field",
                    entity_id=ids["field"],
                    revision=1,
                    before={"value_text": "1905"},
                    after={"value_text": "1925", "value_num": 1925},
                    reviewer_id=reviewer,
                ),
                Correction(
                    study_id=ids["study_id"],
                    entity_type="asset",
                    entity_id=ids["cabinet_asset"],
                    revision=1,
                    after={"bucket": "y7", "recovery_period": 7},
                    reviewer_id=reviewer,
                ),
            ]
        )


async def test_provenance_audit_and_timeline_on_a_corrected_study(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    ids = await seed_revision_one(api, seed, sessionmaker)
    sid = ids["study_id"]
    decided = await reject_with_corrections(api, ids)
    reviewer = uuid.UUID(decided["review"]["reviewer_id"])
    await record_other_corrections(sessionmaker, ids, reviewer)
    await seed_revision_two(sessionmaker, ids)
    # A manual object added, then deleted (the two deletions the audit lists: this and the
    # merged room).
    async with session_scope(sessionmaker) as session:
        objects = PhotoObjectRepository(session)
        row = await objects.add(
            study_id=sid,
            document_id=ids["photo_a"],
            revision=2,
            new=NewObject(
                label="light_fixture",
                x1=0.1,
                y1=0.1,
                x2=0.2,
                y2=0.3,
                area=0.02,
                source="manual",
                created_by=reviewer,
                mask_source="none",
            ),
        )
        await objects.soft_delete(row, reviewer)
        object_id = row.id

    # ---- provenance on the rows
    rooms = (await api.get("alice", f"/v1/studies/{sid}/rooms", params={"revision": 2})).json()
    [kitchen] = rooms["items"]
    assert kitchen["provenance"]["origin"] == "engineer_modified"
    assert kitchen["provenance"]["before"] == {"name": "Kitchen"}
    assert kitchen["provenance"]["after"] == {"name": "Main Kitchen"}
    assert kitchen["provenance"]["changed"] == ["name"]
    assert kitchen["provenance"]["stage"] == "rooms_review"
    assert kitchen["provenance"]["revision"] == 1
    assert kitchen["provenance"]["by"] == str(reviewer)
    assert kitchen["provenance"]["carried_from"] == str(ids["kitchen"])
    photos = {p["document_id"]: p["provenance"] for p in kitchen["photos"]}
    assert photos[str(ids["photo_a"])]["origin"] == "model"
    assert photos[str(ids["photo_b"])]["origin"] == "engineer_modified"
    assert photos[str(ids["photo_b"])]["before"] == {"room_id": str(ids["bath"])}
    assert photos[str(ids["photo_b"])]["after"] == {"room_id": str(ids["kitchen"])}
    inventory = {item["label"]: item for item in kitchen["inventory"]}
    assert inventory["kitchen_cabinet"]["provenance"]["origin"] == "engineer_modified"
    assert inventory["kitchen_cabinet"]["provenance"]["before"] == {"count": 6, "material": "wood"}
    assert inventory["kitchen_cabinet"]["provenance"]["changed"] == ["count", "material"]
    assert inventory["bathtub"]["provenance"]["origin"] == "model"
    assert inventory["light_fixture"]["provenance"]["origin"] == "engineer_added"
    assert inventory["light_fixture"]["provenance"]["after"]["label"] == "light_fixture"
    assert inventory["light_fixture"]["added"] is True

    fields = (await api.get("alice", f"/v1/studies/{sid}/fields", params={"revision": 2})).json()
    [field] = fields["items"]
    assert field["provenance"]["origin"] == "engineer_modified"
    assert field["provenance"]["before"]["value_text"] == "1905"
    assert field["provenance"]["after"]["value_text"] == "1925"
    assert field["provenance"]["carried_from"] == str(ids["field"])
    history = (await api.get("alice", f"/v1/studies/{sid}/fields", params={"revision": 1})).json()
    assert history["items"][0]["provenance"]["origin"] == "model"

    assets = (await api.get("alice", f"/v1/studies/{sid}/assets", params={"revision": 2})).json()
    by_component = {a["component_id"]: a for a in assets["items"]}
    cabinet_asset = by_component[str(ids["cabinet_2"])]
    assert cabinet_asset["provenance"]["origin"] == "engineer_modified"
    assert cabinet_asset["provenance"]["before"]["bucket"] == "y5"
    assert cabinet_asset["provenance"]["after"] == {"bucket": "y7", "recovery_period": 7}
    assert cabinet_asset["provenance"]["carried_from"] == str(ids["cabinet_asset"])
    lamp_asset = by_component[str(ids["lamp_2"])]
    assert lamp_asset["provenance"]["origin"] == "engineer_added"
    assert lamp_asset["review_codes"] == []

    # ---- the audit listing
    audit = (await api.get("alice", f"/v1/studies/{sid}/audit")).json()
    items = audit["items"]
    actions = sorted((i["kind"], i["action"], i["entity_type"]) for i in items)
    assert actions == sorted(
        [
            ("decision", "rejected", "review"),
            ("correction", "modified", "room"),
            ("correction", "merged", "room"),
            ("correction", "moved", "photo_room"),
            ("correction", "modified", "component"),
            ("correction", "added", "room_component"),
            ("correction", "modified", "extraction_field"),
            ("correction", "modified", "asset"),
            ("object_added", "added", "photo_object"),
            ("object_deleted", "deleted", "photo_object"),
        ]
    )
    assert [i["at"] for i in items] == sorted(i["at"] for i in items)
    rename = next(i for i in items if i["action"] == "modified" and i["entity_type"] == "room")
    assert rename["produced"] == [str(ids["kitchen_2"])]  # the forward link from the event
    assert rename["reason"] == "the listing calls it the main kitchen"
    assert rename["review_id"] == decided["review"]["id"]
    assert rename["stage"] == "rooms_review" and rename["by"] == str(reviewer)
    merged = next(i for i in items if i["action"] == "merged")
    assert merged["entity_id"] == str(ids["bath"]) and merged["changed"] == ["merge_into"]
    deleted = next(i for i in items if i["kind"] == "object_deleted")
    assert deleted["entity_id"] == str(object_id) and deleted["before"]["label"] == "light_fixture"

    # ---- the timeline
    timeline = (await api.get("alice", f"/v1/studies/{sid}/timeline")).json()
    types = [e["event_type"] for e in timeline["items"]]
    assert types[0] == "study.created"
    assert types.count("stage.entered") >= 1  # the decision moved the study
    assert "review.decided" in types and types.count("correction.recorded") == 3
    assert "entity.corrected" in types
    assert [e["seq"] for e in timeline["items"]] == sorted(e["seq"] for e in timeline["items"])
    decided_event = next(e for e in timeline["items"] if e["event_type"] == "review.decided")
    assert decided_event["actor_type"] == "engineer" and decided_event["actor_id"] == str(reviewer)
    assert decided_event["detail"]["corrections"] == 3
    assert decided_event["request_id"]  # the API request that caused it
    moved = next(e for e in timeline["items"] if e["event_type"] == "stage.entered" and e["before"])
    assert moved["before"] == {"stage": "rooms_review", "revision": 1}
    assert moved["after"] == {"stage": "extracting", "revision": 2}
    assert moved["actor_type"] == "engineer"
    # Pagination and the filter.
    page = (await api.get("alice", f"/v1/studies/{sid}/timeline", params={"limit": 2})).json()
    assert len(page["items"]) == 2 and page["next_cursor"] == str(page["items"][-1]["seq"])
    rest = (
        await api.get(
            "alice", f"/v1/studies/{sid}/timeline", params={"cursor": page["next_cursor"]}
        )
    ).json()
    assert [e["seq"] for e in page["items"]] + [e["seq"] for e in rest["items"]] == [
        e["seq"] for e in timeline["items"]
    ]
    only = (
        await api.get(
            "alice", f"/v1/studies/{sid}/timeline", params={"event_type": ["review.decided"]}
        )
    ).json()
    assert [e["event_type"] for e in only["items"]] == ["review.decided"]
    bad = await api.get("alice", f"/v1/studies/{sid}/timeline", params={"cursor": "x"})
    assert bad.status_code == 422 and bad.json()["code"] == E.API_INVALID_CURSOR
    steps = (await api.get("alice", f"/v1/studies/{sid}/timeline/steps")).json()
    assert steps["steps"] == []  # no worker ran here
    [visit] = steps["stages"]
    assert (visit["stage"], visit["revision"], visit["entered_by"]) == ("extracting", 2, "engineer")

    # ---- viewer access, 404 for a stranger, with codes
    forbidden = await api.get("bob", f"/v1/studies/{sid}/timeline")
    assert forbidden.status_code == 404 and forbidden.json()["code"] == E.STUDY_NOT_FOUND
    assert forbidden.json()["type"].endswith("/not-found")


async def test_problem_responses_carry_the_catalogue_code(
    api: Api, workflows: FakeWorkflowService
) -> None:
    study = await api.create_study("alice")
    sid = study["id"]
    started = await api.post("alice", f"/v1/studies/{sid}/start")
    assert started.status_code == 422
    body = started.json()
    assert body["type"].endswith("/intake-required") and body["code"] == E.STUDY_INTAKE_REQUIRED
    put = await api.client.put(
        f"/v1/studies/{sid}/intake",
        json=COMPLETE_INTAKE,
        headers={"Authorization": "Bearer uid:alice"},
    )
    assert put.status_code == 200
    started = await api.post("alice", f"/v1/studies/{sid}/start")
    assert started.status_code == 409
    assert started.json()["code"] == E.STUDY_APPRAISAL_REQUIRED
    missing = await api.get("alice", f"/v1/studies/{uuid.uuid4()}")
    assert missing.json()["code"] == E.STUDY_NOT_FOUND
    no_key = await api.client.post(
        f"/v1/studies/{sid}/cancel", headers={"Authorization": "Bearer uid:alice"}
    )
    assert no_key.status_code == 400 and no_key.json()["code"] == E.API_IDEMPOTENCY_KEY_MISSING
    unauthenticated = await api.client.get("/v1/studies")
    assert unauthenticated.json()["code"] == E.API_AUTH_MISSING
    bad_body = await api.post("alice", "/v1/studies", {"property_name": 1})
    assert bad_body.status_code == 422 and bad_body.json()["code"] == E.API_VALIDATION
    approval = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        {
            "stage": "appraisal_review",
            "revision": 1,
            "decision": "approve",
            "corrections": [
                {"entity_type": "extraction_field", "entity_id": str(uuid.uuid4()), "after": {}}
            ],
        },
    )
    assert approval.status_code == 422
    assert approval.json()["code"] == E.STUDY_APPROVAL_WITH_CORRECTIONS
    # The intake event is on the timeline, with the document.
    timeline = (await api.get("alice", f"/v1/studies/{sid}/timeline")).json()
    intake = next(e for e in timeline["items"] if e["event_type"] == "study.intake_set")
    assert intake["after"]["purchase_price"] == "650000.00" and intake["before"] is None
    audit = (await api.get("alice", f"/v1/studies/{sid}/audit")).json()
    assert [i["kind"] for i in audit["items"]] == ["intake"]
    assert datetime.fromisoformat(audit["items"][0]["at"]).tzinfo is not None
    assert datetime.now(UTC) > datetime.fromisoformat(audit["items"][0]["at"])
