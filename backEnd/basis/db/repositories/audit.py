"""The audit listing: every engineer action on a study, in order
(``GET /v1/studies/{id}/audit``, docs/api/TIMELINE_AND_AUDIT.md).

Read from the tables that are the source of truth, so a study whose rows predate the
timeline lists the same: ``reviews`` (each decision), ``corrections`` (each correction, with
the review it belonged to and what it did: ``modified``, ``added`` (a component added to a
room, an appraisal field added), ``moved``, ``merged``, ``reanalyzed``, ``removed`` (a
component row left out of the next revision; it produced no row, so ``produced`` stays
empty)), manual ``photo_objects`` (``added`` when created, ``deleted`` when soft
deleted), and the ``study.intake_set`` events (the intake is engineer input too). Where
``study_events`` recorded which rows a correction produced (``entity.*`` events naming the
correction), the item lists them as ``produced``, so a reviewer can go from the engineer's
input to the rows that carry it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import Decision
from basis.db.models import Correction, PhotoObject, Review, StudyEvent

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


@dataclass(frozen=True, slots=True)
class AuditItem:
    kind: str  # decision | correction | object_added | object_deleted | intake
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


def correction_action(entity_type: str, after: Any) -> str:
    if entity_type == "room" and isinstance(after, dict) and "merge_into" in after:
        return "merged"
    return ACTIONS.get(entity_type, "modified")


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_study(self, study_id: uuid.UUID) -> list[AuditItem]:
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
                        StudyEvent.event_type.in_(
                            [
                                "entity.corrected",
                                "entity.added",
                                "entity.moved",
                                "entity.merged",
                                "study.intake_set",
                            ]
                        ),
                    )
                    .order_by(StudyEvent.seq)
                )
            ).all()
        )
        produced: dict[uuid.UUID, list[uuid.UUID]] = {}
        intakes: list[AuditItem] = []
        for event in events:
            if event.event_type == "study.intake_set":
                intakes.append(
                    AuditItem(
                        kind="intake",
                        at=event.created_at,
                        by=event.actor_id,
                        stage=event.stage.value if event.stage is not None else None,
                        revision=event.revision,
                        action="modified" if event.before is not None else "added",
                        entity_type="study",
                        entity_id=event.entity_id,
                        before=event.before,
                        after=event.after,
                        changed=tuple(sorted(_changed(event.before, event.after))),
                    )
                )
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
        items.sort(key=lambda item: (item.at, item.kind, str(item.entity_id)))
        return items


def _changed(before: Any, after: Any) -> set[str]:
    if not isinstance(after, dict):
        return set()
    if not isinstance(before, dict):
        return set(after)
    return {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
