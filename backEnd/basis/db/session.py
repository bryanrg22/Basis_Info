"""Sessions and the per-request unit of work (S1_SPEC §3).

One transaction per request: :func:`get_session` yields an ``AsyncSession`` inside
``session.begin()``; the transaction commits when the endpoint returns and rolls back
when it raises. Repositories never commit; they only ``flush``.

Wiring (owned by the API application factory)::

    engine = create_engine(settings)
    app.state.db_sessionmaker = create_sessionmaker(engine)

    @router.post(...)
    async def endpoint(session: SessionDep) -> ...: ...

``SessionDep`` declares the dependency with ``scope="function"``, so the commit runs
after the endpoint returns but **before** the response is sent: a failed commit becomes
an error response instead of a 2xx for data that was never stored.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Final

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

SESSIONMAKER_STATE_ATTR: Final = "db_sessionmaker"


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to ``engine``.

    ``expire_on_commit=False`` keeps loaded attributes readable after commit (an async
    session cannot lazily reload them), so endpoints can serialize returned rows.
    """
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session in one transaction: commit on normal exit, rollback on exception."""
    async with sessionmaker() as session, session.begin():
        yield session


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one transaction per request (see module docstring)."""
    sessionmaker: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, SESSIONMAKER_STATE_ATTR, None
    )
    if sessionmaker is None:
        raise RuntimeError(
            f"app.state.{SESSIONMAKER_STATE_ATTR} is not set; create it at startup with "
            "basis.db.session.create_sessionmaker(engine)"
        )
    async with session_scope(sessionmaker) as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
