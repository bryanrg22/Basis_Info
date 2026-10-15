"""Database probes shared by the failure tests (imported as a top-level module).

Both read ``pg_stat_activity`` through a connection of their own, so they see the
sessions of the code under test (an API, an in-process worker or a worker process).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_OTHER_OPEN = (
    "SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
    "AND state LIKE 'idle in transaction%'"
)


async def writing_transactions(sm: async_sessionmaker[AsyncSession]) -> int:
    """Other sessions idle inside a transaction that has written (it has an xid)."""
    async with sm() as session:
        found = await session.scalar(text(_OTHER_OPEN + " AND backend_xid IS NOT NULL"))
    return int(found or 0)


async def wait_for_writing_transaction(
    sm: async_sessionmaker[AsyncSession], timeout: float = 30.0
) -> None:
    """Wait until some other session is inside a transaction that has written."""
    async with asyncio.timeout(timeout):
        while not await writing_transactions(sm):
            await asyncio.sleep(0.1)


async def no_open_transactions(sm: async_sessionmaker[AsyncSession], timeout: float = 10.0) -> None:
    """Wait until no other session is inside a transaction (the server rolled back the
    transactions of connections that died)."""
    async with asyncio.timeout(timeout):
        while True:
            async with sm() as session:
                if not await session.scalar(text(_OTHER_OPEN)):
                    return
            await asyncio.sleep(0.1)
