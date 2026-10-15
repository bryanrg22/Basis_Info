"""``export_report`` (Stage 2 body): the approved revision as one JSON document in storage.

Written to ``gs://<bucket>/studies/<study_id>/exports/rev-<n>.json``. The document is
canonical JSON (sorted keys, no insignificant whitespace, rows in a fixed order, no
timestamps), so exporting the same revision twice yields byte-identical output and the
same sha256; it also embeds a sha256 per section so a consumer can check each part. Stage
3/4 adds the PDF and the engineer's evidence pack next to it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import heartbeat, parse_uuid, run_once, validation_error
from basis.activities.deps import Deps
from basis.db.repositories.asset import AssetRepository
from basis.db.repositories.extraction import ExtractionRepository
from basis.db.repositories.room import RoomRepository
from basis.db.repositories.study import StudyRepository
from basis.storage.base import SCHEME
from basis.workflow.contracts import ExportReportInput, ExportReportOutput

FORMAT: Final = "basis-export/1"
CONTENT_TYPE: Final = "application/json"


def export_uri(bucket: str, study_id: uuid.UUID, revision: int) -> str:
    return f"{SCHEME}{bucket}/studies/{study_id}/exports/rev-{revision}.json"


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _id(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


async def build_export(session: AsyncSession, study_id: uuid.UUID, revision: int) -> bytes:
    study = await StudyRepository(session).get(study_id)
    if study is None:
        raise validation_error(f"study {study_id} not found")
    extractions_repo = ExtractionRepository(session)
    extractions = []
    for extraction in await extractions_repo.list_for_revision(study_id, revision):
        fields = await extractions_repo.fields(extraction.id)
        extractions.append(
            {
                "document_id": str(extraction.document_id),
                "method": extraction.method,
                "fields": [
                    {
                        "section": f.section,
                        "field": f.field,
                        "value_text": f.value_text,
                        "value_num": _num(f.value_num),
                        "value_date": f.value_date.isoformat() if f.value_date else None,
                        "source": f.source,
                    }
                    for f in fields
                ],
            }
        )
    heartbeat("extraction")
    rooms_repo = RoomRepository(session)
    rooms = [
        {"id": str(r.id), "name": r.name, "room_type": r.room_type, "floor": r.floor}
        for r in await rooms_repo.list_for_revision(study_id, revision)
    ]
    photos = [
        {"document_id": str(p.document_id), "room_id": str(p.room_id)}
        for p in await rooms_repo.photo_rooms(study_id, revision)
    ]
    components = [
        {
            "id": str(c.id),
            "document_id": _id(c.document_id),
            "room_id": _id(c.room_id),
            "label": c.label,
            "material": c.material,
            "attachment": c.attachment,
        }
        for c in await rooms_repo.components(study_id, revision)
    ]
    heartbeat("rooms")
    assets_repo = AssetRepository(session)
    asset_rows = await assets_repo.list_for_revision(study_id, revision)
    ids = [a.id for a in asset_rows]
    takeoffs = await assets_repo.takeoffs(ids)
    costs = await assets_repo.cost_estimates(ids)
    citations: dict[uuid.UUID, list[str]] = {}
    for citation in await assets_repo.citations(ids):
        citations.setdefault(citation.asset_id, []).append(str(citation.chunk_id))
    assets = []
    for a in asset_rows:
        takeoff = takeoffs.get(a.id)
        cost = costs.get(a.id)
        assets.append(
            {
                "id": str(a.id),
                "component_id": _id(a.component_id),
                "category": a.category,
                "bucket": a.bucket.value,
                "recovery_period": _num(a.recovery_period),
                "irs_section": a.irs_section,
                "method": a.method.value,
                "needs_review": a.needs_review,
                "takeoff": None
                if takeoff is None
                else {"quantity": _num(takeoff.quantity), "unit": takeoff.unit},
                "cost": None
                if cost is None
                else {"unit_cost": _num(cost.unit_cost), "total": _num(cost.total)},
                "citations": sorted(citations.get(a.id, [])),
            }
        )
    heartbeat("assets")
    sections = {
        "extraction": extractions,
        "rooms": rooms,
        "photos": photos,
        "components": components,
        "assets": assets,
    }
    document = {
        "format": FORMAT,
        "study_id": str(study_id),
        "property_name": study.property_name,
        "revision": revision,
        **sections,
        "checksums": {
            name: hashlib.sha256(canonical_json(rows)).hexdigest()
            for name, rows in sections.items()
        },
    }
    return canonical_json(document)


class ExportReport:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="export_report")
    async def run(self, input: ExportReportInput) -> ExportReportOutput:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> ExportReportOutput:
            data = await build_export(session, study_id, input.revision)
            uri = export_uri(self._deps.bucket, study_id, input.revision)
            # Written before the completion commits: if the commit fails the retry
            # rewrites the same bytes, so the object is never half of two versions.
            await self._deps.storage.put(uri, data, CONTENT_TYPE)
            return ExportReportOutput(export_uri=uri, sha256=hashlib.sha256(data).hexdigest())

        return await run_once(
            self._deps,
            name="export_report",
            key=input.idempotency_key,
            output_type=ExportReportOutput,
            work=work,
        )
