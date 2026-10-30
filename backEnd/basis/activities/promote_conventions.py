"""``promote_conventions``: the approved revision's rulings become the firm's conventions.

Runs after the ``assets_review`` approval and before ``exporting`` (patch
``firm-conventions-v1``; docs/review_2026-10-15/CLASSIFICATION_CONVENTIONS.md section 5).
For the approved revision it promotes, per context key (``basis.classify.cache``):

* every ``manual`` asset (an engineer's correction): the ruling itself, with the
  correction's ``reason`` (``corrections``, found along the asset's lineage by
  :class:`~basis.db.repositories.provenance.ProvenanceResolver`) as the rationale, and the
  asset's verified reference citations when it has any (a ruling needs none);
* every ``llm`` asset with at least one verified reference citation: an engineer-approved
  model answer, so the next identical component costs nothing;
* every ``cache`` asset (a convention the engineer let stand) adds one approval to its
  convention, if the convention still gives that answer; it never creates or replaces one;
* never ``rule`` (free and deterministic).

When several approved assets share a key, the engineer's corrections win, then cited model
answers, then conventions let stand; if the ones that win disagree with each other, the key
is ambiguous and skipped.
A study's own appraisal is evidence for that study only, so only citations into the
reference corpus are kept.

Each promoted key writes the firm's row (``ClassificationCacheRepository.put``: new, one
more approval of the same answer, or a different answer replacing it) and a
``convention.promoted`` timeline event (``detail``: key, bucket, ``agrees_with_rules``,
outcome, approvals); a replacement also writes ``convention.replaced`` with the replaced
row as ``before`` and ``E.ASSETS_CONVENTION_REPLACED``. All of it commits with the activity's
completion in one transaction.

Idempotent: ``run_once`` returns the stored output for the same activity key, and a key
this study already promoted (a ``convention.promoted`` event of the study) is skipped, so a
re-run after a workflow reset neither counts an approval twice nor writes a second event.
A failure is not fatal: the workflow logs it with
``E.ASSETS_CONVENTION_PROMOTION_FAILED`` and exports.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import logger, parse_uuid, run_once, validation_error
from basis.activities.deps import Deps
from basis.activities.process_assets import property_class_for
from basis.classify.cache import ClassificationCacheRepository, answer_of, cache_key
from basis.config import Settings
from basis.db.enums import ClassificationMethod, Corpus, Decision, Stage
from basis.db.models import (
    Asset,
    Chunk,
    Citation,
    Component,
    CorpusVersion,
    Correction,
    Review,
    Room,
    StudyEvent,
)
from basis.db.repositories.asset import AssetRepository
from basis.db.repositories.provenance import ProvenanceResolver
from basis.db.repositories.room import RoomRepository
from basis.db.repositories.study import StudyRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.domain.rules import lookup
from basis.errors.codes import E
from basis.workflow.contracts import PromoteConventionsInput, PromoteConventionsOutput

ACTIVITY: Final = "promote_conventions"
PROMOTED_EVENT: Final = "convention.promoted"
REPLACED_EVENT: Final = "convention.replaced"
PROMOTED_METHODS: Final = (
    ClassificationMethod.MANUAL,
    ClassificationMethod.LLM,
    ClassificationMethod.CACHE,
)
# Which approved answer speaks for a key when several do: the engineer's correction, then a
# cited model answer, then a convention the engineer let stand.
PRIORITY: Final = (
    ClassificationMethod.MANUAL,
    ClassificationMethod.LLM,
    ClassificationMethod.CACHE,
)


class PromoteConventions:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="promote_conventions")
    async def run(self, input: PromoteConventionsInput) -> PromoteConventionsOutput:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> PromoteConventionsOutput:
            return await promote(session, self._deps.settings, study_id, input.revision)

        return await run_once(
            self._deps,
            name=ACTIVITY,
            key=input.idempotency_key,
            output_type=PromoteConventionsOutput,
            work=work,
        )


@dataclass(frozen=True, slots=True)
class Candidate:
    """One approved asset that can seed the convention for its context key."""

    asset: Asset
    key: str
    result: dict[str, Any]
    rule_key: str | None  # the rules table's row for the same key (None: no row)
    agrees_with_rules: bool | None


async def promote(
    session: AsyncSession, settings: Settings, study_id: uuid.UUID, revision: int
) -> PromoteConventionsOutput:
    """Promote the approved ``revision`` of ``study_id`` (module doc); refuses a revision
    with something to promote and no ``approve`` at ``assets_review`` on record."""
    study = await StudyRepository(session).get(study_id)
    if study is None:
        raise validation_error(f"study {study_id} not found", E.ASSETS_CONVENTION_PROMOTION_FAILED)
    candidates = await _candidates(session, settings, study_id, revision, study.intake)
    if not candidates:
        return PromoteConventionsOutput()  # nothing to promote, so nothing to authorize
    # The approval on record (``reviews``, written by the API before the workflow hears of
    # it) is the authority: no row, no promotion.
    review = (
        await session.scalars(
            select(Review).where(
                Review.study_id == study_id,
                Review.stage == Stage.ASSETS_REVIEW,
                Review.revision == revision,
                Review.decision == Decision.APPROVE,
            )
        )
    ).one_or_none()
    if review is None:
        raise validation_error(
            f"revision {revision} of study {study_id} is not approved at assets_review",
            E.ASSETS_CONVENTION_PROMOTION_FAILED,
        )
    already = await promoted_keys(session, study_id)
    groups: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.key, []).append(candidate)
    repo = ClassificationCacheRepository(session)
    events = StudyEventRepository(session)
    counts = {"created": 0, "confirmed": 0, "replaced": 0, "skipped": 0}
    for key in sorted(groups):
        if key in already:
            counts["skipped"] += 1
            continue
        chosen = _choose(groups[key])
        if chosen is None:
            counts["skipped"] += 1
            logger.info("convention %s skipped: the approved assets disagree", key)
            continue
        result, rule_key, agrees = chosen.result, chosen.rule_key, chosen.agrees_with_rules
        if chosen.asset.method is ClassificationMethod.CACHE:
            # The engineer let the convention stand: one more approval of it, if it still
            # gives this answer (a convention replaced since is not put back).
            confirmed = await repo.confirm(study.org_id, key, result)
            if confirmed is None:
                counts["skipped"] += 1
                continue
            written = confirmed
        else:
            written = await repo.put(
                study.org_id,
                key,
                result,
                source=f"approved_asset:{chosen.asset.id}",
                study_id=study_id,
                reviewer_id=review.reviewer_id,
                approved_at=review.created_at,
                rule_key=rule_key,
                agrees_with_rules=agrees,
            )
        counts[written.outcome] += 1
        common: dict[str, Any] = {
            "actor": "engineer",
            "actor_id": review.reviewer_id,
            "stage": Stage.ASSETS_REVIEW,
            "revision": revision,
            "entity_type": "asset",
            "entity_id": chosen.asset.id,
        }
        if written.previous is not None:
            await events.record(
                study_id,
                REPLACED_EVENT,
                code=E.ASSETS_CONVENTION_REPLACED,
                before=written.previous,
                after={
                    "result": result,
                    "study_id": str(study_id),
                    "reviewer_id": str(review.reviewer_id),
                    "approved_at": review.created_at.isoformat(),
                },
                detail={"key": key},
                **common,
            )
        await events.record(
            study_id,
            PROMOTED_EVENT,
            detail={
                "key": key,
                "bucket": result["bucket"],
                "agrees_with_rules": agrees,
                "rule_key": rule_key,
                "outcome": written.outcome,
                "approvals": written.approvals,
                "method": chosen.asset.method.value,
                "cited": bool(result["citations"]),
            },
            **common,
        )
    return PromoteConventionsOutput(**counts)


def _choose(group: Sequence[Candidate]) -> Candidate | None:
    """The candidate that speaks for a key: the engineer's corrections, else cited model
    answers, else conventions let stand (:data:`PRIORITY`); ``None`` when the ones that speak
    disagree (bucket, recovery period or section). Among equal answers the one with
    citations, then the smallest asset id, so the choice is stable."""
    pool = next(
        [c for c in group if c.asset.method is method]
        for method in PRIORITY
        if any(c.asset.method is method for c in group)
    )
    if len({answer_of(c.result) for c in pool}) > 1:
        return None
    return min(pool, key=lambda c: (not c.result["citations"], str(c.asset.id)))


async def promoted_keys(session: AsyncSession, study_id: uuid.UUID) -> set[str]:
    """The keys this study has already promoted (its ``convention.promoted`` events)."""
    rows = await session.scalars(
        select(StudyEvent.detail["key"].astext).where(
            StudyEvent.study_id == study_id, StudyEvent.event_type == PROMOTED_EVENT
        )
    )
    return {key for key in rows.all() if key}


async def _candidates(
    session: AsyncSession,
    settings: Settings,
    study_id: uuid.UUID,
    revision: int,
    intake: Any,
) -> list[Candidate]:
    assets = [
        a
        for a in await AssetRepository(session).list_for_revision(study_id, revision)
        if a.method in PROMOTED_METHODS and a.component_id is not None
    ]
    if not assets:
        return []
    cited = await reference_citations(session, study_id, [a.id for a in assets])
    assets = [a for a in assets if a.method is not ClassificationMethod.LLM or cited.get(a.id)]
    if not assets:
        return []
    components = {
        c.id: c
        for c in (
            await session.scalars(
                select(Component).where(
                    Component.study_id == study_id,
                    Component.id.in_([a.component_id for a in assets]),
                )
            )
        ).all()
    }
    rooms: dict[uuid.UUID, Room] = {
        r.id: r for r in await RoomRepository(session).list_for_revision(study_id, revision)
    }
    manual = [a for a in assets if a.method is ClassificationMethod.MANUAL]
    reasons = await correction_reasons(session, study_id, manual, components)
    property_class, _ = property_class_for(intake, settings)
    found: list[Candidate] = []
    for asset in assets:
        component = components.get(asset.component_id) if asset.component_id else None
        if component is None:
            continue
        room = rooms.get(component.room_id) if component.room_id else None
        room_type = room.room_type if room else None
        key = cache_key(
            component.label, component.material, component.attachment, property_class, room_type
        )
        hit = lookup(
            component.label,
            component.material,
            component.attachment,
            property_class,
            room_type=room_type,
        )
        result: dict[str, Any] = {
            "category": asset.category,
            "section": asset.irs_section,
            "bucket": asset.bucket.value,
            "recovery_period": str(asset.recovery_period),
            "asset_class": None,
            "citations": cited.get(asset.id, []),
            "rationale": reasons.get(asset.id, ""),
            "method": asset.method.value,
            "source_asset_id": str(asset.id),
            # What the rules table says, for the convention's rationale when it is served.
            "rule_bucket": None if hit is None else hit.bucket.value,
        }
        found.append(
            Candidate(
                asset=asset,
                key=key,
                result=result,
                rule_key=None if hit is None else hit.key,
                agrees_with_rules=None if hit is None else hit.bucket is asset.bucket,
            )
        )
    return found


async def reference_citations(
    session: AsyncSession, study_id: uuid.UUID, asset_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, str]]]:
    """Each asset's verified citations into the reference corpus, in a stable order."""
    rows = await session.execute(
        select(Citation.asset_id, Citation.chunk_id, Citation.excerpt)
        .join(Chunk, Chunk.id == Citation.chunk_id)
        .join(CorpusVersion, CorpusVersion.id == Chunk.corpus_version_id)
        .where(
            Citation.study_id == study_id,
            Citation.asset_id.in_(asset_ids),
            Citation.verified_at.is_not(None),
            CorpusVersion.corpus == Corpus.REFERENCE.value,
        )
        .order_by(Citation.asset_id, Citation.chunk_id, Citation.excerpt)
    )
    found: dict[uuid.UUID, list[dict[str, str]]] = {}
    for asset_id, chunk_id, excerpt in rows.all():
        found.setdefault(asset_id, []).append({"chunk_id": str(chunk_id), "excerpt": excerpt})
    return found


async def correction_reasons(
    session: AsyncSession,
    study_id: uuid.UUID,
    assets: list[Asset],
    components: dict[uuid.UUID, Component],
) -> dict[uuid.UUID, str]:
    """The ``reason`` of the corrections that made each manual asset (its lineage's nearest
    ``asset`` correction; several on one target are joined with ``; ``)."""
    if not assets:
        return {}
    provenance = await ProvenanceResolver(session, study_id).assets(assets, components)
    found: dict[uuid.UUID, str] = {}
    for asset in assets:
        origin = provenance.get(asset.id)
        if origin is None or origin.carried_from is None:
            continue
        rows = await session.scalars(
            select(Correction.reason)
            .where(
                Correction.study_id == study_id,
                Correction.entity_type == "asset",
                Correction.entity_id == origin.carried_from,
                Correction.revision == origin.revision,
            )
            .order_by(Correction.created_at, Correction.id)
        )
        reasons = [r for r in rows.all() if r]
        if reasons:
            found[asset.id] = "; ".join(dict.fromkeys(reasons))
    return found


__all__ = ["ACTIVITY", "PROMOTED_EVENT", "REPLACED_EVENT", "PromoteConventions", "promote"]
