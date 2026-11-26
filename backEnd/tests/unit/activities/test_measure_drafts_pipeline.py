"""``process_assets`` with AI-drafted measurements (EVAL_REPORT 110).

The measured house of ``test_process_assets`` (GLA 2,400 SF, two stories, a 440 SF garage), its
exterior rows (two roof rows, siding, gutter) and its kitchen and bathroom, with a confirmed pin
and a gather that stored the synthetic gable house's shell (``tests/unit/geo/test_shell.py``):

* **off** (the default): the rows are byte for byte today's (the values main writes, pinned in
  ``test_process_assets``) even with a shell and room drafts available;
* **exterior on**: the roof, siding and gutter rows are the shell's drafts (the measurement
  record, its factor, its review flag, its exposure); the footprint-based rows are untouched;
  an unconfirmed pin, a pin moved since the gather and no shell each leave today's row with the
  reason;
* **interior on**: a kitchen's draft sizes its floor, wall, countertop and cabinet rows (the
  cabinet run no longer an ``input:cabinet_run`` blocker), a room whose photos changed and a
  failed draft keep today's rows, and a later revision carries the drafts it priced on;
* **pricing rule (a)**: a pending draft above the threshold is the result's
  ``measure:draft_unverified`` blocker and the allocation is withheld; verifying it (the stored
  measurement sent back ``verified: true``) clears it at the same quantity; an engineer's edit
  replaces it, keeping the draft as the measurement's ``original`` and on the draft record; a
  typed quantity replaces it too.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fixtures import fixture_path
from lifecycletest_support import SeededStudy, make_deps
from sqlalchemy import delete, select

from basis.activities import Deps
from basis.activities.process_assets import ProcessAssets
from basis.db.models import (
    Asset,
    Citation,
    ClientError,
    Component,
    CostEstimate,
    GeoGather,
    PropertyPin,
    Room,
    Takeoff,
)
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.domain import measure_drafts as md
from basis.domain import traced
from basis.errors.codes import E
from basis.evidence.result import compute_result, load_schedule
from basis.geo import shell
from basis.workflow.contracts import CorrectionInput, RoomDraftOut

from ..geo.test_parcels_and_lidar import LAT, LON
from ..geo.test_shell import cloud, derive
from .conftest import Env
from .test_process_assets import (
    MEASURED_FIELDS,
    process,
    seed_extraction,
    start_revision_two,
)
from .test_process_assets import analyzed as analyzed  # noqa: F401 - the fixture

pytestmark = pytest.mark.integration

D = Decimal
SHELL = shell.as_record(derive(cloud(), overhang_m=0.5))
KITCHEN_ANSWER = {
    "floor_area_sf": 180.0,
    "length_ft": 15.0,
    "width_ft": 12.0,
    "ceiling_height_ft": 8.0,
    "perimeter_ft": 54.0,
    "wall_area_sf": 432.0,
    "countertop_sf": 28.0,
    "cabinet_lf": 16.0,
}


def settings_for(deps: Deps, **update: Any) -> Deps:
    settings = deps.settings.model_copy(
        update={
            "cost_table_path": str(fixture_path("cost_table.json")),
            "cost_year": 2026,
            **update,
        }
    )
    return make_deps(deps.sessionmaker, deps.storage, settings)


async def add_exterior(deps: Deps, seeded: SeededStudy) -> None:
    async with session_scope(deps.sessionmaker) as session:
        repo = RoomRepository(session)
        exterior, _ = await repo.get_or_create(
            study_id=seeded.study_id, revision=1, name="Front", room_type="exterior", source="stub"
        )
        for label in ("roof", "roof", "siding", "gutter"):
            await repo.add_component(
                study_id=seeded.study_id,
                revision=1,
                document_id=None,
                room_id=exterior.id,
                label=label,
                material=None,
                attachment=None,
                confidence=0.9,
                raw={"source": "vision", "count": 1},
            )


async def add_kitchen_rows(deps: Deps, seeded: SeededStudy) -> Room:
    """A countertop, a cabinet run and a wall finish in the kitchen (priced by the synthetic
    table except the cabinet run), beside the stub's cabinetry and flooring."""
    async with session_scope(deps.sessionmaker) as session:
        room = (
            await session.scalars(
                select(Room).where(Room.study_id == seeded.study_id, Room.name == "Kitchen")
            )
        ).one()
        repo = RoomRepository(session)
        for label in ("countertop", "cabinet", "wall_finish"):
            await repo.add_component(
                study_id=seeded.study_id,
                revision=1,
                document_id=None,
                room_id=room.id,
                label=label,
                material=None,
                attachment=None,
                confidence=0.9,
                raw={"source": "vision", "count": 1},
            )
        return room


async def seed_shell(
    deps: Deps,
    seeded: SeededStudy,
    *,
    confirmed: bool = True,
    moved: bool = False,
    record: dict[str, Any] | None = None,
) -> uuid.UUID:
    gather_id = uuid.uuid4()
    async with session_scope(deps.sessionmaker) as session:
        session.add(
            PropertyPin(
                study_id=seeded.study_id,
                lat=LAT + (0.001 if moved else 0.0),
                lon=LON,
                source="engineer",
                confirmed=confirmed,
                version=1,
            )
        )
        session.add(
            GeoGather(
                id=gather_id,
                study_id=seeded.study_id,
                status="succeeded",
                pin_version=1,
                lat=LAT,
                lon=LON,
                lidar={"project": "IA_Test_2020", "year": 2020}
                | ({} if record == {} else {"shell": record or SHELL}),
                finished_at=datetime.now(UTC),
            )
        )
    return gather_id


async def rows_by_label(
    deps: Deps, study_id: uuid.UUID, revision: int
) -> dict[str, list[tuple[Asset, Takeoff]]]:
    out: dict[str, list[tuple[Asset, Takeoff]]] = {}
    async with session_scope(deps.sessionmaker) as session:
        result = await session.execute(
            select(Component.label, Asset, Takeoff)
            .join(Asset, Asset.component_id == Component.id)
            .join(Takeoff, Takeoff.asset_id == Asset.id)
            .where(Asset.study_id == study_id, Asset.revision == revision)
        )
        for label, asset, takeoff in result.tuples():
            out.setdefault(label, []).append((asset, takeoff))
    for rows in out.values():
        rows.sort(key=lambda r: -r[1].quantity)
    return out


async def snapshot(deps: Deps, study_id: uuid.UUID, revision: int) -> list[tuple[Any, ...]]:
    """Every row's quantity, unit, formula, inputs, review reason and price, by label."""
    out = []
    for label, rows in sorted((await rows_by_label(deps, study_id, revision)).items()):
        for asset, takeoff in rows:
            # A second row of a quantity names the first by its asset id, new on each run.
            inputs = {k: v for k, v in takeoff.inputs.items() if k != "measured_with_asset"}
            formula = "once" if takeoff.formula.startswith("measured once") else takeoff.formula
            out.append(
                (label, takeoff.quantity, takeoff.unit, formula, inputs, asset.review_reason)
            )
    return sorted(out, key=repr)


# --------------------------------------------------------------------------- off: today's path


async def test_off_every_row_is_todays_even_with_a_shell_and_room_drafts(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """The regression line: the roof is main's 2,018.36 SF (test_process_assets), and the run
    with a stored shell and room drafts given writes exactly what a run without either writes."""
    await seed_extraction(deps, analyzed, MEASURED_FIELDS)
    await add_exterior(deps, analyzed)
    kitchen = await add_kitchen_rows(deps, analyzed)
    plain = settings_for(deps)
    await activity_env.run(ProcessAssets(plain).run, process(analyzed))
    before = await snapshot(deps, analyzed.study_id, 1)
    roofs = (await rows_by_label(deps, analyzed.study_id, 1))["roof"]
    assert [t.quantity for _, t in roofs] == [D("2018.36"), D("0.00")]  # main's value
    async with session_scope(deps.sessionmaker) as session:
        for table in (Citation, CostEstimate, Takeoff, Asset):
            await session.execute(delete(table))
    await seed_shell(deps, analyzed)
    given = process(analyzed, key="k-again").model_copy(
        update={"room_drafts": (kitchen_draft(kitchen, analyzed),)}
    )
    await activity_env.run(ProcessAssets(plain).run, given)
    after = await snapshot(deps, analyzed.study_id, 1)
    assert after == before
    for row in after:
        assert not ({md.DRAFT, "draft_not_used", traced.MEASUREMENT} & set(row[4]))


def kitchen_draft(room: Room, seeded: SeededStudy, **over: Any) -> RoomDraftOut:
    return RoomDraftOut(
        room_id=str(room.id),
        room_type="kitchen",
        photos=tuple(str(p) for p in seeded.photo_ids[:2]),
        status="drafted",
        quantities=KITCHEN_ANSWER,
        model="claude-sonnet-5-5",
        prompt_version="measure_interior_reference/v1",
        confidence="medium",
        room_fully_seen=False,
        notes="measured against the door",
        **over,
    )


# --------------------------------------------------------------------------- exterior


async def test_exterior_on_the_shell_drafts_roof_siding_and_gutter(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    await seed_extraction(deps, analyzed, MEASURED_FIELDS)
    await add_exterior(deps, analyzed)
    gather_id = await seed_shell(deps, analyzed)
    on = settings_for(deps, measure_drafts_exterior=True, measure_draft_verify_usd=1.0)
    await activity_env.run(ProcessAssets(on).run, process(analyzed))
    rows = await rows_by_label(deps, analyzed.study_id, 1)
    expected, _ = md.exterior_drafts(
        md.ShellSource(str(gather_id), "IA_Test_2020", 2020, SHELL), {}, {}
    )

    (roof, roof_t), (second, second_t) = rows["roof"]
    assert roof_t.quantity == expected["roof"].quantity and roof_t.unit == "SF"
    assert second_t.quantity == 0 and md.DRAFT not in second_t.inputs  # measured once
    assert roof_t.inputs["quantity_source"] == "draft"
    assert roof_t.inputs["replaced_quantity"] == "2018.36 SF"  # today's, kept
    draft = roof_t.inputs[md.DRAFT]
    assert draft["drawn_by"] == "model:lidar-shell@v1" and draft["verified"] is False
    assert draft["replaces"]["quantity"] == "2018.36 SF"
    measurement = roof_t.inputs[traced.MEASUREMENT]
    assert measurement["drawn_by"] == "model:lidar-shell@v1" and measurement["verified"] is False
    assert md.DRAFT_UNVERIFIED in (roof.review_reason or "") and roof.needs_review
    # Priced: exposure = 10.4 % of the total; above the $1 threshold, so it holds the report.
    async with session_scope(deps.sessionmaker) as session:
        cost = (
            await session.scalars(select(CostEstimate).where(CostEstimate.asset_id == roof.id))
        ).one()
    assert cost.total is not None
    assert D(draft["exposure_usd"]) == (cost.total * D("10.4") / 100).quantize(D("0.01"))
    assert draft["blocks"] is True and draft["verify_above_usd"] == "1.0"

    ((siding, siding_t),) = rows["siding"]
    assert siding_t.quantity == expected["siding"].quantity
    assert siding_t.inputs[md.DRAFT]["factor"] == "0.85"
    # The synthetic table has no siding row: unpriced, so it moves no dollars and holds nothing.
    assert siding_t.inputs[md.DRAFT]["exposure_usd"] is None
    assert siding_t.inputs[md.DRAFT]["blocks"] is False
    ((_, gutter_t),) = rows["gutter"]
    assert gutter_t.quantity == expected["gutter"].quantity

    async with session_scope(deps.sessionmaker) as session:
        schedule = await load_schedule(session, analyzed.study_id, 1)
    result = compute_result(schedule, None, 1)
    draft_blockers = [b for b in result.blockers if b.code == md.BLOCKER]
    assert len(draft_blockers) == 1 and draft_blockers[0].assets == 1
    assert draft_blockers[0].fix == "verify or edit this measurement in the measure tool"
    # Verifying is the engineer's ordinary step: not an issue recorded for the operators.
    async with session_scope(deps.sessionmaker) as session:
        recorded = (
            await session.scalars(
                select(ClientError.error_type).where(ClientError.study_id == analyzed.study_id)
            )
        ).all()
    assert md.BLOCKER not in recorded


@pytest.mark.parametrize(
    ("how", "reason"),
    [
        ("unconfirmed", "the pin is not confirmed (nothing is measured before it)"),
        ("moved", "the newest shell was gathered around an earlier pin"),
        ("nopin", "the study has no pin on the building"),
        ("noshell", "no public records gather stored a building shell (GEO_SHELL)"),
    ],
)
async def test_exterior_on_without_a_usable_shell_keeps_todays_rows_with_the_reason(
    deps: Deps, analyzed: SeededStudy, activity_env: Env, how: str, reason: str
) -> None:
    await seed_extraction(deps, analyzed, MEASURED_FIELDS)
    await add_exterior(deps, analyzed)
    if how != "nopin":
        await seed_shell(
            deps,
            analyzed,
            confirmed=how != "unconfirmed",
            moved=how == "moved",
            record={} if how == "noshell" else None,
        )
    on = settings_for(deps, measure_drafts_exterior=True)
    await activity_env.run(ProcessAssets(on).run, process(analyzed))
    rows = await rows_by_label(deps, analyzed.study_id, 1)
    roof_t = rows["roof"][0][1]
    assert roof_t.quantity == D("2018.36")  # today's
    assert roof_t.inputs["draft_not_used"] == reason
    assert roof_t.inputs["draft_not_used_code"] == E.MEASURE_EXTERIOR_NOT_DRAFTED
    assert md.DRAFT not in roof_t.inputs and traced.MEASUREMENT not in roof_t.inputs


async def test_a_shell_part_refused_keeps_that_row_today_and_drafts_the_rest(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    record = copy.deepcopy(SHELL)
    del record["roof_outline"]  # a shell stored before the outline was kept
    await seed_extraction(deps, analyzed, MEASURED_FIELDS)
    await add_exterior(deps, analyzed)
    await seed_shell(deps, analyzed, record=record)
    on = settings_for(deps, measure_drafts_exterior=True)
    await activity_env.run(ProcessAssets(on).run, process(analyzed))
    rows = await rows_by_label(deps, analyzed.study_id, 1)
    assert rows["roof"][0][1].quantity == D("2018.36")
    assert rows["roof"][0][1].inputs["draft_not_used"] == "no roof outline stored with the shell"
    assert rows["siding"][0][1].inputs["quantity_source"] == "draft"


async def exterior_revision_two(
    deps: Deps, seeded: SeededStudy, activity_env: Env, after: Any
) -> tuple[Takeoff, Asset, Any]:
    """Revision 1 drafted (threshold $1), then an assets rejection correcting the roof with
    ``after`` (None: no correction); revision 2's roof row and the result's blockers."""
    await seed_extraction(deps, seeded, MEASURED_FIELDS)
    await add_exterior(deps, seeded)
    await seed_shell(deps, seeded)
    on = settings_for(deps, measure_drafts_exterior=True, measure_draft_verify_usd=1.0)
    await activity_env.run(ProcessAssets(on).run, process(seeded))
    roof, roof_t = (await rows_by_label(deps, seeded.study_id, 1))["roof"][0]
    await start_revision_two(deps, seeded, activity_env)
    corrections: tuple[CorrectionInput, ...] = ()
    if after is not None:
        body = after(roof_t.inputs[traced.MEASUREMENT]) if callable(after) else after
        corrections = (CorrectionInput(entity_type="asset", entity_id=str(roof.id), after=body),)
    await activity_env.run(
        ProcessAssets(on).run, process(seeded, revision=2, corrections=corrections)
    )
    new, new_t = (await rows_by_label(deps, seeded.study_id, 2))["roof"][0]
    async with session_scope(deps.sessionmaker) as session:
        schedule = await load_schedule(session, seeded.study_id, 2)
    return new_t, new, compute_result(schedule, None, 2).blockers


def body_of(record: dict[str, Any]) -> dict[str, Any]:
    keep = {"unit", "space", "shapes", "scale", "image", "height_ft", "drawn_by", "verified"}
    return {k: copy.deepcopy(v) for k, v in record.items() if k in keep}


def verified(record: dict[str, Any]) -> dict[str, Any]:
    return {traced.MEASUREMENT: {**body_of(record), "verified": True}}


def edited(record: dict[str, Any]) -> dict[str, Any]:
    """The engineer drags the outline's first corner 2 m out (the tool sends the draft as the
    original)."""
    body = body_of(record)
    ring = body["shapes"][0]["ring"]
    ring[0] = [ring[0][0] - 0.00002, ring[0][1] - 0.00002]
    return {
        traced.MEASUREMENT: {
            **body,
            "drawn_by": "engineer",
            "verified": True,
            "original": body_of(record),
        }
    }


async def test_a_pending_draft_holds_the_report_on_the_next_revision_too(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    roof_t, roof, blockers = await exterior_revision_two(deps, analyzed, activity_env, None)
    assert roof_t.inputs[md.DRAFT]["blocks"] is True
    assert [b.code for b in blockers].count(md.BLOCKER) == 1


async def test_verifying_the_draft_clears_the_blocker_at_the_same_quantity(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    roof_t, roof, blockers = await exterior_revision_two(deps, analyzed, activity_env, verified)
    first = (await rows_by_label(deps, analyzed.study_id, 1))["roof"][0][1]
    assert roof_t.quantity == first.quantity  # the shape x the slope factor, unchanged
    assert roof_t.inputs["quantity_source"] == "engineer"
    assert roof_t.inputs[md.DRAFT]["verified"] is True and "blocks" not in roof_t.inputs[md.DRAFT]
    assert roof_t.inputs[traced.MEASUREMENT]["verified"] is True
    assert "kept from Basis's draft" in roof_t.formula
    assert md.DRAFT_UNVERIFIED not in (roof.review_reason or "")
    assert md.BLOCKER not in [b.code for b in blockers]


async def test_an_edit_replaces_the_draft_and_keeps_it_as_the_original(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    roof_t, roof, blockers = await exterior_revision_two(deps, analyzed, activity_env, edited)
    first = (await rows_by_label(deps, analyzed.study_id, 1))["roof"][0][1]
    assert roof_t.quantity > first.quantity  # the corner moved out
    measurement = roof_t.inputs[traced.MEASUREMENT]
    assert measurement["drawn_by"] == "engineer"
    assert measurement["original"]["drawn_by"] == "model:lidar-shell@v1"
    assert measurement["original"]["quantity"] == first.inputs[traced.MEASUREMENT]["quantity"]
    record = roof_t.inputs[md.DRAFT]
    assert record["replaced_by"] == "engineer" and D(record["quantity"]) == first.quantity
    assert record["engineer_quantity"] == f"{roof_t.quantity.quantize(D('0.01'))} SF"
    # The slope stays the draft's: the engineer checked the outline.
    shape = D(measurement["quantity"])
    assert roof_t.quantity == (shape * D(record["factor"])).quantize(D("0.01"))
    assert md.BLOCKER not in [b.code for b in blockers]


async def test_a_typed_quantity_replaces_the_draft_and_keeps_its_shape_on_the_record(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    typed = {"quantity": "2500", "quantity_unit": "SF", "quantity_source": "roofer's report"}
    roof_t, roof, blockers = await exterior_revision_two(deps, analyzed, activity_env, typed)
    assert roof_t.quantity == D("2500.00") and roof_t.inputs["quantity_source"] == "engineer"
    assert traced.MEASUREMENT not in roof_t.inputs
    record = roof_t.inputs[md.DRAFT]
    assert record["replaced_by"] == "engineer" and record["measurement"]["drawn_by"] == (
        "model:lidar-shell@v1"
    )
    assert md.DRAFT_UNVERIFIED not in (roof.review_reason or "")
    assert md.BLOCKER not in [b.code for b in blockers]


async def test_the_draft_sent_back_unverified_stays_the_draft(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    roof_t, roof, blockers = await exterior_revision_two(
        deps, analyzed, activity_env, lambda r: {traced.MEASUREMENT: body_of(r)}
    )
    assert roof_t.inputs["quantity_source"] == "draft"
    assert roof_t.inputs[md.DRAFT]["blocks"] is True
    assert md.BLOCKER in [b.code for b in blockers]


# --------------------------------------------------------------------------- interior


async def interior_run(
    deps: Deps, seeded: SeededStudy, activity_env: Env, draft: RoomDraftOut | None = None
) -> dict[str, list[tuple[Asset, Takeoff]]]:
    await seed_extraction(deps, seeded, MEASURED_FIELDS)
    kitchen = await add_kitchen_rows(deps, seeded)
    on = settings_for(deps, measure_drafts_interior=True, measure_draft_verify_usd=1000.0)
    given = process(seeded).model_copy(
        update={"room_drafts": (draft or kitchen_draft(kitchen, seeded),)}
    )
    await activity_env.run(ProcessAssets(on).run, given)
    return await rows_by_label(deps, seeded.study_id, 1)


async def test_interior_on_the_kitchens_draft_sizes_its_rows(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    rows = await interior_run(deps, analyzed, activity_env)
    ((counter, counter_t),) = rows["countertop"]
    assert (counter_t.quantity, counter_t.unit) == (D("28.00"), "SF")
    assert counter_t.inputs["replaced_missing_input"] == "cabinet_run"
    record = counter_t.inputs[md.DRAFT]
    assert record["drawn_by"] == "model:claude-sonnet-5-5@measure_interior_reference-v1"
    assert record["photos"] == [str(p) for p in analyzed.photo_ids[:2]]
    assert record["exposure_usd"] is not None and record["blocks"] is False  # under $1,000
    assert md.DRAFT_UNVERIFIED in (counter.review_reason or "")
    assert "input:cabinet_run" not in (counter.review_reason or "")
    # A "cabinet" is a kitchen cabinet unit (the rules' alias), counted and priced per unit
    # (EVAL_REPORT 27): the draft's cabinet run does not touch it.
    ((_, cabinet_t),) = rows["cabinet"]
    assert (cabinet_t.quantity, cabinet_t.unit) == (D("1.00"), "EA")
    assert md.DRAFT not in cabinet_t.inputs
    ((wall, wall_t),) = rows["wall_finish"]
    assert wall_t.quantity == D("432.00")
    assert "x 3.5" in wall_t.inputs["replaced_formula"]  # today's 3.5 x the GLA share
    # Two flooring rows (one per kitchen photo): measured once, 180 SF.
    floors = rows["flooring"]
    assert [t.quantity for _, t in floors] == [D("180.00"), D("0.00")]
    # The bathroom has no draft: today's tile.
    ((_, tile_t),) = rows["tile_flooring"]
    assert tile_t.inputs["room_area_source"] == "gla_share"
    assert md.DRAFT not in tile_t.inputs


async def test_a_failed_draft_or_changed_photos_keep_todays_rows(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    async with session_scope(deps.sessionmaker) as session:
        kitchen = (
            await session.scalars(
                select(Room).where(Room.study_id == analyzed.study_id, Room.name == "Kitchen")
            )
        ).one()
    failed = kitchen_draft(kitchen, analyzed).model_copy(
        update={"status": "failed", "quantities": {}, "code": E.MEASURE_ROOM_NOT_DRAFTED}
    )
    rows = await interior_run(deps, analyzed, activity_env, failed)
    ((counter, counter_t),) = rows["countertop"]
    assert counter_t.quantity == D("0.00") and "input:cabinet_run" in (counter.review_reason or "")
    assert md.DRAFT not in counter_t.inputs


async def test_a_draft_of_other_photos_is_not_used(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    async with session_scope(deps.sessionmaker) as session:
        kitchen = (
            await session.scalars(
                select(Room).where(Room.study_id == analyzed.study_id, Room.name == "Kitchen")
            )
        ).one()
    other = kitchen_draft(kitchen, analyzed).model_copy(
        update={"photos": (str(analyzed.photo_ids[0]),)}
    )
    rows = await interior_run(deps, analyzed, activity_env, other)
    assert md.DRAFT not in rows["countertop"][0][1].inputs


async def test_the_next_revision_carries_the_drafts_it_priced_on(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """A reprice or a reopened execution passes no drafts: the rejected revision's rows give
    them (followed along rooms.carried_from)."""
    await interior_run(deps, analyzed, activity_env)
    await start_revision_two(deps, analyzed, activity_env)
    on = settings_for(deps, measure_drafts_interior=True, measure_draft_verify_usd=1000.0)
    await activity_env.run(ProcessAssets(on).run, process(analyzed, revision=2))
    rows = await rows_by_label(deps, analyzed.study_id, 2)
    ((_, counter_t),) = rows["countertop"]
    assert counter_t.quantity == D("28.00") and counter_t.inputs[md.DRAFT]["verified"] is False
    assert rows["flooring"][0][1].quantity == D("180.00")


async def test_interior_drafts_given_with_the_setting_off_are_ignored(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    await seed_extraction(deps, analyzed, MEASURED_FIELDS)
    kitchen = await add_kitchen_rows(deps, analyzed)
    given = process(analyzed).model_copy(
        update={"room_drafts": (kitchen_draft(kitchen, analyzed),)}
    )
    await activity_env.run(ProcessAssets(settings_for(deps)).run, given)
    rows = await rows_by_label(deps, analyzed.study_id, 1)
    assert md.DRAFT not in rows["countertop"][0][1].inputs
    assert "input:cabinet_run" in (rows["countertop"][0][0].review_reason or "")


# ------------------------------------------------------- the corrected row first, always


@pytest.mark.regression
async def test_a_typed_roof_on_the_second_row_is_not_counted_twice_with_drafts_off(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """EVAL_REPORT 110.6.1, made deterministic: a quantity measured once per property is carried
    by the first row of its kind, and the rows are ordered by (document, label, id) with ids new
    on every revision. Correcting the roof row that revision 2 orders SECOND priced today's
    formula on the first row and the engineer's quantity on the second (2,018 + 2,500 SF in 2 of
    6 runs). The corrected row now goes first with both draft settings off too: the property's
    roof is the engineer's 2,500 SF once."""
    await seed_extraction(deps, analyzed, MEASURED_FIELDS)
    await add_exterior(deps, analyzed)
    off = settings_for(deps)
    await activity_env.run(ProcessAssets(off).run, process(analyzed))
    await start_revision_two(deps, analyzed, activity_env)
    async with session_scope(deps.sessionmaker) as session:
        roofs = list(
            await session.scalars(
                select(Component)
                .where(
                    Component.study_id == analyzed.study_id,
                    Component.revision == 2,
                    Component.label == "roof",
                )
                .order_by(Component.document_id, Component.label, Component.id)
            )
        )
        assert len(roofs) == 2
        second_origin = roofs[1].raw["carried_from"]  # the row today's order processes second
        target = await session.scalar(
            select(Asset).where(
                Asset.study_id == analyzed.study_id,
                Asset.revision == 1,
                Asset.component_id == uuid.UUID(str(second_origin)),
            )
        )
    assert target is not None
    typed = {"quantity": "2500", "quantity_unit": "SF", "quantity_source": "roofer's report"}
    correction = CorrectionInput(entity_type="asset", entity_id=str(target.id), after=typed)
    await activity_env.run(
        ProcessAssets(off).run, process(analyzed, revision=2, corrections=(correction,))
    )
    rows = (await rows_by_label(deps, analyzed.study_id, 2))["roof"]
    quantities = sorted(t.quantity for _, t in rows)
    assert quantities == [D("0"), D("2500.00")], quantities  # not formula + 2,500
