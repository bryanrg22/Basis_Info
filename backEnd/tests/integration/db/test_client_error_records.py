"""``client_errors`` on PostgreSQL (migration ``0028_client_errors``, docs/api/CLIENT_ERRORS.md):
the repository's insert (once per ``event_id``), listing (newest first, every filter, keyset
cursor), the never-updated rule, the retention purge (older than the cutoff, in batches, dry
run counts and changes nothing) and the CLI's summary line."""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.models import ClientError, ClientErrorIssue
from basis.db.repositories import (
    ClientErrorFilters,
    ClientErrorRepository,
    NewClientError,
    UserRepository,
)
from basis.db.repositories.client_error import fingerprint, normalize_message, parse_issue_cursor
from basis.db.session import session_scope
from basis.retention import __main__ as cli
from basis.retention.client_errors import cutoff_for, purge_client_errors

pytestmark = pytest.mark.integration


async def a_user(committed: async_sessionmaker[AsyncSession], uid: str) -> uuid.UUID:
    async with session_scope(committed) as session:
        user = await UserRepository(session).upsert_by_firebase_uid(uid, f"{uid}@x.test", uid)
        return user.id


def new(user_id: uuid.UUID, **overrides: object) -> NewClientError:
    values = {
        "event_id": uuid.uuid4(),
        "occurred_at": datetime.now(UTC),
        "user_id": user_id,
        "kind": "error",
        "message": "boom",
        "event": {"message": "boom"},
    }
    values.update(overrides)
    return NewClientError(**values)  # type: ignore[arg-type]


async def test_record_stores_once_per_event_id_and_returns_the_row(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    user = await a_user(committed, "u1")
    first = new(user, code="BASIS-API-024", http_status=500, request_id="r1")
    async with session_scope(committed) as session:
        repo = ClientErrorRepository(session)
        row = await repo.record(first)
        assert row is not None
        assert row.seq >= 1 and row.created_at is not None
        assert (row.code, row.http_status, row.request_id) == ("BASIS-API-024", 500, "r1")
        again = await repo.record(new(user, event_id=first.event_id, message="other"))
        assert again is None
        other = await repo.record(new(user))
        assert other is not None and other.seq > row.seq
    async with committed() as session:
        assert await session.scalar(select(func.count()).select_from(ClientError)) == 2
        stored = await session.get(ClientError, row.id)
        assert stored is not None and stored.message == "boom"


async def test_rows_are_never_updated(committed: async_sessionmaker[AsyncSession]) -> None:
    user = await a_user(committed, "u1")
    async with session_scope(committed) as session:
        row = await ClientErrorRepository(session).record(new(user))
        assert row is not None
    async with committed() as session, session.begin():
        with pytest.raises(IntegrityError, match="append-only"):
            await session.execute(
                update(ClientError).where(ClientError.id == row.id).values(message="edited")
            )


async def test_kind_and_status_are_checked(committed: async_sessionmaker[AsyncSession]) -> None:
    user = await a_user(committed, "u1")
    for bad in ({"kind": "panic"}, {"http_status": 1000}, {"http_status": -1}):
        async with committed() as session, session.begin():
            with pytest.raises(IntegrityError):
                await ClientErrorRepository(session).record(new(user, **bad))


async def test_list_is_newest_first_filtered_and_paged(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    alice = await a_user(committed, "alice")
    bob = await a_user(committed, "bob")
    org = uuid.uuid4()
    async with session_scope(committed) as session:
        await session.execute(text("INSERT INTO orgs (id, name) VALUES (:id, 'Acme')"), {"id": org})
        await session.execute(
            text(
                "INSERT INTO studies (id, org_id, created_by, property_name) "
                "VALUES (:id, :org, :by, 'P')"
            ),
            {"id": (study := uuid.uuid4()), "org": org, "by": alice},
        )
    async with session_scope(committed) as session:
        repo = ClientErrorRepository(session)
        a = await repo.record(new(alice, org_id=org, kind="error"))
        b = await repo.record(
            new(
                alice,
                org_id=org,
                study_id=study,
                kind="api",
                code="BASIS-STUDY-003",
                request_id="r9",
            )
        )
        c = await repo.record(new(bob, kind="console"))
        assert a and b and c
    async with committed() as session:
        repo = ClientErrorRepository(session)

        async def ids(**filters: object) -> list[uuid.UUID]:
            page = await repo.list(filters=ClientErrorFilters(**filters))  # type: ignore[arg-type]
            return [r.record.id for r in page.items]

        assert await ids() == [c.id, b.id, a.id]
        assert await ids(fingerprint=a.fingerprint) == [a.id]
        assert await ids(status="open") == [c.id, b.id, a.id]
        assert await ids(status="resolved") == []
        assert await ids(org_id=org) == [b.id, a.id]
        assert await ids(user_id=bob) == [c.id]
        assert await ids(study_id=study) == [b.id]
        assert await ids(code="BASIS-STUDY-003") == [b.id]
        assert await ids(kind="console") == [c.id]
        assert await ids(request_id="r9") == [b.id]
        assert await ids(since=datetime.now(UTC) + timedelta(minutes=1)) == []
        assert await ids(until=datetime.now(UTC) - timedelta(minutes=1)) == []
        assert await ids(
            since=datetime.now(UTC) - timedelta(minutes=1),
            until=datetime.now(UTC) + timedelta(minutes=1),
        ) == [c.id, b.id, a.id]
        first = await repo.list(limit=2)
        assert [r.record.id for r in first.items] == [c.id, b.id] and first.next_seq == b.seq
        second = await repo.list(limit=2, before_seq=first.next_seq)
        assert [r.record.id for r in second.items] == [a.id] and second.next_seq is None
        with pytest.raises(ValueError):
            await repo.list(limit=0)


async def test_the_purge_deletes_only_records_older_than_the_cutoff_in_batches(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    user = await a_user(committed, "u1")
    async with session_scope(committed) as session:
        repo = ClientErrorRepository(session)
        old = [await repo.record(new(user)) for _ in range(5)]
        fresh = await repo.record(new(user))
        assert fresh is not None
        # created_at is the database clock and the trigger forbids updates, so the old rows
        # are aged by moving the cutoff, not the rows.
    cutoff = datetime.now(UTC) + timedelta(seconds=1)
    async with session_scope(committed) as session:
        assert await purge_client_errors(session, cutoff=cutoff, dry_run=True) == 6
    async with committed() as session:
        assert await session.scalar(select(func.count()).select_from(ClientError)) == 6
    # A cutoff before every row: nothing goes.
    async with session_scope(committed) as session:
        assert await purge_client_errors(session, cutoff=datetime.now(UTC) - timedelta(days=1)) == 0
    async with session_scope(committed) as session:
        assert await purge_client_errors(session, cutoff=cutoff, batch=2) == 6
    async with committed() as session:
        assert await session.scalar(select(func.count()).select_from(ClientError)) == 0
    assert all(r is not None for r in old)


def test_cutoff_for_counts_days_back() -> None:
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    assert cutoff_for(180, now) == datetime(2026, 5, 5, 12, tzinfo=UTC)
    with pytest.raises(ValueError):
        cutoff_for(0, now)


async def test_the_cli_prints_a_summary_line(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    user = await a_user(committed, "u1")
    async with session_scope(committed) as session:
        await ClientErrorRepository(session).record(new(user))
    out = io.StringIO()
    code = await cli.run_client_error_purge(
        committed, days=1, dry_run=True, out=out, now=datetime.now(UTC) + timedelta(days=2)
    )
    assert code == 0
    summary = json.loads(out.getvalue())
    assert summary["summary"] is True and summary["dry_run"] is True
    assert summary["would_delete"] == 1 and summary["retention_days"] == 1
    out = io.StringIO()
    await cli.run_client_error_purge(
        committed, days=1, dry_run=False, out=out, now=datetime.now(UTC) + timedelta(days=2)
    )
    assert json.loads(out.getvalue())["deleted"] == 1
    args = cli.build_parser().parse_args(["purge-client-errors", "--dry-run", "--days", "30"])
    assert (args.command, args.dry_run, args.days) == ("purge-client-errors", True, 30)
    assert cli.build_parser().parse_args(["purge-client-errors"]).days is None


def test_fingerprint_names_the_bug_not_the_occurrence() -> None:
    base = dict(kind="error", error_type="TypeError", route=None, http_status=None)
    a = fingerprint(
        message="Cannot read 'rooms' of 3f2c1a2b-1234-4bcd-8ef0-123456789abc at row 7",
        source={"file": "http://h/live/live-app.js?v=live-16", "function": "render", "line": 120},
        **base,
    )
    b = fingerprint(
        message="Cannot read 'rooms' of 5f0c6a52-1234-4bcd-8ef0-123456789abc at row 12",
        source={"file": "http://h/live/live-app.js?v=live-17", "function": "render", "line": 131},
        **base,
    )
    assert a == b and len(a) == 16 and int(a, 16) >= 0
    assert fingerprint(message="Cannot read 'rooms'", source=None, **base) != a
    assert (
        fingerprint(
            message="Cannot read 'rooms' of x at row 7",
            source={"file": "http://h/live/other.js", "function": "render"},
            **base,
        )
        != a
    )
    assert fingerprint(
        message="x",
        source=None,
        kind="api",
        error_type="ApiError",
        route="/v1/studies/{id}/start",
        http_status=422,
    ) != fingerprint(
        message="x",
        source=None,
        kind="api",
        error_type="ApiError",
        route="/v1/studies/{id}/start",
        http_status=409,
    )
    assert (
        normalize_message("Failed 409 at 3f2c1a2b-1234-4bcd-8ef0-123456789abc  twice")
        == "Failed # at {id} twice"
    )
    assert parse_issue_cursor("garbage") is None
    assert parse_issue_cursor("2026-11-01T00:00:00|0123456789abcdef") is None  # naive
    assert parse_issue_cursor("2026-11-01T00:00:00+00:00|0123456789abcdef") is not None


async def test_issues_count_occurrences_and_regress_only_on_another_build(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    user = await a_user(committed, "u1")
    bug = dict(message="boom at 1", event={"source": {"file": "x.js", "function": "f"}})
    async with session_scope(committed) as session:
        repo = ClientErrorRepository(session)
        first = await repo.record(new(user, build_sha="b1", **bug))
        assert first is not None
        issue = await repo.get_issue(first.fingerprint)
        assert issue is not None
        assert (issue.status, issue.occurrences, issue.last_build_sha) == ("open", 1, "b1")
        assert issue.first_seen_at == issue.last_seen_at == first.occurred_at
        later = datetime.now(UTC) + timedelta(seconds=5)
        second = await repo.record(
            new(user, build_sha="b1", occurred_at=later, message="boom at 2", event=bug["event"])
        )
        assert second is not None and second.fingerprint == first.fingerprint
        await session.refresh(issue)
        assert (issue.occurrences, issue.last_seen_at) == (2, later)
        # A duplicate event id counts nothing.
        assert await repo.record(new(user, event_id=first.event_id, **bug)) is None
        await session.refresh(issue)
        assert issue.occurrences == 2
        resolved = await repo.set_status(
            first.fingerprint, status="resolved", note="fixed in b2", user_id=user
        )
        assert (
            resolved is not None
            and resolved.status_build_sha == "b1"
            and resolved.status_set_by == user
        )
        # Same build: stays resolved. Another build: regressed.
        await repo.record(new(user, build_sha="b1", **bug))
        await session.refresh(issue)
        assert issue.status == "resolved"
        await repo.record(new(user, build_sha="b2", **bug))
        await session.refresh(issue)
        assert (issue.status, issue.occurrences, issue.last_build_sha) == ("regressed", 4, "b2")
        assert await repo.set_status("0" * 16, status="open", note=None, user_id=user) is None
        with pytest.raises(ValueError):
            await repo.set_status(first.fingerprint, status="regressed", note=None, user_id=user)
        page = await repo.list_issues(limit=1)
        assert [i.fingerprint for i in page.items] == [
            first.fingerprint
        ] and page.next_cursor is None
        assert (await repo.list_issues(status="regressed")).items[0].occurrences == 4
        with pytest.raises(ValueError):
            await repo.list_issues(cursor="nope")
    async with committed() as session:
        assert await session.scalar(select(func.count()).select_from(ClientErrorIssue)) == 1
