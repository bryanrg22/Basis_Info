"""``GET /v1/studies/{id}/evidence``: what supports each asset of the current revision.

Viewer access. Per asset (``basis.db.repositories.evidence``): the component, its room, the
room's photo documents (the component's own photo first, as ``source``), the localized
instances of the component's label in those photos, and the rule citations with the cited
passage's document and pages and the verification status. Keyset pagination on the asset
id (``limit``, ``cursor``; a cursor stays on the revision it was issued for), filterable by
``asset_id`` (an asset that is not in the revision gives an empty page).

Until S3 this route returned an empty list for one required ``asset_id``; ``asset_id`` is
optional now and the body is a page object.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import Depends, Query

from basis.api.routes.common import problems, read_router
from basis.api.routes.objects import object_out
from basis.api.schemas.evidence import (
    AssetEvidenceOut,
    CitationEvidenceOut,
    CitedChunkOut,
    EvidencePageOut,
    EvidencePhotoOut,
    RoomRefOut,
)
from basis.api.users import study_access
from basis.db.enums import OrgRole
from basis.db.repositories import VisibleStudy
from basis.db.repositories.evidence import (
    MAX_EVIDENCE_PAGE,
    AssetEvidence,
    EvidenceRepository,
)
from basis.db.session import SessionDep

DEFAULT_EVIDENCE_PAGE: Final = 20
MAX_CURSOR_LENGTH: Final = 512

reads = read_router(tags=["evidence"])


def asset_evidence_out(item: AssetEvidence) -> AssetEvidenceOut:
    asset, component, room = item.asset, item.component, item.room
    return AssetEvidenceOut(
        asset_id=asset.id,
        category=asset.category,
        bucket=asset.bucket,
        recovery_period=asset.recovery_period,
        method=asset.method.value,
        needs_review=asset.needs_review,
        component_id=asset.component_id,
        component_label=component.label if component is not None else None,
        room=RoomRefOut.model_validate(room) if room is not None else None,
        photos=[
            EvidencePhotoOut(
                document_id=photo.document.id,
                filename=photo.document.filename,
                role=photo.role,  # type: ignore[arg-type]
                localization_status=(
                    photo.localization.status  # type: ignore[arg-type]
                    if photo.localization is not None
                    else None
                ),
                instances=[object_out(row) for row in photo.instances],
            )
            for photo in item.photos
        ],
        citations=[
            CitationEvidenceOut(
                id=c.citation.id,
                asset_id=c.citation.asset_id,
                chunk_id=c.citation.chunk_id,
                excerpt=c.citation.excerpt,
                verified_at=c.citation.verified_at,
                verification_method=c.citation.verification_method,
                verified=c.citation.verified_at is not None,
                source=CitedChunkOut.model_validate(c.chunk),
            )
            for c in item.citations
        ],
    )


@reads.get(
    "/studies/{study_id}/evidence",
    summary="What supports each asset: room, photos, localized instances, citations",
    responses=problems(404),
)
async def list_evidence(
    access: Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))],
    session: SessionDep,
    asset_id: Annotated[uuid.UUID | None, Query(description="Only this asset.")] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_EVIDENCE_PAGE, description="Assets per page.")
    ] = DEFAULT_EVIDENCE_PAGE,
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=MAX_CURSOR_LENGTH,
            description="Opaque `next_cursor` from the previous page.",
        ),
    ] = None,
) -> EvidencePageOut:
    page = await EvidenceRepository(session).page(
        access.study.id, access.study.revision, limit=limit, cursor=cursor, asset_id=asset_id
    )
    return EvidencePageOut(
        revision=page.revision,
        items=[asset_evidence_out(item) for item in page.items],
        next_cursor=page.next_cursor,
    )
