"""Manual add through the workflow: an engineer's objects reach the rules and the assets.

At the production defaults (the room step on, so rooms have reconciled inventories; see
``test_production_defaults``) with the offline fixture providers; rows, storage, the
Temporal server and the activities are real, SAM 3 is replaced by rows written as
``localize_photo`` and the objects route write them.

The components review (``GET /rooms``; the single rooms review of an execution without
patch ``components-gate-v1``) shows each room's count beside the most instances one
photo of it holds, SAM 3's and the engineer's (``suggested_counts``). The engineer
confirms a raised count (a ``component`` correction of the inventory row) and adds the
component the inventory is missing (a ``room_component`` correction) with a rejection; the
workflow starts revision 2 (``apply_corrections``; the room step finds the corrected
inventory current and keeps it), and ``process_assets`` builds revision 2's assets: the
corrected row takes off the new quantity, and the added component is classified by the
rules table by its label, material and attachment, with a verified citation, like a
component the model found.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lifecycle_support import Harness
from llm_fakes import FixtureVisionProvider
from sqlalchemy import select
from test_production_defaults import production

from basis.api.routes.outputs import room_out
from basis.db.enums import Bucket, ClassificationMethod
from basis.db.models import Asset, Citation, Component, Room, Takeoff
from basis.db.repositories.outputs import OutputRepository
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository
from basis.db.session import session_scope
from basis.vision.rooms import is_room_inventory, is_room_manual
from basis.workflow.contracts import CorrectionInput

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def rows(harness: Harness, model: Any, study_id: uuid.UUID, **where: Any) -> list[Any]:
    async with session_scope(harness.sessionmaker) as session:
        stmt = select(model).where(model.study_id == study_id)
        for name, value in where.items():
            stmt = stmt.where(getattr(model, name) == value)
        return list((await session.scalars(stmt)).all())


async def suggestions(harness: Harness, study_id: uuid.UUID, revision: int) -> dict[str, Any]:
    """What ``GET /rooms`` answers for the Kitchen: ``{label: suggested count}``."""
    async with session_scope(harness.sessionmaker) as session:
        page = await OutputRepository(session).rooms(study_id, revision, limit=50)
    [kitchen] = [room_out(row) for row in page.items if row.room.name == "Kitchen"]
    return {s.label: s for s in kitchen.suggested_counts}


def instance(label: str, at: int, *, user: uuid.UUID | None = None) -> NewObject:
    """A counted instance as localize_photo (model) or the objects route (manual) writes it."""
    x1 = 0.05 + 0.1 * at
    return NewObject(
        label=label,
        x1=x1,
        y1=0.1,
        x2=x1 + 0.08,
        y2=0.3,
        area=0.016,
        source="manual" if user else "model",
        score=None if user else 0.9,
        phrase=None if user else label,
        created_by=user,
        mask_source="none" if user else None,
    )


@pytest.mark.parametrize("components_gate", [True, False], ids=["components_gate", "single_gate"])
async def test_a_count_correction_and_an_added_component_reach_the_assets(
    harness: Harness, components_gate: bool
) -> None:
    """At the components review (patch components-gate-v1), or at the single rooms review
    of an execution without it."""
    gate = "components_review" if components_gate else "rooms_review"
    run = production(harness, FixtureVisionProvider())
    try:
        async with run.worker():
            started = await run.start(components_gate=components_gate)
            handle, sid = started.handle, started.study_id
            owner = started.study.owner_id
            await run.approve(handle, "appraisal_review")
            if components_gate:
                await run.approve(handle, "rooms_review")
            await run.wait_awaiting(handle, gate, 1)  # type: ignore[arg-type]
            kitchen = next(r for r in await rows(run, Room, sid, revision=1) if r.name == "Kitchen")
            [cabinets] = [
                c
                for c in await rows(run, Component, sid, revision=1, room_id=kitchen.id)
                if is_room_inventory(c.raw, c.document_id) and c.label == "kitchen_cabinet"
            ]
            assert cabinets.raw["count"] == 6
            photo_a, photo_b = cabinets.raw["photos"]
            # SAM 3 counted six cabinet units on one photo; the engineer added two it missed
            # there, and outlined three light fixtures (a label the inventory lacks) on the
            # other photo.
            async with session_scope(run.sessionmaker) as session:
                objects = PhotoObjectRepository(session)
                new = [(photo_a, instance("kitchen_cabinet", i)) for i in range(6)]
                new += [(photo_a, instance("kitchen_cabinet", 6 + i, user=owner)) for i in range(2)]
                new += [(photo_b, instance("light_fixture", i, user=owner)) for i in range(3)]
                for document_id, obj in new:
                    await objects.add(
                        study_id=sid, document_id=uuid.UUID(document_id), revision=1, new=obj
                    )
            raised = await suggestions(run, sid, 1)
            assert {
                label: (s.inventory_count, s.photo_count, s.manual_count)
                for label, s in raised.items()
            } == {"kitchen_cabinet": (6, 8, 2), "light_fixture": (0, 3, 3)}
            # The engineer confirms both suggestions with a rejection (an approval cannot
            # carry corrections).
            corrections = [
                CorrectionInput(
                    entity_type="component",
                    entity_id=str(cabinets.id),
                    after={"count": raised["kitchen_cabinet"].suggested},
                ),
                CorrectionInput(
                    entity_type="room_component",
                    entity_id=str(kitchen.id),
                    after={
                        "label": "light_fixture",
                        "count": raised["light_fixture"].suggested,
                        "material": "metal",
                        "attachment": "permanent",
                    },
                ),
            ]
            await run.decide(
                handle,
                run.decision(gate, 1, "reject", corrections=corrections),  # type: ignore[arg-type]
            )
            await run.wait_awaiting(handle, gate, 2)  # type: ignore[arg-type]

            kitchen_2 = next(
                r for r in await rows(run, Room, sid, revision=2) if r.name == "Kitchen"
            )
            in_kitchen = await rows(run, Component, sid, revision=2, room_id=kitchen_2.id)
            [cabinets_2] = [
                c
                for c in in_kitchen
                if is_room_inventory(c.raw, c.document_id) and c.label == "kitchen_cabinet"
            ]
            # Carried and corrected, not rebuilt by the room step (its photos did not change).
            assert cabinets_2.raw["carried_from"] == str(cabinets.id)
            assert (cabinets_2.raw["count"], cabinets_2.raw["count_before"]) == (8, 6)
            [lights] = [c for c in in_kitchen if is_room_manual(c.raw, c.document_id)]
            assert (lights.label, lights.material, lights.attachment, lights.raw["count"]) == (
                "light_fixture",
                "metal",
                "permanent",
                3,
            )
            # Nothing is raised any more: the counts now match what the photos show.
            assert {
                label: (s.inventory_count, s.suggested)
                for label, s in (await suggestions(run, sid, 2)).items()
            } == {"kitchen_cabinet": (8, 8), "light_fixture": (3, 3)}

            await run.approve(handle, gate, 2)  # type: ignore[arg-type]
            await run.wait_awaiting(handle, "assets_review", 2)
            assets = {a.component_id: a for a in await rows(run, Asset, sid, revision=2)}
            assert len(assets) == 5  # the four inventory objects and the added fixtures
            cabinet_asset, light_asset = assets[cabinets_2.id], assets[lights.id]
            [cabinet_takeoff] = await rows(run, Takeoff, sid, asset_id=cabinet_asset.id)
            assert (cabinet_takeoff.quantity, cabinet_takeoff.unit) == (8, "EA")
            assert cabinet_takeoff.inputs["count"] == 8
            # The added component gets its rule, with its citation: a light fixture is a
            # building component (section 1250, 27.5 years) since RULES_VERSION 2026-10-29.1.
            assert (light_asset.bucket, light_asset.method, light_asset.irs_section) == (
                Bucket.Y27_5,
                ClassificationMethod.RULE,
                "1250",
            )
            [light_takeoff] = await rows(run, Takeoff, sid, asset_id=light_asset.id)
            assert (light_takeoff.quantity, light_takeoff.unit) == (3, "EA")
            citations = await rows(run, Citation, sid, asset_id=light_asset.id)
            assert citations and all(c.verified_at is not None for c in citations)
            assert any("artificial illumination" in c.excerpt for c in citations)

            await run.approve(handle, "assets_review", 2)
            result = await run.result(handle)
    finally:
        await run.cleanup()
    assert (result.outcome, result.revision) == ("completed", 2)
