"""``export_report``: the approved revision as canonical JSON plus a CSV of its assets.

Written to ``gs://<bucket>/studies/<study_id>/exports/rev-<n>.json`` and
``rev-<n>.assets.csv``. The JSON holds the extraction, rooms, photo assignments,
components, and the evidence pack (:mod:`basis.evidence`): every asset with bucket,
recovery period, quantity, cost and its citations (excerpt, document, pages), the
groupings by stage, component and document, and the per-bucket summary. It names the CSV
and the CSV's sha256 (``files``).

Every row carries its ``provenance`` (who produced it: the model, or an engineer's
addition or correction, with the correction; :mod:`basis.db.repositories.provenance`), and
``summary.timeline`` is the study's history up to the export step (events, steps with
durations, stage visits; :mod:`basis.audit.timeline`, docs/api/TIMELINE_AND_AUDIT.md). The
export step's own events (its ``export_report`` attempts, ``export.written``) are left out,
so that exporting the same revision twice still yields byte-identical files: a retried or
repeated export would otherwise differ only by the record of the earlier attempt. The API's
timeline has them.

Both files are canonical (sorted keys, no insignificant whitespace, rows in a fixed order,
no timestamps of the export itself), so exporting the same revision twice yields byte-identical
files and the same sha256; the JSON also embeds a sha256 per section so a consumer can check
each part.
The spend (EVAL_REPORT section 40): ``summary.spend`` is the study's paid-call rollup (the
whole study, by stage, activity, component, model and kind: calls, tokens of every kind,
dollars, latency), and ``rev-<n>.calls.jsonl`` is the transcript, one JSON line per paid
call, oldest first, with the call's model, parameters (effort, temperature, output cap),
tokens, cost and latency beside the prompt as sent (images by ref and size) and the raw
response, read back from the ledger's stored payloads. The ledger is append-only and the
export makes no paid call, so two exports with no call between them are byte-identical.
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
from basis.audit.timeline import timeline_document
from basis.config import Settings
from basis.db.enums import Stage
from basis.db.models import LlmCall
from basis.db.repositories.asset import AssetRepository
from basis.db.repositories.extraction import ExtractionRepository
from basis.db.repositories.llm_call import LlmCallRepository, Spend, SpendGroup
from basis.db.repositories.provenance import MODEL, Provenance, ProvenanceResolver, provenance_json
from basis.db.repositories.room import RoomRepository
from basis.db.repositories.study import StudyRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.errors.codes import E
from basis.evidence.export import pack_document, pack_to_csv, sha256_hex
from basis.evidence.pack import load_pack
from basis.storage import ObjectNotFound, Storage
from basis.storage.base import SCHEME
from basis.workflow.contracts import ExportReportInput, ExportReportOutput

FORMAT: Final = "basis-export/2"
# The export step's own events stay out of the exported timeline (module doc).
EXPORT_ACTIVITY: Final = "export_report"
EXPORT_EVENT: Final = "export.written"
CONTENT_TYPE: Final = "application/json"
CSV_CONTENT_TYPE: Final = "text/csv"
JSONL_CONTENT_TYPE: Final = "application/jsonl"


def export_uri(bucket: str, study_id: uuid.UUID, revision: int) -> str:
    return f"{SCHEME}{bucket}/studies/{study_id}/exports/rev-{revision}.json"


def csv_uri(bucket: str, study_id: uuid.UUID, revision: int) -> str:
    return f"{SCHEME}{bucket}/studies/{study_id}/exports/rev-{revision}.assets.csv"


def calls_uri(bucket: str, study_id: uuid.UUID, revision: int) -> str:
    return f"{SCHEME}{bucket}/studies/{study_id}/exports/rev-{revision}.calls.jsonl"


@dataclass(frozen=True, slots=True)
class ExportFiles:
    json: bytes
    csv: bytes
    calls: bytes = b""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _id(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def _provenance(found: dict[uuid.UUID, Provenance], key: uuid.UUID) -> dict[str, Any]:
    return provenance_json(found.get(key, MODEL))


def spend_line(group: SpendGroup) -> dict[str, Any]:
    return {
        "key": group.key,
        "calls": group.calls,
        "errors": group.errors,
        "input_tokens": group.input_tokens,
        "cache_read_input_tokens": group.cache_read_input_tokens,
        "cache_creation_input_tokens": group.cache_creation_input_tokens,
        "output_tokens": group.output_tokens,
        "reasoning_output_tokens": group.reasoning_output_tokens,
        "cost_usd": str(group.cost_usd),
        "latency_ms_total": group.latency_ms_total,
        "latency_ms_max": group.latency_ms_max,
        "quantity": _num(group.quantity),
    }


def spend_document(spend: Spend) -> dict[str, Any]:
    """``summary.spend``: the rollup as the API serves it (``GET /v1/studies/{id}/spend``)."""
    return {
        "total": spend_line(spend.total),
        "by_stage": [spend_line(g) for g in spend.by_stage],
        "by_activity": [spend_line(g) for g in spend.by_activity],
        "by_component": [spend_line(g) for g in spend.by_component],
        "by_model": [spend_line(g) for g in spend.by_model],
        "by_kind": [spend_line(g) for g in spend.by_kind],
    }


def call_line(row: LlmCall, request: Any, response: Any) -> dict[str, Any]:
    """One transcript line: the ledger row's fields and the stored payloads."""
    return {
        "id": str(row.id),
        "created_at": row.created_at.isoformat(),
        "kind": row.kind,
        "status": row.status,
        "error": row.error,
        "stage": row.stage,
        "revision": row.revision,
        "activity": row.activity,
        "activity_attempt": row.activity_attempt,
        "component": row.component,
        "entity_type": row.entity_type,
        "entity_id": _id(row.entity_id),
        "provider": row.provider,
        "endpoint": row.endpoint,
        "requested_model": row.requested_model,
        "model": row.model,
        "response_id": row.response_id,
        "finish_reason": row.finish_reason,
        "prompt_version": row.prompt_version,
        "prompt_sha256": row.prompt_sha256,
        "reasoning_effort": row.reasoning_effort,
        "temperature": row.temperature,
        "max_output_tokens": row.max_output_tokens,
        "response_format": row.response_format,
        "request_params": row.request_params,
        "input_tokens": row.input_tokens,
        "cache_read_input_tokens": row.cache_read_input_tokens,
        "cache_creation_input_tokens": row.cache_creation_input_tokens,
        "output_tokens": row.output_tokens,
        "reasoning_output_tokens": row.reasoning_output_tokens,
        "quantity": _num(row.quantity),
        "unit": row.unit,
        "unit_price_usd": _num(row.unit_price_usd),
        "cost_usd": str(row.cost_usd),
        "price_row": row.price_row,
        "pricing_version": row.pricing_version,
        "latency_ms": row.latency_ms,
        "elapsed_ms": row.elapsed_ms,
        "build_sha": row.build_sha,
        "request_uri": row.request_uri,
        "response_uri": row.response_uri,
        "request": request,
        "response": response,
    }


async def _payload(storage: Storage | None, uri: str | None) -> Any:
    if storage is None or uri is None:
        return None
    try:
        return json.loads(await storage.read(uri))
    except (ObjectNotFound, ValueError):
        return None


async def build_transcript(
    session: AsyncSession, study_id: uuid.UUID, storage: Storage | None
) -> bytes:
    """``rev-<n>.calls.jsonl``: every paid call of the study, oldest first (module doc)."""
    lines = []
    for row in await LlmCallRepository(session).list_for_study(study_id):
        heartbeat("calls")
        request = await _payload(storage, row.request_uri)
        response = await _payload(storage, row.response_uri)
        lines.append(canonical_json(call_line(row, request, response)))
    return b"".join(line + b"\n" for line in lines)


async def build_export(
    session: AsyncSession,
    study_id: uuid.UUID,
    revision: int,
    csv_location: str = "",
    *,
    storage: Storage | None = None,
    calls_location: str = "",
    settings: Settings | None = None,
) -> ExportFiles:
    study = await StudyRepository(session).get(study_id)
    if study is None:
        raise validation_error(f"study {study_id} not found", E.EXPORT_STUDY_NOT_FOUND)
    resolver = ProvenanceResolver(session, study_id)
    extractions_repo = ExtractionRepository(session)
    extractions = []
    for extraction in await extractions_repo.list_for_revision(study_id, revision):
        fields = await extractions_repo.fields(extraction.id)
        field_provenance = await resolver.fields(fields)
        extractions.append(
            {
                "document_id": str(extraction.document_id),
                "method": extraction.method,
                "fields": [
                    {
                        "id": str(f.id),
                        "section": f.section,
                        "field": f.field,
                        "value_text": f.value_text,
                        "value_num": _num(f.value_num),
                        "value_date": f.value_date.isoformat() if f.value_date else None,
                        "source": f.source,
                        "provenance": _provenance(field_provenance, f.id),
                    }
                    for f in fields
                ],
            }
        )
    heartbeat("extraction")
    rooms_repo = RoomRepository(session)
    room_rows = await rooms_repo.list_for_revision(study_id, revision)
    room_provenance = await resolver.rooms(room_rows)
    rooms = [
        {
            "id": str(r.id),
            "name": r.name,
            "room_type": r.room_type,
            "floor": r.floor,
            "provenance": _provenance(room_provenance, r.id),
        }
        for r in room_rows
    ]
    photo_rows = await rooms_repo.photo_rooms(study_id, revision)
    photo_provenance = await resolver.photo_rooms(photo_rows)
    photos = [
        {
            "document_id": str(p.document_id),
            "room_id": str(p.room_id),
            "provenance": _provenance(photo_provenance, p.document_id),
        }
        for p in photo_rows
    ]
    component_rows = await rooms_repo.components(study_id, revision)
    component_provenance = await resolver.components(component_rows)
    components = [
        {
            "id": str(c.id),
            "document_id": _id(c.document_id),
            "room_id": _id(c.room_id),
            "label": c.label,
            "material": c.material,
            "attachment": c.attachment,
            "provenance": _provenance(component_provenance, c.id),
        }
        for c in component_rows
    ]
    heartbeat("rooms")
    pack = await load_pack(session, study_id, revision, settings)
    evidence = pack_document(pack)
    asset_rows = await AssetRepository(session).list_for_revision(study_id, revision)
    asset_provenance = await resolver.assets(asset_rows, {c.id: c for c in component_rows})
    for entry in evidence["assets"]:
        entry["provenance"] = _provenance(asset_provenance, uuid.UUID(entry["id"]))
    events = (await StudyEventRepository(session).list_for_study(study_id)).items
    evidence["summary"]["timeline"] = timeline_document(
        [e for e in events if e.activity != EXPORT_ACTIVITY and e.event_type != EXPORT_EVENT]
    )
    heartbeat("assets")
    spend = await LlmCallRepository(session).spend(study_id)
    calls_bytes = await build_transcript(session, study_id, storage)
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
        "summary": {**evidence["summary"], "spend": spend_document(spend)},
    }
    csv_bytes = pack_to_csv(pack)
    document = {
        "format": FORMAT,
        "study_id": str(study_id),
        "property_name": study.property_name,
        "revision": revision,
        **sections,
        "files": {
            "assets_csv": {"uri": csv_location, "sha256": sha256_hex(csv_bytes)},
            "calls_jsonl": {"uri": calls_location, "sha256": sha256_hex(calls_bytes)},
        },
        "checksums": {
            name: hashlib.sha256(canonical_json(rows)).hexdigest()
            for name, rows in sections.items()
        },
    }
    return ExportFiles(json=canonical_json(document), csv=csv_bytes, calls=calls_bytes)


class ExportReport:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="export_report")
    async def run(self, input: ExportReportInput) -> ExportReportOutput:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> ExportReportOutput:
            table_uri = csv_uri(self._deps.bucket, study_id, input.revision)
            transcript_uri = calls_uri(self._deps.bucket, study_id, input.revision)
            files = await build_export(
                session,
                study_id,
                input.revision,
                table_uri,
                storage=self._deps.storage,
                calls_location=transcript_uri,
                settings=self._deps.settings,
            )
            uri = export_uri(self._deps.bucket, study_id, input.revision)
            # Written before the completion commits: if the commit fails the retry
            # rewrites the same bytes, so the objects are never half of two versions.
            await self._deps.storage.put(table_uri, files.csv, CSV_CONTENT_TYPE)
            await self._deps.storage.put(transcript_uri, files.calls, JSONL_CONTENT_TYPE)
            await self._deps.storage.put(uri, files.json, CONTENT_TYPE)
            digest = sha256_hex(files.json)
            await StudyEventRepository(session).record(
                study_id,
                "export.written",
                actor="system",
                stage=Stage.EXPORTING,
                revision=input.revision,
                detail={"uri": uri, "sha256": digest, "assets_csv": table_uri},
            )
            return ExportReportOutput(export_uri=uri, sha256=digest)

        return await run_once(
            self._deps,
            name="export_report",
            key=input.idempotency_key,
            output_type=ExportReportOutput,
            work=work,
        )
