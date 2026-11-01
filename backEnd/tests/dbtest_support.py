"""Helpers shared by the database-backed tests (``tests/integration/db`` and ``tests/api``).

Imported as a top-level module: pytest puts ``tests/`` on ``sys.path`` because
``tests/conftest.py`` lives there; the name is unique to avoid collisions with the legacy
``tests`` packages. The session-wide ``postgres`` and ``database_url`` fixtures that use
it are in ``tests/conftest.py``, so every suite shares one container.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import asyncpg
from alembic.config import Config
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basis.config import Settings
from basis.db.enums import OrgRole, Stage, status_for_stage
from basis.db.models import Base, Org, OrgMember, Study, User
from basis.db.repositories import StudyRepository, UserRepository

PG_IMAGE = "pgvector/pgvector:pg16"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str, **attributes: Any) -> Config:
    """Alembic config for ``backEnd/alembic.ini`` pointed at ``database_url``."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logger"] = False
    config.attributes.update(attributes)
    return config


def plain_dsn(database_url: str) -> str:
    """asyncpg DSN (no ``+asyncpg`` driver suffix) for direct admin connections."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


async def execute_admin(database_url: str, *statements: str) -> None:
    connection = await asyncpg.connect(plain_dsn(database_url))
    try:
        for statement in statements:
            await connection.execute(statement)
    finally:
        await connection.close()


async def ensure_own_firm(session: AsyncSession, user: User) -> None:
    """Give a user who belongs to no org a firm of their own, as its owner.

    Firms are invite-only (a new user joins no org), but most tests are about studies,
    not onboarding: their users start as the owner of their own firm, as if its admin had
    invited them, so two test users are in different firms unless a test adds them to one.
    The user's row lock makes concurrent first requests create one firm. The onboarding
    tests (``tests/api/test_firms.py``) build their app without this.
    """
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    if await UserRepository(session).memberships(user.id):
        return
    owner = (user.display_name or "").strip() or user.email or user.firebase_uid
    org = Org(name=f"{owner} (test firm)")
    session.add(org)
    await session.flush()
    session.add(OrgMember(org_id=org.id, user_id=user.id, role=OrgRole.OWNER.value))
    await session.flush()


RACE_HOLD_S = 0.3  # how long each contender keeps its transaction open after its write


async def race[T](
    sm: async_sessionmaker[AsyncSession],
    *actions: Callable[[AsyncSession], Awaitable[T]],
    hold_s: float = RACE_HOLD_S,
) -> list[T | BaseException]:
    """Run each action in its own session (its own pooled connection) and transaction,
    released together by a barrier; the winner keeps its transaction open for ``hold_s``
    so the loser really waits on the lock instead of running after the winner is done. The
    results (or exceptions) come back in the actions' order (S1_SPEC section 7)."""
    from basis.db.session import session_scope

    barrier = asyncio.Barrier(len(actions))

    async def contender(action: Callable[[AsyncSession], Awaitable[T]]) -> T:
        async with session_scope(sm) as session:
            await session.execute(text("SELECT 1"))  # check out a connection first
            await barrier.wait()
            result = await action(session)
            await asyncio.sleep(hold_s)
            return result

    return await asyncio.gather(*(contender(a) for a in actions), return_exceptions=True)


async def backends(session: AsyncSession) -> list[tuple[Any, ...]]:
    """The other backends of this database: state, what they wait on, their last query.

    Read fresh each time: PostgreSQL keeps the first activity snapshot of a transaction for
    the rest of it, and the caller polls from inside the one that holds the lock."""
    await session.execute(text("SELECT pg_stat_clear_snapshot()"))
    rows = await session.execute(
        text(
            "SELECT state, wait_event_type, wait_event, left(query, 60) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
    )
    return [tuple(row) for row in rows.all()]


def waiting_on_a_lock(rows: list[tuple[Any, ...]]) -> int:
    return sum(1 for row in rows if row[1] == "Lock")


async def queued_behind_a_lock[T](
    sm: async_sessionmaker[AsyncSession],
    lock: Any,
    *requests: Callable[[], Awaitable[T]],
    timeout_s: float = 10.0,
) -> list[T]:
    """Make a race deterministic and ordered: hold ``lock`` (a statement run in a
    transaction of its own: a ``SELECT ... FOR UPDATE`` of the contested row, or a ``LOCK
    TABLE``), start each request only once the previous one is seen waiting on it
    (``pg_stat_activity``), then release the lock and gather the answers.

    Every request has therefore read whatever it reads before the lock (an unlocked access
    check, a stat of storage) while the row was still in its initial state, and they take
    the lock in the order given (PostgreSQL grants a row lock to its waiters in queue
    order), so the test chooses who wins. A request that never waits on the lock fails the
    test: the code under test does not take it, which is itself the finding."""
    tasks: list[asyncio.Task[T]] = []
    loop = asyncio.get_running_loop()
    async with sm() as locker, locker.begin():
        await locker.execute(lock)
        for n, request in enumerate(requests, start=1):
            tasks.append(asyncio.create_task(request()))
            deadline = loop.time() + timeout_s
            while waiting_on_a_lock(rows := await backends(locker)) < n:
                if loop.time() >= deadline:
                    for task in tasks:
                        task.cancel()
                    raise AssertionError(f"request {n} never waited on the lock: {rows}")
                await asyncio.sleep(0.02)
    # The commit released the lock: the requests now take it one after another.
    return list(await asyncio.gather(*tasks))


def db_settings(database_url: str, **overrides: Any) -> Settings:
    """Settings for the test database (BASIS_ENV=test comes from tests/conftest.py)."""
    return Settings(_env_file=None, database_url=database_url, **overrides)


async def truncate_all(engine: AsyncEngine) -> None:
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} CASCADE"))


class Factory:
    """Creates committed-or-flushed rows through the real repositories."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def user(self, uid: str | None = None, email: str | None = None) -> User:
        """A user who owns a firm of their own (:func:`ensure_own_firm`)."""
        uid = uid or f"uid-{uuid.uuid4().hex}"
        user = await UserRepository(self.session).upsert_by_firebase_uid(
            uid, email or f"{uid}@example.test", None
        )
        await ensure_own_firm(self.session, user)
        return user

    async def personal_org_id(self, user: User) -> uuid.UUID:
        """The firm :meth:`user` gave the user (formerly their personal org)."""
        memberships = await UserRepository(self.session).memberships(user.id)
        owned = [org_id for org_id, role in memberships.items() if role is OrgRole.OWNER]
        assert len(owned) == 1
        return owned[0]

    async def org(self, name: str = "Acme Engineering") -> Org:
        org = Org(name=name)
        self.session.add(org)
        await self.session.flush()
        return org

    async def member(self, org_id: uuid.UUID, user: User, role: OrgRole) -> None:
        self.session.add(OrgMember(org_id=org_id, user_id=user.id, role=role.value))
        await self.session.flush()

    async def study(
        self,
        owner: User | None = None,
        *,
        org_id: uuid.UUID | None = None,
        stage: Stage = Stage.UPLOADING,
        revision: int = 1,
        property_name: str = "12 Main St",
    ) -> Study:
        owner = owner or await self.user()
        org_id = org_id or await self.personal_org_id(owner)
        study = await StudyRepository(self.session).create(org_id, owner.id, property_name)
        if stage is not Stage.UPLOADING or revision != 1:
            await self.session.execute(
                update(Study)
                .where(Study.id == study.id)
                .values(current_stage=stage, status=status_for_stage(stage), revision=revision)
            )
            await self.session.refresh(study)
        return study
