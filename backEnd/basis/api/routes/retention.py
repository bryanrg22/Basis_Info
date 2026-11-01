"""``/v1/orgs/{org_id}/retention``: the firm's retention rule for client data
(docs/api/RETENTION_AND_ALERTS.md section 1).

A number of days, or null (keep everything, the default). Members read it; owners set it
(the firm's admin role, like members and invitations). The rule itself deletes nothing: the
purge job (``python -m basis.retention purge``) applies it, so setting a shorter rule takes
effect at the job's next run, and setting null before that run keeps everything. No entity
tag: the value is one integer, and the last write wins.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends

from basis.api.routes.common import problems, read_router, write_router
from basis.api.schemas.retention import RetentionIn, RetentionOut
from basis.api.users import CurrentUserDep, org_access
from basis.db.enums import OrgRole
from basis.db.errors import NotVisibleError
from basis.db.repositories import OrgRepository
from basis.db.session import SessionDep

reads = read_router(tags=["firms"])
writes = write_router(tags=["firms"])


@reads.get(
    "/orgs/{org_id}/retention",
    summary="The firm's retention rule for client data (members)",
    responses=problems(403, 404),
)
async def get_retention(
    org_id: Annotated[uuid.UUID, Depends(org_access(OrgRole.VIEWER))], session: SessionDep
) -> RetentionOut:
    org = await OrgRepository(session).get(org_id)
    if org is None:  # the dependency found a membership, so the firm exists
        raise NotVisibleError(f"org {org_id} not found")
    return RetentionOut(org_id=org.id, retention_days=org.retention_days)


@writes.put(
    "/orgs/{org_id}/retention",
    summary="Set the firm's retention rule for client data (owner)",
    description="`retention_days` 1 to 3650, or `null` to keep everything. Applied by the "
    "purge job to studies that are completed or archived and whose row last changed more "
    "than this many days ago: their uploaded documents, photos, rendered pages and model "
    "payloads are deleted from storage; the export, the signed report, every database row "
    "and the timeline stay, and `study.purged` records what went. A shorter rule than the "
    "age of existing finished studies purges them at the job's next run.",
    responses=problems(403, 404),
)
async def put_retention(
    body: RetentionIn,
    org_id: Annotated[uuid.UUID, Depends(org_access(OrgRole.OWNER))],
    user: CurrentUserDep,
    session: SessionDep,
) -> RetentionOut:
    org = await OrgRepository(session).set_retention(org_id, body.retention_days, user.id)
    return RetentionOut(org_id=org.id, retention_days=org.retention_days)
