"""Steps and stages of a study, derived from its ``study_events`` rows (pure; no I/O).

* A **step** is one activity id (``analyze_photo:<doc>:r1``): every attempt that ended
  (``activity.completed`` / ``activity.failed`` / ``activity.cancelled`` rows, one per
  attempt, written by the worker's interceptor), its first start, its last end, the sum of
  the attempts' durations (``duration_ms``: measured on the worker, so a step's time never
  depends on two clocks), the attempts' count and the outcome of the last one with its
  error code. Like an OpenTelemetry span: a start, an end, a status that is set only on
  failure and an ``error.type``-like code.
* A **stage** is one visit of the study to a stage at a revision (``stage.entered`` rows,
  in order): entered when the row moved, left when the next ``stage.entered`` happened
  (open while the study is there), its duration, and for a review stage when the workflow
  opened the gate (``gate.opened``) and when the engineer decided (``review.decided``), so
  the review wait is separable from the processing time.

Timestamps are the rows' ``created_at`` (the database clock, one clock for the whole
timeline), durations the differences between them, except a step's own ``duration_ms``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from basis.db.models import StudyEvent

ACTIVITY_END_EVENTS: Final = frozenset(
    {"activity.completed", "activity.failed", "activity.cancelled"}
)


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt: int
    started_at: datetime | None
    ended_at: datetime
    duration_ms: int | None
    outcome: str  # completed | failed | cancelled
    code: str | None
    error: str | None


@dataclass(slots=True)
class Step:
    activity: str
    activity_id: str
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def started_at(self) -> datetime | None:
        starts = [a.started_at for a in self.attempts if a.started_at is not None]
        return min(starts) if starts else None

    @property
    def ended_at(self) -> datetime:
        return max(a.ended_at for a in self.attempts)

    @property
    def duration_ms(self) -> int | None:
        """The attempts' durations summed; None when no attempt measured one."""
        measured = [a.duration_ms for a in self.attempts if a.duration_ms is not None]
        return sum(measured) if measured else None

    @property
    def outcome(self) -> str:
        return self.attempts[-1].outcome

    @property
    def code(self) -> str | None:
        return self.attempts[-1].code


@dataclass(slots=True)
class StageVisit:
    stage: str
    revision: int | None
    entered_at: datetime
    entered_by: str  # actor type of the move
    left_at: datetime | None = None
    gate_opened_at: datetime | None = None
    decided_at: datetime | None = None
    decision: str | None = None

    @property
    def duration_ms(self) -> int | None:
        return None if self.left_at is None else _ms(self.left_at - self.entered_at)

    @property
    def review_wait_ms(self) -> int | None:
        """From the gate opening to the decision (the engineer's time), when both happened."""
        if self.gate_opened_at is None or self.decided_at is None:
            return None
        return _ms(self.decided_at - self.gate_opened_at)


def _ms(delta: Any) -> int:
    return int(round(delta.total_seconds() * 1000))


def steps(events: Iterable[StudyEvent]) -> list[Step]:
    """The steps of a timeline, in the order their first attempt ended."""
    by_id: dict[str, Step] = {}
    for event in events:
        if event.event_type not in ACTIVITY_END_EVENTS or event.activity_id is None:
            continue
        step = by_id.get(event.activity_id)
        if step is None:
            step = by_id[event.activity_id] = Step(
                activity=event.activity or "", activity_id=event.activity_id
            )
        detail = event.detail if isinstance(event.detail, dict) else {}
        step.attempts.append(
            Attempt(
                attempt=event.attempt or len(step.attempts) + 1,
                started_at=event.started_at,
                ended_at=event.created_at,
                duration_ms=event.duration_ms,
                outcome=event.event_type.split(".", 1)[1],
                code=event.code,
                error=detail.get("error"),
            )
        )
    return list(by_id.values())


def stages(events: Sequence[StudyEvent]) -> list[StageVisit]:
    """The stage visits of a timeline, in order; the last one is open (``left_at`` None)
    unless the study completed."""
    visits: list[StageVisit] = []
    for event in events:
        if event.event_type != "stage.entered" or event.stage is None:
            continue
        if visits:
            visits[-1].left_at = event.created_at
        visits.append(
            StageVisit(
                stage=event.stage.value,
                revision=event.revision,
                entered_at=event.created_at,
                entered_by=event.actor_type,
            )
        )
    for event in events:
        if event.stage is None:
            continue
        visit = _visit(visits, event.stage.value, event.revision, event.created_at)
        if visit is None:
            continue
        if event.event_type == "gate.opened" and visit.gate_opened_at is None:
            visit.gate_opened_at = event.created_at
        elif event.event_type == "review.decided":
            detail = event.detail if isinstance(event.detail, dict) else {}
            visit.decided_at = event.created_at
            decision = detail.get("decision")
            visit.decision = decision if isinstance(decision, str) else None
    return visits


def _visit(
    visits: list[StageVisit], stage: str, revision: int | None, at: datetime
) -> StageVisit | None:
    """The visit of ``stage``/``revision`` open at ``at`` (the latest one entered before)."""
    found = None
    for visit in visits:
        if visit.stage == stage and visit.revision == revision and visit.entered_at <= at:
            found = visit
    return found


# --------------------------------------------------------------------------- JSON shapes
# Shared by ``GET /v1/studies/{id}/timeline`` (through its response models) and the export's
# ``summary.timeline``, so the two documents are the same shape.


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def event_json(event: StudyEvent) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "at": _iso(event.created_at),
        "event_type": event.event_type,
        "actor_type": event.actor_type,
        "actor_id": None if event.actor_id is None else str(event.actor_id),
        "stage": None if event.stage is None else event.stage.value,
        "revision": event.revision,
        "workflow_id": event.workflow_id,
        "run_id": event.run_id,
        "activity": event.activity,
        "activity_id": event.activity_id,
        "attempt": event.attempt,
        "started_at": _iso(event.started_at),
        "duration_ms": event.duration_ms,
        "entity_type": event.entity_type,
        "entity_id": None if event.entity_id is None else str(event.entity_id),
        "code": event.code,
        "request_id": event.request_id,
        "before": event.before,
        "after": event.after,
        "detail": event.detail,
    }


def step_json(step: Step) -> dict[str, Any]:
    return {
        "activity": step.activity,
        "activity_id": step.activity_id,
        "started_at": _iso(step.started_at),
        "ended_at": _iso(step.ended_at),
        "duration_ms": step.duration_ms,
        "attempts": [
            {
                "attempt": a.attempt,
                "started_at": _iso(a.started_at),
                "ended_at": _iso(a.ended_at),
                "duration_ms": a.duration_ms,
                "outcome": a.outcome,
                "code": a.code,
                "error": a.error,
            }
            for a in step.attempts
        ],
        "outcome": step.outcome,
        "code": step.code,
    }


def stage_json(visit: StageVisit) -> dict[str, Any]:
    return {
        "stage": visit.stage,
        "revision": visit.revision,
        "entered_at": _iso(visit.entered_at),
        "entered_by": visit.entered_by,
        "left_at": _iso(visit.left_at),
        "duration_ms": visit.duration_ms,
        "gate_opened_at": _iso(visit.gate_opened_at),
        "decided_at": _iso(visit.decided_at),
        "decision": visit.decision,
        "review_wait_ms": visit.review_wait_ms,
    }


def timeline_document(events: Sequence[StudyEvent]) -> dict[str, Any]:
    """``{"events", "steps", "stages"}`` of a whole timeline (the export's
    ``summary.timeline``)."""
    return {
        "events": [event_json(e) for e in events],
        "steps": [step_json(s) for s in steps(events)],
        "stages": [stage_json(v) for v in stages(events)],
    }
