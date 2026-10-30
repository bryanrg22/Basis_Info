"""The cost index store on the real database: append-only, versioned, never a duplicate;
the refresh job seeds the book, asks each source once for what the store lacks, records a
ledger row per attempt, and never writes the same value twice; the selection reads the
store with the configured priority (COST_INDICES.md sections 9-12)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from dbtest_support import db_settings
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.config import Settings
from basis.costs.indices.book import BookFetcher
from basis.costs.indices.protocol import Fetched, Wanted
from basis.costs.indices.rsmeans_api import RsmeansApiFetcher
from basis.costs.indices.select import load_index_values
from basis.costs.refresh import (
    SKIPPED_FETCH_OFF,
    manual_entry,
    record_manual,
    refresh,
    refresh_source,
)
from basis.db.models import CostIndexRun, CostIndexValue
from basis.db.repositories.cost_index import CostIndexRepository, IndexEntry, Series
from basis.db.session import session_scope
from basis.domain.cost_indices import BOOK_SOURCE, indices
from basis.errors.codes import E

pytestmark = pytest.mark.integration

D = Decimal
TODAY = date(2026, 10, 30)


def entry(
    year: int,
    value: str,
    *,
    source: str = "manual",
    citation: str = "a citation",
    series: Series = "historical",
    region: str = "US",
    derived: str | None = None,
) -> IndexEntry:
    return IndexEntry(
        source=source,
        series=series,
        year=year,
        region=region,
        value=D(value),
        as_of=date(year, 7, 1),
        citation=citation,
        derived=derived,
    )


class FakeFetcher:
    """A network source that answers from a script and counts its calls."""

    name = "rsmeans_public"
    network = True

    def __init__(self, answers: Mapping[int, str], *, fail: bool = False) -> None:
        self.answers = dict(answers)
        self.fail = fail
        self.calls: list[Wanted] = []

    def unavailable(self, settings: Settings) -> str | None:
        return None

    def wanted(
        self, have: Mapping[Series, Mapping[int, Any]], today: date
    ) -> dict[Series, list[int]]:
        held = set(have.get("historical", {}))
        return {"historical": [y for y in sorted(self.answers) if y not in held], "city": []}

    async def fetch(self, wanted: Wanted, *, timeout: float) -> Fetched:
        self.calls.append(wanted)
        if self.fail:
            raise TimeoutError("the page did not answer")
        entries = tuple(
            entry(y, self.answers[y], source=self.name, citation=f"page {y}")
            for y in wanted.get("historical", ())
            if y in self.answers
        )
        missing = [y for y in wanted.get("historical", ()) if y not in self.answers]
        return Fetched(
            entries=entries,
            document_sha256="f" * 64,
            failures=tuple(f"{y}: not published" for y in missing),
        )


# ------------------------------------------------------------------ the repository


async def test_append_versions_a_revised_value_and_never_writes_the_same_twice(
    session: AsyncSession,
) -> None:
    repo = CostIndexRepository(session)
    first = await repo.append([entry(2024, "294.8")], obtained_by=None, obtained_by_name="ann")
    assert len(first.added) == 1 and first.unchanged == 0
    assert first.added[0].version == 1 and first.years_added == [2024]
    again = await repo.append([entry(2024, "294.8")], obtained_by=None, obtained_by_name="ann")
    assert again.added == [] and again.unchanged == 1
    revised = await repo.append(
        [entry(2024, "295.0", citation="the 2025 book p.1")],
        obtained_by=None,
        obtained_by_name="bob",
    )
    assert revised.added[0].version == 2 and revised.added[0].citation == "the 2025 book p.1"
    rows = (
        await session.scalars(
            select(CostIndexValue)
            .where(CostIndexValue.year == 2024)
            .order_by(CostIndexValue.version)
        )
    ).all()
    assert [(r.version, r.value, r.obtained_by_name) for r in rows] == [
        (1, D("294.8"), "ann"),
        (2, D("295.0"), "bob"),
    ]  # the old row stays
    current = {r.year: r for r in await repo.latest("historical")}
    assert current[2024].version == 2
    # A different citation for the same value is a revision too (the citation is the fact).
    cited = await repo.append(
        [entry(2024, "295.0", citation="the 2025 book p.2")],
        obtained_by=None,
        obtained_by_name="bob",
    )
    assert cited.added[0].version == 3


async def test_the_same_key_twice_in_one_batch_is_a_revision_or_unchanged(
    session: AsyncSession,
) -> None:
    repo = CostIndexRepository(session)
    plan = await repo.plan([entry(2024, "1"), entry(2024, "1"), entry(2024, "2"), entry(2025, "3")])
    assert [(e.year, v) for e, v in plan.new] == [(2024, 1), (2024, 2), (2025, 1)]
    assert plan.unchanged == 1 and plan.years == [2024, 2025]


async def test_the_rows_cannot_be_updated_or_deleted(session: AsyncSession) -> None:
    repo = CostIndexRepository(session)
    added = await repo.append([entry(2024, "294.8")], obtained_by=None, obtained_by_name="ann")
    row_id = added.added[0].id
    run = await repo.record_run(
        source="manual",
        trigger="manual",
        actor="engineer",
        status="ok",
        started_at=TODAY
        and __import__("datetime").datetime(2026, 10, 30, tzinfo=__import__("datetime").UTC),
        finished_at=__import__("datetime").datetime(
            2026, 10, 30, tzinfo=__import__("datetime").UTC
        ),
        rows_added=1,
    )
    for statement, params in (
        ("UPDATE cost_indices SET value = 1 WHERE id = :id", {"id": row_id}),
        ("DELETE FROM cost_indices WHERE id = :id", {"id": row_id}),
        ("UPDATE cost_index_runs SET status = 'failed' WHERE id = :id", {"id": run.id}),
        ("DELETE FROM cost_index_runs WHERE id = :id", {"id": run.id}),
    ):
        # Each refusal in its own savepoint, so the rows stay for the next statement.
        with pytest.raises(DBAPIError, match="append-only|forbid|RestrictViolation|cannot"):
            async with session.begin_nested():
                await session.execute(text(statement), params)


async def test_held_and_page_read_the_current_versions(session: AsyncSession) -> None:
    repo = CostIndexRepository(session)
    await repo.append(
        [
            entry(2024, "294.8", source="rsmeans_public"),
            entry(2025, "300.7", source="rsmeans_public"),
        ],
        obtained_by=None,
        obtained_by_name="rsmeans_public",
    )
    await repo.append(
        [entry(2024, "295.0", source="rsmeans_public")], obtained_by=None, obtained_by_name="x"
    )
    held = await repo.held("rsmeans_public", "historical")
    assert {y: r.version for y, r in held.items()} == {2024: 2, 2025: 1}
    assert await repo.years("rsmeans_public", "historical") == {2024, 2025}
    current = await repo.page(limit=10)
    assert sorted((r.year, r.version) for r in current.items) == [(2024, 2), (2025, 1)]
    every = await repo.page(limit=10, current_only=False)
    assert len(every.items) == 3 and every.next_cursor is None
    one = await repo.page(limit=1, current_only=False)
    assert len(one.items) == 1 and one.next_cursor is not None
    when, _, row_id = one.next_cursor.partition("|")
    from datetime import datetime
    from uuid import UUID

    rest = await repo.page(
        limit=10, current_only=False, cursor=(datetime.fromisoformat(when), UUID(row_id))
    )
    assert len(rest.items) == 2 and {r.id for r in rest.items}.isdisjoint({one.items[0].id})
    only_2024 = await repo.page(limit=10, year=2024, current_only=False)
    # Both versions were written in one transaction, so they share obtained_at (now() is
    # the transaction time) and the id tiebreak is arbitrary: the set is what is pinned.
    assert sorted(r.version for r in only_2024.items) == [1, 2]


# ------------------------------------------------------------------ the refresh job


async def test_the_book_seeds_once_and_a_second_run_adds_nothing(
    committed: async_sessionmaker[AsyncSession], database_url: str
) -> None:
    settings = db_settings(database_url)
    first = await refresh(committed, settings, fetchers=[BookFetcher()], trigger="seed")
    assert [(r.source, r.status, r.rows_added) for r in first] == [(BOOK_SOURCE, "ok", 702)]
    assert (
        first[0].years_wanted == list(range(1970, 2021))
        and first[0].years_added == first[0].years_wanted
    )
    second = await refresh(committed, settings, fetchers=[BookFetcher()])
    assert [(r.status, r.rows_added, r.years_wanted) for r in second] == [("ok", 0, [])]
    assert second[0].reason is not None and "nothing new" in second[0].reason
    async with session_scope(committed) as session:
        repo = CostIndexRepository(session)
        assert await repo.count() == 702
        runs = await repo.runs()
        assert [(r.trigger, r.status, r.rows_added) for r in runs] == [
            ("refresh", "ok", 0),
            ("seed", "ok", 702),
        ]
        assert runs[1].actor_type == "system" and runs[1].document_sha256 == indices().sha256
        seeded = await session.scalar(
            select(CostIndexValue).where(
                CostIndexValue.year == 2020, CostIndexValue.series == "historical"
            )
        )
        assert seeded is not None and seeded.run_id == runs[1].id
        assert seeded.citation == "RSMeans Building Construction Costs 2020 p.778"
        # The seeded store prices exactly as the file alone.
        values = await load_index_values(session, settings)
        assert values.origin == "store"
        assert {y: i.index for y, i in values.years.items()} == {
            y: i.index for y, i in indices().years.items()
        }
        assert values.location_table_for(2024).cities == 651


async def test_a_network_source_is_skipped_while_fetching_is_off_then_asked_once(
    committed: async_sessionmaker[AsyncSession], database_url: str
) -> None:
    off = db_settings(database_url)
    fetcher = FakeFetcher({2024: "294.8", 2025: "300.7"})
    skipped = await refresh_source(committed, off, fetcher)
    assert (skipped.status, skipped.reason, skipped.code) == (
        "skipped",
        SKIPPED_FETCH_OFF,
        E.COST_INDEX_SOURCE_UNAVAILABLE,
    )
    assert fetcher.calls == []
    on = off.model_copy(update={"cost_index_fetch": "on"})
    asked = await refresh_source(committed, on, fetcher, today=TODAY)
    assert (asked.status, asked.rows_added, asked.years_added) == ("ok", 2, [2024, 2025])
    assert fetcher.calls == [{"historical": [2024, 2025], "city": []}]
    again = await refresh_source(committed, on, fetcher, today=TODAY)
    assert (again.status, again.rows_added) == ("ok", 0)
    assert fetcher.calls == [{"historical": [2024, 2025], "city": []}]  # not asked twice
    # A new year appears: only that year is asked for.
    fetcher.answers[2026] = "314.5"
    third = await refresh_source(committed, on, fetcher, today=TODAY)
    assert (third.rows_added, third.years_added) == (1, [2026])
    assert fetcher.calls[-1] == {"historical": [2026], "city": []}
    async with session_scope(committed) as session:
        repo = CostIndexRepository(session)
        assert await repo.count() == 3
        runs = await repo.runs(source="rsmeans_public")
        assert [r.status for r in runs] == ["ok", "ok", "ok", "skipped"]
        assert runs[-1].detail == {
            "reason": SKIPPED_FETCH_OFF,
            "code": E.COST_INDEX_SOURCE_UNAVAILABLE,
        }
        values = await load_index_values(session, on)
        assert values.years[2024].index == D("294.8") and values.years[2024].source == "page 2024"


async def test_a_failing_source_is_a_failed_run_row_and_nothing_is_written(
    committed: async_sessionmaker[AsyncSession], database_url: str
) -> None:
    on = db_settings(database_url).model_copy(update={"cost_index_fetch": "on"})
    failed = await refresh_source(committed, on, FakeFetcher({2024: "1"}, fail=True), today=TODAY)
    assert failed.status == "failed" and failed.code == E.COST_INDEX_FETCH_FAILED
    assert failed.failures == ["TimeoutError: the page did not answer"]
    partial = FakeFetcher({2024: "294.8"})
    partial.answers.update({2025: "x"})  # listed but not answered by the fake page
    partial.answers.pop(2025)
    report = await refresh_source(committed, on, partial, today=TODAY)
    assert report.status == "ok" and report.rows_added == 1
    async with session_scope(committed) as session:
        repo = CostIndexRepository(session)
        assert await repo.count() == 1
        runs = await repo.runs()
        assert [(r.status, r.rows_added) for r in runs] == [("ok", 1), ("failed", 0)]
        assert runs[1].failures == ["TimeoutError: the page did not answer"]


async def test_the_rsmeans_api_stub_is_skipped_without_credentials_and_fails_with_them(
    committed: async_sessionmaker[AsyncSession], database_url: str
) -> None:
    on = db_settings(database_url).model_copy(update={"cost_index_fetch": "on"})
    without = await refresh_source(committed, on, RsmeansApiFetcher(), today=TODAY)
    assert without.status == "skipped" and "RSMEANS_API_URL" in (without.reason or "")
    configured = on.model_copy(
        update={"rsmeans_api_url": "https://api.example", "rsmeans_api_key": "k"}
    )
    with_keys = await refresh_source(committed, configured, RsmeansApiFetcher(), today=TODAY)
    assert with_keys.status == "failed" and with_keys.years_wanted == list(range(2021, 2027))
    assert with_keys.failures and "not implemented" in with_keys.failures[0]


async def test_a_manual_entry_carries_its_user_and_ledger_row_and_outranks_the_fetched_value(
    committed: async_sessionmaker[AsyncSession], database_url: str
) -> None:
    on = db_settings(database_url).model_copy(update={"cost_index_fetch": "on"})
    await refresh(
        committed, on, fetchers=[BookFetcher(), FakeFetcher({2024: "294.8"})], today=TODAY
    )
    async with session_scope(committed) as session:
        from basis.db.repositories import UserRepository

        user = await UserRepository(session).upsert_by_firebase_uid(
            "ann", "ann@firm.example", "Ann"
        )
        rows, run, unchanged = await record_manual(
            session,
            [
                manual_entry(
                    series="historical",
                    year=2024,
                    value=D("295.6"),
                    as_of=date(2024, 1, 1),
                    citation="RSMeans Building Construction Costs 2025 p.1 (Jan 2024 line)",
                )
            ],
            actor_id=user.id,
            actor_name="ann@firm.example",
        )
        assert len(rows) == 1 and unchanged == 0
        assert (rows[0].obtained_by, rows[0].obtained_by_name, rows[0].run_id) == (
            user.id,
            "ann@firm.example",
            run.id,
        )
        assert (run.trigger, run.actor_type, run.actor_id, run.rows_added) == (
            "manual",
            "engineer",
            user.id,
            1,
        )
    async with session_scope(committed) as session:
        values = await load_index_values(session, on)
        assert values.years[2024].index == D("295.6")  # manual first
        assert values.years[2024].as_of == date(2024, 1, 1)
        assert 2023 not in values.years  # nothing invented for a year nobody loaded
        run_rows = (await session.scalars(select(CostIndexRun))).all()
        assert sorted(r.source for r in run_rows) == ["manual", BOOK_SOURCE, "rsmeans_public"]
