"""The client-error retention purge: delete browser error records older than
``CLIENT_ERRORS_RETENTION_DAYS`` (docs/api/CLIENT_ERRORS.md section 6).

Why a fixed period and not the firm's retention rule: ``client_errors`` is operational data
about the application (what a user's browser did when something broke), not a client file;
it names the user, the firm and a study id, never a document, a price or a photo. The
firm's rule (``orgs.retention_days``) governs the client's uploads. Sentry's default event
retention is 90 days; 180 keeps the record through a quarter-late bug report. The
``client.error`` timeline rows are not touched: the timeline is append-only and the event's
summary there is the study's record.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.repositories.client_error import PURGE_BATCH, ClientErrorRepository
from basis.errors.codes import E

logger = logging.getLogger(__name__)


def cutoff_for(days: int, now: datetime | None = None) -> datetime:
    if days < 1:
        raise ValueError("days must be positive")
    return (now or datetime.now(UTC)) - timedelta(days=days)


async def purge_client_errors(
    session: AsyncSession, *, cutoff: datetime, dry_run: bool = False, batch: int = PURGE_BATCH
) -> int:
    """Delete every record received before ``cutoff`` in batches; the number deleted (or,
    on a dry run, the number that would be)."""
    repo = ClientErrorRepository(session)
    if dry_run:
        return await repo.count_received_before(cutoff)
    deleted = 0
    while True:
        n = await repo.delete_received_before(cutoff, limit=batch)
        deleted += n
        if n < batch:
            break
    logger.info(
        "client errors purged",
        extra={
            "error_code": E.CLIENT_ERROR_PURGED,
            "deleted": deleted,
            "cutoff": cutoff.isoformat(),
        },
    )
    return deleted
