"""``GET /v1/studies/{id}/evidence?asset_id=``: citations for an asset.

Stage 1 returns an empty list: citations are produced by the asset pipeline in S3. The
route, its authorization (viewer) and its response shape are fixed now so the frontend
and the contract test can depend on them.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Query

from basis.api.routes.common import problems, read_router
from basis.api.schemas.evidence import CitationOut
from basis.api.users import study_access
from basis.db.enums import OrgRole
from basis.db.repositories import VisibleStudy

reads = read_router(tags=["evidence"])


@reads.get(
    "/studies/{study_id}/evidence",
    summary="Citations supporting an asset's classification (empty until S3)",
    responses=problems(404),
)
async def list_evidence(
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))],
    asset_id: Annotated[uuid.UUID, Query(description="Asset id.")],
) -> list[CitationOut]:
    return []
