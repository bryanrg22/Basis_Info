"""``carry_revision``: the copy a reopen or restore starts from (docs/api/REVISIONS.md).

Revision 1 is built by the real activities (the extraction, three photos analyzed, a room
inventory written the way ``reconcile_rooms`` writes it, the assets), then copied through
each gate: below the components review the inventory rows stay behind and no asset is
copied; at the components review the inventories come along; at the assets review the
assets too, each on its component's copy, with its takeoff, cost estimate and citations.
The rows of revision 1 are never changed, a retried attempt returns the stored output
without a second copy, and the ``revision.carried`` event says what was copied.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lifecycletest_support import SeededStudy
from sqlalchemy import select

from basis.activities import Deps
from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.carry_revision import CarryRevision
from basis.activities.extract_fields import ExtractFields
from basis.activities.process_assets import ProcessAssets
from basis.db.models import (
    Asset,
    Citation,
    Component,
    CostEstimate,
    Extraction,
    ExtractionField,
    PhotoLocalization,
    PhotoObject,
    PhotoRoom,
    Room,
    StudyEvent,
    Takeoff,
)
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.vision.rooms import ROOM_INVENTORY_SOURCE, is_room_inventory
from basis.workflow.contracts import (
    AnalyzePhotoInput,
    CarryRevisionInput,
    ExtractFieldsInput,
    ProcessAssetsInput,
)

from .conftest import Env

pytestmark = pytest.mark.integration


async def rows(deps: Deps, model: Any, study_id: uuid.UUID, revision: int) -> list[Any]:
    async with session_scope(deps.sessionmaker) as session:
        stmt = select(model).where(model.study_id == study_id, model.revision == revision)
        return list((await session.scalars(stmt)).all())


@pytest.fixture
async def revision_one(deps: Deps, seeded: SeededStudy, activity_env: Env) -> SeededStudy:
    """Revision 1 as the pipeline leaves it at the assets review: the extraction, the three
    photos analyzed, the kitchen's inventory (two rows, as the room step writes them) and
    the assets of every component."""
    sid = seeded.study_id
    await activity_env.run(
        ExtractFields(deps).run,
        ExtractFieldsInput(
            study_id=str(sid),
            document_id=str(seeded.appraisal_id),
            revision=1,
            idempotency_key="k-x1",
        ),
    )
    for photo in seeded.photo_ids:
        await activity_env.run(
            AnalyzePhoto(deps).run,
            AnalyzePhotoInput(
                study_id=str(sid),
                document_id=str(photo),
                revision=1,
                idempotency_key=f"k-p-{photo}",
                budget_remaining_usd=1.0,
            ),
        )
    async with session_scope(deps.sessionmaker) as session:
        repo = RoomRepository(session)
        kitchen = next(r for r in await repo.list_for_revision(sid, 1) if r.name == "Kitchen")
        photos = sorted(
            str(pr.document_id) for pr in await repo.photo_rooms(sid, 1) if pr.room_id == kitchen.id
        )
        for label in ("kitchen_cabinet", "sink"):
            await repo.add_component(
                study_id=sid,
                revision=1,
                document_id=None,
                room_id=kitchen.id,
                label=label,
                material="wood",
                attachment=None,
                confidence=0.9,
                bbox=None,
                raw={"source": ROOM_INVENTORY_SOURCE, "count": 2, "photos": photos},
            )
    async with session_scope(deps.sessionmaker) as session:
        # A localization and an object per photo, as the harness without a localizer leaves
        # them (skipped) and as an engineer's manual selection would.
        objects = PhotoObjectRepository(session)
        for photo in seeded.photo_ids:
            await objects.record_localization(
                study_id=sid, document_id=photo, revision=1, status="skipped", reason="test"
            )
            await objects.add(
                study_id=sid,
                document_id=photo,
                revision=1,
                new=NewObject(
                    label="window", x1=0.1, y1=0.1, x2=0.3, y2=0.3, area=0.04, source="model"
                ),
            )
    await activity_env.run(
        ProcessAssets(deps).run,
        ProcessAssetsInput(
            study_id=str(sid), revision=1, idempotency_key="k-a1", budget_remaining_usd=1.0
        ),
    )
    return seeded


def carry(seeded: SeededStudy, through: str, to_revision: int, key: str) -> CarryRevisionInput:
    return CarryRevisionInput(
        study_id=str(seeded.study_id),
        from_revision=1,
        to_revision=to_revision,
        through=through,  # type: ignore[arg-type]
        reason="reopen",
        idempotency_key=key,
    )


async def inventories(deps: Deps, sid: uuid.UUID, revision: int) -> list[Component]:
    return [
        c
        for c in await rows(deps, Component, sid, revision)
        if is_room_inventory(c.raw, c.document_id)
    ]


async def test_below_the_components_review_the_inventories_and_assets_stay_behind(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    before = {
        model: len(await rows(deps, model, sid, 1))
        for model in (Extraction, Room, PhotoRoom, Component, Asset, PhotoLocalization)
    }
    assert len(await inventories(deps, sid, 1)) == 2 and before[Asset] > 0

    output = await activity_env.run(
        CarryRevision(deps).run, carry(revision_one, "rooms_review", 2, "k-c2")
    )

    assert output.document_id == str(revision_one.appraisal_id)
    assert output.report_pdf is not None  # recorded on the extraction by UAD 3.6 intake
    assert (output.extractions, output.rooms, output.assets) == (1, 2, 0)
    assert output.components == before[Component] - 2  # the per-photo rows, not the inventory
    assert await inventories(deps, sid, 2) == []
    assert await rows(deps, Asset, sid, 2) == []
    assert len(await rows(deps, Room, sid, 2)) == before[Room]
    assert len(await rows(deps, PhotoRoom, sid, 2)) == before[PhotoRoom]
    assert len(await rows(deps, PhotoLocalization, sid, 2)) == before[PhotoLocalization] == 3
    assert output.objects == len(await rows(deps, PhotoObject, sid, 2)) == 3
    for copied_object in await rows(deps, PhotoObject, sid, 2):
        assert copied_object.carried_from is not None
    async with session_scope(deps.sessionmaker) as session:
        counts = []
        for revision in (1, 2):
            extraction_ids = select(Extraction.id).where(
                Extraction.study_id == sid, Extraction.revision == revision
            )
            fields = await session.scalars(
                select(ExtractionField.id).where(ExtractionField.extraction_id.in_(extraction_ids))
            )
            counts.append(len(fields.all()))
    assert counts[1] == counts[0] > 0  # the fields came with the extraction
    # Every copy names its parent; revision 1 is untouched.
    for room in await rows(deps, Room, sid, 2):
        assert room.carried_from is not None
    for component in await rows(deps, Component, sid, 2):
        assert isinstance(component.raw, dict) and "carried_from" in component.raw
    after = {model: len(await rows(deps, model, sid, 1)) for model in before}
    assert after == before


async def test_the_components_review_carries_the_inventories(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id

    output = await activity_env.run(
        CarryRevision(deps).run, carry(revision_one, "components_review", 2, "k-c2")
    )

    copied = await inventories(deps, sid, 2)
    assert {c.label for c in copied} == {"kitchen_cabinet", "sink"}
    assert output.components == len(await rows(deps, Component, sid, 1))
    assert output.assets == 0 and await rows(deps, Asset, sid, 2) == []


async def test_the_assets_review_carries_the_assets_on_their_components_copies(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id
    assets_1 = await rows(deps, Asset, sid, 1)

    output = await activity_env.run(
        CarryRevision(deps).run, carry(revision_one, "assets_review", 2, "k-c2")
    )

    assets_2 = await rows(deps, Asset, sid, 2)
    assert output.assets == len(assets_2) == len(assets_1)
    components_2 = {c.id: c for c in await rows(deps, Component, sid, 2)}
    parents = {
        c.id: uuid.UUID(c.raw["carried_from"])
        for c in components_2.values()
        if c.raw.get("carried_from")
    }
    by_parent_component = {a.component_id: a for a in assets_1 if a.component_id is not None}
    async with session_scope(deps.sessionmaker) as session:
        ids_1 = [a.id for a in assets_1]
        ids_2 = [a.id for a in assets_2]
        takeoffs = {
            t.asset_id: t
            for t in (
                await session.scalars(select(Takeoff).where(Takeoff.asset_id.in_(ids_1 + ids_2)))
            ).all()
        }
        costs = {
            c.asset_id: c
            for c in (
                await session.scalars(
                    select(CostEstimate).where(CostEstimate.asset_id.in_(ids_1 + ids_2))
                )
            ).all()
        }
        citations = (
            await session.scalars(select(Citation).where(Citation.asset_id.in_(ids_1 + ids_2)))
        ).all()
    cited_1 = sum(c.asset_id in set(ids_1) for c in citations)
    cited_2 = sum(c.asset_id in set(ids_2) for c in citations)
    assert cited_2 == cited_1
    for asset in assets_2:
        if asset.component_id is None:
            continue
        assert asset.component_id in components_2  # on the copy, not the old row
        old = by_parent_component[parents[asset.component_id]]
        assert (asset.category, asset.bucket, asset.recovery_period, asset.method) == (
            old.category,
            old.bucket,
            old.recovery_period,
            old.method,
        )
        assert takeoffs[asset.id].quantity == takeoffs[old.id].quantity
        assert costs[asset.id].total == costs[old.id].total
        assert costs[asset.id].trail == costs[old.id].trail


async def test_the_copy_is_one_transaction_recorded_once_and_idempotent(
    deps: Deps, revision_one: SeededStudy, activity_env: Env
) -> None:
    sid = revision_one.study_id

    first = await activity_env.run(
        CarryRevision(deps).run, carry(revision_one, "assets_review", 2, "k-c2")
    )
    again = await activity_env.run(
        CarryRevision(deps).run, carry(revision_one, "assets_review", 2, "k-c2")
    )

    assert again == first
    assert len(await rows(deps, Asset, sid, 2)) == first.assets  # no second copy
    async with session_scope(deps.sessionmaker) as session:
        events = (
            await session.scalars(
                select(StudyEvent).where(
                    StudyEvent.study_id == sid, StudyEvent.event_type == "revision.carried"
                )
            )
        ).all()
    assert len(events) == 1
    (event,) = events
    assert event.revision == 2 and event.stage is not None and event.stage.value == "assets_review"
    assert event.detail["from_revision"] == 1 and event.detail["through"] == "assets_review"
    assert event.detail["reason"] == "reopen" and event.detail["assets"] == first.assets
