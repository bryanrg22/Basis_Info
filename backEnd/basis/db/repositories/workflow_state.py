"""The worker's writes to the study row: converging stage transitions and stalls.

The API and the workflow both move a study. The decision endpoint moves it first
(``ReviewRepository.record_decision``) and the workflow follows when the decision is
delivered, so the workflow's transition must accept that the API already got there.
:meth:`WorkflowStateRepository.converge` is that rule; it is a thin layer over
``StudyRepository.transition`` (the compare-and-set) and adds nothing else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import Stage, StudyStatus, status_for_stage
from basis.db.errors import NotVisibleError, StaleStateError
from basis.db.models import Study
from basis.db.repositories.study import StudyRepository


@dataclass(frozen=True, slots=True)
class ConvergeResult:
    study: Study
    changed: bool


class WorkflowStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def converge(
        self,
        study_id: uuid.UUID,
        from_stage: Stage,
        from_revision: int,
        to_stage: Stage,
        to_revision: int,
    ) -> ConvergeResult:
        """Bring the study to ``(to_stage, to_revision)``.

        * Already there: no change, except that a ``stalled`` status is cleared back to the
          stage's status (a late decision un-stalls the study).
        * At ``(from_stage, from_revision)``: compare-and-set transition.
        * Anywhere else, or archived: ``StaleStateError``; missing: ``NotVisibleError``.

        The row is locked first, so a concurrent API decision on the same study waits.
        """
        study = (
            await self._session.scalars(
                select(Study)
                .where(Study.id == study_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if study is None:
            raise NotVisibleError(f"study {study_id} not found")
        if study.status is StudyStatus.ARCHIVED:
            raise StaleStateError(f"study {study_id} is archived")
        if study.current_stage is to_stage and study.revision == to_revision:
            expected = status_for_stage(to_stage)
            if study.status is not expected:
                await self._session.execute(
                    update(Study)
                    .where(Study.id == study_id)
                    .values(status=expected)
                    .execution_options(synchronize_session=False)
                )
                await self._session.refresh(study)
                return ConvergeResult(study=study, changed=True)
            return ConvergeResult(study=study, changed=False)
        moved = await StudyRepository(self._session).transition(
            study_id,
            from_stage,
            to_stage,
            from_revision,
            new_revision=to_revision if to_revision != from_revision else None,
        )
        return ConvergeResult(study=moved, changed=True)

    async def mark_stalled(self, study_id: uuid.UUID, stage: Stage, revision: int) -> bool:
        """Set ``status = stalled`` while the study still waits at ``stage``/``revision``.

        False (no change) when the study has moved on, so a late timer cannot stall a
        study that was decided in the meantime.
        """
        result = await self._session.execute(
            update(Study)
            .where(
                Study.id == study_id,
                Study.current_stage == stage,
                Study.revision == revision,
                Study.status == StudyStatus.WAITING_REVIEW,
            )
            .values(status=StudyStatus.STALLED)
            .execution_options(synchronize_session=False)
        )
        return bool(getattr(result, "rowcount", 0))
