"""``demo_requests`` on PostgreSQL (migration 0035, docs/api/DEMO_REQUESTS.md): the repository's
duplicate window (the same lower-cased email, by the database clock, the newest earlier
request returned), the listing (newest first, the keyset cursor, the page bounds), the
relay's two notification writes (``record_notify_error`` never overwrites a sent request),
and the retention purge (only rows received before the cutoff, in batches; a dry run counts
and changes nothing; the CLI's summary line and arguments; nothing in ``outbox`` names a
purged request's details).
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.models import DemoRequest, OutboxMessage
from basis.db.repositories.demo_request import DemoRequestRepository, NewDemoRequest
from basis.db.session import session_scope
from basis.retention import __main__ as cli
from basis.retention.demo_requests import purge_demo_requests

pytestmark = pytest.mark.integration


def new(**overrides: Any) -> NewDemoRequest:
    values: dict[str, Any] = {
        "name": "Dana",
        "email": "dana@acme.example",
        "company": "Acme",
        "role": "engineer",
        **overrides,
    }
    return NewDemoRequest(**values)


async def record(sm: async_sessionmaker[AsyncSession], **overrides: Any) -> uuid.UUID:
    async with session_scope(sm) as session:
        return (await DemoRequestRepository(session).record(new(**overrides))).request.id


async def age(
    sm: async_sessionmaker[AsyncSession], demo_request_id: uuid.UUID, by: timedelta
) -> None:
    async with session_scope(sm) as session:
        await session.execute(
            update(DemoRequest)
            .where(DemoRequest.id == demo_request_id)
            .values(created_at=DemoRequest.created_at - by)
        )


async def count(sm: async_sessionmaker[AsyncSession]) -> int:
    async with sm() as session:
        return int(await session.scalar(select(func.count()).select_from(DemoRequest)) or 0)


async def test_the_window_is_per_email_and_returns_the_newest_earlier_request(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    first = await record(committed)
    await age(committed, first, timedelta(minutes=30))
    second = await record(committed, note="later")  # the first is out of the window
    assert second != first
    async with session_scope(committed) as session:
        repo = DemoRequestRepository(session)
        again = await repo.record(new())
        assert (again.created, again.request.id) == (False, second)
        # The email is compared as stored: the API lower-cases it before it gets here.
        other = await repo.record(new(email="sam@acme.example"))
        assert other.created
        # A shorter window (the parameter) makes the same email new again.
        await session.execute(
            update(DemoRequest)
            .where(DemoRequest.id == second)
            .values(created_at=func.now() - timedelta(minutes=2))
        )
        assert (await repo.record(new(), window=timedelta(minutes=1))).created
    assert await count(committed) == 4


async def test_listing_is_newest_first_with_a_cursor(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    ids = [await record(committed, email=f"p{n}@x.example") for n in range(5)]
    async with committed() as session:
        repo = DemoRequestRepository(session)
        page = await repo.list(limit=2)
        assert [r.id for r in page.items] == [ids[4], ids[3]]
        assert page.next_seq == page.items[-1].seq
        rest = await repo.list(before_seq=page.next_seq, limit=10)
        assert [r.id for r in rest.items] == [ids[2], ids[1], ids[0]]
        assert rest.next_seq is None
        for limit in (0, 201):
            with pytest.raises(ValueError):
                await repo.list(limit=limit)


async def test_the_notification_columns_are_the_relays(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    demo_request_id = await record(committed)
    async with session_scope(committed) as session:
        repo = DemoRequestRepository(session)
        await repo.record_notify_error(demo_request_id, "x" * 3000)
    async with committed() as session:
        row = await session.get(DemoRequest, demo_request_id)
        assert row is not None and len(row.notify_error or "") == 2000
    async with session_scope(committed) as session:
        repo = DemoRequestRepository(session)
        await repo.record_notified(demo_request_id)
        # A late error (a redelivered row's) never marks a sent request as failed.
        await repo.record_notify_error(demo_request_id, "late")
    async with committed() as session:
        row = await session.get(DemoRequest, demo_request_id)
        assert row is not None and row.notified_at is not None and row.notify_error is None


async def test_the_purge_deletes_only_requests_older_than_the_cutoff_in_batches(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    old = [await record(committed, email=f"old{n}@x.example") for n in range(5)]
    for demo_request_id in old:
        await age(committed, demo_request_id, timedelta(days=400))
    fresh = await record(committed, email="fresh@x.example")
    cutoff = datetime.now(UTC) - timedelta(days=365)
    async with session_scope(committed) as session:
        assert await purge_demo_requests(session, cutoff=cutoff, dry_run=True) == 5
    assert await count(committed) == 6
    async with session_scope(committed) as session:
        assert await purge_demo_requests(session, cutoff=cutoff, batch=2) == 5
    async with committed() as session:
        assert list(await session.scalars(select(DemoRequest.id))) == [fresh]
        # The outbox rows that asked for the emails carry the id and nothing else.
        payloads = [m.payload for m in await session.scalars(select(OutboxMessage))]
    assert len(payloads) == 6
    assert all(set(p) == {"type", "demo_request_id"} for p in payloads)


async def test_the_cli_prints_a_summary_line(committed: async_sessionmaker[AsyncSession]) -> None:
    await record(committed)
    out = io.StringIO()
    later = datetime.now(UTC) + timedelta(days=2)
    assert (
        await cli.run_demo_request_purge(committed, days=1, dry_run=True, out=out, now=later) == 0
    )
    summary = json.loads(out.getvalue())
    assert summary["summary"] is True and summary["dry_run"] is True
    assert summary["would_delete"] == 1 and summary["retention_days"] == 1
    out = io.StringIO()
    await cli.run_demo_request_purge(committed, days=1, dry_run=False, out=out, now=later)
    assert json.loads(out.getvalue())["deleted"] == 1
    assert await count(committed) == 0
    args = cli.build_parser().parse_args(["purge-demo-requests", "--dry-run", "--days", "30"])
    assert (args.command, args.dry_run, args.days) == ("purge-demo-requests", True, 30)
    assert cli.build_parser().parse_args(["purge-demo-requests"]).days is None
    assert cli.main(["purge-demo-requests", "--days", "0"]) == 2
