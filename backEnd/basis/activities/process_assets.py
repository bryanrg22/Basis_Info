"""``process_assets`` (Stage 2 stub): classify, measure and price every component.

Stage 2 body: for each component of the revision, one ``assets`` row classified by a tiny
label map (``method="rule"``), one ``takeoffs`` row, one ``cost_estimates`` row, and one
``citations`` row pointing at the study corpus's first chunk (when one exists; the FK
guarantees a citation cannot point nowhere). A label without a rule is flagged
``needs_review``. Stage 3 fans this out per component with retrieval and the classifier.

``asset`` corrections (an assets rejection) name an asset of the rejected revision; the
new revision's asset for the same component (found through the ``carried_from`` marker
that ``apply_corrections`` writes on the copied component) takes the corrected values with
``method="manual"``.

The stub charges ``COMPONENT_COST_USD`` per component against the budget and raises the
non-retryable ``BudgetExceededError`` up front when the remaining budget cannot cover the
whole revision (no partial rows).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import (
    budget_exceeded,
    heartbeat,
    parse_uuid,
    run_once,
    validation_error,
)
from basis.activities.apply_corrections import CARRIED_FROM
from basis.activities.corrections import check_asset, parse_bucket, parse_recovery_period
from basis.activities.deps import Deps
from basis.activities.stub_rules import COMPONENT_COST_USD, LABEL_RULES, rule_for
from basis.db.enums import ClassificationMethod
from basis.db.models import Component
from basis.db.repositories.asset import AssetRepository
from basis.db.repositories.corpus import CorpusRepository
from basis.db.repositories.room import RoomRepository
from basis.workflow.contracts import CorrectionInput, ProcessAssetsInput, ProcessAssetsOutput

COST_YEAR: Final = 2026


class ProcessAssets:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="process_assets")
    async def run(self, input: ProcessAssetsInput) -> ProcessAssetsOutput:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> ProcessAssetsOutput:
            assets = AssetRepository(session)
            existing = await assets.list_for_revision(study_id, input.revision)
            if existing:
                # Written for this revision by an earlier execution (e.g. after a reset).
                return ProcessAssetsOutput(
                    assets=len(existing), needs_review=sum(a.needs_review for a in existing)
                )
            components = await RoomRepository(session).components(study_id, input.revision)
            cost = round(COMPONENT_COST_USD * len(components), 6)
            if cost > input.budget_remaining_usd:
                raise budget_exceeded(
                    f"{len(components)} components need ${cost}; "
                    f"${input.budget_remaining_usd:.4f} remains"
                )
            overrides = await _overrides(assets, study_id, components, input.corrections)
            chunks = await CorpusRepository(session).study_chunks(study_id)
            needs_review = 0
            for component in components:
                heartbeat(str(component.id))
                flagged = await _classify(
                    assets, component, input.revision, overrides.get(component.id), chunks
                )
                needs_review += flagged
            return ProcessAssetsOutput(
                assets=len(components), needs_review=needs_review, cost_usd=cost
            )

        return await run_once(
            self._deps,
            name="process_assets",
            key=input.idempotency_key,
            output_type=ProcessAssetsOutput,
            work=work,
        )


async def _overrides(
    assets: AssetRepository,
    study_id: uuid.UUID,
    components: list[Component],
    corrections: tuple[CorrectionInput, ...],
) -> dict[uuid.UUID, dict[str, Any]]:
    by_origin = {
        str(c.raw[CARRIED_FROM]): c.id
        for c in components
        if isinstance(c.raw, dict) and CARRIED_FROM in c.raw
    }
    overrides: dict[uuid.UUID, dict[str, Any]] = {}
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
        overrides.setdefault(component_id, {}).update(correction.after)
    return overrides


async def _classify(
    assets: AssetRepository,
    component: Component,
    revision: int,
    override: dict[str, Any] | None,
    chunks: list[Any],
) -> bool:
    """Write the asset, its takeoff, cost estimate and citation; True if it needs review."""
    rule = rule_for(component.label)
    known = component.label in LABEL_RULES
    if override:
        bucket = parse_bucket(override.get("bucket", rule.bucket.value))
        asset = await assets.create(
            study_id=component.study_id,
            revision=revision,
            component_id=component.id,
            category=str(override.get("category", rule.category)),
            bucket=bucket,
            recovery_period=parse_recovery_period(
                override.get("recovery_period", rule.recovery_period)
            ),
            irs_section=override.get("irs_section", rule.irs_section),
            method=ClassificationMethod.MANUAL,
            confidence=1.0,
        )
    else:
        asset = await assets.create(
            study_id=component.study_id,
            revision=revision,
            component_id=component.id,
            category=rule.category,
            bucket=rule.bucket,
            recovery_period=rule.recovery_period,
            irs_section=rule.irs_section,
            method=ClassificationMethod.RULE,
            confidence=0.9 if known else 0.3,
            needs_review=not known,
            review_reason=None if known else f"no rule for component label {component.label!r}",
        )
    quantity = Decimal(1)
    await assets.add_takeoff(
        asset=asset,
        quantity=quantity,
        unit=rule.unit,
        formula="stub: one unit per detected component",
        inputs={"component_id": str(component.id)},
    )
    await assets.add_cost_estimate(
        asset=asset,
        unit_cost=rule.unit_cost,
        total=rule.unit_cost * quantity,
        source_row=f"stub:{component.label}",
        cost_year=COST_YEAR,
    )
    if chunks:
        await assets.add_citation(
            asset=asset,
            chunk_id=chunks[0].id,
            excerpt=chunks[0].text[:200],
            verification_method="stub",
        )
    return asset.needs_review
