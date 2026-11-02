"""``python -m basis.retention``: apply the firms' retention rules
(docs/api/RETENTION_AND_ALERTS.md).

::

    python -m basis.retention purge --dry-run            # what would be purged; nothing changes
    python -m basis.retention purge                      # purge it
    python -m basis.retention purge --org-id UUID --limit 20
    python -m basis.retention purge-client-errors [--dry-run] [--days 180]   # browser error records

Reads ``BASIS_ENV``, ``DATABASE_URL`` (or ``CLOUD_SQL_INSTANCE``) and ``GCS_BUCKET`` like
every process; without ``GCS_BUCKET`` it uses the local storage directory the API uses in
dev. One JSON object per study on stdout, then a summary line; exit status 0, or 1 when a
study's objects could not be deleted (that study is not marked purged; run again). Each
study is its own transaction, so a failure on one does not undo the others.

Scheduling is documented, not enabled: infra/staging/RUNBOOK.md section 3.1 (a Cloud Run
job run daily by Cloud Scheduler, or a cron line on a local stack).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.config import Settings, get_settings
from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker, session_scope
from basis.obs.logging import configure_logging
from basis.retention.client_errors import cutoff_for, purge_client_errors
from basis.retention.purge import DEFAULT_LIMIT, purge_study, select_candidates
from basis.storage import Storage

PROG: Final = "python -m basis.retention"

logger = logging.getLogger(__name__)


def default_storage(settings: Settings) -> tuple[Storage, str]:
    """GCS when ``GCS_BUCKET`` is set, else the API's local storage directory and bucket."""
    from basis.api.app import default_storage as api_default_storage

    return api_default_storage(settings)


async def run_purge(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: Storage,
    bucket: str,
    *,
    dry_run: bool,
    org_id: uuid.UUID | None,
    limit: int,
    out: Any = None,
    now: datetime | None = None,
) -> int:
    """Purge (or list) every candidate, one transaction each; the exit status. ``now`` is
    for tests (the rule is evaluated as of that moment); the CLI always uses the clock."""
    out = out or sys.stdout
    async with session_scope(sessionmaker) as session:
        candidates = await select_candidates(session, now=now, org_id=org_id, limit=limit)
        for candidate in candidates:
            await session.refresh(candidate.study)
    purged = failed = 0
    for candidate in candidates:
        try:
            async with session_scope(sessionmaker) as session:
                report = await purge_study(
                    session, storage, bucket, candidate, dry_run=dry_run, now=now
                )
        except Exception as exc:
            failed += 1
            print(
                json.dumps(
                    {
                        "study_id": str(candidate.study.id),
                        "org_id": str(candidate.org_id),
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                ),
                file=out,
            )
            continue
        purged += 1
        print(json.dumps(report.as_json()), file=out)
    print(
        json.dumps(
            {
                "summary": True,
                "dry_run": dry_run,
                "candidates": len(candidates),
                "purged" if not dry_run else "would_purge": purged,
                "failed": failed,
            }
        ),
        file=out,
    )
    return 1 if failed else 0


async def run_client_error_purge(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    days: int,
    dry_run: bool,
    out: Any = None,
    now: datetime | None = None,
) -> int:
    """Delete the browser error records older than ``days`` (one JSON summary line); 0."""
    out = out or sys.stdout
    cutoff = cutoff_for(days, now)
    async with session_scope(sessionmaker) as session:
        count = await purge_client_errors(session, cutoff=cutoff, dry_run=dry_run)
    print(
        json.dumps(
            {
                "summary": True,
                "dry_run": dry_run,
                "cutoff": cutoff.isoformat(),
                "retention_days": days,
                "deleted" if not dry_run else "would_delete": count,
            }
        ),
        file=out,
    )
    return 0


async def _main(args: argparse.Namespace, settings: Settings) -> int:
    engine = create_engine(settings)
    try:
        if args.command == "purge-client-errors":
            return await run_client_error_purge(
                create_sessionmaker(engine),
                days=args.days or settings.client_errors_retention_days,
                dry_run=args.dry_run,
            )
        storage, bucket = default_storage(settings)
        return await run_purge(
            create_sessionmaker(engine),
            storage,
            bucket,
            dry_run=args.dry_run,
            org_id=args.org_id,
            limit=args.limit,
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    purge = sub.add_parser("purge", help="apply every firm's retention rule")
    purge.add_argument("--dry-run", action="store_true", help="list; change nothing")
    purge.add_argument("--org-id", type=uuid.UUID, default=None, help="one firm only")
    purge.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="studies per run")
    errors = sub.add_parser(
        "purge-client-errors",
        help="delete browser error records older than CLIENT_ERRORS_RETENTION_DAYS",
    )
    errors.add_argument("--dry-run", action="store_true", help="count; change nothing")
    errors.add_argument(
        "--days", type=int, default=None, help="override CLIENT_ERRORS_RETENTION_DAYS"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "purge" and args.limit < 1:
        print("--limit must be positive", file=sys.stderr)
        return 2
    if args.command == "purge-client-errors" and args.days is not None and args.days < 1:
        print("--days must be positive", file=sys.stderr)
        return 2
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.env != "dev")
    return asyncio.run(_main(args, settings))


if __name__ == "__main__":
    sys.exit(main())
