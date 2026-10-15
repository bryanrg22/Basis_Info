"""``export_report``: the approved revision as canonical JSON plus a CSV of its assets.

Written to ``gs://<bucket>/studies/<study_id>/exports/rev-<n>.json`` and
``rev-<n>.assets.csv``. The JSON holds the extraction, rooms, photo assignments,
components, and the evidence pack (:mod:`basis.evidence`): every asset with bucket,
recovery period, quantity, cost and its citations (excerpt, document, pages), the
groupings by stage, component and document, and the per-bucket summary. It names the CSV
and the CSV's sha256 (``files``).

Both files are canonical (sorted keys, no insignificant whitespace, rows in a fixed order,
no timestamps), so exporting the same revision twice yields byte-identical files and the
same sha256; the JSON also embeds a sha256 per section so a consumer can check each part.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import heartbeat, parse_uuid, run_once, validation_error
from basis.activities.deps import Deps
from basis.db.repositories.extraction import ExtractionRepository
from basis.db.repositories.room import RoomRepository
from basis.db.repositories.study import StudyRepository
from basis.evidence.export import pack_document, pack_to_csv, sha256_hex
from basis.evidence.pack import load_pack
from basis.storage.base import SCHEME
from basis.workflow.contracts import ExportReportInput, ExportReportOutput

FORMAT: Final = "basis-export/2"
CONTENT_TYPE: Final = "application/json"
CSV_CONTENT_TYPE: Final = "text/csv"


def export_uri(bucket: str, study_id: uuid.UUID, revision: int) -> str:
    return f"{SCHEME}{bucket}/studies/{study_id}/exports/rev-{revision}.json"


def csv_uri(bucket: str, study_id: uuid.UUID, revision: int) -> str:
    return f"{SCHEME}{bucket}/studies/{study_id}/exports/rev-{revision}.assets.csv"


@dataclass(frozen=True, slots=True)
class ExportFiles:
    json: bytes
    csv: bytes


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _id(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


async def build_export(
    session: AsyncSession, study_id: uuid.UUID, revision: int, csv_location: str = ""
) -> ExportFiles:
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
    pack = await load_pack(session, study_id, revision)
    evidence = pack_document(pack)
    heartbeat("assets")
    sections = {
        "extraction": extractions,
        "rooms": rooms,
        "photos": photos,
        "components": components,
        "assets": evidence["assets"],
        "evidence": {
            "by_stage": evidence["by_stage"],
            "by_component": evidence["by_component"],
            "by_document": evidence["by_document"],
        },
        "summary": evidence["summary"],
    }
    csv_bytes = pack_to_csv(pack)
    document = {
        "format": FORMAT,
        "study_id": str(study_id),
        "property_name": study.property_name,
        "revision": revision,
        **sections,
        "files": {"assets_csv": {"uri": csv_location, "sha256": sha256_hex(csv_bytes)}},
        "checksums": {
            name: hashlib.sha256(canonical_json(rows)).hexdigest()
            for name, rows in sections.items()
        },
    }
    return ExportFiles(json=canonical_json(document), csv=csv_bytes)


class ExportReport:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="export_report")
    async def run(self, input: ExportReportInput) -> ExportReportOutput:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> ExportReportOutput:
            table_uri = csv_uri(self._deps.bucket, study_id, input.revision)
            files = await build_export(session, study_id, input.revision, table_uri)
            uri = export_uri(self._deps.bucket, study_id, input.revision)
            # Written before the completion commits: if the commit fails the retry
            # rewrites the same bytes, so the objects are never half of two versions.
            await self._deps.storage.put(table_uri, files.csv, CSV_CONTENT_TYPE)
            await self._deps.storage.put(uri, files.json, CONTENT_TYPE)
            return ExportReportOutput(export_uri=uri, sha256=sha256_hex(files.json))

        return await run_once(
            self._deps,
            name="export_report",
            key=input.idempotency_key,
            output_type=ExportReportOutput,
            work=work,
        )
