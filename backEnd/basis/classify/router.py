"""``classify(component, ctx)``: the firm's conventions, then the rules table, then the model.

1. **Convention** (``ctx.cache``, the study's firm's rows of ``classification_cache``
   prefetched by label and indexed by tier; docs/review_2026-10-15/CLASSIFICATION_CONVENTIONS.md
   sections 5 and 9): no model call. The most specific tier with a ruling serves: the exact
   context key, then the same label and room whatever material and attachment the photo
   step read, then the label alone. An engineer's approved ruling beats the rules table, so
   it is not sent to review for disagreeing with it, except that a ruling matched on the
   label alone (another room) does not outrank an uncontested rule row (section 9.2). The
   rationale says who approved it, on which study, when, how many approvals it has, whether
   the rules table agrees and which tier matched. Its citations, when it has any, are
   re-verified against the reference corpus version in use; an uncited ruling is served as
   such (``citation: firm convention, uncited`` in its provenance). Rulings that disagree
   with equal weight inside a tier serve nothing and flag the asset
   (``convention_conflict:<tier>``, section 9.3).
2. **Rule** (:func:`basis.domain.rules.lookup`, with the component's room so the context
   variants fire): no model call. The rule's citation claims are resolved against the
   reference corpus (search for the excerpt, keep a hit from the cited document and page
   whose text contains it).
3. **LLM** (:mod:`basis.classify.residual`): the bounded tool loop. Citations must
   resolve, be in scope, have been retrieved during this loop, and contain the excerpt.

Review policy (any one sets ``needs_review``; all reasons are kept, in a fixed order):
a dropped or unresolvable citation (``citation_unverified``), zero verified citations on
the model path or on a cited convention (``citation_unverified``), a model answer without
any search (``missing_evidence``), a contested rule row, a model that asks
for review, a loop failure, and any validator error or warning. When the model gives no
usable answer the asset gets the building bucket for the property class (no accelerated
depreciation is claimed) with confidence 0 and the failure as the reason. A model answer
without a search or without any verified citation has its confidence capped at 0.3.

The result carries no study status and nothing here writes one; the caller turns results
into rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Final, Literal, NamedTuple

from basis.classify.cache import (
    CachedClassification,
    ClassificationCacheRepository,
    ConventionConflict,
    ConventionIndex,
    ConventionMatch,
    Tier,
    cache_key,
)
from basis.classify.prompt import PROMPT_VERSION, load_prompt
from basis.classify.residual import (
    DEFAULT_LIMITS,
    RESERVE_USD_PER_CALL,
    CallRecord,
    LoopLimits,
    PriceFn,
    ResidualOutcome,
    ToolCallTrace,
    default_price,
    run_residual,
)
from basis.classify.schemas import CitationClaim, Classification, ComponentView
from basis.classify.store_protocol import ChunkStore
from basis.classify.tools import GET_CHUNK, GET_TABLE, SEARCH_REFERENCE, SearchFn, build_tools
from basis.classify.verify import (
    DroppedCitation,
    VerifiedCitation,
    excerpt_supported,
    verify_citations,
)
from basis.db.enums import Bucket
from basis.domain.rules import RECOVERY_YEARS, PropertyClass, RuleHit, lookup
from basis.domain.validators import BUILDING_BUCKET_FOR_CLASS, validate_classification
from basis.llm.call import CallContext
from basis.llm.provider import Budget, ChatProvider

Method = Literal["rule", "cache", "llm"]

# The residual classifier may search the reference corpus and fetch chunks and tables.
# ``search_study`` exists (build_tools) but is not offered for classification.
RESIDUAL_ALLOWLIST: Final = frozenset({SEARCH_REFERENCE, GET_CHUNK, GET_TABLE})
MISSING_EVIDENCE_CONFIDENCE: Final = 0.3
CACHE_CONFIDENCE: Final = 0.95
RULE_RESOLVE_K: Final = 10

CITATION_UNVERIFIED: Final = "citation_unverified"
MISSING_EVIDENCE: Final = "missing_evidence"
CONVENTION_CONFLICT: Final = "convention_conflict"


@dataclass(frozen=True)
class ClassifyContext:
    study_id: str
    property_class: PropertyClass
    budget: Budget
    store: ChunkStore | None = None
    reference_version_id: uuid.UUID | None = None
    study_version_id: uuid.UUID | None = None
    provider: ChatProvider | None = None
    # The firm's conventions by tier (a mapping of exact key to row is accepted and indexed).
    cache: ConventionIndex = field(default_factory=ConventionIndex)
    model: str = "gpt-4o-mini"
    limits: LoopLimits = DEFAULT_LIMITS
    price: PriceFn = default_price
    reserve_usd_per_call: float = RESERVE_USD_PER_CALL
    search: SearchFn | None = None
    prompt_version: str = PROMPT_VERSION
    # Reasoning models only; ``None`` leaves the provider's default (ChatRequest).
    reasoning_effort: str | None = None
    # The activity's call context: with it every model call is recorded in the paid-call
    # ledger about the component it classifies (``process_assets``); the evaluation
    # runners leave it unset and price calls with ``price``.
    call: CallContext | None = None
    # The study's firm: a convention of another firm in ``cache`` is never served.
    org_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.cache, ConventionIndex):
            object.__setattr__(self, "cache", ConventionIndex(self.cache))


@dataclass(frozen=True)
class ClassificationResult:
    method: Method
    category: str
    section: str | None
    bucket: Bucket
    recovery_period: Decimal
    asset_class: str | None
    confidence: float
    needs_review: bool
    review_reasons: tuple[str, ...]
    citations: tuple[VerifiedCitation, ...]
    dropped: tuple[DroppedCitation, ...] = ()
    rule_key: str | None = None
    cache_key: str | None = None
    rationale: str = ""
    tool_calls: tuple[ToolCallTrace, ...] = ()
    calls: tuple[CallRecord, ...] = ()
    # The convention's provenance (``CachedClassification.provenance``) on the cache path.
    convention: Mapping[str, Any] | None = None

    @property
    def irs_section(self) -> str | None:
        return self.section

    @property
    def review_reason(self) -> str | None:
        return "; ".join(self.review_reasons) if self.review_reasons else None

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)


def _unique(reasons: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(r for r in reasons if r))


def _validation_reasons(
    component: ComponentView,
    section: str | None,
    bucket: Bucket,
    property_class: str,
    *,
    errors_only: bool = False,
) -> list[str]:
    """The validators' reasons; ``errors_only`` keeps the inconsistencies (a section that
    cannot hold the bucket, a building bucket of the other property class) and drops the
    label heuristics' warnings, which an engineer's ruling has already answered."""
    issues = validate_classification(
        label=component.label,
        section=section,
        bucket=bucket,
        recovery_period=RECOVERY_YEARS.get(bucket, Decimal(0)),
        property_class=property_class,
        room_type=component.room_type,
    )
    return [
        f"validation:{i.code}"
        for i in issues
        if i.needs_review and (i.severity == "error" or not errors_only)
    ]


async def resolve_rule_citations(
    hit: RuleHit, ctx: ClassifyContext
) -> tuple[list[VerifiedCitation], list[DroppedCitation]]:
    """Find the reference chunk behind each ``(doc_id, page, excerpt)`` claim of a rule."""
    verified: list[VerifiedCitation] = []
    dropped: list[DroppedCitation] = []
    for claim in hit.citations:
        label = f"{claim.doc_id}:p{claim.page}"
        if ctx.store is None or ctx.reference_version_id is None:
            dropped.append(DroppedCitation(label, claim.excerpt, "not_found"))
            continue
        search = ctx.search if ctx.search is not None else ctx.store.search_bm25
        hits = await search(ctx.reference_version_id, claim.excerpt, RULE_RESOLVE_K)
        match = next(
            (
                h
                for h in hits
                if h.doc_id == claim.doc_id
                and (h.page_start or 0) <= claim.page <= (h.page_end or h.page_start or 0)
                and excerpt_supported(claim.excerpt, h.text)
            ),
            None,
        )
        if match is None:
            dropped.append(DroppedCitation(label, claim.excerpt, "not_found"))
            continue
        verified.append(
            VerifiedCitation(
                chunk_id=match.chunk_id,
                excerpt=claim.excerpt,
                doc_id=match.doc_id,
                page_start=match.page_start,
                page_end=match.page_end,
                method="rule:resolved+normalized_substring",
            )
        )
    return verified, dropped


async def _from_rule(
    component: ComponentView, hit: RuleHit, ctx: ClassifyContext
) -> ClassificationResult:
    verified, dropped = await resolve_rule_citations(hit, ctx)
    reasons: list[str] = []
    if dropped:
        reasons.append(CITATION_UNVERIFIED)
    if hit.contested:
        reasons.append(f"rule_contested:{hit.key}")
    reasons += _validation_reasons(component, hit.section, hit.bucket, ctx.property_class)
    return ClassificationResult(
        method="rule",
        category=hit.category,
        section=hit.section,
        bucket=hit.bucket,
        recovery_period=hit.recovery_period,
        asset_class=hit.asset_class,
        confidence=hit.confidence,
        needs_review=bool(reasons),
        review_reasons=_unique(reasons),
        citations=tuple(verified),
        dropped=tuple(dropped),
        rule_key=hit.key,
        rationale=hit.irs_note,
    )


TIER_WORDS: Final[dict[Tier, str]] = {
    "exact": "exact",
    "label_room": "label and room",
    "label": "label only",
}


def convention_rationale(cached: CachedClassification, tier: Tier = "exact") -> str:
    """``firm convention (approved by <reviewer> on study <id>, <date>; <n> approvals);
    agrees with the rules table: yes|no (<rule key> says <bucket>); matched: exact``, the
    match naming the ruling's key when a coarser tier served (``matched: label and room
    (ruling keyed <key>)``), then ``uncited`` for a ruling without citations and the
    engineer's reason when there is one."""
    when = cached.approved_at.date().isoformat() if cached.approved_at else "unknown date"
    plural = "approval" if cached.approvals == 1 else "approvals"
    head = (
        f"firm convention (approved by {cached.reviewer_id} on study {cached.study_id}, "
        f"{when}; {cached.approvals} {plural})"
    )
    if cached.rule_key is None or cached.agrees_with_rules is None:
        rules = "agrees with the rules table: n/a (no rule row)"
    else:
        says = cached.rule_bucket.value if cached.rule_bucket is not None else "?"
        verdict = "yes" if cached.agrees_with_rules else "no"
        rules = f"agrees with the rules table: {verdict} ({cached.rule_key} says {says})"
    matched = f"matched: {TIER_WORDS[tier]}"
    if tier != "exact":
        matched += f" (ruling keyed {cached.key})"
    parts = [f"{head}; {rules}; {matched}"]
    if not cached.citations:
        parts.append("uncited")
    if cached.rationale:
        parts.append(f"reason: {cached.rationale}")
    return "; ".join(parts)


async def _from_cache(
    component: ComponentView, match: ConventionMatch, ctx: ClassifyContext
) -> ClassificationResult:
    cached = match.cached
    verified: list[VerifiedCitation] = []
    dropped: list[DroppedCitation] = []
    reasons: list[str] = []
    if cached.citations:
        allowed = frozenset(v for v in (ctx.reference_version_id,) if v is not None)
        verified, dropped = await verify_citations(
            cached.citations,
            ctx.store,
            allowed_versions=allowed,
            method="cache:resolved+substring",
        )
        if dropped or not verified:
            reasons.append(CITATION_UNVERIFIED)
    # A ruling that disagrees with the rules table, or with the validators' label heuristics
    # ("looks like personal property"), is not a reason for review: overriding the defaults
    # is what a convention is for (the rationale says so). An inconsistent ruling still is.
    reasons += _validation_reasons(
        component, cached.section, cached.bucket, ctx.property_class, errors_only=True
    )
    return ClassificationResult(
        method="cache",
        category=cached.category,
        section=cached.section,
        bucket=cached.bucket,
        recovery_period=cached.period,
        asset_class=cached.asset_class,
        confidence=CACHE_CONFIDENCE,
        needs_review=bool(reasons),
        review_reasons=_unique(reasons),
        citations=tuple(verified),
        dropped=tuple(dropped),
        rule_key=cached.rule_key,
        cache_key=cached.key,
        rationale=convention_rationale(cached, match.tier),
        convention=cached.provenance(match.tier),
    )


def _fallback(
    component: ComponentView,
    ctx: ClassifyContext,
    reasons: list[str],
    outcome: ResidualOutcome | None,
) -> ClassificationResult:
    bucket = BUILDING_BUCKET_FOR_CLASS[ctx.property_class]
    return ClassificationResult(
        method="llm",
        category=f"Unclassified: {component.label}"[:120],
        section="1250",
        bucket=bucket,
        recovery_period=RECOVERY_YEARS[bucket],
        asset_class=None,
        confidence=0.0,
        needs_review=True,
        review_reasons=_unique(reasons),
        citations=(),
        rationale="no usable classification; building bucket assumed pending review",
        tool_calls=tuple(outcome.tool_calls) if outcome else (),
        calls=tuple(outcome.calls) if outcome else (),
    )


async def _from_model(component: ComponentView, ctx: ClassifyContext) -> ClassificationResult:
    if ctx.provider is None or ctx.store is None:
        return _fallback(component, ctx, ["classifier_unavailable"], None)
    tools = build_tools(
        ctx.study_id, ctx.reference_version_id, ctx.study_version_id, ctx.store, search=ctx.search
    )
    outcome = await run_residual(
        component,
        provider=ctx.provider,
        tools=tools,
        allowlist=RESIDUAL_ALLOWLIST,
        budget=ctx.budget,
        model=ctx.model,
        system_prompt=load_prompt(),
        prompt_version=ctx.prompt_version,
        study_id=ctx.study_id,
        limits=ctx.limits,
        price=ctx.price,
        reserve_usd_per_call=ctx.reserve_usd_per_call,
        reasoning_effort=ctx.reasoning_effort,
        ledger=(None if ctx.call is None else ctx.call.about("component", component.component_id)),
    )
    answer: Classification | None = outcome.classification
    if answer is None:
        return _fallback(component, ctx, [outcome.reason or "no_answer"], outcome)
    allowed = frozenset(
        v for v in (ctx.reference_version_id, ctx.study_version_id) if v is not None
    )
    verified, dropped = await verify_citations(
        answer.citations,
        ctx.store,
        allowed_versions=allowed,
        retrieved=frozenset(outcome.retrieved),
        method="llm:resolved+retrieved+normalized_substring",
    )
    bucket = Bucket(answer.bucket)
    confidence = answer.confidence
    reasons: list[str] = []
    if not outcome.searched:
        reasons.append(MISSING_EVIDENCE)
    if not outcome.searched or not verified:
        # An answer with no search, or with nothing verifiable behind it, is a guess.
        confidence = min(confidence, MISSING_EVIDENCE_CONFIDENCE)
    if dropped or not verified:
        reasons.append(CITATION_UNVERIFIED)
    if answer.needs_review:
        reasons.append(f"model:{answer.reason or 'requested review'}"[:300])
    reasons += _validation_reasons(component, answer.section, bucket, ctx.property_class)
    return ClassificationResult(
        method="llm",
        category=answer.category,
        section=answer.section,
        bucket=bucket,
        recovery_period=RECOVERY_YEARS[bucket],
        asset_class=answer.asset_class,
        confidence=confidence,
        needs_review=bool(reasons),
        review_reasons=_unique(reasons),
        citations=tuple(verified),
        dropped=tuple(dropped),
        rationale=answer.rationale,
        tool_calls=tuple(outcome.tool_calls),
        calls=tuple(outcome.calls),
    )


async def classify_with_model(
    component: ComponentView, ctx: ClassifyContext
) -> ClassificationResult:
    """The model path alone (rules table and cache skipped): what ``evals.predict_assets``
    scores. Same loop, citation verification and review policy as :func:`classify`."""
    return await _from_model(component, ctx)


def context_key(component: ComponentView, property_class: str) -> str:
    """The component's context key (``basis.classify.cache.cache_key``)."""
    return cache_key(
        component.label,
        component.material,
        component.attachment,
        property_class,
        component.room_type,
    )


class Route(NamedTuple):
    """What :func:`route` decided: the path, what serves it (a convention match, a rule
    hit, or nothing for the model), and the firm's conflicting rulings when a tier
    disagreed with itself and the rules table or the model decides instead."""

    method: Method
    found: ConventionMatch | RuleHit | None
    conflict: ConventionConflict | None = None


def route(component: ComponentView, ctx: ClassifyContext) -> Route:
    """Which path ``classify`` takes, without running it (used to reserve budget): the
    firm's convention at the most specific tier (section 9), else the rules table, else
    the model. A convention matched on the label alone does not outrank an uncontested
    rule row (9.2); a conflict inside a tier serves nothing (9.3)."""
    match = ctx.cache.match(context_key(component, ctx.property_class), org_id=ctx.org_id)
    hit = lookup(
        component.label,
        component.material,
        component.attachment,
        ctx.property_class,
        room_type=component.room_type,
    )
    conflict = match if isinstance(match, ConventionConflict) else None
    if isinstance(match, ConventionMatch) and (
        match.tier != "label" or hit is None or hit.contested
    ):
        return Route("cache", match)
    if hit is not None:
        return Route("rule", hit, conflict)
    return Route("llm", None, conflict)


def _flag_conflict(
    result: ClassificationResult, conflict: ConventionConflict
) -> ClassificationResult:
    reason = f"{CONVENTION_CONFLICT}:{conflict.tier}"
    return replace(
        result, needs_review=True, review_reasons=_unique([*result.review_reasons, reason])
    )


async def classify(component: ComponentView, ctx: ClassifyContext) -> ClassificationResult:
    method, found, conflict = route(component, ctx)
    if method == "cache" and isinstance(found, ConventionMatch):
        return await _from_cache(component, found, ctx)
    if method == "rule" and isinstance(found, RuleHit):
        result = await _from_rule(component, found, ctx)
    else:
        result = await _from_model(component, ctx)
    return result if conflict is None else _flag_conflict(result, conflict)


__all__ = [
    "CONVENTION_CONFLICT",
    "CitationClaim",
    "ClassificationCacheRepository",
    "ClassificationResult",
    "ClassifyContext",
    "Route",
    "classify",
    "classify_with_model",
    "context_key",
    "convention_rationale",
    "resolve_rule_citations",
    "route",
]
