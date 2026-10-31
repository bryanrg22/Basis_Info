"""The per-study timeline (``study_events``): what happened to a study, in order.

Append-only (``forbid_mutation()``), one row per occurrence, written in the same transaction
as the change it records where there is one (a decision, a stage move, a correction applied
to a row), so a rollback removes both; the worker's activity timings are written by the
activity interceptor (``basis.worker.interceptors``) in their own short transaction, since an
activity's own transaction may never commit. The route ``GET /v1/studies/{id}/timeline`` and
the export's ``summary.timeline`` read it (docs/api/TIMELINE_AND_AUDIT.md).

Event vocabulary (``event_type``; the actor in brackets):

* ``study.created`` [engineer], ``study.intake_set`` [engineer] (``detail``: the intake's
  ``version_before``/``version_after`` and ``if_match``), ``study.archived`` [engineer];
* ``document.uploaded`` [engineer]: an upload confirmed (``entity_id`` the document);
* ``study.started`` [engineer]: ``POST .../start`` moved the study to ``extracting``;
* ``run.started`` / ``run.ended`` [system]: the Temporal execution (``detail.outcome``);
* ``stage.entered`` [engineer or system]: the study row moved to ``stage``/``revision``
  (``before.stage``/``before.revision`` where it came from; the engineer's decision moves it
  first, the workflow's ``transition`` converges);
* ``gate.opened`` [system]: the workflow opened a review gate (the study is
  ``waiting_review`` at ``stage``);
* ``activity.completed`` / ``activity.failed`` / ``activity.cancelled`` [system]: one
  attempt of an activity ended; ``started_at``, ``duration_ms``, ``attempt``, ``code`` and
  ``detail.error`` on a failure;
* ``review.decided`` [engineer]: a decision (``entity_id`` the review; ``detail``:
  decision, corrections, comment);
* ``correction.recorded`` [engineer]: one correction the decision carried (``entity_type``,
  ``entity_id`` the corrected row of the decided revision, ``before``/``after``, ``detail.
  correction_id``);
* ``entity.corrected`` / ``entity.added`` / ``entity.moved`` / ``entity.merged``
  [engineer]: the row of the next revision that an applied correction produced
  (``entity_id`` the new row, ``detail.carried_from`` the old one, ``detail.correction_id``);
* ``entity.removed`` [engineer]: a component row an applied ``component_remove`` left out of
  the next revision (``entity_id`` the removed row, which has no successor; ``before`` its
  values; ``detail.kind``); ``entity.added`` also for an appraisal field an
  ``extraction_field_add`` added (``entity_id`` the new field row);
* ``entity.deleted`` [engineer]: a manual photo object soft-deleted; ``entity.added``
  also for a manual photo object;
* ``revision.carried`` [system]: ``apply_corrections`` copied the rest of a revision
  (``detail`` counts per table);
* ``corrections.refused`` [system]: ``apply_corrections`` refused a rejection's corrections
  (``code``); the revision started without them;
* ``inventory.rebuilt`` [model]: ``reconcile_rooms`` replaced a room's inventory rows
  (``entity_id`` the room, ``detail.deleted``/``written``);
* ``room.voted`` [model]: ``reconcile_rooms``' vote changed a file-name room's type or
  floor (``entity_id`` the room, ``before``/``after``, ``detail.sources`` per field);
* ``stage.failed`` / ``stage.failure_cleared`` [system]: ``record_failure`` (``code``);
* ``study.stalled`` / ``review.reminded`` [system]; ``study.retry_requested`` /
  ``study.cancel_requested`` [engineer];
* ``export.written`` [system]: the export (``detail.uri``, ``detail.sha256``);
* ``convention.promoted`` [engineer]: ``promote_conventions`` promoted an approved asset's
  ruling to the firm's conventions (``entity_id`` the asset; ``detail`` key, bucket,
  ``agrees_with_rules``, outcome, approvals); ``convention.replaced`` [engineer]: the
  approved answer replaced a different convention (``code``
  ``E.ASSETS_CONVENTION_REPLACED``, ``before`` the replaced row).

Durations: an ``activity.*`` end row carries ``started_at`` (the attempt's start on the
worker's clock) and ``duration_ms`` (monotonic on the worker), so a step's duration never
depends on two clocks; the stage durations come from consecutive ``stage.entered`` rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import Stage
from basis.db.models import StudyEvent
from basis.obs.logging import get_context

Actor = Literal["engineer", "model", "system"]

MAX_TIMELINE_PAGE: Final = 1000
MAX_ERROR_CHARS: Final = 2000


def request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class EventPage:
    items: list[StudyEvent]
    next_seq: int | None


class StudyEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        study_id: uuid.UUID,
        event_type: str,
        *,
        actor: Actor,
        actor_id: uuid.UUID | None = None,
        stage: Stage | str | None = None,
        revision: int | None = None,
        workflow_id: str | None = None,
        run_id: str | None = None,
        activity: str | None = None,
        activity_id: str | None = None,
        attempt: int | None = None,
        started_at: datetime | None = None,
        duration_ms: int | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        code: str | None = None,
        before: Any | None = None,
        after: Any | None = None,
        detail: dict[str, Any] | None = None,
    ) -> StudyEvent:
        """Append one event (flushed, not committed: the caller's transaction decides)."""
        row = StudyEvent(
            study_id=study_id,
            event_type=event_type,
            stage=Stage(stage) if isinstance(stage, str) else stage,
            revision=revision,
            actor_type=actor,
            actor_id=actor_id,
            workflow_id=workflow_id,
            run_id=run_id,
            activity=activity,
            activity_id=activity_id,
            attempt=attempt,
            started_at=started_at,
            duration_ms=duration_ms,
            entity_type=entity_type,
            entity_id=entity_id,
            code=code,
            request_id=request_id(),
            before=before,
            after=after,
            detail=detail,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for_study(
        self,
        study_id: uuid.UUID,
        *,
        after_seq: int | None = None,
        limit: int | None = None,
        event_types: Sequence[str] | None = None,
    ) -> EventPage:
        """The study's events in ``seq`` order, optionally after ``after_seq`` (keyset) and
        limited; ``next_seq`` is the cursor for the next page (None on the last)."""
        stmt = select(StudyEvent).where(StudyEvent.study_id == study_id)
        if after_seq is not None:
            stmt = stmt.where(StudyEvent.seq > after_seq)
        if event_types:
            stmt = stmt.where(StudyEvent.event_type.in_(list(event_types)))
        stmt = stmt.order_by(StudyEvent.seq)
        if limit is None:
            rows = list((await self._session.scalars(stmt)).all())
            return EventPage(items=rows, next_seq=None)
        rows = list((await self._session.scalars(stmt.limit(limit + 1))).all())
        items = rows[:limit]
        return EventPage(items=items, next_seq=items[-1].seq if len(rows) > limit else None)

    async def for_entities(
        self, study_id: uuid.UUID, entity_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[StudyEvent]]:
        """The entity events (``entity.*``) of each id, in order."""
        if not entity_ids:
            return {}
        stmt = (
            select(StudyEvent)
            .where(
                StudyEvent.study_id == study_id,
                StudyEvent.entity_id.in_(list(entity_ids)),
                StudyEvent.event_type.startswith("entity."),
            )
            .order_by(StudyEvent.seq)
        )
        found: dict[uuid.UUID, list[StudyEvent]] = {}
        for row in (await self._session.scalars(stmt)).all():
            assert row.entity_id is not None
            found.setdefault(row.entity_id, []).append(row)
        return found
