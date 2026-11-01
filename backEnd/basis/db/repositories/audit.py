"""The audit listing: every engineer action on a study, in order
(``GET /v1/studies/{id}/audit``, docs/api/TIMELINE_AND_AUDIT.md).

Read from the tables that are the source of truth, so a study whose rows predate the
timeline lists the same: ``reviews`` (each decision), ``corrections`` (each correction, with
the review it belonged to and what it did: ``modified``, ``added`` (a component added to a
room, an appraisal field added), ``moved``, ``merged``, ``reanalyzed``, ``removed`` (a
component row left out of the next revision; it produced no row, so ``produced`` stays
empty)), manual ``photo_objects`` (``added`` when created, ``deleted`` when soft
deleted), and the engineer's other actions that only ``study_events`` records: the intake
and the report answers set (``intake``, ``report_answers``: engineer input too), an upload
confirmed (``document``), the study started, a retry or a cancel requested (``started``,
``retry``, ``cancel``), the study archived or unarchived (``archived``, ``unarchived``) and
the report signed (``report_signed``). Where ``study_events`` recorded which rows a
correction produced (``entity.*`` events naming the correction), the item lists them as
``produced``, so a reviewer can go from the engineer's input to the rows that carry it.

Pagination: the listing is assembled from four tables with their own time columns, sorted
by ``(at, seq, kind, entity_id)`` (``seq`` the event's insertion order for an item read from
``study_events``, 0 for the others, so several events of one transaction, which share its
``now()``, keep the order they were recorded in), then cut at the page: a keyset cursor
names the last item's sort key (:func:`encode_cursor`), so a page never skips or repeats an
item while new ones are appended (every source is append-only or soft-deleting). The
assembly reads one study's engineer actions, bounded by what engineers did to it, not by
the pipeline's output, which is why it is not pushed into SQL as a union of four shapes.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import Decision
from basis.db.errors import InvalidCursorError
from basis.db.models import Correction, PhotoObject, Review, StudyEvent

MAX_AUDIT_PAGE: Final = 1000

ACTIONS: Final[dict[str, str]] = {
    "extraction_field": "modified",
    "extraction_field_add": "added",
    "room": "modified",
    "component": "modified",
    "component_remove": "removed",
    "room_component": "added",
    "photo_room": "moved",
    "photo": "reanalyzed",
    "asset": "modified",
}


# The engineer's actions that exist only as events: ``event_type -> (kind, action)``. An
# ``intake`` or ``report_answers`` item is ``added`` the first time and ``modified`` after.
EVENT_KINDS: Final[dict[str, tuple[str, str]]] = {
    "study.intake_set": ("intake", "modified"),
    "study.report_answers_set": ("report_answers", "modified"),
    "document.uploaded": ("document", "uploaded"),
    "study.started": ("started", "started"),
    "study.retry_requested": ("retry", "requested"),
    "study.cancel_requested": ("cancel", "requested"),
    "study.archived": ("archived", "archived"),
    "study.unarchived": ("unarchived", "unarchived"),
    "report.signed": ("report_signed", "signed"),
    # docs/api/REVISIONS.md: a completed study reopened at a gate, a revision restored.
    "study.reopened": ("reopened", "reopened"),
    "revision.restored": ("restored", "restored"),
}
_PRODUCING_EVENTS: Final = ("entity.corrected", "entity.added", "entity.moved", "entity.merged")
_VERSIONED: Final = ("intake", "report_answers")

KINDS: Final = (
    "decision",
    "correction",
    "object_added",
    "object_deleted",
    *dict.fromkeys(kind for kind, _ in EVENT_KINDS.values()),
)


@dataclass(frozen=True, slots=True)
class AuditPage:
    items: list[AuditItem]
    next_cursor: str | None


def encode_cursor(item: AuditItem) -> str:
    """Opaque keyset cursor: the item's sort key ``(at, kind, entity_id)``."""
    raw = json.dumps(
        {"t": item.at.isoformat(), "q": item.seq, "k": item.kind, "e": _entity_key(item)},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int, str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        at = datetime.fromisoformat(data["t"])
        seq, kind, entity = data["q"], data["k"], data["e"]
        if (
            at.tzinfo is None
            or isinstance(seq, bool)
            or not isinstance(seq, int)
            or not isinstance(kind, str)
            or not isinstance(entity, str)
        ):
            raise ValueError("not a cursor")
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise InvalidCursorError("invalid pagination cursor") from exc
    return at, seq, kind, entity


def _entity_key(item: AuditItem) -> str:
    return str(item.entity_id)


def sort_key(item: AuditItem) -> tuple[datetime, int, str, str]:
    return (item.at, item.seq, item.kind, _entity_key(item))


@dataclass(frozen=True, slots=True)
class AuditItem:
    kind: str  # one of KINDS
    at: datetime
    by: uuid.UUID | None
    stage: str | None
    revision: int | None
    action: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    before: Any | None = None
    after: Any | None = None
    changed: tuple[str, ...] = ()
    reason: str | None = None
    review_id: uuid.UUID | None = None
    correction_id: uuid.UUID | None = None
    produced: tuple[uuid.UUID, ...] = field(default=())
    seq: int = 0  # the event's ``seq`` for an item read from ``study_events``; the tiebreak


def correction_action(entity_type: str, after: Any) -> str:
    if entity_type == "room" and isinstance(after, dict) and "merge_into" in after:
        return "merged"
    return ACTIONS.get(entity_type, "modified")


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def page_for_study(
        self, study_id: uuid.UUID, *, limit: int, cursor: str | None = None
    ) -> AuditPage:
        """One page of :meth:`list_for_study` after ``cursor`` (keyset on the sort key);
        ``next_cursor`` names the page's last item, None on the last page."""
        if not 1 <= limit <= MAX_AUDIT_PAGE:
            raise ValueError(f"limit must be between 1 and {MAX_AUDIT_PAGE}")
        items = await self.list_for_study(study_id)
        if cursor is not None:
            after = decode_cursor(cursor)
            items = [item for item in items if sort_key(item) > after]
        page = items[:limit]
        return AuditPage(
            items=page, next_cursor=encode_cursor(page[-1]) if len(items) > limit else None
        )

    async def list_for_study(self, study_id: uuid.UUID) -> list[AuditItem]:
        """Every engineer action on the study, in ``sort_key`` order."""
        reviews = list(
            (
                await self._session.scalars(
                    select(Review).where(Review.study_id == study_id).order_by(Review.created_at)
                )
            ).all()
        )
        rejected_at = {r.revision: r for r in reviews if r.decision is Decision.REJECT}
        corrections = list(
            (
                await self._session.scalars(
                    select(Correction)
                    .where(Correction.study_id == study_id)
                    .order_by(Correction.created_at, Correction.id)
                )
            ).all()
        )
        objects = list(
            (
                await self._session.scalars(
                    select(PhotoObject).where(
                        PhotoObject.study_id == study_id, PhotoObject.source == "manual"
                    )
                )
            ).all()
        )
        events = list(
            (
                await self._session.scalars(
                    select(StudyEvent)
                    .where(
                        StudyEvent.study_id == study_id,
                        StudyEvent.event_type.in_([*_PRODUCING_EVENTS, *EVENT_KINDS]),
                    )
                    .order_by(StudyEvent.seq)
                )
            ).all()
        )
        produced: dict[uuid.UUID, list[uuid.UUID]] = {}
        intakes: list[AuditItem] = []
        for event in events:
            if event.event_type in EVENT_KINDS:
                intakes.append(_event_item(event))
                continue
            detail = event.detail if isinstance(event.detail, dict) else {}
            for raw in detail.get("correction_ids") or []:
                try:
                    correction_id = uuid.UUID(str(raw))
                except ValueError:
                    continue
                if event.entity_id is not None:
                    produced.setdefault(correction_id, []).append(event.entity_id)

        items: list[AuditItem] = list(intakes)
        for review in reviews:
            items.append(
                AuditItem(
                    kind="decision",
                    at=review.created_at,
                    by=review.reviewer_id,
                    stage=review.stage.value,
                    revision=review.revision,
                    action="approved" if review.decision is Decision.APPROVE else "rejected",
                    entity_type="review",
                    entity_id=review.id,
                    reason=review.comment,
                    review_id=review.id,
                )
            )
        for correction in corrections:
            rejection = rejected_at.get(correction.revision)
            items.append(
                AuditItem(
                    kind="correction",
                    at=correction.created_at,
                    by=correction.reviewer_id,
                    stage=rejection.stage.value if rejection is not None else None,
                    revision=correction.revision,
                    action=correction_action(correction.entity_type, correction.after),
                    entity_type=correction.entity_type,
                    entity_id=correction.entity_id,
                    before=correction.before,
                    after=correction.after,
                    changed=tuple(sorted(correction.after))
                    if isinstance(correction.after, dict)
                    else (),
                    reason=correction.reason,
                    review_id=rejection.id if rejection is not None else None,
                    correction_id=correction.id,
                    produced=tuple(produced.get(correction.id, ())),
                )
            )
        for obj in objects:
            fields = {
                "label": obj.label,
                "box": [obj.x1, obj.y1, obj.x2, obj.y2],
                "mask_source": obj.mask_source,
                "derived_from": None if obj.derived_from is None else str(obj.derived_from),
                "document_id": str(obj.document_id),
            }
            if obj.carried_from is None:  # a copy into a later revision is not a new action
                items.append(
                    AuditItem(
                        kind="object_added",
                        at=obj.created_at,
                        by=obj.created_by,
                        stage=None,
                        revision=obj.revision,
                        action="added",
                        entity_type="photo_object",
                        entity_id=obj.id,
                        after=fields,
                    )
                )
            if obj.deleted_at is not None:
                items.append(
                    AuditItem(
                        kind="object_deleted",
                        at=obj.deleted_at,
                        by=obj.deleted_by,
                        stage=None,
                        revision=obj.revision,
                        action="deleted",
                        entity_type="photo_object",
                        entity_id=obj.id,
                        before=fields,
                    )
                )
        items.sort(key=sort_key)
        return items


def _event_item(event: StudyEvent) -> AuditItem:
    """An action recorded only as an event, as an audit item: the event's ``before`` and
    ``after`` where it has them (the intake, the report answers), else its ``detail`` as
    ``after`` (what was uploaded, which workflow started, the retry's stage, the cancel's
    reason, the signature's hashes), so the item says what the engineer did."""
    kind, action = EVENT_KINDS[event.event_type]
    if kind in _VERSIONED:
        action = "modified" if event.before is not None else "added"
        before, after = event.before, event.after
    else:
        before, after = event.before, event.after if event.after is not None else event.detail
    return AuditItem(
        kind=kind,
        at=event.created_at,
        by=event.actor_id,
        stage=event.stage.value if event.stage is not None else None,
        revision=event.revision,
        action=action,
        entity_type=event.entity_type if event.entity_type is not None else "study",
        entity_id=event.entity_id if event.entity_id is not None else event.study_id,
        before=before,
        after=after,
        changed=tuple(sorted(_changed(before, after))) if kind in _VERSIONED else (),
        seq=event.seq,
    )


def _changed(before: Any, after: Any) -> set[str]:
    if not isinstance(after, dict):
        return set()
    if not isinstance(before, dict):
        return set(after)
    return {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
