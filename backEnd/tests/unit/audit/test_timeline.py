"""Steps and stages derived from events (``basis.audit.timeline``), on synthetic rows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from basis.audit.timeline import stages, steps, timeline_document
from basis.db.enums import Stage
from basis.db.models import StudyEvent

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
STUDY = uuid.uuid4()


def event(seq: int, event_type: str, offset_s: float, **fields: Any) -> StudyEvent:
    row = StudyEvent(
        study_id=STUDY,
        event_type=event_type,
        actor_type=fields.pop("actor_type", "system"),
        **fields,
    )
    row.seq = seq
    row.created_at = T0 + timedelta(seconds=offset_s)
    return row


def test_steps_sum_attempts_and_keep_the_last_outcome() -> None:
    events = [
        event(
            1,
            "activity.failed",
            5,
            activity="extract_fields",
            activity_id="extract_fields:r1",
            attempt=1,
            started_at=T0 + timedelta(seconds=1),
            duration_ms=4000,
            code="BASIS-PROVIDER-007",
            detail={"error": "ProviderUnavailable: [BASIS-PROVIDER-007] 503"},
        ),
        event(
            2,
            "activity.completed",
            12,
            activity="extract_fields",
            activity_id="extract_fields:r1",
            attempt=2,
            started_at=T0 + timedelta(seconds=7),
            duration_ms=5000,
        ),
        event(
            3,
            "activity.completed",
            3,
            activity="list_photos",
            activity_id="list_photos",
            attempt=1,
            started_at=T0 + timedelta(seconds=2.5),
            duration_ms=500,
        ),
    ]
    found = {s.activity_id: s for s in steps(events)}
    extract = found["extract_fields:r1"]
    assert [a.attempt for a in extract.attempts] == [1, 2]
    assert extract.duration_ms == 9000
    assert extract.started_at == T0 + timedelta(seconds=1)
    assert extract.ended_at == T0 + timedelta(seconds=12)
    assert (extract.outcome, extract.code) == ("completed", None)
    assert extract.attempts[0].error is not None and extract.attempts[0].code is not None
    assert found["list_photos"].duration_ms == 500


def test_stages_measure_visits_gates_and_review_waits() -> None:
    events = [
        event(1, "stage.entered", 0, stage=Stage.EXTRACTING, revision=1, actor_type="engineer"),
        event(2, "stage.entered", 60, stage=Stage.APPRAISAL_REVIEW, revision=1),
        event(3, "gate.opened", 60.5, stage=Stage.APPRAISAL_REVIEW, revision=1),
        event(
            4,
            "review.decided",
            600,
            stage=Stage.APPRAISAL_REVIEW,
            revision=1,
            actor_type="engineer",
            detail={"decision": "approve"},
        ),
        event(5, "stage.entered", 600, stage=Stage.ROOMS_REVIEW, revision=1, actor_type="engineer"),
        event(6, "gate.opened", 700, stage=Stage.ROOMS_REVIEW, revision=1),
    ]
    visits = stages(events)
    assert [(v.stage, v.revision) for v in visits] == [
        ("extracting", 1),
        ("appraisal_review", 1),
        ("rooms_review", 1),
    ]
    extracting, appraisal, rooms = visits
    assert extracting.duration_ms == 60_000 and extracting.entered_by == "engineer"
    assert appraisal.duration_ms == 540_000
    assert appraisal.gate_opened_at == T0 + timedelta(seconds=60.5)
    assert appraisal.review_wait_ms == 539_500 and appraisal.decision == "approve"
    assert rooms.left_at is None and rooms.duration_ms is None
    assert rooms.gate_opened_at is not None and rooms.review_wait_ms is None


def test_document_has_the_three_members() -> None:
    document = timeline_document([])
    assert document == {"events": [], "steps": [], "stages": []}
