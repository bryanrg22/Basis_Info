"""The demo-request retention purge: delete the public site's demo requests older than
``DEMO_REQUESTS_RETENTION_DAYS`` (docs/api/DEMO_REQUESTS.md section 6).

Why a fixed period: a demo request is a visitor's contact details (name, work email, firm,
role, what they wrote), given to be contacted about a demo. It is kept while that purpose
lasts and deleted after, the storage-limitation principle (GDPR Article 5(1)(e); the CCPA
asks a business to state how long it keeps each category, which this setting is). 365 days
covers a professional-services sales cycle and recognising a firm that writes again; a firm
that became a customer is in the firm records, not here. The firm retention rule
(``orgs.retention_days``) is about a firm's uploads and does not apply: a visitor belongs to
no firm. The outbox rows that asked for the emails carry only the request's id.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.repositories.demo_request import PURGE_BATCH, DemoRequestRepository
from basis.errors.codes import E

logger = logging.getLogger(__name__)


async def purge_demo_requests(
    session: AsyncSession, *, cutoff: datetime, dry_run: bool = False, batch: int = PURGE_BATCH
) -> int:
    """Delete every request received before ``cutoff`` in batches; the number deleted (or,
    on a dry run, the number that would be)."""
    repo = DemoRequestRepository(session)
    if dry_run:
        return await repo.count_received_before(cutoff)
    deleted = 0
    while True:
        n = await repo.delete_received_before(cutoff, limit=batch)
        deleted += n
        if n < batch:
            break
    logger.info(
        "demo requests purged",
        extra={
            "error_code": E.DEMO_REQUEST_PURGED,
            "deleted": deleted,
            "cutoff": cutoff.isoformat(),
        },
    )
    return deleted
