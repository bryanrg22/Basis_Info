"""``GET /v1/studies/{id}/fields``, ``/rooms``, ``/assets``: the revision's outputs.

EVAL_REPORT section 23 finding 5 / section 27 item 5: the review gates' contents through
the API (the live client had to read the database). Real Postgres; synthetic rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
    CostEstimate,
    Extraction,
    ExtractionField,
    PhotoRoom,
    Room,
    Takeoff,
)
from basis.db.repositories import DocumentRepository
from basis.db.session import session_scope

pytestmark = pytest.mark.integration


@dataclass
class Seeded:
    study_id: str
    photos: dict[str, uuid.UUID]
    rooms: dict[str, uuid.UUID]
    assets: dict[str, uuid.UUID]
    fields: dict[str, uuid.UUID]


async def seed_outputs(api: Api, seed: Seed, sm: async_sessionmaker[AsyncSession]) -> Seeded:
    """Revision 2 (current) and 1 (history): fields, rooms with inventories, assets."""
    study = await api.create_study("alice")
    sid = uuid.UUID(study["id"])
    await seed.stage(sid, Stage.ASSETS_REVIEW, revision=2)
    photos: dict[str, uuid.UUID] = {}
    async with session_scope(sm) as session:
        documents = DocumentRepository(session)
        for name, kind in (
            ("appraisal.pdf", DocumentKind.APPRAISAL),
            ("Kitchen photo 2.jpg", DocumentKind.PHOTO),
            ("Kitchen photo 1.jpg", DocumentKind.PHOTO),
            ("Bath.jpg", DocumentKind.PHOTO),
        ):
            document_id = uuid.uuid4()
            await documents.create(
                study_id=sid,
                kind=kind,
                filename=name,
                content_type="image/jpeg",
                size_bytes=10,
                sha256=uuid.uuid4().hex * 2,
                gcs_uri=f"gs://basis-local/studies/{sid}/{document_id}/x",
                uploaded_by=None,
                document_id=document_id,
            )
            photos[name] = document_id
        fields: dict[str, uuid.UUID] = {}
        for revision in (1, 2):
            extraction = Extraction(
                study_id=sid,
                document_id=photos["appraisal.pdf"],
                revision=revision,
                method="azure_di+regex",
                overall_confidence=0.6,
                raw={},
            )
            session.add(extraction)
            await session.flush()
            for section, name, text, num, day, source in (
                ("subject", "property_address", "12 Main St", None, None, "azure"),
                ("improvements", "year_built", "1905", Decimal(1905), None, "azure"),
                ("improvements", "bedrooms", "6", Decimal(6), None, "llm"),
                ("reconciliation", "effective_date_of_appraisal", "1/29/25", None,
                 date(2025, 1, 29), "azure"),
                ("neighborhood", "price_low_thousands", "130", Decimal(130), None, "llm"),
            ):  # fmt: skip
                row = ExtractionField(
                    extraction_id=extraction.id,
                    study_id=sid,
                    section=section,
                    field=name,
                    value_text=text,
                    value_num=num,
                    value_date=day,
                    confidence=0.6 if source == "llm" else 0.95,
                    source=source,
                    page=1,
                )
                session.add(row)
                await session.flush()
                if revision == 2:
                    fields[f"{section}.{name}"] = row.id
        kitchen = Room(
            study_id=sid,
            revision=2,
            name="Kitchen",
            room_type="kitchen",
            floor="1",
            source="vision",
            group_key="x_kitchen",
        )
        bath = Room(study_id=sid, revision=2, name="Bath", room_type="bathroom", source="vision")
        old = Room(study_id=sid, revision=1, name="Old", room_type="kitchen", source="vision")
        session.add_all([kitchen, bath, old])
        await session.flush()
        k1, k2, b1 = (
            photos["Kitchen photo 1.jpg"],
            photos["Kitchen photo 2.jpg"],
            photos["Bath.jpg"],
        )
        session.add_all(
            [
                PhotoRoom(study_id=sid, document_id=k2, room_id=kitchen.id, revision=2),
                PhotoRoom(study_id=sid, document_id=k1, room_id=kitchen.id, revision=2),
                PhotoRoom(study_id=sid, document_id=b1, room_id=bath.id, revision=2),
            ]
        )
        per_photo = [
            Component(study_id=sid, revision=2, document_id=k1, room_id=kitchen.id,
                      label="kitchen_cabinet", raw={"source": "vision", "count": 6}),
            Component(study_id=sid, revision=2, document_id=k2, room_id=kitchen.id,
                      label="kitchen_cabinet", raw={"source": "vision", "count": 6}),
            Component(study_id=sid, revision=2, document_id=b1, room_id=bath.id,
                      label="toilet", raw={"source": "vision", "count": 1}),
        ]  # fmt: skip
        inventory = Component(
            study_id=sid,
            revision=2,
            document_id=None,
            room_id=kitchen.id,
            label="kitchen_cabinet",
            material="wood",
            confidence=0.9,
            raw={
                "source": "room_inventory",
                "count": 6,
                "seen_in": [str(k1), str(k2), "not-a-uuid"],
                "room_needs_review": True,
            },
        )
        session.add_all([*per_photo, inventory])
        await session.flush()

        def asset(component: Component | None, revision: int = 2, **extra: Any) -> Asset:
            values: dict[str, Any] = {
                "category": "c",
                "bucket": Bucket.Y5,
                "recovery_period": Decimal("5"),
                "method": ClassificationMethod.RULE,
                **extra,
            }
            return Asset(
                study_id=sid,
                revision=revision,
                component_id=component.id if component is not None else None,
                **values,
            )

        a1 = asset(inventory, category="cabinetry", irs_section="1245")
        a2 = asset(
            per_photo[2],
            bucket=Bucket.Y39,
            recovery_period=Decimal("39"),
            needs_review=True,
            review_reason="cross:COST_ROW_MISSING; rule_contested:toilet",
        )
        a_old = asset(per_photo[0], revision=1)
        extra = [asset(None, category=f"extra-{i}") for i in range(3)]
        session.add_all([a1, a2, a_old, *extra])
        await session.flush()
        session.add_all(
            [
                Takeoff(study_id=sid, asset_id=a1.id, quantity=Decimal("6"), unit="EA",
                        formula="max(count=6, min_per_room=1)"),
                CostEstimate(study_id=sid, asset_id=a1.id, unit_cost=Decimal("543.98"),
                             total=Decimal("3263.88"), source_row="row", cost_year=2025,
                             location_factor=Decimal("1.000")),
                CostEstimate(study_id=sid, asset_id=a2.id, source_row="missing"),
            ]
        )  # fmt: skip
        version = CorpusVersion(corpus="reference", version=1, embedding_model="hash")
        session.add(version)
        await session.flush()
        chunk = Chunk(
            corpus_version_id=version.id,
            doc_id="d",
            chunk_key="d:1:0",
            page_start=1,
            page_end=1,
            section_path="s",
            text="Distributive trades and services ...",
        )
        session.add(chunk)
        await session.flush()
        session.add_all(
            [
                Citation(study_id=sid, asset_id=a1.id, chunk_id=chunk.id, excerpt="x",
                         verified_at=datetime.now(UTC), verification_method="exact"),
                Citation(study_id=sid, asset_id=a1.id, chunk_id=chunk.id, excerpt="y"),
            ]
        )  # fmt: skip
        assets = {"a1": a1.id, "a2": a2.id, "old": a_old.id}
        for i, row in enumerate(extra):
            assets[f"extra{i}"] = row.id
    return Seeded(
        str(sid), photos, {"kitchen": kitchen.id, "bath": bath.id, "old": old.id}, assets, fields
    )


async def pages(api: Api, path: str, limit: int) -> list[dict[str, Any]]:
    """Every page of ``path`` at ``limit``, following ``next_cursor``."""
    found: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = f"?limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        response = await api.get("alice", f"{path}{query}")
        assert response.status_code == 200, response.text
        body = response.json()
        found.append(body)
        cursor = body["next_cursor"]
        if cursor is None:
            return found


# --------------------------------------------------------------------------- fields


async def test_fields_of_the_current_revision(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    seeded = await seed_outputs(api, seed, sessionmaker)
    response = await api.get("alice", f"/v1/studies/{seeded.study_id}/fields")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 2 and body["next_cursor"] is None
    keys = [item["key"] for item in body["items"]]
    assert keys == sorted(keys) and len(keys) == 5  # ordered by section, then field
    by_key = {item["key"]: item for item in body["items"]}
    year = by_key["improvements.year_built"]
    assert year["id"] == str(seeded.fields["improvements.year_built"])
    assert (year["label"], year["critical"], year["value_num"], year["source"]) == (
        "Year Built",
        True,
        "1905",
        "azure",
    )
    assert year["document_id"] == str(seeded.photos["appraisal.pdf"])
    assert by_key["improvements.bedrooms"]["critical"] is True  # section 27
    assert by_key["reconciliation.effective_date_of_appraisal"]["value_date"] == "2025-01-29"
    price = by_key["neighborhood.price_low_thousands"]
    assert (price["label"], price["critical"]) == ("One-Unit Housing PRICE $(000) Low", False)


async def test_fields_paginate_and_an_earlier_revision_is_history(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    seeded = await seed_outputs(api, seed, sessionmaker)
    path = f"/v1/studies/{seeded.study_id}/fields"
    found = await pages(api, path, limit=2)
    assert len(found) == 3 and all(page["revision"] == 2 for page in found)
    ids = [item["id"] for page in found for item in page["items"]]
    assert len(ids) == len(set(ids)) == 5
    old = (await api.get("alice", f"{path}?revision=1")).json()
    assert old["revision"] == 1 and len(old["items"]) == 5
    assert {i["id"] for i in old["items"]}.isdisjoint(ids)
    missing = await api.get("alice", f"{path}?revision=3")
    assert missing.status_code == 404


# --------------------------------------------------------------------------- rooms


async def test_rooms_with_photos_and_inventories(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    seeded = await seed_outputs(api, seed, sessionmaker)
    body = (await api.get("alice", f"/v1/studies/{seeded.study_id}/rooms")).json()
    assert body["revision"] == 2
    assert [room["name"] for room in body["items"]] == ["Bath", "Kitchen"]
    bath, kitchen = body["items"]
    k1, k2 = seeded.photos["Kitchen photo 1.jpg"], seeded.photos["Kitchen photo 2.jpg"]
    assert [p["filename"] for p in kitchen["photos"]] == [
        "Kitchen photo 1.jpg",
        "Kitchen photo 2.jpg",
    ]
    assert (kitchen["group_key"], kitchen["floor"], kitchen["source"]) == (
        "x_kitchen",
        "1",
        "vision",
    )
    # A reconciled room lists its inventory rows, not its per-photo rows.
    assert kitchen["inventory_source"] == "room_inventory" and kitchen["needs_review"] is True
    [cabinets] = kitchen["inventory"]
    assert (cabinets["label"], cabinets["count"], cabinets["material"]) == (
        "kitchen_cabinet",
        6,
        "wood",
    )
    assert cabinets["document_id"] is None
    assert cabinets["seen_in"] == [str(k1), str(k2)]  # the malformed id is skipped
    # A room without an inventory lists its per-photo rows.
    assert bath["inventory_source"] == "per_photo" and bath["needs_review"] is False
    [toilet] = bath["inventory"]
    assert toilet["document_id"] == str(seeded.photos["Bath.jpg"]) and toilet["seen_in"] == []
    assert bath["group_key"] is None


async def test_rooms_paginate(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    seeded = await seed_outputs(api, seed, sessionmaker)
    found = await pages(api, f"/v1/studies/{seeded.study_id}/rooms", limit=1)
    assert [page["items"][0]["name"] for page in found] == ["Bath", "Kitchen"]
    old = (await api.get("alice", f"/v1/studies/{seeded.study_id}/rooms?revision=1")).json()
    assert [r["name"] for r in old["items"]] == ["Old"] and old["items"][0]["photos"] == []


# --------------------------------------------------------------------------- assets


async def test_assets_with_takeoff_cost_and_citations(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    seeded = await seed_outputs(api, seed, sessionmaker)
    body = (await api.get("alice", f"/v1/studies/{seeded.study_id}/assets")).json()
    assert body["revision"] == 2 and len(body["items"]) == 5
    ids = [item["id"] for item in body["items"]]
    assert ids == sorted(ids)
    by_id = {item["id"]: item for item in body["items"]}
    a1 = by_id[str(seeded.assets["a1"])]
    assert (a1["label"], a1["count"], a1["category"], a1["irs_section"]) == (
        "kitchen_cabinet",
        6,
        "cabinetry",
        "1245",
    )
    assert a1["room"]["id"] == str(seeded.rooms["kitchen"])
    assert a1["takeoff"] == {
        "quantity": "6.0000",  # takeoffs.quantity is NUMERIC(.., 4)
        "unit": "EA",
        "formula": "max(count=6, min_per_room=1)",
    }
    assert a1["cost"]["total"] == "3263.88" and a1["cost"]["cost_year"] == 2025
    assert (a1["citations"], a1["verified_citations"]) == (2, 1)
    assert a1["review_reasons"] == [] and a1["needs_review"] is False
    a2 = by_id[str(seeded.assets["a2"])]
    assert a2["review_reasons"] == ["cross:COST_ROW_MISSING", "rule_contested:toilet"]
    assert a2["cost"]["unit_cost"] is None and a2["takeoff"] is None
    assert a2["bucket"] == "y39" and a2["room"]["name"] == "Bath"
    orphan = by_id[str(seeded.assets["extra0"])]
    assert orphan["label"] is None and orphan["room"] is None and orphan["count"] is None


async def test_assets_filter_and_paginate(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    seeded = await seed_outputs(api, seed, sessionmaker)
    path = f"/v1/studies/{seeded.study_id}/assets"
    flagged = (await api.get("alice", f"{path}?needs_review=true")).json()
    assert [i["id"] for i in flagged["items"]] == [str(seeded.assets["a2"])]
    clean = (await api.get("alice", f"{path}?needs_review=false")).json()
    assert len(clean["items"]) == 4
    found = await pages(api, path, limit=2)
    assert [len(page["items"]) for page in found] == [2, 2, 1]
    old = (await api.get("alice", f"{path}?revision=1")).json()
    assert [i["id"] for i in old["items"]] == [str(seeded.assets["old"])]


# --------------------------------------------------------------------------- access and errors


@pytest.mark.parametrize("route", ["fields", "rooms", "assets"])
async def test_access_and_errors(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], route: str
) -> None:
    seeded = await seed_outputs(api, seed, sessionmaker)
    path = f"/v1/studies/{seeded.study_id}/{route}"
    org = (await api.get("alice", f"/v1/studies/{seeded.study_id}")).json()["org_id"]
    await seed.member(uuid.UUID(org), "vera", OrgRole.VIEWER)
    assert (await api.get("vera", path)).status_code == 200  # a viewer may read
    assert (await api.get("mallory", path)).status_code == 404  # not a member
    assert (await api.get("alice", f"/v1/studies/{uuid.uuid4()}/{route}")).status_code == 404
    for bad in ("limit=0", "limit=1000", "revision=0", "cursor=", "cursor=%21%21"):
        response = await api.get("alice", f"{path}?{bad}")
        assert response.status_code == 422, (bad, response.text)
    unauthenticated = await api.client.get(path)
    assert unauthenticated.status_code == 401


async def test_a_study_with_no_outputs_has_empty_pages(api: Api) -> None:
    study = await api.create_study("alice")
    for route in ("fields", "rooms", "assets"):
        body = (await api.get("alice", f"/v1/studies/{study['id']}/{route}")).json()
        assert body == {"revision": 1, "items": [], "next_cursor": None}
