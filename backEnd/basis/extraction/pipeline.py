"""``extract_appraisal``: Azure DI, then the grounded reading or the pipeline, then the gate.

S3_SPEC section 2 (binding signature) and section 4; EVAL_REPORT section 33. One call per
appraisal revision:

1. Read the PDF's text layer and ruled tables (``pdfplumber``); malformed input raises
   :class:`~basis.extraction.pdf_text.InvalidDocument`.
2. Azure DI ``prebuilt-layout`` with key-value pairs when it is configured. When it is not,
   the text layer alone is used (``method="regex"``) and the result says so; a scanned PDF
   then has no text and every critical field is missing, which sends it to review. Azure DI
   answering 4xx (credentials, quota, a refused document) is handled the same way plus an
   ``azure_di_rejected`` issue; 429/5xx and timeouts propagate so the activity retries.
   A stored Azure DI result for the same document (an earlier revision) is reused
   instead of paying for a second analysis.

Then the fields are read by ``ctx.method`` (``EXTRACTION_METHOD``):

**grounded** (the production default, EVAL_REPORT section 33.5;
``method="azure_di+grounded"``): one structured call on ``ctx.grounded``
(``EXTRACTION_GROUNDED_MODEL``, default gpt-6.1-sol at effort low, 32,000 output tokens) over
Azure DI's page text with each selection mark labelled from the geometry, every value checked
mechanically against its verbatim quote, then the presence rules
(:mod:`basis.extraction.grounded`). Every value is an ``llm`` field at the 0.6 confidence cap;
the derived site-area fields (:mod:`basis.extraction.derived`) follow from the site area it
read; each field's region is chosen by the pipeline's highlight rules
(:func:`basis.extraction.regions.field_regions`: the checked box, else the value's words
nearest the caption on the cited page), else the quote's words (measured on the sweep's
subset: 0.996 of the checked highlights on the value, against 0.857 for the quote's words);
then the validation and the review gate as below. A UAD 3.6 report is asked only the keys its form
carries, by its UAD 3.6 captions. The quotes are kept in ``raw["grounded"]``.

**pipeline** (``method="azure_di+regex"``, before section 33):

3. Merge (Azure DI first; regex and tables only fill gaps; disagreement on a critical
   field is an issue), clean every value by its catalogue type, validate.
4. One structured LLM call for the fields still empty or failed (every catalogue field
   with ``EXTRACTION_FALLBACK_SCOPE=missing``, the default; only the critical ones with
   ``critical``; :mod:`basis.extraction.fallback`), then the derived site-area fields
   (:mod:`basis.extraction.derived`), then validate again.
5. The presence rules (:mod:`basis.extraction.presence`): a value for an approach or a
   cost-approach line the appraiser did not develop, or a certification box cited off the
   signature page, is dropped (after the fallback, so a dropped key is never re-asked), then
   validate again.
6. Each field's highlight regions from Azure DI's geometry (:mod:`basis.extraction.regions`;
   none without Azure DI), then the review gate (:mod:`basis.extraction.confidence`).

Errors of the grounded reading follow the pipeline fallback's contract
(:mod:`basis.extraction.fallback`): the extraction is best effort and still finishes. When the
grounded reading cannot run (no Azure DI analysis or no geometry in it, no model provider) or
its call does not answer (a provider error, an answer with no structured content, the study
budget exhausted), the document is read by the pipeline on the same Azure DI analysis (not
paid twice), with a note and the failed attempt in ``raw["grounded"]``; the pipeline's
fallback call is then itself best effort. An account or configuration error
(:class:`~basis.llm.errors.ProviderAccountError`) propagates from either call, so the stage
stops with the reason instead of finishing with fields that were never asked.

Two context hooks exist for the appraisal extraction sweep (``evals/appraisal/systems``) and
default to today's behaviour: ``fallback_pages`` replaces the page texts the pipeline's
fallback model is given (the sweep's ``checkbox_context`` passes Azure DI's page ``content``
with its ``:selected:`` marks instead of the page lines, which carry none), and
``always_ask`` adds keys the model is asked even when Azure DI or the text layer read them
(the checkbox boxes).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from basis.extraction import uad36_pdf
from basis.extraction.azure_di import AzureDiClient, AzureDiResult
from basis.extraction.confidence import review_gate
from basis.extraction.derived import AREA_SQFT, AREA_TEXT, derive_fields
from basis.extraction.fallback import WHOLE_DOCUMENT_CHARS, Scope, fallback_fields, fallback_keys
from basis.extraction.field_mappings import FIELDS
from basis.extraction.forms import FormDetection, detect_form
from basis.extraction.geometry import has_geometry, layout_of
from basis.extraction.grounded import PROMPT as GROUNDED_PROMPT
from basis.extraction.grounded import GroundedModel, read_fields
from basis.extraction.mismo36 import InvalidMismo, extract_mismo
from basis.extraction.package import (
    InvalidPackage,
    is_zip,
    looks_like_xml,
    read_appraisal_upload,
)
from basis.extraction.pdf_text import InvalidDocument, PdfText, read_pdf
from basis.extraction.presence import apply_presence_rules
from basis.extraction.regex_fields import extract_appraisal_fields, table_candidates
from basis.extraction.regions import field_regions, with_regions
from basis.extraction.results import ExtractedField, ExtractionResult, Region, ValidationIssue
from basis.extraction.sections import azure_candidates, merge
from basis.extraction.validation import validate_fields
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.errors import ProviderBadResponse
from basis.llm.prompts import Prompt
from basis.llm.provider import ChatProvider

logger = logging.getLogger("basis.extraction.pipeline")

METHOD_AZURE = "azure_di+regex"
METHOD_REGEX = "regex"
# ``EXTRACTION_METHOD=grounded``: the result's method (Azure DI's page text, one grounded call).
METHOD_GROUNDED = "azure_di+grounded"
ExtractionMethod = Literal["grounded", "pipeline"]
# Appended to the method when the PDF is a UAD 3.6 URAR (read by its own captions).
METHOD_UAD36_SUFFIX = "+uad36"
UAD36_PROMPT_NAME = "appraisal_field_fallback_uad36"


def _uad36_prompt() -> Prompt:
    return prompts.latest(UAD36_PROMPT_NAME)


def _no_heartbeat(_: str) -> None:
    return None


@dataclass
class ExtractionContext:
    """What :func:`extract_appraisal` needs besides the document."""

    call: CallContext
    provider: ChatProvider | None
    azure: AzureDiClient | None
    prompt: Prompt
    text_model: str
    review_threshold: float = 0.90
    fallback_max_tokens: int = 800
    # The fallback call's reasoning effort (``ChatRequest.reasoning_effort``; ignored by a
    # model that takes none). ``None`` = the provider's default.
    fallback_reasoning_effort: str | None = None
    previous_azure: AzureDiResult | None = None
    heartbeat: Callable[[str], None] = field(default=_no_heartbeat)
    # Which fields the fallback asks for and how much of the document it sends. The
    # extract_fields activity passes EXTRACTION_FALLBACK_SCOPE (default "missing") and
    # EXTRACTION_FALLBACK_CONTEXT_CHARS; a caller that sets nothing gets S3's "critical".
    fallback_scope: Scope = "critical"
    fallback_context_chars: int = WHOLE_DOCUMENT_CHARS
    # Module doc: the page texts the fallback is given when Azure DI analyzed the document
    # (``None`` = the Azure DI page lines), and keys always asked of the fallback.
    fallback_pages: Callable[[AzureDiResult], Sequence[str]] | None = None
    always_ask: frozenset[str] = frozenset()
    # The fallback prompt of a UAD 3.6 URAR (``prompt`` is the 1004's).
    uad36_prompt: Prompt = field(default_factory=_uad36_prompt)
    # Module doc: which method reads a PDF. The extract_fields activity passes
    # EXTRACTION_METHOD (default "grounded") and the EXTRACTION_GROUNDED_* model; a caller
    # that sets nothing gets the pipeline. ``grounded`` is required with "grounded".
    method: ExtractionMethod = "pipeline"
    grounded: GroundedModel | None = None


@dataclass
class _Analysis:
    """The document as read before any model call: the text layer, Azure DI, the form."""

    pdf: PdfText
    azure: AzureDiResult | None
    rejected: ValidationIssue | None
    notes: list[str]


async def _analyze(pdf_bytes: bytes, ctx: ExtractionContext) -> _Analysis:
    """Steps 1-2 of the module doc (paid once, whichever method reads the fields)."""
    ctx.heartbeat("read_pdf")
    pdf = await asyncio.to_thread(read_pdf, pdf_bytes)
    notes: list[str] = []
    if pdf.truncated:
        notes.append(f"read the first {len(pdf.pages)} of {pdf.page_count} pages")
    if not pdf.has_text_layer:
        notes.append("the PDF has no text layer (scanned)")

    azure: AzureDiResult | None = ctx.previous_azure
    rejected: ValidationIssue | None = None
    if azure is not None:
        notes.append("reused the Azure DI analysis of an earlier revision")
    elif ctx.azure is not None:
        ctx.heartbeat("azure_di")
        try:
            azure = await ctx.azure.analyze(pdf_bytes)
        except ProviderBadResponse as exc:
            # A 4xx (credentials, quota, a document the service refuses): retrying cannot
            # help and the text layer can still be read, so continue without Azure DI and
            # send the extraction to review with the reason. 429/5xx propagate (retried).
            logger.error("Azure DI rejected the appraisal (%s): %s", exc.status, exc)
            notes.append(f"Azure DI rejected the request ({exc.status}): text layer only")
            rejected = ValidationIssue(
                rule="azure_di_rejected",
                message=f"Azure DI returned {exc.status}: {exc}"[:500],
                severity="error",
            )
    if azure is None and ctx.azure is None:
        notes.append("Azure DI not configured: text layer and tables only")
    return _Analysis(pdf=pdf, azure=azure, rejected=rejected, notes=notes)


async def extract_appraisal(
    pdf_bytes: bytes, doc_id: str, ctx: ExtractionContext
) -> ExtractionResult:
    analysis = await _analyze(pdf_bytes, ctx)
    if ctx.method == "grounded":
        result, attempt = await _read_grounded(pdf_bytes, doc_id, ctx, analysis)
        if result is not None:
            return result
        # Module doc, "Errors": the pipeline reads the document on the same analysis.
        logger.warning(
            "grounded extraction of %s did not run (%s): read by the pipeline",
            doc_id,
            attempt.get("status"),
        )
        analysis.notes.append(
            f"grounded reading unavailable ({attempt.get('status')}): read by the pipeline"
        )
        return await _read_pipeline(doc_id, ctx, analysis, grounded=attempt)
    return await _read_pipeline(doc_id, ctx, analysis)


async def _read_pipeline(
    doc_id: str,
    ctx: ExtractionContext,
    analysis: _Analysis,
    *,
    grounded: dict[str, Any] | None = None,
) -> ExtractionResult:
    """Steps 3-6 of the module doc for ``EXTRACTION_METHOD=pipeline`` (``grounded``: the
    failed grounded attempt this replaces, kept in ``raw``)."""
    pdf, azure, rejected, notes = analysis.pdf, analysis.azure, analysis.rejected, analysis.notes
    method = METHOD_AZURE if azure is not None else METHOD_REGEX

    ctx.heartbeat("map_fields")
    form = detect_form(pdf.pages, azure.page_texts() if azure is not None else ())
    if form.is_uad36:
        method += METHOD_UAD36_SUFFIX
        notes.append(f"UAD 3.6 URAR (footer on {len(form.uad36_pages)} pages): its captions")
    regex = (
        uad36_pdf.text_candidates(pdf.pages)
        if form.is_uad36
        else extract_appraisal_fields(pdf.pages)
    )
    tables, pdf_comparables = table_candidates(
        ((table.page, table.rows) for table in pdf.tables), source="table"
    )
    if form.is_uad36:  # the 1004 cost-table rows are not this form's (the comparables stay)
        tables = []
    if azure is not None:
        primary, comparables = azure_candidates(azure)
        if form.is_uad36:
            primary = uad36_pdf.azure_candidates(azure)
        fields, merge_issues = merge(primary, [*regex, *tables])
        comparables = comparables or pdf_comparables
        context_pages = (
            list(ctx.fallback_pages(azure)) if ctx.fallback_pages else azure.page_texts()
        )
    else:
        fields, merge_issues = merge(regex, tables)
        comparables = pdf_comparables
        context_pages = pdf.pages

    if rejected is not None:
        merge_issues.append(rejected)
    # Keys the form does not carry: never asked, never required (forms module doc).
    off_form = uad36_pdf.NOT_CARRIED if form.is_uad36 else frozenset()
    fields = {**fields, **derive_fields(fields)}
    issues = merge_issues + validate_fields(fields, not_on_form=off_form)
    wanted = fallback_keys(fields, issues, ctx.fallback_scope)
    if ctx.always_ask:
        asked = set(wanted) | ctx.always_ask
        wanted = [spec.key for spec in FIELDS if spec.key in asked]
    if form.is_uad36:
        wanted = [key for key in wanted if key not in off_form]
    ctx.heartbeat("fallback")
    outcome = await fallback_fields(
        wanted,
        context_pages,
        provider=ctx.provider,
        ctx=ctx.call,
        prompt=ctx.uad36_prompt if form.is_uad36 else ctx.prompt,
        model=ctx.text_model,
        max_tokens=ctx.fallback_max_tokens,
        reasoning_effort=ctx.fallback_reasoning_effort,
        whole_document=ctx.fallback_scope == "missing",
        max_context_chars=ctx.fallback_context_chars,
        describe=uad36_pdf.describe if form.is_uad36 else None,
    )
    if outcome.fields:
        filled: dict[str, ExtractedField] = dict(fields)
        filled.update(outcome.fields)
        fields = {**filled, **derive_fields(filled)}
        issues = merge_issues + validate_fields(fields, not_on_form=off_form)
    sources = [pdf.pages] if azure is None else [pdf.pages, azure.page_texts()]
    presence = apply_presence_rules(fields, sources, not_on_form=off_form)
    if presence.dropped:
        fields = presence.fields
        issues = merge_issues + validate_fields(fields, not_on_form=off_form)

    fields = with_regions(fields, azure)
    gate = review_gate(fields, issues, threshold=ctx.review_threshold, not_on_form=off_form)
    summary = {**outcome.summary(), "presence": presence.dropped}
    raw: dict[str, Any] = {
        "method": method,
        "doc_id": doc_id,
        "page_count": pdf.page_count,
        "pages_read": len(pdf.pages),
        "notes": notes,
        "regex_candidates": [c.model_dump(mode="json") for c in [*regex, *tables]],
        "issues": [i.model_dump(mode="json") for i in issues],
        "review_reasons": list(gate.reasons),
        "review_threshold": ctx.review_threshold,
        "fallback": summary,
        "fallback_scope": ctx.fallback_scope,
        "presence": presence.dropped,
        "comparables": comparables,
        "form": _form_json(form),
    }
    if grounded is not None:
        raw["grounded"] = grounded
    if azure is not None:
        raw["azure_di"] = azure.model_dump(mode="json")
    return ExtractionResult(
        doc_id=doc_id,
        method=method,
        page_count=pdf.page_count,
        fields=fields,
        issues=issues,
        overall_confidence=gate.overall_confidence,
        needs_review=gate.needs_review,
        review_reasons=list(gate.reasons),
        comparables=comparables,
        fallback=summary,
        notes=notes,
        raw=raw,
    )


async def _read_grounded(
    pdf_bytes: bytes, doc_id: str, ctx: ExtractionContext, analysis: _Analysis
) -> tuple[ExtractionResult | None, dict[str, Any]]:
    """``EXTRACTION_METHOD=grounded`` (module doc): the result, or ``None`` and why the
    reading did not run (the attempt kept in the pipeline result's ``raw["grounded"]``)."""
    model = ctx.grounded
    if model is None:
        raise ValueError("EXTRACTION_METHOD=grounded needs ExtractionContext.grounded")
    attempt: dict[str, Any] = {
        "model": model.model,
        "reasoning_effort": model.reasoning_effort,
        "max_tokens": model.max_tokens,
    }
    azure = analysis.azure
    if azure is None:
        return None, {**attempt, "status": "no_azure_di"}
    if not has_geometry(azure):
        return None, {**attempt, "status": "no_geometry"}
    if ctx.provider is None:
        return None, {**attempt, "status": "no_provider"}
    ctx.heartbeat("grounded")
    geometry = layout_of(azure)
    reading = await read_fields(
        geometry, provider=ctx.provider, call=ctx.call, model=model, pdf=pdf_bytes
    )
    trace = reading.summary()
    if not reading.answered:
        failed = {k: trace[k] for k in ("status", "error", "calls")}
        return None, {**attempt, **failed}

    pdf, notes = analysis.pdf, analysis.notes
    checked = reading.checked
    method = METHOD_GROUNDED
    off_form: frozenset[str] = frozenset()
    if reading.uad36:
        method += METHOD_UAD36_SUFFIX
        off_form = uad36_pdf.NOT_CARRIED
        pages = len(reading.form.uad36_pages)
        notes.append(f"UAD 3.6 URAR (footer on {pages} pages): its UAD 3.6 captions")
    read: dict[str, ExtractedField] = dict(checked.fields)
    # The site area's other views, from the reading the model quoted.
    read.update(derive_fields(read))
    quoted = {key: _regions(boxes) for key, boxes in checked.regions.items()}
    source = AREA_SQFT if AREA_SQFT in quoted else AREA_TEXT
    fields: dict[str, ExtractedField] = {}
    for key, item in read.items():
        # The pipeline's highlight rules (the checked box, else the value's words nearest the
        # caption on the cited page); else the quote's words.
        boxes = field_regions(item, geometry) or quoted.get(key) or quoted.get(source, ())
        fields[key] = item.model_copy(update={"regions": boxes})
    issues = validate_fields(fields, not_on_form=off_form)
    gate = review_gate(fields, issues, threshold=ctx.review_threshold, not_on_form=off_form)
    _, comparables = azure_candidates(azure)
    if not comparables:
        _, comparables = table_candidates(
            ((table.page, table.rows) for table in pdf.tables), source="table"
        )
    raw: dict[str, Any] = {
        "method": method,
        "doc_id": doc_id,
        "page_count": pdf.page_count,
        "pages_read": len(pdf.pages),
        "notes": notes,
        "issues": [i.model_dump(mode="json") for i in issues],
        "review_reasons": list(gate.reasons),
        "review_threshold": ctx.review_threshold,
        "fallback": trace,
        "presence": reading.presence,
        "comparables": comparables,
        "form": _form_json(reading.form),
        "grounded": {
            **attempt,
            "status": trace["status"],
            "prompt_version": prompts.latest(GROUNDED_PROMPT).version_id,
            "quotes": dict(sorted(checked.quotes.items())),
        },
        "azure_di": azure.model_dump(mode="json"),
    }
    return (
        ExtractionResult(
            doc_id=doc_id,
            method=method,
            page_count=pdf.page_count,
            fields=fields,
            issues=issues,
            overall_confidence=gate.overall_confidence,
            needs_review=gate.needs_review,
            review_reasons=list(gate.reasons),
            comparables=comparables,
            fallback=trace,
            notes=notes,
            raw=raw,
        ),
        attempt,
    )


def _regions(items: Iterable[Mapping[str, Any]]) -> tuple[Region, ...]:
    """:class:`Region` objects from the reading's JSON regions (``{"page", "bbox"}``)."""
    out = []
    for item in items:
        x0, y0, x1, y1 = (float(v) for v in item["bbox"])
        out.append(Region(page=int(item["page"]), bbox=(x0, y0, x1, y1)))
    return tuple(out)


def _form_json(form: FormDetection) -> dict[str, Any]:
    data = form.as_json()
    if form.is_uad36:
        data["not_on_form"] = sorted(uad36_pdf.NOT_CARRIED)
    return data


async def extract_appraisal_upload(
    data: bytes,
    doc_id: str,
    ctx: ExtractionContext,
    *,
    companion_pdf: bytes | None = None,
) -> ExtractionResult:
    """An uploaded appraisal of any accepted kind (:mod:`basis.extraction.package`).

    A PDF goes through :func:`extract_appraisal`, unchanged. A UAD 3.6 XML (bare, or in its
    delivery ZIP) is read by :func:`basis.extraction.mismo36.extract_mismo`: the values come
    from the XML; the PDF (the ZIP's, else ``companion_pdf``, a PDF uploaded separately for the
    same study) is analyzed by Azure DI layout (or the earlier revision's analysis is reused)
    only to place each value's highlight region and cross-check that it is printed. No model is
    called. Unreadable input raises :class:`InvalidDocument`."""
    if not is_zip(data) and not looks_like_xml(data):
        return await extract_appraisal(data, doc_id, ctx)  # a PDF (or not one: its errors)
    try:
        upload = read_appraisal_upload(data)
    except InvalidPackage as exc:
        raise InvalidDocument(str(exc)) from exc
    assert upload.xml is not None
    notes = list(upload.notes)
    pdf = upload.pdf if upload.pdf is not None else companion_pdf
    if upload.pdf is None and companion_pdf is not None:
        notes.append("the report PDF is the study's separately uploaded appraisal PDF")
    azure: AzureDiResult | None = None
    page_count = 0
    if pdf is not None:
        ctx.heartbeat("read_pdf")
        page_count = (await asyncio.to_thread(read_pdf, pdf)).page_count
        azure = ctx.previous_azure
        if azure is not None:
            notes.append("reused the Azure DI analysis of an earlier revision")
        elif ctx.azure is not None:
            ctx.heartbeat("azure_di")
            try:
                azure = await ctx.azure.analyze(pdf)
            except ProviderBadResponse as exc:
                logger.error("Azure DI rejected the appraisal PDF (%s): %s", exc.status, exc)
                notes.append(f"Azure DI rejected the PDF ({exc.status}): no highlight regions")
        else:
            notes.append("Azure DI not configured: no highlight regions")
    else:
        notes.append("no PDF of the report: the XML values have no highlight regions")
    layout = layout_of(azure) if azure is not None and has_geometry(azure) else None
    ctx.heartbeat("mismo_xml")
    try:
        result = await asyncio.to_thread(
            extract_mismo,
            upload.xml,
            doc_id,
            layout=layout,
            page_count=page_count,
            review_threshold=ctx.review_threshold,
            notes=notes,
        )
    except InvalidMismo as exc:
        raise InvalidDocument(str(exc)) from exc
    result.raw["upload_kind"] = upload.kind
    if azure is not None:
        result.raw["azure_di"] = azure.model_dump(mode="json")
    return result
