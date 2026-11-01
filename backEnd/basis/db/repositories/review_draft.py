"""An engineer's pending corrections at a review gate (``review_drafts``, migration ``0022``).

What the browser used to keep in memory until ``POST /v1/studies/{id}/decisions`` sent it:
the corrections and the comment an engineer has lined up for a gate. One row per ``(study,
stage, user)``, so two engineers draft apart; the row's ``version`` is its entity tag
(``If-Match`` on the draft routes, :mod:`basis.api.preconditions`), incremented by every
save, so two tabs of one engineer cannot overwrite each other unknowingly.

A draft is written against the study's ``revision`` of the moment (its corrections name rows
of that revision). It is **stale** once that revision is no longer the study's, or once the
stage has been decided at that revision (``reviews`` has a row for it): its corrections can
never be submitted. :meth:`ReviewDraftRepository.is_decided` answers the second half; the
route compares the first. Not locked against the study row: a decision that moves the study
while a draft is being saved leaves a stale draft, which the next ``GET`` reports as such.

The decision deletes the submitting user's draft on the stage in its own transaction
(:meth:`ReviewRepository.record_decision` calls :meth:`discard_for_user`), so a draft never
outlives the decision it was for.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import Stage
from basis.db.errors import NotVisibleError, PreconditionFailedError, constraint_name
from basis.db.models import Review, ReviewDraft, User

# ``(current version, whether a draft exists) -> may the write proceed``; the route's
# ``If-Match`` (the same shape as the intake's ``IntakePrecondition``).
DraftPrecondition = Callable[[int, bool], bool]
_ONE_PER_USER: str = "uq_review_drafts_study_id_stage_user_id"


@dataclass(frozen=True, slots=True)
class DraftWrite:
    """A draft save: the row after it, the version it replaced (0 when created)."""

    draft: ReviewDraft
    version_before: int

    @property
    def created(self) -> bool:
        return self.version_before == 0


@dataclass(frozen=True, slots=True)
class OtherDraft:
    """Another engineer's draft on the stage, for the "who else is drafting" list."""

    draft: ReviewDraft
    user: User


class ReviewDraftRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, study_id: uuid.UUID, stage: Stage, user_id: uuid.UUID
    ) -> ReviewDraft | None:
        stmt = select(ReviewDraft).where(
            ReviewDraft.study_id == study_id,
            ReviewDraft.stage == stage,
            ReviewDraft.user_id == user_id,
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def others(
        self, study_id: uuid.UUID, stage: Stage, user_id: uuid.UUID
    ) -> list[OtherDraft]:
        """Every other user's draft on the stage, oldest save first, with its user."""
        stmt = (
            select(ReviewDraft, User)
            .join(User, User.id == ReviewDraft.user_id)
            .where(
                ReviewDraft.study_id == study_id,
                ReviewDraft.stage == stage,
                ReviewDraft.user_id != user_id,
            )
            .order_by(ReviewDraft.updated_at, ReviewDraft.id)
        )
        return [
            OtherDraft(draft=draft, user=user)
            for draft, user in (await self._session.execute(stmt)).all()
        ]

    async def is_decided(self, study_id: uuid.UUID, stage: Stage, revision: int) -> bool:
        """Whether ``stage`` of the study has a decision at ``revision`` (a draft for it is
        stale: ``reviews`` is unique per study, stage and revision)."""
        stmt = select(
            exists().where(
                Review.study_id == study_id, Review.stage == stage, Review.revision == revision
            )
        )
        return bool(await self._session.scalar(stmt))

    async def write(
        self,
        study_id: uuid.UUID,
        stage: Stage,
        user_id: uuid.UUID,
        *,
        revision: int,
        document: dict[str, Any],
        precondition: DraftPrecondition | None = None,
    ) -> DraftWrite:
        """Create or replace the user's draft on the stage and increment its ``version``.

        As :meth:`StudyRepository.write_intake`: the existing row is read ``FOR UPDATE`` so
        the version compared is the one replaced and a concurrent save waits; ``precondition``
        (the route's ``If-Match``) is checked against it, false -> ``PreconditionFailedError``
        and nothing written; the ``UPDATE`` carries ``WHERE version = :seen`` as well. With no
        row the write is an insert at version 1, in a SAVEPOINT: of two concurrent first
        saves the second hits the one-per-user constraint and fails the same way (the row is
        now at version 1), leaving the caller's transaction usable.
        """
        locked = (
            await self._session.scalars(
                select(ReviewDraft)
                .where(
                    ReviewDraft.study_id == study_id,
                    ReviewDraft.stage == stage,
                    ReviewDraft.user_id == user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        seen = 0 if locked is None else locked.version
        if precondition is not None and not precondition(seen, locked is not None):
            raise PreconditionFailedError(
                f"the {stage.value} draft of study {study_id} is at version {seen}", version=seen
            )
        if locked is None:
            draft = ReviewDraft(
                study_id=study_id,
                stage=stage,
                revision=revision,
                user_id=user_id,
                document=document,
            )
            try:
                async with self._session.begin_nested():
                    self._session.add(draft)
            except IntegrityError as exc:
                if constraint_name(exc) == _ONE_PER_USER:
                    raise PreconditionFailedError(
                        f"the {stage.value} draft of study {study_id} was created concurrently",
                        version=1,
                    ) from exc
                raise
            await self._session.refresh(draft)
            return DraftWrite(draft=draft, version_before=0)
        stmt = (
            update(ReviewDraft)
            .where(ReviewDraft.id == locked.id, ReviewDraft.version == seen)
            .values(revision=revision, document=document, version=ReviewDraft.version + 1)
            .returning(ReviewDraft)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        written = (await self._session.scalars(stmt)).one_or_none()
        if written is None:  # pragma: no cover - the row lock makes this unreachable
            raise PreconditionFailedError(
                f"the {stage.value} draft of study {study_id} changed while it was written",
                version=seen,
            )
        return DraftWrite(draft=written, version_before=seen)

    async def discard(
        self,
        study_id: uuid.UUID,
        stage: Stage,
        user_id: uuid.UUID,
        *,
        precondition: DraftPrecondition | None = None,
    ) -> ReviewDraft:
        """Delete the user's draft on the stage (``DELETE .../drafts/{stage}``) and return it.

        ``NotVisibleError`` when there is none; ``PreconditionFailedError`` when the route's
        ``If-Match`` does not hold for the row as locked (nothing deleted).
        """
        locked = (
            await self._session.scalars(
                select(ReviewDraft)
                .where(
                    ReviewDraft.study_id == study_id,
                    ReviewDraft.stage == stage,
                    ReviewDraft.user_id == user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if locked is None:
            if precondition is not None and not precondition(0, False):
                raise PreconditionFailedError(
                    f"there is no {stage.value} draft of study {study_id} for this user",
                    version=0,
                )
            raise NotVisibleError(f"no {stage.value} draft of study {study_id} for this user")
        if precondition is not None and not precondition(locked.version, True):
            raise PreconditionFailedError(
                f"the {stage.value} draft of study {study_id} is at version {locked.version}",
                version=locked.version,
            )
        await self._session.execute(
            delete(ReviewDraft)
            .where(ReviewDraft.id == locked.id)
            .execution_options(synchronize_session=False)
        )
        self._session.expunge(locked)
        return locked

    async def discard_for_user(self, study_id: uuid.UUID, stage: Stage, user_id: uuid.UUID) -> bool:
        """Delete the user's draft on the stage, whatever its revision, in the caller's
        transaction (the decision that submits it); False when there was none."""
        result = await self._session.execute(
            delete(ReviewDraft)
            .where(
                ReviewDraft.study_id == study_id,
                ReviewDraft.stage == stage,
                ReviewDraft.user_id == user_id,
            )
            .execution_options(synchronize_session=False)
        )
        return bool(getattr(result, "rowcount", 0))
