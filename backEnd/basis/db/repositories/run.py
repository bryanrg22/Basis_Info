"""Workflow runs (``runs``): one row per Temporal execution ``(workflow_id, run_id)``."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from basis.db.models import Run


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(
        self,
        *,
        study_id: uuid.UUID,
        workflow_id: str,
        run_id: str,
        workflow_version: str,
        input_revision: int,
    ) -> Run:
        """Insert the run, or return the existing one (a retried ``record_run_start``)."""
        await self._session.execute(
            pg_insert(Run)
            .values(
                study_id=study_id,
                workflow_id=workflow_id,
                run_id=run_id,
                workflow_version=workflow_version,
                input_revision=input_revision,
            )
            .on_conflict_do_nothing(index_elements=[Run.workflow_id, Run.run_id])
        )
        run = await self.get_by_execution(workflow_id, run_id)
        if run is None:  # pragma: no cover - inserted or already present
            raise RuntimeError(f"run {workflow_id}/{run_id} vanished after insert")
        return run

    async def get_by_execution(self, workflow_id: str, run_id: str) -> Run | None:
        stmt = (
            select(Run)
            .where(Run.workflow_id == workflow_id, Run.run_id == run_id)
            .execution_options(populate_existing=True)
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def end(self, workflow_id: str, run_id: str, outcome: str) -> bool:
        """Set ``ended_at`` and ``outcome`` once; False when already ended or unknown."""
        result = await self._session.execute(
            update(Run)
            .where(Run.workflow_id == workflow_id, Run.run_id == run_id, Run.ended_at.is_(None))
            .values(ended_at=func.now(), outcome=outcome)
            .execution_options(synchronize_session=False)
        )
        return bool(getattr(result, "rowcount", 0))

    async def list_for_study(self, study_id: uuid.UUID) -> list[Run]:
        stmt = select(Run).where(Run.study_id == study_id).order_by(Run.started_at, Run.id)
        return list((await self._session.scalars(stmt)).all())
