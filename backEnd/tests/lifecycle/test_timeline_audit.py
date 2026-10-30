"""The timeline and the audit trail on real runs of the workflow (docs/api/TIMELINE_AND_AUDIT.md).

* A study approved straight through: every activity the workflow scheduled is a step on the
  timeline with its attempts and a duration that matches its own start and end and fits
  inside Temporal's scheduled-to-completed window; the stage visits are the workflow's
  stages in order with their durations; the export carries the same document.
* A study corrected at every gate, the decisions recorded as the API records them
  (``ReviewRepository.record_decision``: the review, the corrections, the stage move, the
  events) and then delivered to the workflow as the relay does: one correction of each kind
  (a field value, a room rename, a photo moved, a component's material and count, an added
  component, an asset's bucket), and afterwards every row of the final revision says whether
  the model, or the engineer, produced it, and the audit listing has every action in order.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from lifecycle_support import Harness
from lifecycletest_support import SeededStudy
from sqlalchemy import select
from temporalio.api.enums.v1 import EventType

from basis.activities._support import failure_text
from basis.audit.timeline import stages, steps
from basis.db.enums import Decision as DecisionKind
from basis.db.enums import Stage
from basis.db.models import Asset, Component, Extraction, ExtractionField, PhotoRoom, Room
from basis.db.repositories import CorrectionInput as RepoCorrection
from basis.db.repositories import ReviewRepository
from basis.db.repositories.audit import AuditRepository
from basis.db.repositories.provenance import ProvenanceResolver
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import session_scope
from basis.workflow.contracts import CorrectionInput, Decision, ReviewStage
from basis.workflow.study_workflow import StudyWorkflow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

TOLERANCE_MS = 2000
PHOTOS = (
    "Kitchen photo 1.jpg",
    "Kitchen photo 2.jpg",
    "Bathroom photo 1.jpg",
    "Bathroom photo 2.jpg",
)


def dump_for_the_report(name: str, timeline: dict[str, Any], audit: list[Any]) -> None:
    """With ``BASIS_TIMELINE_DUMP=<dir>``: write the run's timeline and audit listing as JSON,
    for EVAL_REPORT (nothing in a normal run)."""
    target = os.environ.get("BASIS_TIMELINE_DUMP")
    if not target:
        return
    items = [
        {
            "kind": i.kind,
            "at": i.at.isoformat(),
            "stage": i.stage,
            "revision": i.revision,
            "action": i.action,
            "entity_type": i.entity_type,
            "entity_id": str(i.entity_id),
            "before": i.before,
            "after": i.after,
            "changed": list(i.changed),
            "reason": i.reason,
            "produced": [str(p) for p in i.produced],
        }
        for i in audit
    ]
    Path(target, f"{name}.json").write_text(
        json.dumps({"timeline": timeline, "audit": items}, indent=1, default=str)
    )


async def events_of(harness: Harness, study_id: uuid.UUID) -> list[Any]:
    async with session_scope(harness.sessionmaker) as session:
        return (await StudyEventRepository(session).list_for_study(study_id)).items


async def rows(harness: Harness, model: Any, study_id: uuid.UUID, **where: Any) -> list[Any]:
    async with session_scope(harness.sessionmaker) as session:
        stmt = select(model).where(model.study_id == study_id)
        for name, value in where.items():
            stmt = stmt.where(getattr(model, name) == value)
        return list((await session.scalars(stmt)).all())


def history_windows(events: Any) -> dict[str, tuple[datetime, datetime]]:
    """Activity id -> (scheduled, completed) of the Temporal history."""
    scheduled: dict[int, tuple[str, datetime]] = {}
    windows: dict[str, tuple[datetime, datetime]] = {}
    for e in events:
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            attrs = e.activity_task_scheduled_event_attributes
            scheduled[e.event_id] = (attrs.activity_id, e.event_time.ToDatetime(tzinfo=UTC))
        elif e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            attrs = e.activity_task_completed_event_attributes
            activity_id, started = scheduled[attrs.scheduled_event_id]
            windows[activity_id] = (started, e.event_time.ToDatetime(tzinfo=UTC))
    return windows


async def test_the_timeline_has_every_step_with_its_duration(harness: Harness) -> None:
    async with harness.worker():
        started = await harness.start()
        handle, sid = started.handle, started.study_id
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        await harness.approve_all(handle)
        result = await harness.result(handle)
        history = (await handle.fetch_history()).events
    assert result.outcome == "completed"

    events = await events_of(harness, sid)
    types = [e.event_type for e in events]
    # The first activity's own event opens the timeline; the interceptor's row for the last
    # activity (record_run_end) closes it, after the activity's own ``run.ended``.
    assert types[0] == "run.started"
    assert types[-2:] == ["run.ended", "activity.completed"]
    assert events[-1].activity == "record_run_end"
    assert types.count("export.written") == 1
    assert events[-2].detail == {"outcome": "completed"}
    assert all(e.workflow_id == handle.id for e in events if e.event_type.startswith("activity."))

    # Every activity the workflow scheduled is a step that completed, and no other.
    windows = history_windows(history)
    found = {s.activity_id: s for s in steps(events)}
    assert set(found) == set(windows)
    assert {s.outcome for s in found.values()} == {"completed"}
    assert all(len(s.attempts) == 1 and s.code is None for s in found.values())
    for activity_id, step in found.items():
        assert step.started_at is not None and step.duration_ms is not None
        own = (step.ended_at - step.started_at).total_seconds() * 1000
        assert abs(own - step.duration_ms) <= TOLERANCE_MS, (activity_id, own, step.duration_ms)
        scheduled, completed = windows[activity_id]
        window = (completed - scheduled).total_seconds() * 1000
        assert step.duration_ms <= window + TOLERANCE_MS, (activity_id, step.duration_ms, window)
    assert found["record_run_start"].started_at is not None
    assert found["export_report:r1"].ended_at < events[-1].created_at

    # The stage visits are the workflow's stages, in order, each closed by the next.
    visits = stages(events)
    assert [v.stage for v in visits] == [
        "extracting",
        "appraisal_review",
        "rooms_review",
        "components_processing",
        "components_review",
        "assets_processing",
        "assets_review",
        "exporting",
        "completed",
    ]
    assert all(v.revision == 1 and v.entered_by == "system" for v in visits)
    assert all(v.duration_ms is not None and v.duration_ms >= 0 for v in visits[:-1])
    assert visits[-1].left_at is None
    for review in ("appraisal_review", "rooms_review", "components_review", "assets_review"):
        visit = next(v for v in visits if v.stage == review)
        assert visit.gate_opened_at is not None
        assert visit.decided_at is None  # decided through the update here, not the API
    total = (visits[-1].entered_at - visits[0].entered_at).total_seconds() * 1000
    # Each closed visit's duration is rounded to the nearest millisecond (``_ms``), so the
    # sum can differ from the unrounded total by half a millisecond per visit (a bound of 1
    # failed in about half the runs once the closed visits' fractions added past it).
    closed = [v for v in visits if v.duration_ms is not None]
    assert abs(sum(v.duration_ms or 0 for v in visits) - total) <= 0.5 * len(closed)

    # The export carries the same document, as of the export.
    exported = json.loads(await harness.deps.storage.read(result.export_uri or ""))
    timeline = exported["summary"]["timeline"]
    dump_for_the_report("happy_path", timeline, [])
    assert set(timeline) == {"events", "steps", "stages"}
    assert [s["stage"] for s in timeline["stages"]] == [v.stage for v in visits][:-1]
    exported_steps = {s["activity_id"] for s in timeline["steps"]}
    assert "export_report:r1" not in exported_steps  # it had not ended when the export was built
    assert exported_steps == set(found) - {
        "export_report:r1",
        "record_run_end",
        "transition:completed:r1",
    }
    for section in ("rooms", "photos", "components"):
        assert all(row["provenance"]["origin"] == "model" for row in exported[section])
    assert all(a["provenance"]["origin"] == "model" for a in exported["assets"])
    assert all(
        f["provenance"]["origin"] == "model" for x in exported["extraction"] for f in x["fields"]
    )
    assert failure_text(RuntimeError("x")) == "RuntimeError: x [BASIS-WF-007]"


async def decide_like_the_api(
    harness: Harness,
    handle: Any,
    study: SeededStudy,
    stage: ReviewStage,
    revision: int,
    decision: str,
    corrections: tuple[RepoCorrection, ...] = (),
    comment: str | None = None,
) -> uuid.UUID:
    """Record the decision as ``POST .../decisions`` does (the review, the corrections, the
    stage move and the events, in one transaction), then deliver it to the workflow as the
    relay does."""
    await harness.wait_awaiting(handle, stage, revision)
    async with session_scope(harness.sessionmaker) as session:
        record = await ReviewRepository(session).record_decision(
            study_id=study.study_id,
            stage=Stage(stage),
            revision=revision,
            decision=DecisionKind(decision),
            reviewer_id=study.owner_id,
            idempotency_key=str(uuid.uuid4()),
            comment=comment,
            corrections=corrections,
        )
        review_id, submitted = record.review.id, record.review.created_at
    delivered = Decision(
        review_id=str(review_id),
        stage=stage,
        revision=revision,
        decision=decision,  # type: ignore[arg-type]
        reviewer_id=str(study.owner_id),
        corrections=tuple(
            CorrectionInput(
                entity_type=c.entity_type,
                entity_id=str(c.entity_id),
                after=c.after,
                reason=c.reason,
            )
            for c in corrections
        ),
        submitted_at=submitted,
    )
    await handle.execute_update(StudyWorkflow.decide, delivered, id=f"decision-{review_id}")
    return review_id


async def test_every_row_says_who_produced_it_after_a_correction_of_each_kind(
    harness: Harness,
) -> None:
    study = await harness.seed(photos=PHOTOS)
    sid = study.study_id
    async with harness.worker():
        started = await harness.start(study, components_gate=True)
        handle = started.handle

        # Appraisal review (revision 1): one field corrected.
        await harness.wait_awaiting(handle, "appraisal_review", 1)
        [extraction] = await rows(harness, Extraction, sid, revision=1)
        fields = sorted(
            (f for f in await rows(harness, ExtractionField, sid, extraction_id=extraction.id)),
            key=lambda f: (f.section, f.field),
        )
        field = next(f for f in fields if f.value_text is not None)
        await decide_like_the_api(
            harness,
            handle,
            study,
            "appraisal_review",
            1,
            "reject",
            (
                RepoCorrection(
                    "extraction_field",
                    field.id,
                    {"value_text": "corrected by hand"},
                    before={"value_text": field.value_text},
                    reason="the deed says so",
                ),
            ),
        )
        await decide_like_the_api(harness, handle, study, "appraisal_review", 2, "approve")

        # Rooms review (revision 2): the kitchen renamed, a bathroom photo moved into it.
        await harness.wait_awaiting(handle, "rooms_review", 2)
        kitchen = next(r for r in await rows(harness, Room, sid, revision=2) if r.name == "Kitchen")
        bathroom_2 = study.photo_ids[3]
        await decide_like_the_api(
            harness,
            handle,
            study,
            "rooms_review",
            2,
            "reject",
            (
                RepoCorrection(
                    "room", kitchen.id, {"name": "Main Kitchen"}, before={"name": "Kitchen"}
                ),
                RepoCorrection("photo_room", bathroom_2, {"room_id": str(kitchen.id)}),
            ),
            comment="the second bathroom photo shows the kitchen pass-through",
        )
        await decide_like_the_api(harness, handle, study, "rooms_review", 3, "approve")

        # Components review (revision 3): a cabinet's material and count, a fixture added.
        await harness.wait_awaiting(handle, "components_review", 3)
        main_kitchen = next(
            r for r in await rows(harness, Room, sid, revision=3) if r.name == "Main Kitchen"
        )
        cabinets = [
            c
            for c in await rows(harness, Component, sid, revision=3, room_id=main_kitchen.id)
            if c.label == "kitchen_cabinet"
        ]
        cabinet = sorted(cabinets, key=lambda c: c.id)[0]
        await decide_like_the_api(
            harness,
            handle,
            study,
            "components_review",
            3,
            "reject",
            (
                RepoCorrection(
                    "component",
                    cabinet.id,
                    {"material": "laminate", "count": 8},
                    before={"material": cabinet.material},
                ),
                RepoCorrection(
                    "room_component",
                    main_kitchen.id,
                    {
                        "label": "light_fixture",
                        "count": 3,
                        "material": "metal",
                        "attachment": "permanent",
                    },
                ),
            ),
        )
        await decide_like_the_api(harness, handle, study, "components_review", 4, "approve")

        # Assets review (revision 4): the cabinet's asset moves to the 7-year bucket.
        await harness.wait_awaiting(handle, "assets_review", 4)
        components_4 = {c.id: c for c in await rows(harness, Component, sid, revision=4)}
        cabinet_4 = next(
            c
            for c in components_4.values()
            if isinstance(c.raw, dict) and c.raw.get("carried_from") == str(cabinet.id)
        )
        [cabinet_asset] = await rows(harness, Asset, sid, revision=4, component_id=cabinet_4.id)
        await decide_like_the_api(
            harness,
            handle,
            study,
            "assets_review",
            4,
            "reject",
            (RepoCorrection("asset", cabinet_asset.id, {"bucket": "y7", "recovery_period": 7}),),
        )
        await decide_like_the_api(harness, handle, study, "assets_review", 5, "approve")
        result = await harness.result(handle)
    assert result.outcome == "completed" and result.revision == 5

    # ---- provenance of every row of the final revision
    async with session_scope(harness.sessionmaker) as session:
        resolver = ProvenanceResolver(session, sid)
        final_rooms = await rows(harness, Room, sid, revision=5)
        room_prov = await resolver.rooms(final_rooms)
        final_components = await rows(harness, Component, sid, revision=5)
        component_prov = await resolver.components(final_components)
        final_assets = await rows(harness, Asset, sid, revision=5)
        asset_prov = await resolver.assets(final_assets)
        assignments = await rows(harness, PhotoRoom, sid, revision=5)
        photo_prov = await resolver.photo_rooms(assignments)
        [final_extraction] = await rows(harness, Extraction, sid, revision=5)
        final_fields = await rows(harness, ExtractionField, sid, extraction_id=final_extraction.id)
        field_prov = await resolver.fields(final_fields)
        audit = await AuditRepository(session).list_for_study(sid)

    by_name = {r.name: r for r in final_rooms}
    assert set(by_name) == {"Main Kitchen", "Bathroom"}
    renamed = room_prov[by_name["Main Kitchen"].id]
    assert renamed.origin == "engineer_modified"
    assert (renamed.before, renamed.after) == ({"name": "Kitchen"}, {"name": "Main Kitchen"})
    assert renamed.stage == "rooms_review" and renamed.revision == 2
    assert renamed.by == study.owner_id and renamed.correction_id is not None
    assert room_prov[by_name["Bathroom"].id].origin == "model"

    moved = photo_prov[bathroom_2]
    assert moved.origin == "engineer_modified" and moved.after == {"room_id": str(kitchen.id)}
    assert moved.before is not None and moved.before["room_id"] is not None
    assert {photo_prov[p].origin for p in study.photo_ids[:3]} == {"model"}
    assert (
        next(a for a in assignments if a.document_id == bathroom_2).room_id
        == by_name["Main Kitchen"].id
    )

    by_label: dict[str, list[Component]] = {}
    for c in final_components:
        by_label.setdefault(c.label, []).append(c)
    corrected_cabinet = next(
        c for c in by_label["kitchen_cabinet"] if component_prov[c.id].origin != "model"
    )
    cabinet_provenance = component_prov[corrected_cabinet.id]
    assert cabinet_provenance.origin == "engineer_modified"
    assert cabinet_provenance.changed == ("count", "material")
    assert cabinet_provenance.after == {"material": "laminate", "count": 8}
    assert cabinet_provenance.before == {
        "count": cabinet.raw.get("count"),
        "material": cabinet.material,
    }
    assert cabinet_provenance.stage == "components_review" and cabinet_provenance.revision == 3
    assert corrected_cabinet.material == "laminate" and corrected_cabinet.raw["count"] == 8
    [lamp] = by_label["light_fixture"]
    assert component_prov[lamp.id].origin == "engineer_added"
    assert component_prov[lamp.id].after["label"] == "light_fixture"
    assert component_prov[lamp.id].stage == "components_review"
    others = [c for c in final_components if c.id not in (corrected_cabinet.id, lamp.id)]
    assert others and {component_prov[c.id].origin for c in others} == {"model"}

    by_component = {a.component_id: a for a in final_assets}
    cabinet_asset_5 = by_component[corrected_cabinet.id]
    bucketed = asset_prov[cabinet_asset_5.id]
    assert bucketed.origin == "engineer_modified" and bucketed.stage == "assets_review"
    assert bucketed.after == {"bucket": "y7", "recovery_period": 7}
    assert bucketed.before is not None and bucketed.before["bucket"] == "y5"
    assert cabinet_asset_5.bucket.value == "y7"
    assert asset_prov[by_component[lamp.id].id].origin == "engineer_added"
    model_assets = [
        a for a in final_assets if a.component_id not in (corrected_cabinet.id, lamp.id)
    ]
    assert model_assets and {asset_prov[a.id].origin for a in model_assets} == {"model"}

    corrected_field = next(
        f for f in final_fields if f.section == field.section and f.field == field.field
    )
    assert corrected_field.value_text == "corrected by hand" and corrected_field.source == "manual"
    field_provenance = field_prov[corrected_field.id]
    assert field_provenance.origin == "engineer_modified"
    assert (
        field_provenance.before is not None
        and field_provenance.before["value_text"] == field.value_text
    )
    assert field_provenance.stage == "appraisal_review" and field_provenance.revision == 1
    assert {field_prov[f.id].origin for f in final_fields if f.id != corrected_field.id} == {
        "model"
    }

    # ---- the audit listing: every action, in order, with what each correction produced
    actions = [(i.kind, i.action, i.entity_type) for i in audit]
    assert actions.count(("decision", "approved", "review")) == 4
    assert actions.count(("decision", "rejected", "review")) == 4
    assert ("correction", "modified", "extraction_field") in actions
    assert ("correction", "modified", "room") in actions
    assert ("correction", "moved", "photo_room") in actions
    assert ("correction", "modified", "component") in actions
    assert ("correction", "added", "room_component") in actions
    assert ("correction", "modified", "asset") in actions
    assert [i.at for i in audit] == sorted(i.at for i in audit)
    rename = next(i for i in audit if i.action == "modified" and i.entity_type == "room")
    assert rename.produced  # the room of revision 3 the rename produced
    assert set(rename.produced) <= {r.id for r in await rows(harness, Room, sid, revision=3)}
    cabinet_item = next(i for i in audit if i.entity_type == "component")
    assert (
        cabinet_item.changed == ("count", "material") and cabinet_item.stage == "components_review"
    )
    assert set(cabinet_item.produced) == {cabinet_4.id}
    field_item = next(i for i in audit if i.entity_type == "extraction_field")
    assert field_item.produced and field_item.reason == "the deed says so"
    asset_item = next(i for i in audit if i.entity_type == "asset")
    assert set(asset_item.produced) == {cabinet_asset_5.id}

    # ---- the timeline has the engineer's events and the copies' lineage
    events = await events_of(harness, sid)
    types = [e.event_type for e in events]
    assert types.count("review.decided") == 8 and types.count("correction.recorded") == 6
    assert types.count("revision.carried") == 4 and "corrections.refused" not in types
    moves = [e for e in events if e.event_type == "entity.moved"]
    assert len(moves) == 1 and moves[0].entity_id == bathroom_2
    assert moves[0].actor_type == "engineer" and moves[0].actor_id == study.owner_id
    entered = [e for e in events if e.event_type == "stage.entered"]
    assert {e.actor_type for e in entered} == {"engineer", "system"}
    visits = stages(events)
    reviews = [v for v in visits if v.stage.endswith("_review")]
    assert all(v.gate_opened_at is not None and v.decided_at is not None for v in reviews)
    assert [v.decision for v in reviews] == ["reject", "approve"] * 4
    assert all((v.review_wait_ms or 0) >= 0 for v in reviews)

    # ---- the export carries the provenance
    exported = json.loads(await harness.deps.storage.read(result.export_uri or ""))
    dump_for_the_report("audit", exported["summary"]["timeline"], audit)
    origins = {c["provenance"]["origin"] for c in exported["components"]}
    assert origins == {"model", "engineer_modified", "engineer_added"}
    assert {a["provenance"]["origin"] for a in exported["assets"]} == origins
    assert {r["provenance"]["origin"] for r in exported["rooms"]} == {"model", "engineer_modified"}
    assert any(p["provenance"]["origin"] == "engineer_modified" for p in exported["photos"])
    assert any(
        f["provenance"]["origin"] == "engineer_modified"
        for x in exported["extraction"]
        for f in x["fields"]
    )
