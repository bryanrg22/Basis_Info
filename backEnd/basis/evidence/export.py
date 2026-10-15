"""Serialize an evidence pack: canonical JSON and a CSV of assets, each with its sha256.

Both are byte-for-byte reproducible for the same pack: JSON with sorted keys and no
insignificant whitespace, CSV with a fixed header, fixed row order (the pack's), ``\\n``
line endings and no timestamps. The JSON lists every asset with bucket, recovery period,
quantity, cost, and its citations with excerpt and page reference.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import asdict
from typing import Any, Final

from basis.evidence.pack import AssetEvidence, EvidencePack

PACK_FORMAT: Final = "basis-evidence/1"
CSV_COLUMNS: Final = (
    "asset_id",
    "component_id",
    "room",
    "room_type",
    "label",
    "material",
    "attachment",
    "category",
    "bucket",
    "recovery_period",
    "irs_section",
    "method",
    "confidence",
    "needs_review",
    "review_reason",
    "quantity",
    "unit",
    "formula",
    "unit_cost",
    "total",
    "cost_year",
    "location_factor",
    "cost_source",
    "citation_count",
    "citations",
)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pack_document(pack: EvidencePack) -> dict[str, Any]:
    """The pack as a JSON-able dict: assets with their citations inline, plus groupings."""
    entries = {e.id: e for e in pack.entries}
    assets = []
    for asset in pack.assets:
        row = asdict(asset)
        row["citations"] = [
            {
                "id": entries[eid].id,
                "chunk_id": entries[eid].chunk_id,
                "doc_id": entries[eid].doc_id,
                "page_start": entries[eid].page_start,
                "page_end": entries[eid].page_end,
                "reference": entries[eid].reference(),
                "corpus": entries[eid].corpus,
                "excerpt": entries[eid].excerpt,
                "verification_method": entries[eid].verification_method,
            }
            for eid in asset.citations
        ]
        assets.append(row)
    return {
        "format": PACK_FORMAT,
        "study_id": pack.study_id,
        "revision": pack.revision,
        "assets": assets,
        "by_stage": pack.by_stage,
        "by_component": pack.by_component,
        "by_document": pack.by_document,
        "summary": pack.summary,
    }


def pack_to_json(pack: EvidencePack) -> bytes:
    return canonical_json(pack_document(pack))


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _csv_row(asset: AssetEvidence, pack: EvidencePack) -> list[str]:
    entries = {e.id: e for e in pack.entries}
    cited = " | ".join(
        f"{entries[eid].reference()}: {entries[eid].excerpt}" for eid in asset.citations
    )
    values: list[object] = [
        asset.id,
        asset.component_id,
        asset.room_name,
        asset.room_type,
        asset.label,
        asset.material,
        asset.attachment,
        asset.category,
        asset.bucket,
        asset.recovery_period,
        asset.irs_section,
        asset.method,
        asset.confidence,
        asset.needs_review,
        asset.review_reason,
        asset.quantity,
        asset.unit,
        asset.formula,
        asset.unit_cost,
        asset.total,
        asset.cost_year,
        asset.location_factor,
        asset.cost_source,
        len(asset.citations),
        cited,
    ]
    return [_cell(v) for v in values]


def pack_to_csv(pack: EvidencePack) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(CSV_COLUMNS)
    for asset in pack.assets:
        writer.writerow(_csv_row(asset, pack))
    return buffer.getvalue().encode("utf-8")
