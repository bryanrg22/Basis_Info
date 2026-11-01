"""``carry_revision``: the first activity of a resumed execution (docs/api/REVISIONS.md).

A completed study reopened at a review gate, or an earlier revision restored, starts a new
execution whose first revision is a **copy**: every revision-scoped row the gate shows is
copied from ``from_revision`` into ``to_revision`` with no corrections, each copy naming its
parent (``carried_from``) as :mod:`basis.activities.apply_corrections` does after a rejection,
so provenance, the audit listing and the revision diff read across the reopen. The rows of
``from_revision`` are never changed.

What is copied depends on the gate (``through``; :data:`basis.workflow.contracts.CARRIED_THROUGH`):

===================  =====================================================================
through              copied into the new revision
===================  =====================================================================
appraisal_review,    the extraction and its fields; the rooms, the photo assignments, the
rooms_review         per-photo components and the engineer-added components, the localized
                     objects. The reconciled room inventories are left behind: the room step
                     rebuilds them from the grouping the engineer approves.
components_review    the same, plus the room inventories (the room step finds them current).
assets_review        the same, plus the assets with their takeoffs, cost estimates and
                     citations, each asset on its component's copy (a concealed row, which
                     has no component, as it is).
===================  =====================================================================

Nothing is re-analyzed, re-localized or re-voted here. The output carries what the intake
would have told the workflow: the extraction source (``document_id``, which an appraisal
rejection's re-extraction needs) and the report PDF it was read with (from the extraction's
own record; None when it has none, and ``extract_fields`` then applies the companion rule).

Idempotent on its key (``run_once``): a retried attempt returns the stored output; the whole
copy is one transaction, so a failure leaves no partial revision.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import activity_key, heartbeat, parse_uuid, run_once
from basis.activities.apply_corrections import (
    CARRIED_FROM,
    _carry_extractions,
    _carry_rooms,
)
from basis.activities.appraisal_sources import recorded_pdf
from basis.activities.deps import Deps
from basis.db.models import Citation, Component, Extraction, PhotoObject
from basis.db.repositories.asset import AssetRepository
from basis.db.repositories.room import RoomRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.workflow.contracts import (
    CARRIED_THROUGH,
    CarryRevisionInput,
    CarryRevisionOutput,
    ReportPdf,
)

INVENTORIES: Final = "room_inventories"
ASSETS: Final = "assets"


class CarryRevision:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="carry_revision")
    async def run(self, input: CarryRevisionInput) -> CarryRevisionOutput:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> CarryRevisionOutput:
            return await carry_revision(session, study_id, input)

        return await run_once(
            self._deps,
            name="carry_revision",
            key=activity_key(input.idempotency_key),
            output_type=CarryRevisionOutput,
            work=work,
        )


async def carry_revision(
    session: AsyncSession, study_id: uuid.UUID, input: CarryRevisionInput
) -> CarryRevisionOutput:
    """The copy (module doc), in the caller's transaction, and its ``revision.carried``
    event."""
    carried = CARRIED_THROUGH[input.through]
    await _carry_extractions(session, study_id, input.from_revision, input.to_revision)
    extractions = len(
        (
            await session.scalars(
                select(Extraction.id).where(
                    Extraction.study_id == study_id, Extraction.revision == input.to_revision
                )
            )
        ).all()
    )
    document_id, report_pdf = await _extraction_source(session, study_id, input.from_revision)
    rooms = RoomRepository(session)
    await _carry_rooms(
        session,
        study_id,
        input.from_revision,
        input.to_revision,
        skip_photos=set(),
        room_corrections=[],
        component_corrections=[],
        added=[],
        moves=[],
        rebuild_inventories=INVENTORIES not in carried,
    )
    new_rooms = await rooms.list_for_revision(study_id, input.to_revision)
    new_components = await rooms.components(study_id, input.to_revision)
    # ``_carry_rooms`` carried the localizations and active objects with the rooms.
    objects = len(
        (
            await session.scalars(
                select(PhotoObject.id).where(
                    PhotoObject.study_id == study_id, PhotoObject.revision == input.to_revision
                )
            )
        ).all()
    )
    assets = 0
    if ASSETS in carried:
        assets = await _carry_assets(
            session, study_id, input.from_revision, input.to_revision, new_components
        )
    await StudyEventRepository(session).record(
        study_id,
        "revision.carried",
        actor="system",
        stage=input.through,
        revision=input.to_revision,
        detail={
            "from_revision": input.from_revision,
            "to_revision": input.to_revision,
            "through": input.through,
            "reason": input.reason,
            "applied": 0,
            "extractions": extractions,
            "rooms": len(new_rooms),
            "components": len(new_components),
            "objects": objects,
            "assets": assets,
        },
    )
    return CarryRevisionOutput(
        document_id=document_id,
        report_pdf=report_pdf,
        extractions=extractions,
        rooms=len(new_rooms),
        components=len(new_components),
        objects=objects,
        assets=assets,
    )


async def _extraction_source(
    session: AsyncSession, study_id: uuid.UUID, revision: int
) -> tuple[str | None, ReportPdf | None]:
    """The extraction source of ``revision`` (its first extraction's document) and the report
    PDF recorded on it; ``(None, None)`` for a revision with no extraction."""
    extraction = await session.scalar(
        select(Extraction)
        .where(Extraction.study_id == study_id, Extraction.revision == revision)
        .order_by(Extraction.created_at, Extraction.id)
        .limit(1)
    )
    if extraction is None:
        return None, None
    known, pdf_id = recorded_pdf(extraction.raw)
    report = ReportPdf(document_id=pdf_id) if known else None
    return str(extraction.document_id), report


async def _carry_assets(
    session: AsyncSession,
    study_id: uuid.UUID,
    from_revision: int,
    to_revision: int,
    new_components: list[Component],
) -> int:
    """Copy the assets of ``from_revision`` with their takeoffs, cost estimates and citations;
    an asset of a component goes on the component's copy (``raw.carried_from``), a concealed
    row (no component) is copied as it is. A component with no copy (its room was dropped: no
    photo, no engineer-added row) loses its asset with it."""
    copies: dict[uuid.UUID, uuid.UUID] = {}
    for component in new_components:
        raw = component.raw if isinstance(component.raw, dict) else {}
        parent = raw.get(CARRIED_FROM)
        if isinstance(parent, str):
            copies[uuid.UUID(parent)] = component.id
    repo = AssetRepository(session)
    old_assets = await repo.list_for_revision(study_id, from_revision)
    ids = [a.id for a in old_assets]
    takeoffs = await repo.takeoffs(ids)
    costs = await repo.cost_estimates(ids)
    by_asset: dict[uuid.UUID, list[Citation]] = {}
    for citation in await repo.citations(ids):
        by_asset.setdefault(citation.asset_id, []).append(citation)
    copied = 0
    for old in old_assets:
        heartbeat("asset")
        if old.component_id is not None and old.component_id not in copies:
            continue
        new = await repo.create(
            study_id=study_id,
            revision=to_revision,
            component_id=None if old.component_id is None else copies[old.component_id],
            category=old.category,
            bucket=old.bucket,
            recovery_period=old.recovery_period,
            irs_section=old.irs_section,
            method=old.method,
            confidence=old.confidence,
            needs_review=old.needs_review,
            review_reason=old.review_reason,
            convention=dict(old.convention) if isinstance(old.convention, dict) else None,
        )
        takeoff = takeoffs.get(old.id)
        if takeoff is not None:
            inputs = dict(takeoff.inputs) if isinstance(takeoff.inputs, dict) else takeoff.inputs
            await repo.add_takeoff(
                asset=new,
                quantity=takeoff.quantity,
                unit=takeoff.unit,
                formula=takeoff.formula,
                inputs=inputs,
            )
        cost = costs.get(old.id)
        if cost is not None:
            await repo.add_cost_estimate(
                asset=new,
                unit_cost=cost.unit_cost,
                total=cost.total,
                source_row=cost.source_row,
                location_factor=cost.location_factor,
                cost_year=cost.cost_year,
                trail=dict(cost.trail) if isinstance(cost.trail, dict) else None,
            )
        for citation in by_asset.get(old.id, []):
            row = await repo.add_citation(
                asset=new,
                chunk_id=citation.chunk_id,
                excerpt=citation.excerpt,
                verification_method=citation.verification_method,
            )
            row.verified_at = citation.verified_at
        copied += 1
    await session.flush()
    return copied
