"""Atomicity of decision recording and of the per-request unit of work (S1_SPEC §3, §7).

These tests use real commits (the ``committed`` sessionmaker) and read the result back
through a *new* session, so "nothing was written" is checked against what PostgreSQL
actually stored, not against the identity map.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from dbtest_support import Factory
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import Decision, Stage, StudyStatus
from basis.db.errors import StaleStateError
from basis.db.models import Correction, Org, OutboxMessage, Review, Study
from basis.db.repositories import (
    CorrectionInput,
    OutboxRepository,
    ReviewRepository,
    StudyRepository,
)
from basis.db.repositories.outbox import WORKFLOW_SIGNAL
from basis.db.session import SessionDep, session_scope

pytestmark = pytest.mark.integration


async def count(sm: async_sessionmaker[AsyncSession], model: Any) -> int:
    async with sm() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def load_study(sm: async_sessionmaker[AsyncSession], study_id: uuid.UUID) -> Study:
    async with sm() as session:
        study = await session.get(Study, study_id)
        assert study is not None
        return study


async def study_at_review(sm: async_sessionmaker[AsyncSession]) -> Study:
    async with session_scope(sm) as session:
        return await Factory(session).study(stage=Stage.APPRAISAL_REVIEW, revision=1)


async def test_decision_commits_review_transition_and_signal_together(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    study = await study_at_review(committed)
    entity = uuid.uuid4()
    async with session_scope(committed) as session:
        record = await ReviewRepository(session).record_decision(
            study_id=study.id,
            stage=Stage.APPRAISAL_REVIEW,
            revision=1,
            decision=Decision.APPROVE,
            reviewer_id=study.created_by,
            idempotency_key="k-approve",
            comment="looks right",
            corrections=[CorrectionInput("extraction_field", entity, {"v": 2}, {"v": 1}, "typo")],
        )

    stored = await load_study(committed, study.id)
    assert (stored.current_stage, stored.status, stored.revision) == (
        Stage.ROOMS_REVIEW,
        StudyStatus.WAITING_REVIEW,
        1,
    )
    async with committed() as session:
        review = (await session.scalars(select(Review))).one()
        assert review.id == record.review.id
        assert (review.stage, review.revision, review.decision) == (
            Stage.APPRAISAL_REVIEW,
            1,
            Decision.APPROVE,
        )
        assert review.idempotency_key == f"{study.created_by}:k-approve"
        signal = (await session.scalars(select(OutboxMessage))).one()
        assert signal.kind == WORKFLOW_SIGNAL
        assert signal.payload == {
            "study_id": str(study.id),
            "workflow_id": None,
            "review_id": str(review.id),
            "stage": "appraisal_review",
            "revision": 1,
            "decision": "approve",
            "to_stage": "rooms_review",
            "new_revision": 1,
        }
        assert signal.delivered_at is None and signal.attempts == 0
        correction = (await session.scalars(select(Correction))).one()
        assert (correction.entity_id, correction.before, correction.after) == (
            entity,
            {"v": 1},
            {"v": 2},
        )


async def test_reject_returns_to_producing_stage_with_new_revision(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    study = await study_at_review(committed)
    async with session_scope(committed) as session:
        await ReviewRepository(session).record_decision(
            study_id=study.id,
            stage=Stage.APPRAISAL_REVIEW,
            revision=1,
            decision=Decision.REJECT,
            reviewer_id=study.created_by,
            idempotency_key="k-reject",
        )
    stored = await load_study(committed, study.id)
    assert (stored.current_stage, stored.status, stored.revision) == (
        Stage.EXTRACTING,
        StudyStatus.PROCESSING,
        2,
    )


async def assert_nothing_recorded(sm: async_sessionmaker[AsyncSession], before: Study) -> None:
    after = await load_study(sm, before.id)
    assert (after.current_stage, after.status, after.revision, after.updated_at) == (
        before.current_stage,
        before.status,
        before.revision,
        before.updated_at,
    )
    assert await count(sm, Review) == 0
    assert await count(sm, Correction) == 0
    assert await count(sm, OutboxMessage) == 0


async def test_failure_after_review_insert_leaves_no_rows(
    committed: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    study = await study_at_review(committed)
    reached: list[str] = []

    async def failing_enqueue(self: OutboxRepository, kind: str, payload: dict[str, Any]) -> None:
        # Prove the review row really was written in this transaction before failing.
        reached.append(str(await self._session.scalar(select(func.count()).select_from(Review))))
        raise RuntimeError("signal store unavailable")

    monkeypatch.setattr(OutboxRepository, "enqueue", failing_enqueue)
    with pytest.raises(RuntimeError, match="signal store unavailable"):
        async with session_scope(committed) as session:
            await ReviewRepository(session).record_decision(
                study_id=study.id,
                stage=Stage.APPRAISAL_REVIEW,
                revision=1,
                decision=Decision.APPROVE,
                reviewer_id=study.created_by,
                idempotency_key="k-fail",
                corrections=[CorrectionInput("asset", uuid.uuid4(), {"bucket": "y5"})],
            )
    assert reached == ["1"]
    await assert_nothing_recorded(committed, study)


async def test_database_error_in_corrections_rolls_back_review_and_transition(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    study = await study_at_review(committed)
    with pytest.raises(IntegrityError, match="not-null"):
        async with session_scope(committed) as session:
            await ReviewRepository(session).record_decision(
                study_id=study.id,
                stage=Stage.APPRAISAL_REVIEW,
                revision=1,
                decision=Decision.APPROVE,
                reviewer_id=study.created_by,
                idempotency_key="k-bad-correction",
                corrections=[CorrectionInput("asset", uuid.uuid4(), after=None)],
            )
    await assert_nothing_recorded(committed, study)


async def test_stale_decision_writes_nothing(committed: async_sessionmaker[AsyncSession]) -> None:
    study = await study_at_review(committed)
    with pytest.raises(StaleStateError):
        async with session_scope(committed) as session:
            await ReviewRepository(session).record_decision(
                study_id=study.id,
                stage=Stage.APPRAISAL_REVIEW,
                revision=2,  # the study is at revision 1
                decision=Decision.APPROVE,
                reviewer_id=study.created_by,
                idempotency_key="k-stale",
            )
    await assert_nothing_recorded(committed, study)


async def test_repositories_do_not_commit(committed: async_sessionmaker[AsyncSession]) -> None:
    async with committed() as session:
        await Factory(session).study()
        await session.rollback()
    assert await count(committed, Study) == 0
    assert await count(committed, Org) == 0


def session_app(sm: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = FastAPI()
    app.state.db_sessionmaker = sm

    @app.post("/orgs/{name}")
    async def create_org(name: str, session: SessionDep, fail: bool = False) -> dict[str, str]:
        session.add(Org(name=name))
        await session.flush()
        if fail:
            raise RuntimeError("handler failed after writing")
        return {"name": name}

    return app


async def test_get_session_commits_on_success_and_rolls_back_on_error(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    transport = httpx.ASGITransport(app=session_app(committed), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.post("/orgs/kept")
        failed = await client.post("/orgs/dropped", params={"fail": "true"})
    assert ok.status_code == 200
    assert failed.status_code == 500
    async with committed() as session:
        names = set((await session.scalars(select(Org.name))).all())
    assert names == {"kept"}


async def test_transition_status_follows_stage(committed: async_sessionmaker[AsyncSession]) -> None:
    study = await study_at_review(committed)
    async with session_scope(committed) as session:
        moved = await StudyRepository(session).transition(
            study.id, Stage.APPRAISAL_REVIEW, Stage.COMPLETED, 1
        )
    assert moved.status is StudyStatus.COMPLETED
    assert (await load_study(committed, study.id)).updated_at > study.updated_at
