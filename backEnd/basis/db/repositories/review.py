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
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import REVIEW_STAGES, Decision, Stage
from basis.db.errors import DuplicateKeyError, StaleStateError, constraint_name
from basis.db.models import Correction, OutboxMessage, Review, Study
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
        study = await StudyRepository(self._session).transition(
            study_id, stage, to_stage, revision, new_revision=new_revision
        )
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
