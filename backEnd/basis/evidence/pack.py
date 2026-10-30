"""The evidence pack of one revision: every asset with its numbers and its cited passages.

Migrated from ``agentic/evidence/{aggregator,models}.py`` and the asset shaping in
``agentic/firestore/writeback.py``. :func:`assemble_pack` is a pure function over rows,
so the same rows always give the same pack; :func:`load_pack` reads the rows of one
revision from Postgres.

* Entries are deduplicated by ``(asset, chunk, normalized excerpt)``. Entry ids are
  derived from that key (legacy used random ids and ``hash()``, which differs per
  process), so the pack is reproducible.
* Entries are grouped by stage (the stage whose output they support; today only
  ``assets_review`` produces citations), by component and by document, as lists of entry
  ids in pack order.
* Every entry carries the chunk's document, pages and corpus (``reference`` or
  ``study``); a citation whose chunk is missing cannot exist (``citations.chunk_id`` is a
  foreign key), so every entry resolves.
* The summary has the legacy totals (citations, documents, assets without evidence), the
  writeback's per-bucket dollars and share of the total, ``basis``: the study result
  (:mod:`basis.evidence.result`): the basis worksheet from the study's intake and the
  schedule allocated to it, or the schedule alone with ``missing: ["intake"]``, and
  ``takeoff``: what the revision's quantities and prices were drawn from
  (:func:`takeoff_summary`), read back from the stored rows so it cannot disagree with
  them: the cost year and location factor with their sources, the property class with
  its source, and how many takeoffs took their area or quantity from each source.

Ordering is fixed: assets by room name, label, category and id; entries by asset order,
document, page, chunk id and excerpt.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.config import Settings, get_settings
from basis.db.models import (
    Asset,
    Chunk,
    Citation,
    Component,
    CorpusVersion,
    CostEstimate,
    Room,
    Takeoff,
)
from basis.db.repositories.asset import AssetRepository
from basis.db.repositories.room import RoomRepository
from basis.db.repositories.study import StudyRepository
from basis.domain.intake import StudyIntake
from basis.domain.structure import StructureInputs
from basis.evidence.result import (
    ScheduleRow,
    compute_result,
    concealed_label,
    load_structure_inputs,
)
from basis.retrieval.verify import normalize_text

EVIDENCE_STAGE: Final = "assets_review"
PERCENT: Final = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class ChunkInfo:
    doc_id: str
    page_start: int | None
    page_end: int | None
    corpus: str


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    id: str
    asset_id: str
    component_id: str | None
    component_label: str | None
    stage: str
    chunk_id: str
    doc_id: str
    page_start: int | None
    page_end: int | None
    corpus: str
    excerpt: str
    verification_method: str | None

    def reference(self) -> str:
        if self.page_start is None:
            return self.doc_id
        if self.page_end is None or self.page_end == self.page_start:
            return f"{self.doc_id}, p.{self.page_start}"
        return f"{self.doc_id}, pp.{self.page_start}-{self.page_end}"


@dataclass(frozen=True, slots=True)
class AssetEvidence:
    id: str
    component_id: str | None
    label: str | None
    material: str | None
    attachment: str | None
    room_id: str | None
    room_name: str | None
    room_type: str | None
    category: str
    bucket: str
    recovery_period: str
    irs_section: str | None
    method: str
    confidence: float | None
    needs_review: bool
    review_reason: str | None
    quantity: str | None
    unit: str | None
    formula: str | None
    takeoff_inputs: Any
    unit_cost: str | None
    total: str | None
    cost_year: int | None
    location_factor: str | None
    cost_source: str | None
    citations: tuple[str, ...]  # entry ids
    # The firm convention it was classified from (``assets.convention``), else None.
    convention: Any = None


@dataclass(frozen=True)
class EvidencePack:
    study_id: str
    revision: int
    assets: tuple[AssetEvidence, ...]
    entries: tuple[EvidenceEntry, ...]
    by_stage: dict[str, list[str]] = field(default_factory=dict)
    by_component: dict[str, list[str]] = field(default_factory=dict)
    by_document: dict[str, list[str]] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def entry(self, entry_id: str) -> EvidenceEntry:
        return next(e for e in self.entries if e.id == entry_id)


def _s(value: object | None) -> str | None:
    return None if value is None else str(value)


def entry_id(asset_id: str, chunk_id: str, excerpt: str) -> str:
    digest = hashlib.sha256(normalize_text(excerpt).encode()).hexdigest()[:12]
    return f"{asset_id}:{chunk_id}:{digest}"


def _variant(entry: EvidenceEntry) -> tuple[str, str]:
    return (entry.excerpt, entry.verification_method or "")


def _sort_key(asset: AssetEvidence) -> tuple[str, str, str, str]:
    return (asset.room_name or "", asset.label or "", asset.category, asset.id)


def assemble_pack(
    *,
    study_id: uuid.UUID | str,
    revision: int,
    assets: Iterable[Asset],
    components: Mapping[uuid.UUID, Component],
    rooms: Mapping[uuid.UUID, Room],
    takeoffs: Mapping[uuid.UUID, Takeoff],
    costs: Mapping[uuid.UUID, CostEstimate],
    citations: Iterable[Citation],
    chunks: Mapping[uuid.UUID, ChunkInfo],
    intake: StudyIntake | None = None,
    structure: StructureInputs | None = None,
) -> EvidencePack:
    """The pack for one revision from its rows (pure; no I/O)."""
    by_asset: dict[uuid.UUID, list[Citation]] = {}
    for citation in citations:
        by_asset.setdefault(citation.asset_id, []).append(citation)

    rows: list[tuple[AssetEvidence, list[EvidenceEntry]]] = []
    for asset in assets:
        component = components.get(asset.component_id) if asset.component_id else None
        room = rooms.get(component.room_id) if component and component.room_id else None
        takeoff = takeoffs.get(asset.id)
        cost = costs.get(asset.id)
        # A concealed row (the structure model) has no component: its label is on the takeoff.
        fallback_label, _concealed = concealed_label(takeoff.inputs if takeoff else None)
        entries: dict[str, EvidenceEntry] = {}
        for citation in by_asset.get(asset.id, []):
            info = chunks.get(citation.chunk_id)
            if info is None:  # unreachable with the FK; kept so a bad row cannot crash export
                continue
            eid = entry_id(str(asset.id), str(citation.chunk_id), citation.excerpt)
            candidate = EvidenceEntry(
                id=eid,
                asset_id=str(asset.id),
                component_id=_s(asset.component_id),
                component_label=component.label if component else None,
                stage=EVIDENCE_STAGE,
                chunk_id=str(citation.chunk_id),
                doc_id=info.doc_id,
                page_start=info.page_start,
                page_end=info.page_end,
                corpus=info.corpus,
                excerpt=citation.excerpt,
                verification_method=citation.verification_method,
            )
            # Duplicates (same chunk, same normalized excerpt) keep the smallest variant,
            # so the surviving text does not depend on row order.
            kept = entries.get(eid)
            if kept is None or _variant(candidate) < _variant(kept):
                entries[eid] = candidate
        ordered = sorted(
            entries.values(),
            key=lambda e: (e.doc_id, e.page_start or 0, e.chunk_id, normalize_text(e.excerpt)),
        )
        row = AssetEvidence(
            id=str(asset.id),
            component_id=_s(asset.component_id),
            label=component.label if component else fallback_label,
            material=component.material if component else None,
            attachment=component.attachment if component else None,
            room_id=_s(room.id) if room else None,
            room_name=room.name if room else None,
            room_type=room.room_type if room else None,
            category=asset.category,
            bucket=asset.bucket.value,
            recovery_period=str(asset.recovery_period),
            irs_section=asset.irs_section,
            method=asset.method.value,
            confidence=asset.confidence,
            needs_review=asset.needs_review,
            review_reason=asset.review_reason,
            quantity=_s(takeoff.quantity) if takeoff else None,
            unit=takeoff.unit if takeoff else None,
            formula=takeoff.formula if takeoff else None,
            takeoff_inputs=takeoff.inputs if takeoff else None,
            unit_cost=_s(cost.unit_cost) if cost else None,
            total=_s(cost.total) if cost else None,
            cost_year=cost.cost_year if cost else None,
            location_factor=_s(cost.location_factor) if cost else None,
            cost_source=cost.source_row if cost else None,
            citations=tuple(e.id for e in ordered),
            convention=asset.convention,
        )
        rows.append((row, ordered))

    rows.sort(key=lambda pair: _sort_key(pair[0]))
    all_entries = [e for _, entries in rows for e in entries]
    by_stage: dict[str, list[str]] = {}
    by_component: dict[str, list[str]] = {}
    by_document: dict[str, list[str]] = {}
    for e in all_entries:
        by_stage.setdefault(e.stage, []).append(e.id)
        by_component.setdefault(e.component_id or "", []).append(e.id)
        by_document.setdefault(e.doc_id, []).append(e.id)
    asset_rows = tuple(row for row, _ in rows)
    return EvidencePack(
        study_id=str(study_id),
        revision=revision,
        assets=asset_rows,
        entries=tuple(all_entries),
        by_stage=by_stage,
        by_component=dict(sorted(by_component.items())),
        by_document=dict(sorted(by_document.items())),
        summary=summarize(asset_rows, all_entries, intake, revision, structure),
    )


def summarize(
    assets: tuple[AssetEvidence, ...],
    entries: list[EvidenceEntry],
    intake: StudyIntake | None = None,
    revision: int = 1,
    structure: StructureInputs | None = None,
) -> dict[str, Any]:
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    for asset in assets:
        counts[asset.bucket] = counts.get(asset.bucket, 0) + 1
        if asset.total is not None:
            totals[asset.bucket] = totals.get(asset.bucket, Decimal(0)) + Decimal(asset.total)
    grand = sum(totals.values(), Decimal(0))
    by_bucket = {
        bucket: {
            "assets": counts[bucket],
            "total": str(totals.get(bucket, Decimal("0.00"))),
            "percent_of_total": (
                None
                if grand == 0
                else str(
                    (totals.get(bucket, Decimal(0)) * 100 / grand).quantize(
                        PERCENT, rounding=ROUND_HALF_UP
                    )
                )
            ),
        }
        for bucket in sorted(counts)
    }
    with_evidence = {e.asset_id for e in entries}
    return {
        "assets": len(assets),
        "needs_review": sum(a.needs_review for a in assets),
        "assets_without_cost": sorted(a.id for a in assets if a.total is None),
        "assets_without_evidence": sorted(a.id for a in assets if a.id not in with_evidence),
        "total_citations": len(entries),
        "unique_documents": len({e.doc_id for e in entries}),
        "total_cost": str(grand),
        "by_bucket": by_bucket,
        "by_method": {
            m: sum(a.method == m for a in assets) for m in sorted({a.method for a in assets})
        },
        "basis": compute_result(
            (
                ScheduleRow(
                    asset_id=a.id,
                    category=a.category,
                    bucket=a.bucket,
                    rcn=None if a.total is None else Decimal(a.total),
                    label=a.label,
                    concealed=concealed_label(a.takeoff_inputs)[1],
                )
                for a in assets
            ),
            intake,
            revision,
            structure,
        ).model_dump(mode="json"),
        "takeoff": takeoff_summary(assets),
    }


# The takeoff inputs ``process_assets`` writes on every takeoff when a source came from the
# study (EVAL_REPORT section 37); absent on rows priced from settings alone or before then.
STUDY_INPUT_KEYS: Final = (
    "cost_year_source",
    "location_source",
    "cost_year_assumption",
    "cost_year_conversion",
    "property_class",
    "property_class_source",
)


def takeoff_summary(assets: tuple[AssetEvidence, ...]) -> dict[str, Any]:
    """What the revision's takeoffs drew on, from the rows themselves.

    ``cost_year`` and ``location_factor`` are the cost rows' (one value per revision; null
    when the rows disagree or none has one), their sources and the property class the
    takeoff inputs' (null when no row carries them: priced from settings alone, or before
    section 37). ``area_sources`` counts the takeoffs by where their quantity came from:
    ``quantity_source`` (``appraisal_derived``: roof, siding, gutters, landscaping) else
    ``room_area_source`` (``gla_share``, ``basement_share``, ``garage_area``,
    ``typical_for_room_type``, ``default_150_sf``, ...), ``none`` for a row with no takeoff;
    ``needs_measurement`` counts the site items awaiting a measurement.
    """
    years = {a.cost_year for a in assets if a.cost_year is not None}
    factors = {a.location_factor for a in assets if a.location_factor is not None}
    study_inputs: dict[str, Any] = dict.fromkeys(STUDY_INPUT_KEYS)
    area_sources: dict[str, int] = {}
    needs_measurement = 0
    for asset in assets:
        inputs = asset.takeoff_inputs if isinstance(asset.takeoff_inputs, dict) else {}
        for key in STUDY_INPUT_KEYS:
            if study_inputs[key] is None and inputs.get(key) is not None:
                study_inputs[key] = inputs[key]
        source = inputs.get("quantity_source") or inputs.get("room_area_source") or "none"
        area_sources[str(source)] = area_sources.get(str(source), 0) + 1
        needs_measurement += "needs_measurement" in inputs
    return {
        "cost_year": next(iter(years)) if len(years) == 1 else None,
        "location_factor": next(iter(factors)) if len(factors) == 1 else None,
        **study_inputs,
        "area_sources": dict(sorted(area_sources.items())),
        "needs_measurement": needs_measurement,
    }


async def load_pack(
    session: AsyncSession,
    study_id: uuid.UUID,
    revision: int,
    settings: Settings | None = None,
) -> EvidencePack:
    """Read one revision's assets, takeoffs, costs, citations and cited chunks, the study's
    intake and the appraisal's structure inputs for the summary's basis (``settings`` for
    the cost index; the process settings when not given)."""
    study = await StudyRepository(session).get(study_id)
    intake = None if study is None else study.intake
    structure = None
    if (settings or get_settings()).structure_step == "on":
        structure = await load_structure_inputs(
            session, study_id, revision, intake, settings or get_settings()
        )
    repo = AssetRepository(session)
    assets = await repo.list_for_revision(study_id, revision)
    ids = [a.id for a in assets]
    rooms_repo = RoomRepository(session)
    components = {c.id: c for c in await rooms_repo.components(study_id, revision)}
    rooms = {r.id: r for r in await rooms_repo.list_for_revision(study_id, revision)}
    citations = await repo.citations(ids)
    chunk_ids = sorted({c.chunk_id for c in citations})
    chunks: dict[uuid.UUID, ChunkInfo] = {}
    if chunk_ids:
        result = await session.execute(
            select(Chunk.id, Chunk.doc_id, Chunk.page_start, Chunk.page_end, CorpusVersion.corpus)
            .join(CorpusVersion, CorpusVersion.id == Chunk.corpus_version_id)
            .where(Chunk.id.in_(chunk_ids))
        )
        for cid, doc_id, start, end, corpus in result.all():
            chunks[cid] = ChunkInfo(doc_id, start, end, corpus)
    return assemble_pack(
        study_id=study_id,
        revision=revision,
        assets=assets,
        components=components,
        rooms=rooms,
        takeoffs=await repo.takeoffs(ids),
        costs=await repo.cost_estimates(ids),
        citations=citations,
        chunks=chunks,
        intake=intake,
        structure=structure,
    )
