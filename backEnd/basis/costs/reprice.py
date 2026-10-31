"""The reprice: rebuild a revision's assets with the study's current intake and cost index
store, keeping every classification (docs/api/REPRICE.md).

Adding an index value to the store, or changing the intake, never changes a priced
revision: its cost rows keep the trail they were priced with (``cost_estimates.trail``) and
its result keeps using it (:mod:`basis.evidence.pricing`), reporting the difference as
``pricing.stale``. Applying the new inputs is an engineer's explicit action:

1. :func:`preview`: what the rebuild would change, per asset and for the reconciliation.
   It runs the code the rebuild runs: ``apply_corrections``' carry of the revision into the
   next one, then ``process_assets``' preparation and write with the ``reprice``
   correction (classifications carried, no model call; a component with nothing to carry
   is classified with no model provider, so a residual one shows as unavailable). All of
   it inside a SAVEPOINT that is rolled back: nothing is written, no event is recorded.
   The preview carries a :func:`fingerprint` of the inputs it was computed on.
2. :func:`request`: the engineer sends the fingerprint back with a reason. The current
   fingerprint must equal it (else 409 ``reprice-inputs-changed``: the intake, the store or
   the model inputs moved since the preview), the property class must be the one the
   revision was classified under (else 409 ``reprice-reclassify``: reject the assets review
   so they are classified again), and the study must be at the assets review of that
   revision (``ReviewRepository.record_decision``'s 409s). It records an assets-review
   rejection whose corrections are the ``reprice`` correction (and the ``structure_model``
   one when given), an ``assets.reprice_requested`` event, and the outbox signal; the
   workflow then carries the revision and ``process_assets`` rebuilds it (status: the
   study's stage and the timeline).

Withholding is unchanged: the rebuilt revision is reconciled like any other, and an
allocation outside tolerance stays withheld.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from basis.activities.apply_corrections import _carry_extractions, _carry_rooms
from basis.activities.deps import Deps
from basis.activities.process_assets import (
    CARRIED_CLASSIFICATION,
    _classify_all,
    _prepare,
    _write,
)
from basis.activities.reprice_corrections import REPRICE, STRUCTURE_MODEL
from basis.api.schemas.reprice import (
    AmountsOut,
    ReconciliationSummaryOut,
    RepriceAssetOut,
    RepricePreviewOut,
)
from basis.config import Settings
from basis.db.enums import Decision, Stage, StudyStatus
from basis.db.models import Study
from basis.db.repositories.asset import AssetRepository
from basis.db.repositories.review import CorrectionInput as ReviewCorrection
from basis.db.repositories.review import DecisionRecord, ReviewRepository
from basis.db.repositories.room import RoomRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.domain.structure import ModelOverride
from basis.errors.codes import E
from basis.evidence.pricing import PricingStatusOut
from basis.evidence.result import (
    StudyResultOut,
    compute_result,
    concealed_label,
    load_result_inputs,
    load_schedule,
)
from basis.workflow.contracts import CorrectionInput, ProcessAssetsInput

PREVIEW_KEY: Final = "reprice-preview"
STRUCTURE_MODEL_SOURCE: Final = "the engineer's structure_model correction (reprice)"


class RepriceRefused(Exception):
    """The rebuild cannot be requested: ``code`` and the reason."""

    def __init__(self, message: str, code: str, type_suffix: str) -> None:
        super().__init__(message)
        self.code = code
        self.type_suffix = type_suffix


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def fingerprint(
    study: Study,
    revision: int,
    pricing: PricingStatusOut | None,
    settings: Settings,
    override: ModelOverride | None,
) -> str:
    """sha256 of every input the rebuild would price with: the study, the revision, the
    intake version, the cost year with its index value and store version, the location
    factor and its row, the property class, the square-foot model override and the pricing
    settings. Any change to one of them changes the fingerprint."""
    current = None if pricing is None else pricing.current
    document: dict[str, Any] = {
        "study_id": str(study.id),
        "revision": revision,
        "intake_version": study.intake_version,
        "cost_index": settings.cost_index,
        "cost_year_setting": settings.cost_year,
        "location_setting": str(settings.cost_location_factor),
        "rcn_source": settings.structure_rcn_source,
        "cost_table": settings.cost_table_path,
        "structure_step": settings.structure_step,
        "current": None if current is None else current.model_dump(mode="json"),
        "override": None if override is None else override.record(),
    }
    blob = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def blocked(study: Study, revision: int) -> tuple[str, str] | None:
    """Why the rebuild cannot be requested now (the reason and its code), or None."""
    if (
        study.current_stage is not Stage.ASSETS_REVIEW
        or study.status is not StudyStatus.WAITING_REVIEW
    ):
        return (
            f"the study is at {study.current_stage.value} ({study.status.value}); a reprice is "
            "requested at an open assets review",
            E.API_REPRICE_NOT_AT_REVIEW,
        )
    if revision != study.revision:
        return (
            f"revision {revision} is not the study's current revision {study.revision}",
            E.API_REPRICE_NOT_AT_REVIEW,
        )
    return None


@dataclass(frozen=True, slots=True)
class _Amounts:
    label: str | None
    category: str
    bucket: str
    concealed: bool
    amounts: AmountsOut


async def _amounts(
    session: AsyncSession, study_id: uuid.UUID, revision: int
) -> dict[uuid.UUID, tuple[_Amounts, Any]]:
    """``{asset id: (amounts, takeoff inputs)}`` of a revision's assets."""
    repo = AssetRepository(session)
    assets = await repo.list_for_revision(study_id, revision)
    ids = [a.id for a in assets]
    costs = await repo.cost_estimates(ids)
    takeoffs = await repo.takeoffs(ids)
    found: dict[uuid.UUID, tuple[_Amounts, Any]] = {}
    components = {c.id: c for c in await RoomRepository(session).components(study_id, revision)}
    for asset in assets:
        cost = costs.get(asset.id)
        takeoff = takeoffs.get(asset.id)
        inputs = None if takeoff is None else takeoff.inputs
        label, concealed = concealed_label(inputs)
        component = components.get(asset.component_id) if asset.component_id else None
        found[asset.id] = (
            _Amounts(
                label=component.label if component is not None else label,
                category=asset.category,
                bucket=asset.bucket.value,
                concealed=concealed,
                amounts=AmountsOut(
                    quantity=None if takeoff is None else takeoff.quantity,
                    unit=None if takeoff is None else takeoff.unit,
                    unit_cost=None if cost is None else cost.unit_cost,
                    total=None if cost is None else cost.total,
                ),
            ),
            inputs,
        )
    return found


def _summary(result: StudyResultOut) -> ReconciliationSummaryOut:
    r = result.reconciliation
    ws = result.worksheet
    rcnld = result.schedule.rcnld_total
    basis = None if ws is None else ws.building_basis
    return ReconciliationSummaryOut(
        status=None if r is None else r.status,
        rcn_total=result.schedule.rcn_total,
        rcnld_total=rcnld,
        building_basis=basis,
        gap=None if rcnld is None or basis is None else rcnld - basis,
        allocated=result.allocation is not None,
        missing=list(result.missing),
    )


async def _result(
    session: AsyncSession, study: Study, revision: int, settings: Settings
) -> StudyResultOut:
    rows = await load_schedule(session, study.id, revision)
    structure, pricing = await load_result_inputs(
        session, study.id, revision, study.intake, study.intake_version, settings
    )
    return compute_result(rows, study.intake, revision, structure, pricing)


def corrections_for(
    study_id: uuid.UUID, print_: str, override: ModelOverride | None
) -> tuple[CorrectionInput, ...]:
    found = [
        CorrectionInput(entity_type=REPRICE, entity_id=str(study_id), after={"fingerprint": print_})
    ]
    if override is not None:
        record = {k: v for k, v in override.record().items() if k != "source" and v is not None}
        found.append(
            CorrectionInput(entity_type=STRUCTURE_MODEL, entity_id=str(study_id), after=record)
        )
    return tuple(found)


async def preview(
    session: AsyncSession,
    deps: Deps,
    study: Study,
    revision: int,
    override: ModelOverride | None = None,
) -> RepricePreviewOut:
    """The impact preview (module doc, step 1). ``study`` must be read in ``session``."""
    settings = deps.settings
    before = await _result(session, study, revision, settings)
    pricing = before.pricing
    print_ = fingerprint(study, revision, pricing, settings, override)
    why = blocked(study, revision)
    if why is None and pricing is not None and pricing.remedy == "reject_assets":
        why = (
            "the property class changed since the assets were classified: reject the assets "
            "review so they are classified again",
            E.API_REPRICE_RECLASSIFY,
        )
    old = await _amounts(session, study.id, revision)
    to_revision = revision + 1
    reclassified = 0
    new: dict[uuid.UUID, tuple[_Amounts, Any]] = {}
    after = before
    if revision == study.revision and old:
        savepoint = await session.begin_nested()
        try:
            await _carry_extractions(session, study.id, revision, to_revision)
            await _carry_rooms(
                session,
                study.id,
                revision,
                to_revision,
                skip_photos=set(),
                room_corrections=[],
                component_corrections=[],
                added=[],
                moves=[],
            )
            input = ProcessAssetsInput(
                study_id=str(study.id),
                revision=to_revision,
                idempotency_key=PREVIEW_KEY,
                budget_remaining_usd=0.0,
                corrections=corrections_for(study.id, print_, override),
            )
            prepared = await _prepare(deps, session, study.id, input)
            if prepared is not None:
                views = [*prepared.views.values(), *prepared.structure_views.values()]
                reclassified = len(prepared.views)
                results = await _classify_all(views, prepared.ctx, 1) if views else {}
                await _write(deps, session, study.id, input, prepared, results)
            new = await _amounts(session, study.id, to_revision)
            after = await _result(session, study, to_revision, settings)
        finally:
            await savepoint.rollback()
    assets: list[RepriceAssetOut] = []
    matched: set[uuid.UUID] = set()
    for _, (row, inputs) in sorted(
        new.items(), key=lambda kv: (kv[1][0].concealed, kv[1][0].label or "")
    ):
        origin = None
        record = inputs.get(CARRIED_CLASSIFICATION) if isinstance(inputs, dict) else None
        if isinstance(record, dict) and record.get("from_asset_id"):
            origin = uuid.UUID(str(record["from_asset_id"]))
        previous = old.get(origin) if origin is not None else None
        if origin is not None and previous is not None:
            matched.add(origin)
        before_amounts = None if previous is None else previous[0].amounts
        change = None
        if (
            before_amounts is not None
            and before_amounts.total is not None
            and row.amounts.total is not None
        ):
            change = row.amounts.total - before_amounts.total
        assets.append(
            RepriceAssetOut(
                asset_id=origin if previous is not None else None,
                label=row.label,
                category=row.category,
                bucket=row.bucket,  # type: ignore[arg-type]
                concealed=row.concealed,
                before=before_amounts,
                after=row.amounts,
                change=change,
            )
        )
    for asset_id, (row, _inputs) in old.items():
        if asset_id in matched or not new:
            continue
        assets.append(
            RepriceAssetOut(
                asset_id=asset_id,
                label=row.label,
                category=row.category,
                bucket=row.bucket,  # type: ignore[arg-type]
                concealed=row.concealed,
                before=row.amounts,
                after=None,
                change=None,
            )
        )
    return RepricePreviewOut(
        revision=revision,
        to_revision=to_revision,
        fingerprint=print_,
        allowed=why is None,
        blocked=None if why is None else why[0],
        blocked_code=None if why is None else why[1],
        pricing=pricing,
        assets=assets,
        reclassified=reclassified,
        before=_summary(before),
        after=_summary(after),
    )


async def request(
    session: AsyncSession,
    settings: Settings,
    study: Study,
    *,
    revision: int,
    sent_fingerprint: str,
    reason: str,
    reviewer_id: uuid.UUID,
    idempotency_key: str,
    override: ModelOverride | None = None,
) -> DecisionRecord:
    """Record the rebuild (module doc, step 2); raises :class:`RepriceRefused`."""
    why = blocked(study, revision)
    if why is not None:
        raise RepriceRefused(why[0], why[1], "gate-not-open")
    structure, pricing = await load_result_inputs(
        session, study.id, revision, study.intake, study.intake_version, settings
    )
    if pricing is not None and pricing.remedy == "reject_assets":
        raise RepriceRefused(
            "the property class changed since the assets were classified: reject the assets "
            "review so they are classified again (a reprice carries classifications)",
            E.API_REPRICE_RECLASSIFY,
            "reprice-reclassify",
        )
    current = fingerprint(study, revision, pricing, settings, override)
    if current != sent_fingerprint:
        raise RepriceRefused(
            "the inputs changed since the preview (intake, cost index store or model): "
            "preview again",
            E.API_REPRICE_INPUTS_CHANGED,
            "reprice-inputs-changed",
        )
    corrections = corrections_for(study.id, current, override)
    record = await ReviewRepository(session).record_decision(
        study_id=study.id,
        stage=Stage.ASSETS_REVIEW,
        revision=revision,
        decision=Decision.REJECT,
        reviewer_id=reviewer_id,
        idempotency_key=idempotency_key,
        comment=reason,
        corrections=[
            ReviewCorrection(
                entity_type=c.entity_type,
                entity_id=study.id,
                before=None,
                after=dict(c.after),
                reason=reason,
            )
            for c in corrections
        ],
    )
    await StudyEventRepository(session).record(
        study.id,
        "assets.reprice_requested",
        actor="engineer",
        actor_id=reviewer_id,
        stage=Stage.ASSETS_REVIEW,
        revision=revision,
        entity_type="study",
        entity_id=study.id,
        before=None if pricing is None else pricing.priced_with.model_dump(mode="json"),
        after=None if pricing is None else pricing.current.model_dump(mode="json"),
        detail={
            "fingerprint": current,
            "reasons": [] if pricing is None else list(pricing.reasons),
            "review_id": str(record.review.id),
            "correction_ids": [str(c.id) for c in record.corrections],
            "structure_model": None if override is None else override.record(),
            "reason": reason,
            "to_revision": revision + 1,
        },
    )
    return record


__all__ = [
    "RepriceRefused",
    "blocked",
    "corrections_for",
    "fingerprint",
    "preview",
    "request",
]
