"""``GET /v1/studies/{id}/fields``, ``/rooms``, ``/assets``: the revision's outputs.

What the three review gates show (EVAL_REPORT section 23, finding 5): the appraisal's
extraction fields, the rooms with their photos and inventories, and the assets with their
takeoff, cost and citation counts. Viewer access, like the evidence page. Each is
keyset-paginated (``limit``, ``cursor``; a cursor stays on the revision it was issued for)
at the study's current revision, or at ``revision`` (an earlier one is history; a later
one is 404). The ids are the ``entity_id`` values the decision route's corrections take.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import Depends, Query

from basis.api.errors import NotFound
from basis.api.routes.common import problems, read_router
from basis.api.schemas.common import MAX_REVISION
from basis.api.schemas.evidence import RoomRefOut
from basis.api.schemas.outputs import (
    AssetOut,
    AssetsPageOut,
    CostOut,
    ExtractionFieldOut,
    ExtractionFieldsPageOut,
    InventoryItemOut,
    RoomOut,
    RoomPhotoOut,
    RoomsPageOut,
    TakeoffOut,
)
from basis.api.users import study_access
from basis.db.enums import OrgRole
from basis.db.repositories import VisibleStudy
from basis.db.repositories.outputs import (
    MAX_OUTPUT_PAGE,
    AssetRow,
    FieldRow,
    OutputRepository,
    RoomRow,
    component_count,
)
from basis.db.session import SessionDep
from basis.extraction.field_mappings import FIELD_BY_KEY
from basis.vision.rooms import ROOM_INVENTORY_SOURCE

DEFAULT_PAGE: Final = 50
MAX_CURSOR_LENGTH: Final = 1024

reads = read_router(tags=["outputs"])

ViewerStudy = Annotated[VisibleStudy, Depends(study_access(OrgRole.VIEWER))]
Limit = Annotated[int, Query(ge=1, le=MAX_OUTPUT_PAGE, description="Items per page.")]
Cursor = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=MAX_CURSOR_LENGTH,
        description="Opaque `next_cursor` from the previous page.",
    ),
]
RevisionQuery = Annotated[
    int | None,
    Query(ge=1, le=MAX_REVISION, description="A revision up to the current one (default)."),
]


def _revision(access: VisibleStudy, revision: int | None) -> int:
    current = access.study.revision
    if revision is None:
        return current
    if revision > current:
        raise NotFound(f"The study has no revision {revision} yet.")
    return revision


def field_out(row: FieldRow) -> ExtractionFieldOut:
    item = row.field
    key = f"{item.section}.{item.field}"
    spec = FIELD_BY_KEY.get(key)
    return ExtractionFieldOut(
        id=item.id,
        extraction_id=item.extraction_id,
        document_id=row.extraction.document_id,
        key=key,
        section=item.section,
        field=item.field,
        label=spec.label if spec is not None else None,
        critical=spec is not None and spec.critical,
        value_text=item.value_text,
        value_num=item.value_num,
        value_date=item.value_date,
        confidence=item.confidence,
        source=item.source,
        page=item.page,
    )


def _uuids(values: object) -> list[uuid.UUID]:
    """The document ids a reconciled row was seen in (anything else is skipped)."""
    found: list[uuid.UUID] = []
    for value in values if isinstance(values, list) else []:
        try:
            found.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return found


def room_out(row: RoomRow) -> RoomOut:
    room = row.room
    items = []
    flagged = False
    for component in row.inventory:
        raw = component.raw if isinstance(component.raw, dict) else {}
        seen = raw.get("seen_in") if row.reconciled else None
        flagged = flagged or bool(raw.get("room_needs_review"))
        items.append(
            InventoryItemOut(
                component_id=component.id,
                label=component.label,
                count=component_count(component),
                material=component.material,
                attachment=component.attachment,
                confidence=component.confidence,
                document_id=component.document_id,
                seen_in=_uuids(seen),
            )
        )
    return RoomOut(
        id=room.id,
        name=room.name,
        room_type=room.room_type,
        floor=room.floor,
        source=room.source,
        group_key=room.group_key,
        photos=[RoomPhotoOut(document_id=p.document_id, filename=p.filename) for p in row.photos],
        inventory_source=ROOM_INVENTORY_SOURCE if row.reconciled else "per_photo",
        needs_review=flagged,
        inventory=items,
    )


def asset_out(row: AssetRow) -> AssetOut:
    asset, component = row.asset, row.component
    return AssetOut(
        id=asset.id,
        component_id=asset.component_id,
        label=component.label if component is not None else None,
        count=component_count(component) if component is not None else None,
        room=RoomRefOut.model_validate(row.room) if row.room is not None else None,
        category=asset.category,
        bucket=asset.bucket,
        recovery_period=asset.recovery_period,
        irs_section=asset.irs_section,
        method=asset.method.value,
        confidence=asset.confidence,
        needs_review=asset.needs_review,
        review_reasons=[r for r in (asset.review_reason or "").split("; ") if r],
        takeoff=(
            TakeoffOut(
                quantity=row.takeoff.quantity, unit=row.takeoff.unit, formula=row.takeoff.formula
            )
            if row.takeoff is not None
            else None
        ),
        cost=(
            CostOut(
                unit_cost=row.cost.unit_cost,
                total=row.cost.total,
                source_row=row.cost.source_row,
                location_factor=row.cost.location_factor,
                cost_year=row.cost.cost_year,
            )
            if row.cost is not None
            else None
        ),
        citations=row.citations,
        verified_citations=row.verified_citations,
    )


@reads.get(
    "/studies/{study_id}/fields",
    summary="The appraisal's extraction fields at a revision (the appraisal review)",
    responses=problems(404),
)
async def list_fields(
    access: ViewerStudy,
    session: SessionDep,
    revision: RevisionQuery = None,
    limit: Limit = DEFAULT_PAGE,
    cursor: Cursor = None,
) -> ExtractionFieldsPageOut:
    page = await OutputRepository(session).fields(
        access.study.id, _revision(access, revision), limit=limit, cursor=cursor
    )
    return ExtractionFieldsPageOut(
        revision=page.revision,
        items=[field_out(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@reads.get(
    "/studies/{study_id}/rooms",
    summary="The rooms at a revision with their photos and inventories (the rooms review)",
    responses=problems(404),
)
async def list_rooms(
    access: ViewerStudy,
    session: SessionDep,
    revision: RevisionQuery = None,
    limit: Limit = DEFAULT_PAGE,
    cursor: Cursor = None,
) -> RoomsPageOut:
    page = await OutputRepository(session).rooms(
        access.study.id, _revision(access, revision), limit=limit, cursor=cursor
    )
    return RoomsPageOut(
        revision=page.revision,
        items=[room_out(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@reads.get(
    "/studies/{study_id}/assets",
    summary="The assets at a revision with takeoff, cost and citations (the assets review)",
    responses=problems(404),
)
async def list_assets(
    access: ViewerStudy,
    session: SessionDep,
    revision: RevisionQuery = None,
    limit: Limit = DEFAULT_PAGE,
    cursor: Cursor = None,
    needs_review: Annotated[
        bool | None, Query(description="Only assets that need review (true) or not (false).")
    ] = None,
) -> AssetsPageOut:
    page = await OutputRepository(session).assets(
        access.study.id,
        _revision(access, revision),
        limit=limit,
        cursor=cursor,
        needs_review=needs_review,
    )
    return AssetsPageOut(
        revision=page.revision,
        items=[asset_out(row) for row in page.items],
        next_cursor=page.next_cursor,
    )
