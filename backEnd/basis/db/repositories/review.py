"""Review decisions: review row + stage transition + outbox signal in one transaction.

Where a decision moves the study (``DECISION_TRANSITIONS``):

=================  ===================  ===================================
stage              approve              reject (revision + 1)
=================  ===================  ===================================
appraisal_review   rooms_review         extracting
rooms_review       assets_processing    extracting
assets_review      exporting            assets_processing
=================  ===================  ===================================

A rejection sends the study back to the stage that produced the rejected output and
starts a new revision (S1_SPEC §1: outputs are grouped by revision per study), so the
rejected revision is never decided twice and its rows stay as history. The table is the
single place to change if S2's workflow defines the stage graph differently.

The ``workflow_signal`` outbox payload carries every field of
``basis.workflow.contracts.Decision`` (review id, stage, revision, decision, reviewer,
corrections, submission time) plus the routing fields, so ``basis.relay`` builds the
Temporal update from the message alone.

:meth:`ReviewRepository.missing_targets` answers which correction targets do not exist in
a study's revision (``CORRECTION_TARGET_TYPES``: extraction fields, rooms, components and
assets of that revision; photos are documents of kind ``photo``, which are not
revisioned). The API uses it to refuse a correction the workflow's ``apply_corrections``
would refuse later.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import REVIEW_STAGES, Decision, DocumentKind, Stage, StudyStatus
from basis.db.errors import (
    DuplicateKeyError,
    GateNotOpenError,
    StaleStateError,
    constraint_name,
)
from basis.db.models import (
    Asset,
    Component,
    Correction,
    Document,
    Extraction,
    ExtractionField,
    OutboxMessage,
    Review,
    Room,
    Study,
)
from basis.db.repositories.idempotency import scoped_key
from basis.db.repositories.outbox import WORKFLOW_SIGNAL, OutboxRepository
from basis.db.repositories.study import StudyRepository

DECISION_TRANSITIONS: Final[dict[tuple[Stage, Decision], Stage]] = {
    (Stage.APPRAISAL_REVIEW, Decision.APPROVE): Stage.ROOMS_REVIEW,
    (Stage.APPRAISAL_REVIEW, Decision.REJECT): Stage.EXTRACTING,
    (Stage.ROOMS_REVIEW, Decision.APPROVE): Stage.ASSETS_PROCESSING,
    (Stage.ROOMS_REVIEW, Decision.REJECT): Stage.EXTRACTING,
    (Stage.ASSETS_REVIEW, Decision.APPROVE): Stage.EXPORTING,
    (Stage.ASSETS_REVIEW, Decision.REJECT): Stage.ASSETS_PROCESSING,
}


def decision_target(stage: Stage, decision: Decision, revision: int) -> tuple[Stage, int]:
    """``(to_stage, new_revision)`` for a decision; ``StaleStateError`` if not a review stage."""
    if stage not in REVIEW_STAGES:
        raise StaleStateError(f"{stage.value} is not a review stage")
    to_stage = DECISION_TRANSITIONS[(stage, decision)]
    return to_stage, revision + 1 if decision is Decision.REJECT else revision


def _target_query(
    entity_type: str, study_id: uuid.UUID, revision: int, ids: Sequence[uuid.UUID]
) -> Select[uuid.UUID]:
    """Ids among ``ids`` that are a ``entity_type`` of ``study_id`` at ``revision``."""
    if entity_type == "extraction_field":
        return (
            select(ExtractionField.id)
            .join(Extraction, Extraction.id == ExtractionField.extraction_id)
            .where(
                ExtractionField.study_id == study_id,
                Extraction.revision == revision,
                ExtractionField.id.in_(ids),
            )
        )
    if entity_type == "photo":
        return select(Document.id).where(
            Document.study_id == study_id,
            Document.kind == DocumentKind.PHOTO,
            Document.id.in_(ids),
        )
    if entity_type == "room":
        return select(Room.id).where(
            Room.study_id == study_id, Room.revision == revision, Room.id.in_(ids)
        )
    if entity_type == "component":
        return select(Component.id).where(
            Component.study_id == study_id, Component.revision == revision, Component.id.in_(ids)
        )
    if entity_type == "asset":
        return select(Asset.id).where(
            Asset.study_id == study_id, Asset.revision == revision, Asset.id.in_(ids)
        )
    raise KeyError(entity_type)


# Every entity type a correction can target (the union of
# ``basis.workflow.contracts.CORRECTION_TYPES``; a unit test keeps the two equal).
CORRECTION_TARGET_TYPES: Final = frozenset(
    {"extraction_field", "room", "component", "asset", "photo"}
)


@dataclass(frozen=True, slots=True)
class CorrectionInput:
    entity_type: str
    entity_id: uuid.UUID
    after: Any
    before: Any = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    review: Review
    study: Study
    signal: OutboxMessage
    corrections: list[Correction]


class ReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _ensure_gate_open(self, study_id: uuid.UUID, stage: Stage, revision: int) -> None:
        """``GateNotOpenError`` when the study is at ``stage``/``revision`` but not waiting
        for a decision (``waiting_review``, or ``stalled`` past the deadline). The row is
        locked, so the workflow's transition and this decision serialize."""
        status = await self._session.scalar(
            select(Study.status)
            .where(Study.id == study_id, Study.current_stage == stage, Study.revision == revision)
            .with_for_update()
        )
        if status is not None and status not in (StudyStatus.WAITING_REVIEW, StudyStatus.STALLED):
            raise GateNotOpenError(
                f"{stage.value} revision {revision} of study {study_id} is not open for a "
                f"decision yet (status {status.value})"
            )

    async def record_decision(
        self,
        *,
        study_id: uuid.UUID,
        stage: Stage,
        revision: int,
        decision: Decision,
        reviewer_id: uuid.UUID,
        idempotency_key: str,
        comment: str | None = None,
        corrections: Sequence[CorrectionInput] = (),
    ) -> DecisionRecord:
        """Record an approve/reject in the caller's transaction (it commits or rolls back).

        Order: the compare-and-set transition first, so it takes the study's row lock and
        a concurrent decision on the same stage/revision waits, re-checks, and fails with
        ``StaleStateError``; then the review, the corrections, and the outbox signal. The
        unique constraints on ``reviews`` are the backstop for any path that bypasses the
        transition. Nothing is committed here; any exception leaves the caller to roll
        back everything.
        """
        to_stage, new_revision = decision_target(stage, decision, revision)
        await self._ensure_gate_open(study_id, stage, revision)
        study = await StudyRepository(self._session).transition(
            study_id, stage, to_stage, revision, new_revision=new_revision
        )
        if to_stage in REVIEW_STAGES:
            # The next gate opens when the workflow says so (its transition converges the
            # status to waiting_review): until then the study is processing, and that gate
            # refuses decisions (EVAL_REPORT 25: photos run behind the appraisal review).
            await self._session.execute(
                update(Study)
                .where(Study.id == study_id)
                .values(status=StudyStatus.PROCESSING)
                .execution_options(synchronize_session=False)
            )
            await self._session.refresh(study)
        review = Review(
            study_id=study_id,
            stage=stage,
            revision=revision,
            decision=decision,
            reviewer_id=reviewer_id,
            comment=comment,
            idempotency_key=scoped_key(reviewer_id, idempotency_key),
        )
        self._session.add(review)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            name = constraint_name(exc)
            if name == "uq_reviews_study_id_stage_revision":
                raise StaleStateError(
                    f"{stage.value} revision {revision} of study {study_id} is already decided"
                ) from exc
            if name == "uq_reviews_idempotency_key":
                raise DuplicateKeyError("idempotency key already used for a decision") from exc
            raise
        await self._session.refresh(review)

        rows = [
            Correction(
                study_id=study_id,
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                revision=revision,
                before=item.before,
                after=item.after,
                reason=item.reason,
                reviewer_id=reviewer_id,
            )
            for item in corrections
        ]
        if rows:
            self._session.add_all(rows)
            await self._session.flush()

        signal = await OutboxRepository(self._session).enqueue(
            WORKFLOW_SIGNAL,
            {
                "study_id": str(study_id),
                "workflow_id": study.workflow_id,
                "review_id": str(review.id),
                "stage": stage.value,
                "revision": revision,
                "decision": decision.value,
                "to_stage": to_stage.value,
                "new_revision": new_revision,
                # The rest of basis.workflow.contracts.Decision, so the relay can build the
                # update from the message alone (S2_SPEC section 6).
                "reviewer_id": str(reviewer_id),
                "submitted_at": review.created_at.isoformat(),
                "corrections": [
                    {
                        "entity_type": row.entity_type,
                        "entity_id": str(row.entity_id),
                        "after": row.after,
                        "reason": row.reason,
                    }
                    for row in rows
                ],
            },
        )
        return DecisionRecord(review=review, study=study, signal=signal, corrections=rows)

    async def missing_targets(
        self,
        study_id: uuid.UUID,
        revision: int,
        targets: Sequence[tuple[str, uuid.UUID]],
    ) -> set[tuple[str, uuid.UUID]]:
        """The ``(entity_type, entity_id)`` pairs that are not in ``revision`` of the study.

        Rooms, components, assets and extraction fields (through their extraction) must
        belong to ``revision``; a photo must be a ``photo`` document of the study. One query
        per entity type present. ``KeyError`` for a type not in ``CORRECTION_TARGET_TYPES``.
        """
        by_type: dict[str, set[uuid.UUID]] = {}
        for entity_type, entity_id in targets:
            if entity_type not in CORRECTION_TARGET_TYPES:
                raise KeyError(entity_type)
            by_type.setdefault(entity_type, set()).add(entity_id)
        missing: set[tuple[str, uuid.UUID]] = set()
        for entity_type, ids in sorted(by_type.items()):
            query = _target_query(entity_type, study_id, revision, sorted(ids))
            found = set((await self._session.scalars(query)).all())
            missing.update((entity_type, entity_id) for entity_id in ids - found)
        return missing

    async def get_by_idempotency_key(
        self, reviewer_id: uuid.UUID, idempotency_key: str
    ) -> Review | None:
        stmt = select(Review).where(
            Review.idempotency_key == scoped_key(reviewer_id, idempotency_key)
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def list_for_study(self, study_id: uuid.UUID) -> list[Review]:
        stmt = (
            select(Review).where(Review.study_id == study_id).order_by(Review.created_at, Review.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def latest_by_stage(self, study_id: uuid.UUID) -> dict[Stage, Review]:
        """The decision with the highest revision for each review stage of the study."""
        stmt = (
            select(Review)
            .where(Review.study_id == study_id)
            .ext(distinct_on(Review.stage))
            .order_by(Review.stage, Review.revision.desc())
        )
        return {review.stage: review for review in (await self._session.scalars(stmt)).all()}
