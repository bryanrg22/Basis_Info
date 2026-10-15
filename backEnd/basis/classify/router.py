"""``classify(component, ctx)``: rules table, then the approved-answer cache, then the model.

1. **Rule** (:func:`basis.domain.rules.lookup`): no model call. The rule's citation
   claims are resolved against the reference corpus (search for the excerpt, keep a hit
   from the cited document and page whose text contains it).
2. **Cache** (``ctx.cache``, prefetched from ``classification_cache``): no model call;
   cached citations are re-verified against the reference corpus version in use.
3. **LLM** (:mod:`basis.classify.residual`): the bounded tool loop. Citations must
   resolve, be in scope, have been retrieved during this loop, and contain the excerpt.

Review policy (any one sets ``needs_review``; all reasons are kept, in a fixed order):
a dropped or unresolvable citation (``citation_unverified``), zero verified citations on
the model path (``citation_unverified``), a model answer without any search
(``missing_evidence``, confidence capped at 0.3), a contested rule row, a model that asks
for review, a loop failure, and any validator error or warning. When the model gives no
usable answer the asset gets the building bucket for the property class (no accelerated
depreciation is claimed) with confidence 0 and the failure as the reason.

The result carries no study status and nothing here writes one; the caller turns results
into rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final, Literal

from basis.classify.cache import (
    CachedClassification,
    ClassificationCacheRepository,
    cache_key,
    promote_to_cache,
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


@dataclass(frozen=True)
class ClassifyContext:
    study_id: str
    property_class: PropertyClass
    budget: Budget
    store: ChunkStore | None = None
    reference_version_id: uuid.UUID | None = None
    study_version_id: uuid.UUID | None = None
    provider: ChatProvider | None = None
    cache: Mapping[str, CachedClassification] = field(default_factory=dict)
    model: str = "gpt-4o-mini"
    limits: LoopLimits = DEFAULT_LIMITS
    price: PriceFn = default_price
    reserve_usd_per_call: float = RESERVE_USD_PER_CALL
    search: SearchFn | None = None
    prompt_version: str = PROMPT_VERSION


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
    component: ComponentView, section: str | None, bucket: Bucket, property_class: str
) -> list[str]:
    issues = validate_classification(
        label=component.label,
        section=section,
        bucket=bucket,
        recovery_period=RECOVERY_YEARS.get(bucket, Decimal(0)),
        property_class=property_class,
        room_type=component.room_type,
    )
    return [f"validation:{i.code}" for i in issues if i.needs_review]


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


async def _from_cache(
    component: ComponentView, cached: CachedClassification, ctx: ClassifyContext
) -> ClassificationResult:
    allowed = frozenset(v for v in (ctx.reference_version_id,) if v is not None)
    verified, dropped = await verify_citations(
        cached.citations, ctx.store, allowed_versions=allowed, method="cache:resolved+substring"
    )
    reasons: list[str] = []
    if dropped or not verified:
        reasons.append(CITATION_UNVERIFIED)
    reasons += _validation_reasons(component, cached.section, cached.bucket, ctx.property_class)
    return ClassificationResult(
        method="cache",
        category=cached.category,
        section=cached.section,
        bucket=cached.bucket,
        recovery_period=RECOVERY_YEARS[cached.bucket],
        asset_class=cached.asset_class,
        confidence=CACHE_CONFIDENCE,
        needs_review=bool(reasons),
        review_reasons=_unique(reasons),
        citations=tuple(verified),
        dropped=tuple(dropped),
        cache_key=cached.key,
        rationale=f"engineer-approved classification ({cached.source})",
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


def route(
    component: ComponentView, ctx: ClassifyContext
) -> tuple[Method, RuleHit | CachedClassification | None]:
    """Which path ``classify`` takes, without running it (used to reserve budget)."""
    hit = lookup(component.label, component.material, component.attachment, ctx.property_class)
    if hit is not None:
        return "rule", hit
    key = cache_key(component.label, component.material, component.attachment, ctx.property_class)
    cached = ctx.cache.get(key)
    if cached is not None:
        return "cache", cached
    return "llm", None


async def classify(component: ComponentView, ctx: ClassifyContext) -> ClassificationResult:
    method, found = route(component, ctx)
    if method == "rule" and isinstance(found, RuleHit):
        return await _from_rule(component, found, ctx)
    if method == "cache" and isinstance(found, CachedClassification):
        return await _from_cache(component, found, ctx)
    return await _from_model(component, ctx)


__all__ = [
    "CitationClaim",
    "ClassificationCacheRepository",
    "ClassificationResult",
    "ClassifyContext",
    "classify",
    "promote_to_cache",
    "resolve_rule_citations",
    "route",
]
