"""What every appraisal-extraction sweep system shares: its inputs, its output, the per-document
analyses, and the field rules the systems agree on.

A system is ``async run(env) -> SystemOutput``: the production
:class:`~basis.extraction.results.ExtractionResult` (so the scorer is unchanged) plus, per
field, the **regions** the value was read from (``[{page, bbox}]``, bbox as page fractions;
``None`` for a system that cannot say, which the highlight metric reports as n/a), its
**origin** (which reader produced it: ``azure`` / ``regex`` / ``table`` / ``llm`` from the
production pipeline, ``azure_1004``, ``llm_reask``, ``vote_agree`` ...) and, for the grounded
model systems, the verbatim **quote**.

:class:`DocumentSources` analyzes a document once per run and model (the Azure DI layout, the
``prebuilt-mortgage.us.1004`` model) however many systems or repeats ask for it; it counts the
pages each model analyzed (the sweep's Azure cost) and keeps the raw layout for the geometry
(:mod:`evals.appraisal.systems.layout`).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Protocol

import basis.extraction.derived as _derived
import basis.extraction.field_mappings as _catalogue
import basis.extraction.grounded as _grounded
from basis.config import Settings
from basis.extraction.azure_di import MODEL_ID as LAYOUT_MODEL_ID
from basis.extraction.azure_di import AzureDiClient, AzureDiResult
from basis.extraction.confidence import review_gate
from basis.extraction.results import ExtractedField, ExtractionResult, ValidationIssue
from basis.extraction.validation import validate_fields
from basis.llm.call import CallContext
from basis.llm.errors import ProviderBadResponse
from basis.llm.provider import ChatProvider
from evals.appraisal.systems.layout import Layout, from_raw

MORTGAGE_1004_MODEL_ID: Final = "prebuilt-mortgage.us.1004"
# Answer budget per model call of the grounded systems: llm_all asks ~125 keys at ~50
# output tokens each with the quote (~6,000), and an OpenAI reasoning model spends part of
# max_completion_tokens reasoning, so twice that; Claude adds its thinking headroom on top
# (basis.llm.cost). A cut-off answer is unusable (no fields), so this errs high: only the
# tokens used are billed.
DEFAULT_MAX_TOKENS: Final = 16000
# Page images of llm_pages / llm_sections (basis.extraction.grounded): the DPI, and the Azure
# DI word count under which a page is a photo, sketch or map page and gets no image.
DEFAULT_DPI: Final = _grounded.DEFAULT_DPI
DEFAULT_MIN_PAGE_WORDS: Final = _grounded.DEFAULT_MIN_PAGE_WORDS

# Keys filled from another field's reading (basis.extraction.derived), never asked.
DERIVED_KEYS: Final = _derived.DERIVED_KEYS
ASKED_KEYS: Final[tuple[str, ...]] = _grounded.ASKED_KEYS

# The legacy 1004's checkbox groups live in the catalogue (the pipeline's highlight regions use
# them too); re-exported here for the sweep systems.
CHECKBOX_KEYS: Final = _catalogue.CHECKBOX_KEYS
SINGLE_CHECKBOX_KEYS: Final = _catalogue.SINGLE_CHECKBOX_KEYS
MULTI_CHECKBOX_KEYS: Final = _catalogue.MULTI_CHECKBOX_KEYS


class MissingInput(RuntimeError):
    """A system cannot run on this document (a missing input it needs)."""


_GROUNDED_DEFAULTS: Final = {
    name: Settings.model_fields[f"extraction_grounded_{name}"].default
    for name in ("model", "reasoning_effort", "max_tokens")
}


@dataclass(frozen=True)
class ExtractionSettings:
    """The production settings that shape the extraction (what ``context.json`` records).

    ``method``: ``EXTRACTION_METHOD``; ``grounded_*``: the ``EXTRACTION_GROUNDED_*`` model of
    the grounded reading. A context recorded before those settings existed (every recording
    of the pipeline sweep) replays with the current values of the keys it lacks
    (:meth:`from_json`); a value built without them is the pipeline."""

    text_model: str
    reasoning_effort: str | None
    output_budget: int
    fallback_scope: str
    context_chars: int
    review_threshold: float
    method: str = "pipeline"
    grounded_model: str = _GROUNDED_DEFAULTS["model"]
    grounded_reasoning_effort: str | None = _GROUNDED_DEFAULTS["reasoning_effort"]
    grounded_max_tokens: int = _GROUNDED_DEFAULTS["max_tokens"]

    @classmethod
    def from_settings(cls, settings: Settings) -> ExtractionSettings:
        return cls(
            text_model=settings.text_model,
            reasoning_effort=settings.extraction_reasoning_effort,
            output_budget=settings.extraction_output_budget,
            fallback_scope=settings.extraction_fallback_scope,
            context_chars=settings.extraction_fallback_context_chars,
            review_threshold=settings.extraction_review_threshold,
            method=settings.extraction_method,
            grounded_model=settings.extraction_grounded_model,
            grounded_reasoning_effort=settings.extraction_grounded_reasoning_effort,
            grounded_max_tokens=settings.extraction_grounded_max_tokens,
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Any], current: ExtractionSettings) -> ExtractionSettings:
        """The recorded context; the review threshold, and any key the recording predates,
        from ``current``."""
        return cls(
            text_model=str(data["text_model"]),
            reasoning_effort=data.get("reasoning_effort"),
            output_budget=int(data["output_budget"]),
            fallback_scope=str(data["fallback_scope"]),
            context_chars=int(data["context_chars"]),
            review_threshold=current.review_threshold,
            method=str(data.get("method", current.method)),
            grounded_model=str(data.get("grounded_model", current.grounded_model)),
            grounded_reasoning_effort=data.get(
                "grounded_reasoning_effort", current.grounded_reasoning_effort
            ),
            grounded_max_tokens=int(data.get("grounded_max_tokens", current.grounded_max_tokens)),
        )


@dataclass(frozen=True)
class ModelChoice:
    """``--model`` / ``--effort`` / ``--max-tokens``. ``model=None``: the production
    ``TEXT_MODEL`` (and its effort) for the production-based systems; required elsewhere."""

    model: str | None = None
    effort: str | None = None
    max_tokens: int = DEFAULT_MAX_TOKENS


@dataclass(frozen=True)
class VoterSpec:
    system: str
    model: ModelChoice


@dataclass(frozen=True)
class SystemSpec:
    """Which system and its options (the CLI's ``--system`` and friends)."""

    name: str = "production"
    model: ModelChoice = field(default_factory=ModelChoice)
    base: str | None = None  # validate_reask
    base_model: ModelChoice | None = None  # validate_reask: the base's model (default: model)
    voters: tuple[VoterSpec, ...] = ()  # vote
    dpi: int = DEFAULT_DPI
    with_images: bool = False  # llm_sections
    min_page_words: int = DEFAULT_MIN_PAGE_WORDS

    def label(self) -> str:
        """A short id for run directories and reports."""
        parts = [self.name]
        if self.base:
            parts.append(f"base-{self.base}")
        if self.voters:
            parts.append(
                "+".join(f"{v.system}@{v.model.model or 'text_model'}" for v in self.voters)
            )
        elif self.model.model:
            parts.append(self.model.model)
        if self.model.effort:
            parts.append(self.model.effort)
        if self.with_images:
            parts.append("images")
        return "-".join(parts)


class RawAnalyzer(Protocol):
    """A recorded or replayed Document Intelligence model returning the raw ``as_dict()``."""

    async def analyze(self, pdf_bytes: bytes) -> dict[str, Any]: ...


class DocumentSources:
    """One document's analyses, each run at most once (concurrent callers wait for it).

    ``layout``: the production layout client (recording, replay); its ``raw`` attribute, when
    it has one, is the ``as_dict()`` the geometry is read from. ``mortgage``: the 1004 model.
    A refused document (:class:`ProviderBadResponse`) is remembered and raised again."""

    def __init__(self, layout: AzureDiClient | None, mortgage: RawAnalyzer | None = None) -> None:
        self._layout_client = layout
        self._mortgage_client = mortgage
        self._lock = asyncio.Lock()
        self._layout: AzureDiResult | None = None
        self._layout_error: ProviderBadResponse | None = None
        self._layout_done = False
        self._geometry: Layout | None = None
        self._mortgage: dict[str, Any] | None = None
        self._mortgage_error: ProviderBadResponse | None = None
        self.pages_by_model: dict[str, int] = {}

    @property
    def has_layout(self) -> bool:
        return self._layout_client is not None

    async def layout(self, pdf: bytes) -> AzureDiResult | None:
        """The production layout analysis (``None`` without a layout client)."""
        if self._layout_client is None:
            return None
        async with self._lock:
            if not self._layout_done:
                try:
                    self._layout = await self._layout_client.analyze(pdf)
                    self.pages_by_model[LAYOUT_MODEL_ID] = len(self._layout.pages)
                except ProviderBadResponse as error:
                    self._layout_error = error
                self._layout_done = True
        if self._layout_error is not None:
            raise self._layout_error
        return self._layout

    async def geometry(self, pdf: bytes) -> Layout:
        """Words, marks and page text with marks from the raw layout recording."""
        if self._geometry is None:
            if await self.layout(pdf) is None:
                raise MissingInput("this system needs the Azure DI layout analysis")
            raw = getattr(self._layout_client, "raw", None)
            if not isinstance(raw, Mapping):
                raise MissingInput(
                    "this system needs the raw Azure DI layout (azure_di.json: words, "
                    "selection marks); the recording has only the converted result"
                )
            self._geometry = from_raw(raw)
        return self._geometry

    def layout_raw(self) -> Mapping[str, Any] | None:
        """The raw layout ``as_dict()`` once analyzed (``None`` before, or when the
        recording holds only the converted result)."""
        raw = getattr(self._layout_client, "raw", None) if self._layout_done else None
        return raw if isinstance(raw, Mapping) else None

    def geometry_if_loaded(self) -> Layout | None:
        """The geometry when the raw layout is at hand (no analysis is started)."""
        raw = self.layout_raw()
        if self._geometry is None and raw is not None:
            self._geometry = from_raw(raw)
        return self._geometry

    async def mortgage(self, pdf: bytes) -> dict[str, Any]:
        """The raw ``prebuilt-mortgage.us.1004`` analysis."""
        if self._mortgage_client is None:
            raise MissingInput("this system needs the prebuilt-mortgage.us.1004 client")
        async with self._lock:
            if self._mortgage is None and self._mortgage_error is None:
                try:
                    self._mortgage = await self._mortgage_client.analyze(pdf)
                    pages = len(self._mortgage.get("pages") or ())
                    self.pages_by_model[MORTGAGE_1004_MODEL_ID] = pages
                except ProviderBadResponse as error:
                    self._mortgage_error = error
        if self._mortgage_error is not None:
            raise self._mortgage_error
        assert self._mortgage is not None
        return self._mortgage


@dataclass
class SystemEnv:
    """What a system gets for one document and repeat."""

    pdf: bytes
    doc_id: str
    call: CallContext
    provider: ChatProvider | None
    sources: DocumentSources
    extraction: ExtractionSettings
    pdf_path: Path | None = None  # the PDF on disk (mismo_xml reads the XML beside it)


@dataclass
class SystemOutput:
    result: ExtractionResult
    regions: dict[str, list[dict[str, Any]]] | None = None  # None: the system has none
    origins: dict[str, str] = field(default_factory=dict)
    quotes: dict[str, str] = field(default_factory=dict)

    def origin(self, key: str) -> str:
        item = self.result.fields.get(key)
        return self.origins.get(key) or (item.source if item is not None else "none")


class System(Protocol):
    name: str

    async def run(self, env: SystemEnv) -> SystemOutput: ...


# --------------------------------------------------------------------------- results


def finish(
    *,
    doc_id: str,
    method: str,
    page_count: int,
    fields: Mapping[str, ExtractedField],
    review_threshold: float,
    extra_issues: Sequence[ValidationIssue] = (),
    extra_reasons: Sequence[str] = (),
    fallback: Mapping[str, Any] | None = None,
    notes: Sequence[str] = (),
    raw: Mapping[str, Any] | None = None,
    today: date | None = None,
) -> ExtractionResult:
    """An :class:`ExtractionResult` the way the production pipeline closes one: the
    per-field and cross-field validation, the system's own issues, the review gate."""
    issues = [*extra_issues, *validate_fields(fields, today=today)]
    gate = review_gate(fields, issues, threshold=review_threshold)
    reasons = list(gate.reasons) + [r for r in extra_reasons if r not in gate.reasons]
    return ExtractionResult(
        doc_id=doc_id,
        method=method,
        page_count=page_count,
        fields=dict(fields),
        issues=issues,
        overall_confidence=gate.overall_confidence,
        needs_review=gate.needs_review or bool(reasons),
        review_reasons=reasons,
        fallback=dict(fallback or {}),
        notes=list(notes),
        raw=dict(raw or {}),
    )


def own_issues(result: ExtractionResult, today: date | None = None) -> list[ValidationIssue]:
    """The issues of ``result`` that its fields' validation does not produce again (merge
    disagreements, an Azure DI refusal, a system's own checks): what survives re-validating
    changed fields."""
    recomputed = validate_fields(result.fields, today=today)
    return [issue for issue in result.issues if issue not in recomputed]


# --------------------------------------------------------------------------- comparing values

_PARTS: Final = re.compile(r"[,;\n]")


def fold_value(text: str | None) -> str:
    """Letters and digits only, casefolded (``"Det."`` = ``"det"``, ``"$85,000"`` = ``"85000"``)."""
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def value_parts(text: str | None) -> frozenset[str]:
    """The folded options of a value (``"Crawl Space, Full Basement"``)."""
    return frozenset(p for p in (fold_value(part) for part in _PARTS.split(text or "")) if p)


def same_value(a: ExtractedField, b: ExtractedField) -> bool:
    """Two readings of one box agree: equal typed values (numbers, dates) when both have one,
    else the same folded options (order, case and punctuation ignored)."""
    ta, tb = a.typed, b.typed
    if isinstance(ta, Decimal | date) and isinstance(tb, Decimal | date):
        return ta == tb
    return value_parts(a.value_text) == value_parts(b.value_text) and bool(
        value_parts(a.value_text)
    )
