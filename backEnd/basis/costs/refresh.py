"""The refresh job and the operator CLI of the cost index store (COST_INDICES.md 12).

``python -m basis.costs.refresh`` (cron or Cloud Scheduler, monthly, or on demand)::

    python -m basis.costs.refresh                      # ask every enabled source for what
                                                       # the store lacks (seeds the book first)
    python -m basis.costs.refresh seed                 # the released 2020-book tables only
    python -m basis.costs.refresh refresh --source rsmeans_public
    python -m basis.costs.refresh enter --series historical --year 2024 --value 326.6 \\
        --as-of 2024-01-01 --citation "RSMeans Building Construction Costs 2024 p.<n>" \\
        --by engineer@firm.example
    python -m basis.costs.refresh import supplement.json --by engineer@firm.example
    python -m basis.costs.refresh export --out indices.json   # the current values, for
                                                              # evals.reprice_export
    python -m basis.costs.refresh list [--series historical] [--year 2024] [--all-versions]
    python -m basis.costs.refresh runs [--limit 50]

The job asks each enabled fetcher (``COST_INDEX_SOURCES``, in order) only for the years the
store lacks from that source (:meth:`Fetcher.wanted`), with a timeout, and appends what
comes back (append-only, versioned, never a duplicate: ``CostIndexRepository``). It never
touches a study: a study prices with whatever the store holds when its assets step runs,
and a value that arrives later reaches it when the step reruns. Every attempt is one
``cost_index_runs`` row (source, years wanted, years added, failures, who), so the ledger
shows when a value arrived and why one did not. A network fetcher is skipped, with the
reason recorded, while ``COST_INDEX_FETCH`` is off (the default) or when it lacks its
credentials. Network fetches happen only here, never inside a request.

Exit status: 0 when every run is ``ok`` or ``skipped``, 1 when one ``failed``, 2 on a
usage or configuration error. Output: one JSON object per run on stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.config import ConfigError, Settings
from basis.costs.indices.protocol import Fetched, Fetcher
from basis.costs.indices.select import MANUAL_SOURCE, load_index_values
from basis.db.repositories.cost_index import (
    NATIONAL,
    SERIES,
    CostIndexRepository,
    IndexEntry,
    Series,
    Trigger,
)
from basis.db.session import session_scope
from basis.domain.cost_indices import (
    SUPPLEMENT_SCHEMA_VERSION,
    CostIndexError,
    YearIndex,
    parse_supplement,
)
from basis.errors.codes import E

logger = logging.getLogger(__name__)

PROG: Final = "python -m basis.costs.refresh"
JOB_NAME: Final = "basis.costs.refresh"
SKIPPED_FETCH_OFF: Final = "COST_INDEX_FETCH is off: network sources are not asked"


class RefreshError(Exception):
    """A usage or configuration refusal (exit status 2)."""


@dataclass(frozen=True, slots=True)
class RunReport:
    """One ``cost_index_runs`` row, as the job reports it."""

    source: str
    trigger: str
    status: str
    started_at: str
    finished_at: str
    rows_added: int = 0
    unchanged: int = 0
    years_wanted: list[int] | None = None
    years_added: list[int] | None = None
    failures: list[str] | None = None
    reason: str | None = None
    document_sha256: str | None = None
    run_id: str | None = None
    code: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ one source


async def _record(
    session: AsyncSession,
    fetcher: Fetcher,
    *,
    trigger: Trigger,
    status: str,
    started: datetime,
    wanted: dict[Series, list[int]] | None,
    fetched: Fetched | None,
    reason: str | None = None,
    failures: Sequence[str] = (),
    code: str | None = None,
) -> RunReport:
    repo = CostIndexRepository(session)
    finished = _now()
    years_wanted = (
        None if wanted is None else sorted({y for years in wanted.values() for y in years})
    )
    detail: dict[str, Any] = {}
    if reason:
        detail["reason"] = reason
    if code:
        detail["code"] = code
    if wanted is not None:
        detail["wanted"] = {k: list(v) for k, v in wanted.items() if v}
    rows_added = 0
    years_added: list[int] | None = None
    unchanged = 0
    document = None if fetched is None else fetched.document_sha256
    run_id = uuid.uuid4()
    if fetched is not None and fetched.entries:
        plan = await repo.plan(fetched.entries)
        rows_added, unchanged = len(plan.new), plan.unchanged
        years_added = plan.years
        detail["unchanged"] = unchanged
    run = await repo.record_run(
        source=fetcher.name,
        trigger=trigger,
        actor="system",
        actor_name=JOB_NAME,
        status=status,  # type: ignore[arg-type]
        started_at=started,
        finished_at=finished,
        rows_added=rows_added,
        years_wanted=years_wanted,
        years_added=years_added,
        failures=list(failures) or None,
        document_sha256=document,
        detail=detail or None,
    )
    run_id = run.id
    if fetched is not None and fetched.entries:
        await repo.insert(plan, obtained_by=None, obtained_by_name=fetcher.name, run_id=run_id)
    return RunReport(
        source=fetcher.name,
        trigger=trigger,
        status=status,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        rows_added=rows_added,
        unchanged=unchanged,
        years_wanted=years_wanted,
        years_added=years_added,
        failures=list(failures) or None,
        reason=reason,
        document_sha256=document,
        run_id=str(run_id),
        code=code,
    )


async def refresh_source(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    fetcher: Fetcher,
    *,
    trigger: Trigger = "refresh",
    today: date | None = None,
) -> RunReport:
    """Ask one source for what the store lacks and append it; one ledger row either way."""
    started = _now()
    today = today or started.date()
    reason = fetcher.unavailable(settings)
    if reason is None and fetcher.network and settings.cost_index_fetch == "off":
        reason = SKIPPED_FETCH_OFF
    if reason is not None:
        async with session_scope(sessionmaker) as session:
            return await _record(
                session,
                fetcher,
                trigger=trigger,
                status="skipped",
                started=started,
                wanted=None,
                fetched=None,
                reason=reason,
                code=E.COST_INDEX_SOURCE_UNAVAILABLE,
            )
    async with session_scope(sessionmaker) as session:
        repo = CostIndexRepository(session)
        have = {series: await repo.held(fetcher.name, series) for series in SERIES}
    wanted = fetcher.wanted(have, today)
    if not any(wanted.values()):
        async with session_scope(sessionmaker) as session:
            return await _record(
                session,
                fetcher,
                trigger=trigger,
                status="ok",
                started=started,
                wanted=wanted,
                fetched=None,
                reason="nothing new to ask for: the store holds every year this source offers",
            )
    try:
        fetched = await asyncio.wait_for(
            fetcher.fetch(wanted, timeout=settings.cost_index_fetch_timeout_s),
            timeout=settings.cost_index_fetch_timeout_s + 5,
        )
    except Exception as exc:  # noqa: BLE001 - every failure is a ledger row, never a crash
        logger.warning("cost index fetch failed", extra={"source": fetcher.name, "error": str(exc)})
        async with session_scope(sessionmaker) as session:
            return await _record(
                session,
                fetcher,
                trigger=trigger,
                status="failed",
                started=started,
                wanted=wanted,
                fetched=None,
                failures=[f"{type(exc).__name__}: {exc}"],
                code=E.COST_INDEX_FETCH_FAILED,
            )
    status = "ok" if fetched.entries or not fetched.failures else "failed"
    async with session_scope(sessionmaker) as session:
        return await _record(
            session,
            fetcher,
            trigger=trigger,
            status=status,
            started=started,
            wanted=wanted,
            fetched=fetched,
            failures=fetched.failures,
            code=E.COST_INDEX_FETCH_FAILED if status == "failed" else None,
        )


async def refresh(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    fetchers: Sequence[Fetcher] | None = None,
    trigger: Trigger = "refresh",
    today: date | None = None,
) -> list[RunReport]:
    """Run every enabled fetcher (or ``fetchers``) in order; one report each."""
    from basis.costs.indices.registry import fetchers_for

    reports = []
    for fetcher in fetchers if fetchers is not None else fetchers_for(settings):
        reports.append(
            await refresh_source(sessionmaker, settings, fetcher, trigger=trigger, today=today)
        )
    return reports


# ------------------------------------------------------------------ manual entry


async def record_manual(
    session: AsyncSession,
    entries: Sequence[IndexEntry],
    *,
    actor_id: uuid.UUID | None,
    actor_name: str,
    trigger: Trigger = "manual",
    detail: dict[str, Any] | None = None,
) -> tuple[list[Any], Any, int]:
    """Append manual entries with their ledger row in one transaction (the API route and
    the CLI): ``(rows written, the run row, entries already held unchanged)``."""
    repo = CostIndexRepository(session)
    started = _now()
    plan = await repo.plan(entries)
    run = await repo.record_run(
        source=MANUAL_SOURCE,
        trigger=trigger,
        actor="engineer",
        actor_id=actor_id,
        actor_name=actor_name,
        status="ok",
        started_at=started,
        finished_at=_now(),
        rows_added=len(plan.new),
        years_wanted=[e.year for e in entries],
        years_added=plan.years,
        detail={**(detail or {}), "unchanged": plan.unchanged},
    )
    rows = await repo.insert(plan, obtained_by=actor_id, obtained_by_name=actor_name, run_id=run.id)
    return rows, run, plan.unchanged


def manual_entry(
    *,
    series: Series,
    year: int,
    value: Decimal,
    as_of: date,
    citation: str,
    region: str = NATIONAL,
    url: str | None = None,
    detail: dict[str, Any] | None = None,
) -> IndexEntry:
    return IndexEntry(
        source=MANUAL_SOURCE,
        series=series,
        year=year,
        region=region,
        value=value,
        as_of=as_of,
        citation=citation,
        url=url,
        detail=detail,
    )


def entries_from_supplement(years: dict[int, YearIndex]) -> list[IndexEntry]:
    """A supplement file's values (``parse_supplement``) as manual entries."""
    return [
        manual_entry(
            series="historical",
            year=row.year,
            value=row.index,
            as_of=row.as_of,
            citation=row.source,
            detail={"estimated": row.estimated} if row.estimated else None,
        )
        for row in sorted(years.values(), key=lambda r: r.year)
    ]


def export_document(years: dict[int, YearIndex]) -> dict[str, Any]:
    """The current Historical Cost Index values as a supplement-format document (what
    ``evals.reprice_export --supplement`` reads), so an offline replay prices with exactly
    the store's values."""
    return {
        "schema_version": SUPPLEMENT_SCHEMA_VERSION,
        "historical_cost_index": [
            {
                "year": row.year,
                "as_of": row.as_of.isoformat(),
                "index": str(row.index),
                "source": row.source,
                **({"estimated": True} if row.estimated else {}),
                **({"derived": row.derived} if row.derived else {}),
            }
            for row in sorted(years.values(), key=lambda r: r.year)
        ],
    }


# ------------------------------------------------------------------ CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("refresh", help="ask every enabled source for what the store lacks")
    run.add_argument("--source", action="append", default=None, help="only these sources")
    run.add_argument("--trigger", default="refresh", choices=["refresh", "seed"])
    commands.add_parser("seed", help="the released 2020-book tables only (idempotent)")
    enter = commands.add_parser("enter", help="enter one value by hand (source manual)")
    enter.add_argument("--series", required=True, choices=list(SERIES))
    enter.add_argument("--year", required=True, type=int)
    enter.add_argument("--region", default=NATIONAL, help="US (national), a state, or ST:prefixes")
    enter.add_argument("--value", required=True, help="the index as printed, e.g. 326.6")
    enter.add_argument("--as-of", required=True, help="ISO date the value is for")
    enter.add_argument("--citation", required=True, help="publication, table, page or URL")
    enter.add_argument("--url", default=None)
    enter.add_argument("--by", required=True, help="who enters it (name or email)")
    imp = commands.add_parser("import", help="append a supplement-format JSON file")
    imp.add_argument("file", type=Path)
    imp.add_argument("--by", required=True, help="who imports it (name or email)")
    exp = commands.add_parser("export", help="the current values as a supplement-format JSON")
    exp.add_argument("--out", type=Path, default=None, help="default: stdout")
    lst = commands.add_parser("list", help="the store's current values")
    lst.add_argument("--series", default=None, choices=list(SERIES))
    lst.add_argument("--year", type=int, default=None)
    lst.add_argument("--source", default=None)
    lst.add_argument("--all-versions", action="store_true")
    lst.add_argument("--limit", type=int, default=1000)
    runs = commands.add_parser("runs", help="the ledger, newest first")
    runs.add_argument("--limit", type=int, default=50)
    runs.add_argument("--source", default=None)
    return parser


def _row_json(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "source": row.source,
        "series": row.series,
        "year": row.year,
        "region": row.region,
        "value": str(row.value),
        "as_of": row.as_of.isoformat(),
        "citation": row.citation,
        "url": row.url,
        "derived": row.derived,
        "version": row.version,
        "obtained_at": row.obtained_at.isoformat(),
        "obtained_by": None if row.obtained_by is None else str(row.obtained_by),
        "obtained_by_name": row.obtained_by_name,
        "document_sha256": row.document_sha256,
        "run_id": None if row.run_id is None else str(row.run_id),
        "detail": row.detail,
    }


def _run_json(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "source": row.source,
        "trigger": row.trigger,
        "actor_type": row.actor_type,
        "actor_id": None if row.actor_id is None else str(row.actor_id),
        "actor_name": row.actor_name,
        "status": row.status,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat(),
        "rows_added": row.rows_added,
        "years_wanted": row.years_wanted,
        "years_added": row.years_added,
        "failures": row.failures,
        "document_sha256": row.document_sha256,
        "detail": row.detail,
    }


def _decimal(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise RefreshError(f"--value is not a number: {text!r}") from exc
    if not value.is_finite() or value <= 0:
        raise RefreshError(f"--value must be positive, got {text!r}")
    return value


async def _run(args: argparse.Namespace) -> tuple[list[dict[str, Any]], int]:
    from basis.config import load_settings
    from basis.costs.indices.registry import FETCHERS, fetchers_for
    from basis.db.engine import create_engine
    from basis.db.session import create_sessionmaker

    settings = load_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    try:
        command = args.command or "refresh"
        if command in ("refresh", "seed"):
            if command == "seed":
                fetchers: Sequence[Fetcher] = [FETCHERS["rsmeans_2020_book"]()]
                trigger: Trigger = "seed"
            else:
                fetchers = fetchers_for(settings, args.source)
                trigger = args.trigger
            reports = await refresh(sessionmaker, settings, fetchers=fetchers, trigger=trigger)
            status = 1 if any(r.status == "failed" for r in reports) else 0
            return [r.as_json() for r in reports], status
        if command == "enter":
            try:
                as_of = date.fromisoformat(args.as_of)
            except ValueError as exc:
                raise RefreshError(f"--as-of is not an ISO date: {args.as_of!r}") from exc
            entry = manual_entry(
                series=args.series,
                year=args.year,
                region=args.region,
                value=_decimal(args.value),
                as_of=as_of,
                citation=args.citation,
                url=args.url,
            )
            async with session_scope(sessionmaker) as session:
                rows, run, unchanged = await record_manual(
                    session, [entry], actor_id=None, actor_name=args.by, trigger="manual"
                )
                return [
                    {
                        "run": _run_json(run),
                        "rows": [_row_json(r) for r in rows],
                        "unchanged": unchanged,
                    }
                ], 0
        if command == "import":
            try:
                years = parse_supplement(json.loads(args.file.read_text(encoding="utf-8")))
            except (OSError, ValueError, CostIndexError) as exc:
                raise RefreshError(f"cannot import {args.file}: {exc}") from exc
            async with session_scope(sessionmaker) as session:
                rows, run, unchanged = await record_manual(
                    session,
                    entries_from_supplement(years),
                    actor_id=None,
                    actor_name=args.by,
                    trigger="import",
                    detail={"file": args.file.name},
                )
                return [
                    {
                        "run": _run_json(run),
                        "rows": [_row_json(r) for r in rows],
                        "unchanged": unchanged,
                    }
                ], 0
        if command == "export":
            async with session_scope(sessionmaker) as session:
                values = await load_index_values(session, settings)
            document = export_document(dict(values.years))
            if args.out is not None:
                args.out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
                return [
                    {"written": str(args.out), "years": len(document["historical_cost_index"])}
                ], 0
            return [document], 0
        if command == "list":
            async with session_scope(sessionmaker) as session:
                page = await CostIndexRepository(session).page(
                    limit=args.limit,
                    series=args.series,
                    year=args.year,
                    source=args.source,
                    current_only=not args.all_versions,
                )
            return [_row_json(r) for r in page.items], 0
        if command == "runs":
            async with session_scope(sessionmaker) as session:
                rows = await CostIndexRepository(session).runs(limit=args.limit, source=args.source)
            return [_run_json(r) for r in rows], 0
        raise RefreshError(f"unknown command {command!r}")
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        lines, status = asyncio.run(_run(args))
    except (RefreshError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(json.dumps(line, default=str))
    return status


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "JOB_NAME",
    "SKIPPED_FETCH_OFF",
    "RefreshError",
    "RunReport",
    "entries_from_supplement",
    "export_document",
    "manual_entry",
    "record_manual",
    "refresh",
    "refresh_source",
]
