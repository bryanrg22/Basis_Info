"""``process_assets``: classify, measure and price every component of a revision.

For each component of the revision (S3_SPEC section 5):

1. classify it (:func:`basis.classify.router.classify`: rules table, then the approved
   cache, then the bounded residual model), with ``settings.classify_concurrency``
   components in flight;
2. measure it (:func:`basis.domain.takeoffs.estimate_quantity`, room area from the room
   type's typical area until rooms carry a measured area). Area and perimeter takeoffs
   are measured once per room and takeoff row: a second photo of the same kitchen floor
   gets quantity 0 with a formula naming the component that carries the area;
3. price it (:func:`basis.domain.costs.estimate_cost`, cost year and location factor from
   settings); a missing cost row writes a ``cost_estimates`` row with no amounts;
4. cross-validate classification, takeoff and cost; warnings and errors set
   ``needs_review`` and every reason is kept in ``review_reason``;
5. write ``assets``, ``takeoffs``, ``cost_estimates``, ``citations`` (verified ones only)
   and one ``llm_calls`` row per model call, all in the activity's one transaction.

Idempotent per revision: when assets already exist for the revision (an earlier execution,
a workflow reset), nothing is recomputed and the stored counts are returned.

Budget: before any model call, the worst case for the components that need the model
(``max_iterations`` reservations each) must fit in ``budget_remaining_usd``; otherwise the
non-retryable ``BudgetExceededError`` is raised up front and nothing is written (the
workflow flags the stage and continues to review). The output's ``cost_usd`` is what the
model calls actually cost. Without a wired provider (``Deps.chat_provider is None``)
nothing is reserved and residual components are written ``classifier_unavailable``.

``asset`` corrections (an assets rejection) name an asset of the rejected revision; the
new revision's component carried from it (``carried_from``, written by
``apply_corrections``) takes the old asset's values overlaid with the correction's
``after``, ``method="manual"``, without a model call. The applied correction is recorded
in the takeoff's ``inputs`` (``asset_correction``).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import (
    budget_exceeded,
    heartbeat,
    parse_uuid,
    run_once,
    validation_error,
    workflow_execution,
)
from basis.activities.apply_corrections import CARRIED_FROM
from basis.activities.corrections import check_asset, parse_bucket, parse_recovery_period
from basis.activities.deps import Deps
from basis.classify.cache import ClassificationCacheRepository, cache_key
from basis.classify.residual import CallRecord
from basis.classify.router import ClassificationResult, ClassifyContext, classify, route
from basis.classify.schemas import ComponentView
from basis.classify.verify import VerifiedCitation
from basis.db.enums import ClassificationMethod, Corpus
from basis.db.models import Asset, Component, CorpusVersion, LlmCall, Room
from basis.db.repositories.asset import AssetRepository
from basis.db.repositories.room import RoomRepository
from basis.db.repositories.run import RunRepository
from basis.domain.costs import CostError, CostEstimate, estimate_cost
from basis.domain.cross_validation import cross_validate
from basis.domain.rooms import guidance_for, typical_area_sf
from basis.domain.rules import RECOVERY_YEARS, lookup
from basis.domain.takeoffs import Takeoff, estimate_quantity, takeoff_key
from basis.domain.validators import validate_classification
from basis.llm.cost import cost_usd
from basis.llm.provider import Budget
from basis.workflow.contracts import CorrectionInput, ProcessAssetsInput, ProcessAssetsOutput

ACTIVITY: Final = "process_assets"
METHODS: Final = {
    "rule": ClassificationMethod.RULE,
    "cache": ClassificationMethod.CACHE,
    "llm": ClassificationMethod.LLM,
}


@dataclass(frozen=True, slots=True)
class Override:
    """An engineer's correction of the rejected revision's asset for this component."""

    old: Asset
    after: dict[str, Any]


class ProcessAssets:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="process_assets")
    async def run(self, input: ProcessAssetsInput) -> ProcessAssetsOutput:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> ProcessAssetsOutput:
            return await _process(self._deps, session, study_id, input)

        return await run_once(
            self._deps,
            name=ACTIVITY,
            key=input.idempotency_key,
            output_type=ProcessAssetsOutput,
            work=work,
        )


async def _process(
    deps: Deps, session: AsyncSession, study_id: uuid.UUID, input: ProcessAssetsInput
) -> ProcessAssetsOutput:
    assets = AssetRepository(session)
    existing = await assets.list_for_revision(study_id, input.revision)
    if existing:
        return ProcessAssetsOutput(
            assets=len(existing), needs_review=sum(a.needs_review for a in existing)
        )
    settings = deps.settings
    rooms_repo = RoomRepository(session)
    components = await rooms_repo.components(study_id, input.revision)
    rooms = {r.id: r for r in await rooms_repo.list_for_revision(study_id, input.revision)}
    overrides = await _overrides(assets, study_id, components, input.corrections)
    property_class = settings.property_class
    views = {
        c.id: component_view(c, rooms.get(c.room_id) if c.room_id else None, property_class)
        for c in components
        if c.id not in overrides
    }
    misses = {
        cache_key(v.label, v.material, v.attachment, property_class)
        for v in views.values()
        if lookup(v.label, v.material, v.attachment, property_class) is None
    }
    cache_repo = ClassificationCacheRepository(session)
    reference_version, study_version = await corpus_versions(session, study_id)
    budget = Budget(remaining_usd=max(input.budget_remaining_usd, 0.0))
    ctx = ClassifyContext(
        study_id=str(study_id),
        property_class=property_class,
        budget=budget,
        store=deps.chunk_store,
        reference_version_id=reference_version,
        study_version_id=study_version,
        provider=deps.chat_provider,
        cache=await cache_repo.get_many(misses),
        model=settings.classify_model,
        price=cost_usd,
    )
    residual = [v for v in views.values() if route(v, ctx)[0] == "llm"]
    if residual and deps.chat_provider is not None:
        worst = round(len(residual) * ctx.limits.worst_case_usd(ctx.reserve_usd_per_call), 6)
        if worst > input.budget_remaining_usd:
            raise budget_exceeded(
                f"{len(residual)} components need the model (up to ${worst}); "
                f"${input.budget_remaining_usd:.4f} remains"
            )

    results = await _classify_all(list(views.values()), ctx, settings.classify_concurrency)
    measured: dict[tuple[uuid.UUID | None, str], uuid.UUID] = {}
    cache_hits: Counter[str] = Counter()
    calls: list[CallRecord] = []
    needs_review = 0
    for component in components:
        heartbeat(str(component.id))
        room = rooms.get(component.room_id) if component.room_id else None
        override = overrides.get(component.id)
        if override is not None:
            asset, rule_key, extra_inputs = await _write_manual(
                assets, component, input.revision, override, property_class, room
            )
            citations: tuple[VerifiedCitation, ...] = ()
        else:
            result = results[component.id]
            calls.extend(result.calls)
            if result.cache_key is not None:
                cache_hits[result.cache_key] += 1
            asset = await _write_classified(assets, component, input.revision, result)
            rule_key, extra_inputs = result.rule_key, {}
            citations = result.citations
        takeoff = _takeoff(component, room, rule_key, property_class, measured, asset.id)
        cost = _cost(takeoff, settings.cost_year, settings.cost_location_factor)
        issues = [
            i
            for i in cross_validate(
                section=asset.irs_section, bucket=asset.bucket, takeoff=takeoff, cost=cost
            )
            if i.needs_review
        ]
        if issues:
            reasons = [r for r in (asset.review_reason or "").split("; ") if r]
            reasons += [f"cross:{i.code}" for i in issues]
            asset.needs_review = True
            asset.review_reason = "; ".join(dict.fromkeys(reasons))
        await assets.add_takeoff(
            asset=asset,
            quantity=takeoff.quantity,
            unit=takeoff.unit,
            formula=takeoff.formula,
            inputs={
                **takeoff.inputs,
                "method": takeoff.method,
                "assumptions": list(takeoff.assumptions),
                **extra_inputs,
            },
        )
        await assets.add_cost_estimate(
            asset=asset,
            unit_cost=None if cost is None else cost.unit_cost,
            total=None if cost is None else cost.total,
            source_row=_source_row(takeoff, cost),
            location_factor=Decimal(str(settings.cost_location_factor)),
            cost_year=settings.cost_year,
        )
        for citation in citations:
            row = await assets.add_citation(
                asset=asset,
                chunk_id=citation.chunk_id,
                excerpt=citation.excerpt,
                verification_method=citation.method,
            )
            row.verified_at = datetime.now(UTC)
        needs_review += asset.needs_review
    await session.flush()
    await cache_repo.record_hits(cache_hits)
    await _record_calls(session, study_id, calls)
    return ProcessAssetsOutput(
        assets=len(components), needs_review=needs_review, cost_usd=round(budget.spent_usd, 6)
    )


def component_view(component: Component, room: Room | None, property_class: str) -> ComponentView:
    raw = component.raw if isinstance(component.raw, dict) else {}
    notes = raw.get("notes")
    guidance = guidance_for(room.room_type if room else None)
    return ComponentView(
        component_id=str(component.id),
        label=component.label,
        material=component.material,
        attachment=component.attachment,
        room_type=room.room_type if room else None,
        room_name=room.name if room else None,
        property_class=property_class,  # type: ignore[arg-type]
        count=_count(component),
        notes=notes[:2000] if isinstance(notes, str) else None,
        room_guidance=guidance.guidance if guidance.found else None,
    )


def _count(component: Component) -> int:
    raw = component.raw if isinstance(component.raw, dict) else {}
    count = raw.get("count")
    return count if isinstance(count, int) and not isinstance(count, bool) and count >= 1 else 1


async def corpus_versions(
    session: AsyncSession, study_id: uuid.UUID
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """``(newest published reference version, newest study version)``; ``None`` if absent."""
    reference = await session.scalar(
        select(CorpusVersion.id)
        .where(
            CorpusVersion.corpus == Corpus.REFERENCE.value,
            CorpusVersion.published_at.is_not(None),
        )
        .order_by(CorpusVersion.version.desc())
        .limit(1)
    )
    study = await session.scalar(
        select(CorpusVersion.id)
        .where(CorpusVersion.corpus == Corpus.STUDY.value, CorpusVersion.study_id == study_id)
        .order_by(CorpusVersion.version.desc())
        .limit(1)
    )
    return reference, study


async def _classify_all(
    views: list[ComponentView], ctx: ClassifyContext, concurrency: int
) -> dict[uuid.UUID, ClassificationResult]:
    gate = asyncio.Semaphore(concurrency)

    async def one(view: ComponentView) -> tuple[uuid.UUID, ClassificationResult]:
        async with gate:
            result = await classify(view, ctx)
            heartbeat(view.component_id)
            return uuid.UUID(view.component_id), result

    return dict(await asyncio.gather(*(one(v) for v in views)))


async def _write_classified(
    assets: AssetRepository, component: Component, revision: int, result: ClassificationResult
) -> Asset:
    return await assets.create(
        study_id=component.study_id,
        revision=revision,
        component_id=component.id,
        category=result.category,
        bucket=result.bucket,
        recovery_period=result.recovery_period,
        irs_section=result.irs_section,
        method=METHODS[result.method],
        confidence=result.confidence,
        needs_review=result.needs_review,
        review_reason=result.review_reason,
    )


async def _write_manual(
    assets: AssetRepository,
    component: Component,
    revision: int,
    override: Override,
    property_class: str,
    room: Room | None,
) -> tuple[Asset, str | None, dict[str, Any]]:
    old, after = override.old, override.after
    bucket = parse_bucket(after.get("bucket", old.bucket.value))
    if "recovery_period" in after:
        recovery = parse_recovery_period(after["recovery_period"])
    elif bucket is old.bucket:
        recovery = old.recovery_period
    elif bucket in RECOVERY_YEARS:
        recovery = RECOVERY_YEARS[bucket]
    else:
        raise validation_error(f"asset {old.id}: a {bucket.value} correction needs recovery_period")
    section = after.get("irs_section", old.irs_section)
    issues = validate_classification(
        label=component.label,
        section=None if section is None else str(section),
        bucket=bucket,
        recovery_period=recovery,
        property_class=property_class,
        room_type=room.room_type if room else None,
    )
    reasons = [f"validation:{i.code}" for i in issues if i.needs_review]
    asset = await assets.create(
        study_id=component.study_id,
        revision=revision,
        component_id=component.id,
        category=str(after.get("category", old.category)),
        bucket=bucket,
        recovery_period=recovery,
        irs_section=None if section is None else str(section),
        method=ClassificationMethod.MANUAL,
        confidence=1.0,
        needs_review=bool(reasons),
        review_reason="; ".join(reasons) or None,
    )
    hit = lookup(component.label, component.material, component.attachment, property_class)
    record = {"asset_correction": {"from_asset_id": str(old.id), "after": after}}
    return asset, None if hit is None else hit.key, record


def _takeoff(
    component: Component,
    room: Room | None,
    rule_key: str | None,
    property_class: str,
    measured: dict[tuple[uuid.UUID | None, str], uuid.UUID],
    asset_id: uuid.UUID,
) -> Takeoff:
    area, known = typical_area_sf(room.room_type if room else None, property_class)
    takeoff = estimate_quantity(rule_key or component.label, area, _count(component))
    inputs: dict[str, str | int | bool | None] = {
        **takeoff.inputs,
        "room_area_source": "typical_for_room_type" if known else "default_150_sf",
    }
    if not takeoff.is_measured:
        return Takeoff(**{**_fields(takeoff), "inputs": inputs})
    key = (component.room_id, takeoff_key(takeoff.component))
    first = measured.setdefault(key, asset_id)
    if first == asset_id:
        return Takeoff(**{**_fields(takeoff), "inputs": inputs})
    inputs["measured_with_asset"] = str(first)
    return Takeoff(
        component=takeoff.component,
        quantity=Decimal("0.00"),
        unit=takeoff.unit,
        formula=f"measured once per room: see asset {first}",
        inputs=inputs,
        method=takeoff.method,
        assumptions=takeoff.assumptions,
    )


def _fields(takeoff: Takeoff) -> dict[str, Any]:
    return {
        "component": takeoff.component,
        "quantity": takeoff.quantity,
        "unit": takeoff.unit,
        "formula": takeoff.formula,
        "inputs": takeoff.inputs,
        "method": takeoff.method,
        "assumptions": takeoff.assumptions,
    }


def _cost(takeoff: Takeoff, cost_year: int, location_factor: float) -> CostEstimate | None:
    try:
        return estimate_cost(takeoff, cost_year, Decimal(str(location_factor)))
    except CostError:
        return None


def _source_row(takeoff: Takeoff, cost: CostEstimate | None) -> str:
    if cost is not None:
        return cost.source_row
    return f"missing: no cost row for {takeoff.component!r} per {takeoff.unit}"


async def _record_calls(
    session: AsyncSession, study_id: uuid.UUID, calls: list[CallRecord]
) -> None:
    if not calls:
        return
    workflow_id, run_id = workflow_execution()
    run = await RunRepository(session).get_by_execution(workflow_id, run_id)
    for c in calls:
        session.add(
            LlmCall(
                study_id=study_id,
                run_id=None if run is None else run.id,
                activity=ACTIVITY,
                provider=c.provider,
                model=c.model,
                prompt_version=c.prompt_version,
                input_tokens=c.input_tokens,
                output_tokens=c.output_tokens,
                cost_usd=Decimal(str(round(c.cost_usd, 6))),
                latency_ms=c.latency_ms,
            )
        )
    await session.flush()


async def _overrides(
    assets: AssetRepository,
    study_id: uuid.UUID,
    components: list[Component],
    corrections: tuple[CorrectionInput, ...],
) -> dict[uuid.UUID, Override]:
    by_origin = {
        str(c.raw[CARRIED_FROM]): c.id
        for c in components
        if isinstance(c.raw, dict) and CARRIED_FROM in c.raw
    }
    overrides: dict[uuid.UUID, Override] = {}
    for correction in corrections:
        if correction.entity_type != "asset":
            raise validation_error(f"process_assets cannot apply a {correction.entity_type}")
        check_asset(correction.after)
        old = await assets.get(study_id, parse_uuid(correction.entity_id, "entity_id"))
        if old is None or old.component_id is None:
            raise validation_error(f"asset {correction.entity_id} not found in study {study_id}")
        component_id = by_origin.get(str(old.component_id))
        if component_id is None:
            raise validation_error(f"asset {old.id} has no component in this revision")
        previous = overrides.get(component_id)
        merged = {**(previous.after if previous else {}), **correction.after}
        overrides[component_id] = Override(old=old, after=merged)
    return overrides


__all__ = ["ProcessAssets", "component_view", "corpus_versions"]
