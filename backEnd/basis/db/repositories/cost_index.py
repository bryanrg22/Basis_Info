"""The cost index store (``cost_indices``) and its ledger (``cost_index_runs``).

Both tables are append-only (``forbid_mutation()``; migration ``0016_cost_indices``). A value
is keyed by ``(source, series, year, region)``; :meth:`CostIndexRepository.append` writes a
row only when the key has no row yet or its latest row carries a different value or
citation, as the next ``version``, so nothing is ever written twice and a revision keeps
its history. Every append happens under one advisory lock so two refresh runs cannot race
to the same version. Reads: :meth:`latest` (the current version of every key of a series,
every source), :meth:`have` (one source's current keys, for a fetcher to ask only for what
the store lacks) and :meth:`page` (the API's listing). :meth:`record_run` writes the ledger
row of one attempt (COST_INDICES.md sections 9 and 12).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, Literal

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import CostIndexRun, CostIndexValue
from basis.obs.logging import get_context

Series = Literal["historical", "city"]
SERIES: Final[tuple[Series, ...]] = ("historical", "city")
NATIONAL: Final = "US"
MAX_PAGE: Final = 1000
LOCK_KEY: Final = "cost_indices"

Trigger = Literal["seed", "refresh", "manual", "import"]
RunStatus = Literal["ok", "skipped", "failed"]
Actor = Literal["engineer", "system"]


def request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One value to store, as a fetcher or an engineer hands it in."""

    source: str
    series: Series
    year: int
    region: str
    value: Decimal
    as_of: date
    citation: str
    url: str | None = None
    derived: str | None = None
    document_sha256: str | None = None
    detail: dict[str, Any] | None = None

    @property
    def key(self) -> tuple[str, Series, int, str]:
        return (self.source, self.series, self.year, self.region)


@dataclass(slots=True)
class AppendResult:
    added: list[CostIndexValue] = field(default_factory=list)
    unchanged: int = 0  # entries whose latest stored version already carries the value

    @property
    def years_added(self) -> list[int]:
        return sorted({row.year for row in self.added})


@dataclass(slots=True)
class Plan:
    """What an append will write: each new entry with its version, and the count of
    entries the store already holds with the same value and citation."""

    new: list[tuple[IndexEntry, int]] = field(default_factory=list)
    unchanged: int = 0

    @property
    def years(self) -> list[int]:
        return sorted({entry.year for entry, _ in self.new})


@dataclass(frozen=True, slots=True)
class IndexPage:
    items: list[CostIndexValue]
    next_cursor: str | None


def _same(row: CostIndexValue, entry: IndexEntry) -> bool:
    return row.value == entry.value and row.citation == entry.citation


def _same_entry(a: IndexEntry, b: IndexEntry) -> bool:
    return a.value == b.value and a.citation == b.citation


class CostIndexRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ writes

    async def lock(self) -> None:
        """Serialize appends (transaction-scoped advisory lock)."""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY}
        )

    async def plan(self, entries: Sequence[IndexEntry]) -> Plan:
        """Under the lock: which entries are new (with the version each will take) and how
        many the store already holds unchanged. The caller records the run, then
        :meth:`insert` the plan in the same transaction."""
        plan = Plan()
        if not entries:
            return plan
        await self.lock()
        versions: dict[tuple[str, Series, int, str], int] = {}
        last: dict[tuple[str, Series, int, str], IndexEntry] = {}
        for entry in entries:
            if entry.key in last:
                # The same key twice in one batch: a repeat is unchanged, a different value
                # is the next revision.
                if _same_entry(last[entry.key], entry):
                    plan.unchanged += 1
                    continue
                versions[entry.key] += 1
            else:
                latest = await self._latest_version(entry)
                if latest is not None and _same(latest, entry):
                    plan.unchanged += 1
                    versions[entry.key] = latest.version
                    last[entry.key] = entry
                    continue
                versions[entry.key] = 1 if latest is None else latest.version + 1
            last[entry.key] = entry
            plan.new.append((entry, versions[entry.key]))
        return plan

    async def insert(
        self,
        plan: Plan,
        *,
        obtained_by: uuid.UUID | None,
        obtained_by_name: str,
        run_id: uuid.UUID | None = None,
    ) -> list[CostIndexValue]:
        """Write the planned rows (flushed, not committed). ``obtained_by`` is the user of
        a manual entry (then ``obtained_by_name`` is their name or email), None for a
        fetcher (its name); ``run_id`` the ledger row of this attempt."""
        rows = [
            CostIndexValue(
                source=entry.source,
                series=entry.series,
                year=entry.year,
                region=entry.region,
                value=entry.value,
                as_of=entry.as_of,
                citation=entry.citation,
                url=entry.url,
                derived=entry.derived,
                version=version,
                obtained_by=obtained_by,
                obtained_by_name=obtained_by_name,
                document_sha256=entry.document_sha256,
                run_id=run_id,
                detail=entry.detail,
            )
            for entry, version in plan.new
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return rows

    async def append(
        self,
        entries: Sequence[IndexEntry],
        *,
        obtained_by: uuid.UUID | None,
        obtained_by_name: str,
        run_id: uuid.UUID | None = None,
    ) -> AppendResult:
        """Append every entry whose key is new or whose latest version differs
        (:meth:`plan` then :meth:`insert`); flushed, not committed."""
        plan = await self.plan(entries)
        rows = await self.insert(
            plan, obtained_by=obtained_by, obtained_by_name=obtained_by_name, run_id=run_id
        )
        return AppendResult(added=rows, unchanged=plan.unchanged)

    async def record_run(
        self,
        *,
        source: str,
        trigger: Trigger,
        actor: Actor,
        status: RunStatus,
        started_at: datetime,
        finished_at: datetime,
        actor_id: uuid.UUID | None = None,
        actor_name: str | None = None,
        rows_added: int = 0,
        years_wanted: Sequence[int] | None = None,
        years_added: Sequence[int] | None = None,
        failures: Sequence[str] | None = None,
        document_sha256: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> CostIndexRun:
        row = CostIndexRun(
            source=source,
            trigger=trigger,
            actor_type=actor,
            actor_id=actor_id,
            actor_name=actor_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            rows_added=rows_added,
            years_wanted=None if years_wanted is None else sorted(set(years_wanted)),
            years_added=None if years_added is None else sorted(set(years_added)),
            failures=None if failures is None else list(failures),
            document_sha256=document_sha256,
            request_id=request_id(),
            detail=detail,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    # ------------------------------------------------------------------ reads

    async def _latest_version(self, entry: IndexEntry) -> CostIndexValue | None:
        stmt = (
            select(CostIndexValue)
            .where(
                CostIndexValue.source == entry.source,
                CostIndexValue.series == entry.series,
                CostIndexValue.year == entry.year,
                CostIndexValue.region == entry.region,
            )
            .order_by(CostIndexValue.version.desc())
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()

    def _current(self, series: Series | None = None, source: str | None = None) -> Any:
        """The latest version of every key (``DISTINCT ON`` the key, highest version)."""
        stmt = select(CostIndexValue).distinct(
            CostIndexValue.source,
            CostIndexValue.series,
            CostIndexValue.year,
            CostIndexValue.region,
        )
        if series is not None:
            stmt = stmt.where(CostIndexValue.series == series)
        if source is not None:
            stmt = stmt.where(CostIndexValue.source == source)
        return stmt.order_by(
            CostIndexValue.source,
            CostIndexValue.series,
            CostIndexValue.year,
            CostIndexValue.region,
            CostIndexValue.version.desc(),
        )

    async def latest(self, series: Series | None = None) -> list[CostIndexValue]:
        """The current version of every key, every source, of ``series`` (or all)."""
        return list((await self._session.scalars(self._current(series))).all())

    async def have(self, source: str, series: Series) -> dict[tuple[int, str], CostIndexValue]:
        """One source's current rows by ``(year, region)``."""
        rows = (await self._session.scalars(self._current(series, source))).all()
        return {(row.year, row.region): row for row in rows}

    async def held(self, source: str, series: Series) -> dict[int, CostIndexValue]:
        """One source's current rows by year: the national row of the historical series,
        any one row of a city year (what a fetcher's ``wanted`` looks at)."""
        rows = await self.have(source, series)
        by_year: dict[int, CostIndexValue] = {}
        for (year, region), row in sorted(rows.items()):
            if series == "historical" and region != NATIONAL:
                continue
            by_year.setdefault(year, row)
        return by_year

    async def years(self, source: str, series: Series) -> set[int]:
        stmt = (
            select(CostIndexValue.year)
            .where(CostIndexValue.source == source, CostIndexValue.series == series)
            .distinct()
        )
        return set((await self._session.scalars(stmt)).all())

    async def count(self) -> int:
        return int(
            await self._session.scalar(select(func.count()).select_from(CostIndexValue)) or 0
        )

    async def page(
        self,
        *,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None = None,
        series: Series | None = None,
        year: int | None = None,
        source: str | None = None,
        current_only: bool = True,
    ) -> IndexPage:
        """Rows newest first (``obtained_at``, ``id`` keyset); ``current_only`` hides the
        superseded versions."""
        limit = max(1, min(limit, MAX_PAGE))
        if current_only:
            current = self._current(series, source).subquery()
            base = select(CostIndexValue).join(current, CostIndexValue.id == current.c.id)
        else:
            base = select(CostIndexValue)
            if series is not None:
                base = base.where(CostIndexValue.series == series)
            if source is not None:
                base = base.where(CostIndexValue.source == source)
        if year is not None:
            base = base.where(CostIndexValue.year == year)
        if cursor is not None:
            obtained_at, row_id = cursor
            base = base.where(
                or_(
                    CostIndexValue.obtained_at < obtained_at,
                    and_(CostIndexValue.obtained_at == obtained_at, CostIndexValue.id < row_id),
                )
            )
        base = base.order_by(CostIndexValue.obtained_at.desc(), CostIndexValue.id.desc()).limit(
            limit + 1
        )
        rows: Sequence[CostIndexValue] = (await self._session.scalars(base)).all()
        items = list(rows[:limit])
        next_cursor = (
            f"{items[-1].obtained_at.isoformat()}|{items[-1].id}" if len(rows) > limit else None
        )
        return IndexPage(items=items, next_cursor=next_cursor)

    async def runs(self, *, limit: int = 100, source: str | None = None) -> list[CostIndexRun]:
        """The ledger, newest first."""
        stmt = select(CostIndexRun)
        if source is not None:
            stmt = stmt.where(CostIndexRun.source == source)
        stmt = stmt.order_by(CostIndexRun.started_at.desc(), CostIndexRun.id.desc()).limit(
            max(1, min(limit, MAX_PAGE))
        )
        return list((await self._session.scalars(stmt)).all())


__all__ = [
    "MAX_PAGE",
    "NATIONAL",
    "SERIES",
    "Actor",
    "AppendResult",
    "CostIndexRepository",
    "IndexEntry",
    "IndexPage",
    "Plan",
    "RunStatus",
    "Series",
    "Trigger",
]
