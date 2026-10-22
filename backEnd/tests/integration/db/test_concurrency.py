"""Races between concurrent transactions on separate connections (S1_SPEC §7).

Each contender runs in its own session (its own pooled connection) and transaction. A
barrier makes them issue the contested statement at the same time, and the winner keeps
its transaction open for a moment so the loser really waits on the lock instead of
running after the winner has finished. The outcome is checked in a fresh session.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from dbtest_support import Factory
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import Forbidden
from basis.api.routes.orgs import lock_as_owner
from basis.api.users import CurrentUser
from basis.auth.firebase import Principal
from basis.db.enums import Decision, OrgRole, Stage, StudyStatus
from basis.db.errors import StaleStateError, constraint_name
from basis.db.models import Org, OrgMember, OutboxMessage, Review, Study, User
from basis.db.repositories import (
    IdempotencyRepository,
    OrgRepository,
    OutboxRepository,
    ReviewRepository,
    StudyRepository,
    UserRepository,
)
from basis.db.session import session_scope

pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]

HOLD_S = 0.3  # how long each contender keeps its transaction open after its write


async def race[T](
    sm: async_sessionmaker[AsyncSession],
    *actions: Callable[[AsyncSession], Awaitable[T]],
) -> list[T | BaseException]:
    """Run each action in its own transaction, released together by a barrier."""
    barrier = asyncio.Barrier(len(actions))

    async def contender(action: Callable[[AsyncSession], Awaitable[T]]) -> T:
        async with session_scope(sm) as session:
            await session.execute(text("SELECT 1"))  # check out a connection first
            await barrier.wait()
            result = await action(session)
            await asyncio.sleep(HOLD_S)
            return result

    return await asyncio.gather(*(contender(a) for a in actions), return_exceptions=True)


def split(results: list[Any]) -> tuple[list[Any], list[BaseException]]:
    ok = [r for r in results if not isinstance(r, BaseException)]
    errors = [r for r in results if isinstance(r, BaseException)]
    return ok, errors


async def setup_study(sm: async_sessionmaker[AsyncSession]) -> tuple[Study, User]:
    async with session_scope(sm) as session:
        make = Factory(session)
        study = await make.study(stage=Stage.APPRAISAL_REVIEW, revision=1)
        second = await make.user()
        await make.member(study.org_id, second, OrgRole.ENGINEER)
        return study, second


async def test_concurrent_transitions_from_same_stage_exactly_one_wins(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    study, _ = await setup_study(committed)

    def move(to_stage: Stage, new_revision: int) -> Callable[[AsyncSession], Awaitable[Study]]:
        async def action(session: AsyncSession) -> Study:
            return await StudyRepository(session).transition(
                study.id, Stage.APPRAISAL_REVIEW, to_stage, 1, new_revision=new_revision
            )

        return action

    results = await race(committed, move(Stage.ROOMS_REVIEW, 1), move(Stage.EXTRACTING, 2))
    winners, errors = split(results)
    assert len(winners) == 1 and len(errors) == 1
    assert isinstance(errors[0], StaleStateError)
    async with committed() as session:
        stored = await session.get(Study, study.id)
    assert stored is not None
    assert (stored.current_stage, stored.revision) == (
        winners[0].current_stage,
        winners[0].revision,
    )


async def test_two_reviewers_approving_same_revision_record_one_review(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    study, second = await setup_study(committed)

    def approve(reviewer: uuid.UUID, key: str) -> Callable[[AsyncSession], Awaitable[Any]]:
        async def action(session: AsyncSession) -> Any:
            return await ReviewRepository(session).record_decision(
                study_id=study.id,
                stage=Stage.APPRAISAL_REVIEW,
                revision=1,
                decision=Decision.APPROVE,
                reviewer_id=reviewer,
                idempotency_key=key,
            )

        return action

    results = await race(
        committed, approve(study.created_by, "first"), approve(second.id, "second")
    )
    winners, errors = split(results)
    assert len(winners) == 1 and len(errors) == 1
    assert isinstance(errors[0], StaleStateError)
    async with committed() as session:
        assert await session.scalar(select(func.count()).select_from(Review)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 1
        stored = await session.get(Study, study.id)
        assert stored is not None and stored.current_stage is Stage.ROOMS_REVIEW


async def test_unique_review_constraint_holds_without_the_transition(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    """The DB backstop: raw concurrent inserts for one stage/revision -> one row."""
    study, second = await setup_study(committed)

    def insert(reviewer: uuid.UUID, key: str) -> Callable[[AsyncSession], Awaitable[None]]:
        async def action(session: AsyncSession) -> None:
            session.add(
                Review(
                    study_id=study.id,
                    stage=Stage.APPRAISAL_REVIEW,
                    revision=1,
                    decision=Decision.APPROVE,
                    reviewer_id=reviewer,
                    idempotency_key=key,
                )
            )
            await session.flush()

        return action

    results = await race(committed, insert(study.created_by, "a"), insert(second.id, "b"))
    _, errors = split(results)
    assert len(errors) == 1
    assert isinstance(errors[0], IntegrityError)
    assert constraint_name(errors[0]) == "uq_reviews_study_id_stage_revision"


async def test_concurrent_first_logins_create_one_user_and_no_org(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    async def login(session: AsyncSession) -> User:
        return await UserRepository(session).upsert_by_firebase_uid(
            "same-uid", "a@example.test", "Ada"
        )

    results = await race(committed, login, login, login)
    users, errors = split(results)
    assert errors == []
    assert len({user.id for user in users}) == 1
    async with committed() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        # Firms are invite-only: signing in creates no org and no membership.
        assert await session.scalar(select(func.count()).select_from(Org)) == 0
        assert await session.scalar(select(func.count()).select_from(OrgMember)) == 0


async def test_two_owners_demoting_each_other_at_once_leave_one_owner(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    """The route's sequence (lock the firm, re-check the caller is an owner, change the
    role) in two transactions released together: the second waits for the firm's lock,
    then finds its caller demoted and is refused. Without the lock both would pass their
    checks on the same snapshot and leave the firm with no owner."""
    async with session_scope(committed) as session:
        make = Factory(session)
        org = await make.org("Two owners")
        first, second = await make.user(), await make.user()
        for user in (first, second):
            await make.member(org.id, user, OrgRole.OWNER)

    def demote(caller: User, target: User) -> Callable[[AsyncSession], Awaitable[OrgRole]]:
        async def action(session: AsyncSession) -> OrgRole:
            principal = Principal(uid=caller.firebase_uid)
            await lock_as_owner(session, CurrentUser(user=caller, principal=principal), org.id)
            member = await OrgRepository(session).set_role(org.id, target.id, OrgRole.ENGINEER)
            return member.role

        return action

    results = await race(committed, demote(first, second), demote(second, first))
    done, errors = split(results)
    assert done == [OrgRole.ENGINEER]
    assert len(errors) == 1 and isinstance(errors[0], Forbidden)
    async with committed() as session:
        owners = await session.scalar(
            select(func.count()).where(OrgMember.org_id == org.id, OrgMember.role == "owner")
        )
    assert owners == 1


async def test_concurrent_idempotency_claims_have_one_owner(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(committed) as session:
        user = await Factory(session).user()

    async def claim(session: AsyncSession) -> bool:
        result = await IdempotencyRepository(session).claim(
            user.id, "same-key", "POST /v1/studies", "a" * 64
        )
        return result.created

    results = await race(committed, claim, claim)
    assert sorted(results, key=str) == [False, True]


async def test_outbox_relays_never_claim_the_same_message(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(committed) as session:
        for n in range(6):
            await OutboxRepository(session).enqueue("workflow_signal", {"n": n})

    async def relay(session: AsyncSession) -> set[uuid.UUID]:
        return {m.id for m in await OutboxRepository(session).claim_due(limit=4)}

    first, second = await race(committed, relay, relay)
    assert isinstance(first, set) and isinstance(second, set)
    assert first.isdisjoint(second)
    assert len(first | second) == 6


async def test_archived_study_cannot_transition(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    study, _ = await setup_study(committed)
    async with session_scope(committed) as session:
        archived = await StudyRepository(session).archive(study.id)
        assert archived.status is StudyStatus.ARCHIVED
    with pytest.raises(StaleStateError, match="archived"):
        async with session_scope(committed) as session:
            await StudyRepository(session).transition(
                study.id, Stage.APPRAISAL_REVIEW, Stage.ROOMS_REVIEW, 1
            )
