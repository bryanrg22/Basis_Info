"""``GET /v1/studies/{id}/fields``, ``/extraction``, ``/rooms``, ``/assets``: the outputs.

``/extraction`` is the appraisal extraction of a revision: its source document and kind, the
report PDF its regions are drawn on (``pdf_document_id``), its issues and review reasons, and
the study corpus (docs/api/UAD36_INTAKE.md); 404 at a revision with no extraction (only the
first revision and appraisal rejections extract).

What the review gates show (EVAL_REPORT section 23, finding 5): the appraisal's
extraction fields, the rooms with their photos and inventories (the rooms review shows the
grouping, the components review the inventories and ``suggested_counts``; patch
``components-gate-v1``), and the assets with their takeoff (its inputs as stored), cost
(with the cost trail recorded when it was priced, read as stored and never recomputed:
docs/api/COST_INDICES.md, "Per-asset cost trail") and citation counts.
Viewer access, like the evidence page. Each is keyset-paginated (``limit``, ``cursor``; a
cursor stays on the revision it was issued for) at the study's current revision, or at
``revision`` (an earlier one is history; a later one is 404). The ids are the
``entity_id`` values the decision route's corrections take.
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
    ConventionOut,
    CostOut,
    CostTrailOut,
    ExtractionFieldOut,
    ExtractionFieldsPageOut,
    ExtractionIssueOut,
    ExtractionOut,
    FieldRegionOut,
    InventoryItemOut,
    RoomOut,
    RoomPhotoOut,
    RoomsPageOut,
    StudyCorpusOut,
    SuggestedCountOut,
    TakeoffOut,
)
from basis.api.schemas.timeline import ProvenanceOut
from basis.api.users import study_access
from basis.db.enums import OrgRole
from basis.db.models import CostEstimate, Extraction, Takeoff
from basis.db.repositories import VisibleStudy
from basis.db.repositories.outputs import (
    MAX_OUTPUT_PAGE,
    AssetRow,
    FieldRow,
    OutputRepository,
    RoomRow,
    StudyCorpus,
    component_count,
)
from basis.db.repositories.provenance import Provenance, ProvenanceResolver, provenance_json
from basis.db.repositories.room import RoomRepository
from basis.db.session import SessionDep
from basis.errors.catalogue import codes_for_reasons
from basis.errors.codes import E
from basis.extraction.field_mappings import FIELD_BY_KEY
from basis.vision.rooms import ROOM_INVENTORY_SOURCE, is_room_manual
from basis.vision.selection import suggested_counts

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
        raise NotFound(f"The study has no revision {revision} yet.", code=E.STUDY_NO_SUCH_REVISION)
    return revision


def provenance_out(provenance: Provenance | None) -> ProvenanceOut | None:
    return None if provenance is None else ProvenanceOut.model_validate(provenance_json(provenance))


def field_out(row: FieldRow, provenance: Provenance | None = None) -> ExtractionFieldOut:
    item = row.field
    key = f"{item.section}.{item.field}"
    spec = FIELD_BY_KEY.get(key)
    return ExtractionFieldOut(
        id=item.id,
        extraction_id=item.extraction_id,
        document_id=row.extraction.document_id,
        pdf_document_id=extraction_source(row.extraction)[1],
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
        regions=[FieldRegionOut.model_validate(r) for r in item.regions or []],
        provenance=provenance_out(provenance),
    )


def extraction_source(extraction: Extraction) -> tuple[str, uuid.UUID | None]:
    """``(upload_kind, pdf_document_id)`` of an extraction (``raw["source"]``; stored before
    it: a PDF, or ``raw["upload_kind"]`` with the document's own PDF unless a bare XML)."""
    raw = extraction.raw if isinstance(extraction.raw, dict) else {}
    source = raw.get("source")
    if isinstance(source, dict):
        kind = str(source.get("upload_kind") or "pdf")
        pdf = source.get("pdf_document_id")
        return kind, uuid.UUID(pdf) if isinstance(pdf, str) else None
    kind = str(raw.get("upload_kind") or "pdf")
    return kind, None if kind == "xml" else extraction.document_id


def _issue(value: object) -> ExtractionIssueOut | None:
    if not isinstance(value, dict) or not isinstance(value.get("rule"), str):
        return None
    keys = value.get("keys")
    return ExtractionIssueOut(
        rule=value["rule"],
        severity="warning" if value.get("severity") == "warning" else "error",
        keys=[str(k) for k in keys] if isinstance(keys, list) else [],
        message=str(value.get("message") or ""),
    )


def _strings(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def extraction_out(extraction: Extraction, located: bool, corpus: StudyCorpus) -> ExtractionOut:
    raw = extraction.raw if isinstance(extraction.raw, dict) else {}
    kind, pdf_id = extraction_source(extraction)
    form = raw.get("form")
    issues = [issue for v in raw.get("issues") or [] if (issue := _issue(v)) is not None]
    return ExtractionOut(
        revision=extraction.revision,
        extraction_id=extraction.id,
        document_id=extraction.document_id,
        upload_kind=kind if kind in ("pdf", "zip", "xml") else "pdf",  # type: ignore[arg-type]
        pdf_document_id=pdf_id,
        regions_available=located,
        form=form.get("form") if isinstance(form, dict) and kind == "pdf" else None,
        method=extraction.method,
        needs_review=extraction.needs_review,
        overall_confidence=extraction.overall_confidence,
        review_reasons=_strings(raw.get("review_reasons")),
        review_codes=codes_for_reasons(_strings(raw.get("review_reasons"))),
        issues=issues,
        notes=_strings(raw.get("notes")),
        corpus=StudyCorpusOut(
            status=corpus.status,
            corpus_version_id=corpus.corpus_version_id,
            document_id=corpus.document_id,
            chunks=corpus.chunks,
        ),
        created_at=extraction.created_at,
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


def room_out(
    row: RoomRow,
    provenance: Provenance | None = None,
    photo_provenance: dict[uuid.UUID, Provenance] | None = None,
    component_provenance: dict[uuid.UUID, Provenance] | None = None,
) -> RoomOut:
    room = row.room
    items = []
    flagged = False
    photo_provenance = photo_provenance or {}
    component_provenance = component_provenance or {}
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
                added=is_room_manual(component.raw, component.document_id),
                provenance=provenance_out(component_provenance.get(component.id)),
            )
        )
    inventory: dict[str, int] = {}
    for item in items:
        inventory[item.label] = inventory.get(item.label, 0) + item.count
    return RoomOut(
        id=room.id,
        name=room.name,
        room_type=room.room_type,
        floor=room.floor,
        source=room.source,
        group_key=room.group_key,
        photos=[
            RoomPhotoOut(
                document_id=p.document_id,
                filename=p.filename,
                provenance=provenance_out(photo_provenance.get(p.document_id)),
            )
            for p in row.photos
        ],
        inventory_source=ROOM_INVENTORY_SOURCE if row.reconciled else "per_photo",
        needs_review=flagged,
        inventory=items,
        suggested_counts=[
            SuggestedCountOut(
                label=s.label,
                inventory_count=s.inventory_count,
                photo_count=s.photo_count,
                manual_count=s.manual_count,
                document_id=s.document_id,
                suggested=s.suggested,
            )
            for s in suggested_counts(inventory, row.objects)
        ],
        provenance=provenance_out(provenance),
    )


def takeoff_out(takeoff: Takeoff) -> TakeoffOut:
    """The stored takeoff, with its inputs as written (``method`` and ``assumptions`` are
    keys of them; a takeoff stored without them has null and [])."""
    inputs = takeoff.inputs if isinstance(takeoff.inputs, dict) else None
    method = None if inputs is None else inputs.get("method")
    assumptions = None if inputs is None else inputs.get("assumptions")
    return TakeoffOut(
        quantity=takeoff.quantity,
        unit=takeoff.unit,
        formula=takeoff.formula,
        method=method if isinstance(method, str) else None,
        inputs=inputs,
        assumptions=[str(a) for a in assumptions] if isinstance(assumptions, list) else [],
    )


def cost_out(cost: CostEstimate) -> CostOut:
    """The stored cost row and the trail recorded when it was priced (read as stored,
    never recomputed: a row priced before the trail existed has ``trail`` null)."""
    trail = cost.trail if isinstance(cost.trail, dict) else None
    return CostOut(
        unit_cost=cost.unit_cost,
        total=cost.total,
        source_row=cost.source_row,
        location_factor=cost.location_factor,
        cost_year=cost.cost_year,
        trail=None if trail is None else CostTrailOut.model_validate(trail),
        trail_recorded=trail is not None,
    )


def asset_out(row: AssetRow, provenance: Provenance | None = None) -> AssetOut:
    asset, component = row.asset, row.component
    reasons = [r for r in (asset.review_reason or "").split("; ") if r]
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
        review_reasons=reasons,
        review_codes=codes_for_reasons(reasons),
        takeoff=takeoff_out(row.takeoff) if row.takeoff is not None else None,
        cost=cost_out(row.cost) if row.cost is not None else None,
        citations=row.citations,
        verified_citations=row.verified_citations,
        convention=(
            ConventionOut.model_validate(asset.convention)
            if isinstance(asset.convention, dict)
            else None
        ),
        provenance=provenance_out(provenance),
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
    provenance = await ProvenanceResolver(session, access.study.id).fields(
        [row.field for row in page.items]
    )
    return ExtractionFieldsPageOut(
        revision=page.revision,
        items=[field_out(row, provenance.get(row.field.id)) for row in page.items],
        next_cursor=page.next_cursor,
    )


@reads.get(
    "/studies/{study_id}/extraction",
    summary="The appraisal extraction at a revision: source, report PDF, issues, corpus",
    responses=problems(404),
)
async def get_extraction(
    access: ViewerStudy, session: SessionDep, revision: RevisionQuery = None
) -> ExtractionOut:
    at = _revision(access, revision)
    repo = OutputRepository(session)
    found = await repo.extraction(access.study.id, at)
    if found is None:
        raise NotFound(
            f"The study has no appraisal extraction at revision {at}.", code=E.STUDY_NO_EXTRACTION
        )
    extraction, located = found
    return extraction_out(extraction, located, await repo.study_corpus(access.study.id, at))


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
    resolver = ProvenanceResolver(session, access.study.id)
    rooms = await resolver.rooms([row.room for row in page.items])
    components = await resolver.components([c for row in page.items for c in row.inventory])
    photo_rows = await RoomRepository(session).photo_rooms(access.study.id, page.revision)
    photos = await resolver.photo_rooms(photo_rows)
    return RoomsPageOut(
        revision=page.revision,
        items=[room_out(row, rooms.get(row.room.id), photos, components) for row in page.items],
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
    provenance = await ProvenanceResolver(session, access.study.id).assets(
        [row.asset for row in page.items],
        {row.component.id: row.component for row in page.items if row.component is not None},
    )
    return AssetsPageOut(
        revision=page.revision,
        items=[asset_out(row, provenance.get(row.asset.id)) for row in page.items],
        next_cursor=page.next_cursor,
    )
