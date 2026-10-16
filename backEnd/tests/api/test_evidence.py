"""``GET /v1/studies/{id}/evidence``: per asset, its room and photos, the localized instances
of its label in them, and its rule citations.

Seeded study (revision 2 is current; revision 1 has an asset that must never appear):

* Kitchen with two photos (k1, k2); a cabinet component found in k1 -> asset A1 with a
  verified citation and an unverified one; k1 has two cabinet instances (one deleted) and a
  window instance, k2 one cabinet instance; k1 localized ``done``, k2 not localized.
* Bathroom with one photo (b1); a toilet component -> asset A2, no citations, b1 localized
  ``failed``.
* Three more assets without components, for pagination.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from apitest_support import Api, Seed
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import Bucket, ClassificationMethod, DocumentKind, OrgRole, Stage
from basis.db.models import (
    Asset,
    Chunk,
    Citation,
    Component,
    CorpusVersion,
    PhotoRoom,
    Room,
)
from basis.db.repositories import DocumentRepository
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository
from basis.db.session import session_scope

pytestmark = pytest.mark.integration


@dataclass
class Seeded:
    study_id: str
    photos: dict[str, uuid.UUID]
    assets: dict[str, uuid.UUID]
    objects: dict[str, uuid.UUID]
    rooms: dict[str, uuid.UUID]


async def seed_evidence(api: Api, seed: Seed, sm: async_sessionmaker[AsyncSession]) -> Seeded:
    study = await api.create_study("alice")
    sid = uuid.UUID(study["id"])
    await seed.stage(sid, Stage.ASSETS_REVIEW, revision=2)
    photos: dict[str, uuid.UUID] = {}
    assets: dict[str, uuid.UUID] = {}
    objects: dict[str, uuid.UUID] = {}
    rooms: dict[str, uuid.UUID] = {}
    async with session_scope(sm) as session:
        documents = DocumentRepository(session)
        for name in ("k1", "k2", "b1"):
            document_id = uuid.uuid4()
            await documents.create(
                study_id=sid,
                kind=DocumentKind.PHOTO,
                filename=f"{name}.jpg",
                content_type="image/jpeg",
                size_bytes=10,
                sha256=uuid.uuid4().hex * 2,
                gcs_uri=f"gs://basis-local/studies/{sid}/{document_id}/{name}.jpg",
                uploaded_by=None,
                document_id=document_id,
            )
            photos[name] = document_id
        kitchen = Room(
            study_id=sid, revision=2, name="Kitchen", room_type="kitchen", source="vision"
        )
        bathroom = Room(
            study_id=sid,
            revision=2,
            name="Bathroom",
            room_type="bathroom",
            floor="1",
            source="vision",
        )
        old_room = Room(
            study_id=sid, revision=1, name="Kitchen", room_type="kitchen", source="vision"
        )
        session.add_all([kitchen, bathroom, old_room])
        await session.flush()
        rooms = {"kitchen": kitchen.id, "bathroom": bathroom.id}
        session.add_all(
            [
                PhotoRoom(study_id=sid, document_id=photos["k1"], room_id=kitchen.id, revision=2),
                PhotoRoom(study_id=sid, document_id=photos["k2"], room_id=kitchen.id, revision=2),
                PhotoRoom(study_id=sid, document_id=photos["b1"], room_id=bathroom.id, revision=2),
            ]
        )
        cabinet = Component(
            study_id=sid,
            revision=2,
            document_id=photos["k1"],
            room_id=kitchen.id,
            label="kitchen_cabinet",
        )
        toilet = Component(
            study_id=sid, revision=2, document_id=photos["b1"], room_id=bathroom.id, label="toilet"
        )
        old = Component(
            study_id=sid, revision=1, document_id=photos["k1"], room_id=old_room.id, label="x"
        )
        session.add_all([cabinet, toilet, old])
        await session.flush()

        def asset(component: Component | None, revision: int = 2, category: str = "c") -> Asset:
            return Asset(
                study_id=sid,
                revision=revision,
                component_id=component.id if component is not None else None,
                category=category,
                bucket=Bucket.Y5,
                recovery_period=Decimal("5"),
                method=ClassificationMethod.RULE,
            )

        a1, a2, a_old = asset(cabinet, category="cabinetry"), asset(toilet), asset(old, 1)
        extra = [asset(None, category=f"extra-{i}") for i in range(3)]
        session.add_all([a1, a2, a_old, *extra])
        await session.flush()
        assets = {"a1": a1.id, "a2": a2.id, "old": a_old.id}
        for i, row in enumerate(extra):
            assets[f"extra{i}"] = row.id

        version = CorpusVersion(corpus="reference", version=1, embedding_model="hash")
        session.add(version)
        await session.flush()
        chunk = Chunk(
            corpus_version_id=version.id,
            doc_id="rev_proc_87_56",
            chunk_key="rp8756:12:0",
            page_start=12,
            page_end=13,
            section_path="Asset class 57.0",
            text="Distributive trades and services ...",
        )
        session.add(chunk)
        await session.flush()
        session.add_all(
            [
                Citation(
                    study_id=sid,
                    asset_id=a1.id,
                    chunk_id=chunk.id,
                    excerpt="Distributive trades and services",
                    verified_at=datetime.now(UTC),
                    verification_method="exact",
                ),
                Citation(study_id=sid, asset_id=a1.id, chunk_id=chunk.id, excerpt="unverified"),
            ]
        )
        repo = PhotoObjectRepository(session)

        async def add(name: str, photo: str, label: str, x1: float, revision: int = 2) -> None:
            row = await repo.add(
                study_id=sid,
                document_id=photos[photo],
                revision=revision,
                new=NewObject(
                    label=label,
                    score=0.8,
                    x1=x1,
                    y1=0.1,
                    x2=x1 + 0.1,
                    y2=0.2,
                    area=0.01,
                    source="model",
                ),
            )
            objects[name] = row.id

        await add("k1_cab", "k1", "kitchen_cabinet", 0.1)
        await add("k1_cab_deleted", "k1", "kitchen_cabinet", 0.3)
        await add("k1_window", "k1", "window", 0.5)
        await add("k2_cab", "k2", "kitchen_cabinet", 0.1)
        await add("k1_cab_r1", "k1", "kitchen_cabinet", 0.7, revision=1)
        alice = await seed.user("alice")
        deleted = await repo.get(sid, objects["k1_cab_deleted"])
        assert deleted is not None
        await repo.soft_delete(deleted, alice.id)
        await repo.record_localization(
            study_id=sid, document_id=photos["k1"], revision=2, status="done", instances=3
        )
        await repo.record_localization(
            study_id=sid, document_id=photos["b1"], revision=2, status="failed", reason="429"
        )
    return Seeded(str(sid), photos, assets, objects, rooms)


def by_asset(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["asset_id"]: item for item in body["items"]}


async def test_each_asset_carries_its_room_photos_instances_and_citations(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    s = await seed_evidence(api, seed, sessionmaker)
    response = await api.get("alice", f"/v1/studies/{s.study_id}/evidence")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 2 and body["next_cursor"] is None
    items = by_asset(body)
    assert str(s.assets["old"]) not in items  # revision 1 is history
    assert len(items) == 5

    a1 = items[str(s.assets["a1"])]
    assert (a1["category"], a1["bucket"], a1["component_label"]) == (
        "cabinetry",
        "y5",
        "kitchen_cabinet",
    )
    assert a1["room"] == {
        "id": str(s.rooms["kitchen"]),
        "name": "Kitchen",
        "room_type": "kitchen",
        "floor": None,
    }
    assert [(p["filename"], p["role"], p["localization_status"]) for p in a1["photos"]] == [
        ("k1.jpg", "source", "done"),
        ("k2.jpg", "room", None),
    ]
    k1, k2 = a1["photos"]
    # Only the component's label, only active rows, only this revision.
    assert [i["id"] for i in k1["instances"]] == [str(s.objects["k1_cab"])]
    assert [i["id"] for i in k2["instances"]] == [str(s.objects["k2_cab"])]
    assert k1["instances"][0]["box"] == pytest.approx({"x1": 0.1, "y1": 0.1, "x2": 0.2, "y2": 0.2})

    citations = sorted(a1["citations"], key=lambda c: c["excerpt"])
    assert [(c["excerpt"], c["verified"]) for c in citations] == [
        ("Distributive trades and services", True),
        ("unverified", False),
    ]
    verified = citations[0]
    assert verified["verification_method"] == "exact" and verified["verified_at"]
    assert verified["source"] == {
        "doc_id": "rev_proc_87_56",
        "page_start": 12,
        "page_end": 13,
        "section_path": "Asset class 57.0",
    }

    a2 = items[str(s.assets["a2"])]
    assert a2["room"]["name"] == "Bathroom" and a2["citations"] == []
    assert [
        (p["filename"], p["role"], p["localization_status"], p["instances"]) for p in a2["photos"]
    ] == [("b1.jpg", "source", "failed", [])]

    extra = items[str(s.assets["extra0"])]
    assert (extra["component_id"], extra["room"], extra["photos"]) == (None, None, [])


async def test_filter_by_asset(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    s = await seed_evidence(api, seed, sessionmaker)
    one = await api.get(
        "alice", f"/v1/studies/{s.study_id}/evidence", params={"asset_id": str(s.assets["a2"])}
    )
    assert [i["asset_id"] for i in one.json()["items"]] == [str(s.assets["a2"])]
    old = await api.get(
        "alice", f"/v1/studies/{s.study_id}/evidence", params={"asset_id": str(s.assets["old"])}
    )
    assert old.status_code == 200 and old.json()["items"] == []  # not in the current revision
    unknown = await api.get(
        "alice", f"/v1/studies/{s.study_id}/evidence", params={"asset_id": str(uuid.uuid4())}
    )
    assert unknown.json()["items"] == []


async def test_pages_cover_every_asset_once_in_id_order(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    s = await seed_evidence(api, seed, sessionmaker)
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = (await api.get("alice", f"/v1/studies/{s.study_id}/evidence", params=params)).json()
        pages += 1
        assert len(body["items"]) <= 2
        seen += [i["asset_id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    current = {str(v) for k, v in s.assets.items() if k != "old"}
    assert sorted(seen) == seen and set(seen) == current and len(seen) == len(current)


async def test_a_cursor_stays_on_its_revision(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    s = await seed_evidence(api, seed, sessionmaker)
    first = (
        await api.get("alice", f"/v1/studies/{s.study_id}/evidence", params={"limit": 2})
    ).json()
    await seed.stage(s.study_id, Stage.ROOMS_REVIEW, revision=3)  # a rejection meanwhile
    rest = (
        await api.get(
            "alice",
            f"/v1/studies/{s.study_id}/evidence",
            params={"limit": 100, "cursor": first["next_cursor"]},
        )
    ).json()
    assert rest["revision"] == 2 and len(rest["items"]) == 3
    fresh = (await api.get("alice", f"/v1/studies/{s.study_id}/evidence")).json()
    assert (fresh["revision"], fresh["items"]) == (3, [])


@pytest.mark.parametrize("cursor", ["not-base64!", "e30", "eyJyIjowLCJpZCI6IngifQ"])
async def test_a_bad_cursor_is_422(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], cursor: str
) -> None:
    s = await seed_evidence(api, seed, sessionmaker)
    response = await api.get(
        "alice", f"/v1/studies/{s.study_id}/evidence", params={"cursor": cursor}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("limit", [0, 101])
async def test_limit_is_bounded(api: Api, seed: Seed, limit: int) -> None:
    study = await api.create_study("alice")
    response = await api.get(
        "alice", f"/v1/studies/{study['id']}/evidence", params={"limit": limit}
    )
    assert response.status_code == 422


async def test_a_study_without_assets_has_an_empty_page(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    body = (await api.get("alice", f"/v1/studies/{study['id']}/evidence")).json()
    assert body == {"revision": 1, "items": [], "next_cursor": None}


async def test_viewers_may_read_and_strangers_see_404(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    s = await seed_evidence(api, seed, sessionmaker)
    org = await seed.personal_org("alice")
    await seed.member(org, "victor", OrgRole.VIEWER)
    assert (await api.get("victor", f"/v1/studies/{s.study_id}/evidence")).status_code == 200
    assert (await api.get("mallory", f"/v1/studies/{s.study_id}/evidence")).status_code == 404
