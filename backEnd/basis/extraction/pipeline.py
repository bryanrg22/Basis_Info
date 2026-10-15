"""``extract_appraisal``: Azure DI -> regex/table merge -> clean -> validate -> fallback.

S3_SPEC section 2 (binding signature) and section 4. One call per appraisal revision:

1. Read the PDF's text layer and ruled tables (``pdfplumber``); malformed input raises
   :class:`~basis.extraction.pdf_text.InvalidDocument`.
2. Azure DI ``prebuilt-layout`` with key-value pairs when it is configured
   (``method="azure_di+regex"``). When it is not, the text layer alone is used
   (``method="regex"``) and the result says so; a scanned PDF then has no text and every
   critical field is missing, which sends it to review. Azure DI answering 4xx
   (credentials, quota, a refused document) is handled the same way plus an
   ``azure_di_rejected`` issue; 429/5xx and timeouts propagate so the activity retries.
   A stored Azure DI result for the same document (an earlier revision) is reused
   instead of paying for a second analysis.
3. Merge (Azure DI first; regex and tables only fill gaps; disagreement on a critical
   field is an issue), clean every value by its catalogue type, validate.
4. One structured LLM call for the critical fields that are missing or failed a
   per-field rule (:mod:`basis.extraction.fallback`), then validate again.
5. The review gate (:mod:`basis.extraction.confidence`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from basis.extraction.azure_di import AzureDiClient, AzureDiResult
from basis.extraction.confidence import review_gate
from basis.extraction.fallback import fallback_fields
from basis.extraction.pdf_text import read_pdf
from basis.extraction.regex_fields import extract_appraisal_fields, table_candidates
from basis.extraction.results import ExtractedField, ExtractionResult, ValidationIssue
from basis.extraction.sections import azure_candidates, merge
from basis.extraction.validation import failed_critical_keys, validate_fields
from basis.llm.call import CallContext
from basis.llm.errors import ProviderBadResponse
from basis.llm.prompts import Prompt
from basis.llm.provider import ChatProvider

logger = logging.getLogger("basis.extraction.pipeline")

METHOD_AZURE = "azure_di+regex"
METHOD_REGEX = "regex"


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
    previous_azure: AzureDiResult | None = None
    heartbeat: Callable[[str], None] = field(default=_no_heartbeat)


async def extract_appraisal(
    pdf_bytes: bytes, doc_id: str, ctx: ExtractionContext
) -> ExtractionResult:
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
    method = METHOD_AZURE if azure is not None else METHOD_REGEX
    if azure is None and ctx.azure is None:
        notes.append("Azure DI not configured: text layer and tables only")

    ctx.heartbeat("map_fields")
    regex = extract_appraisal_fields(pdf.pages)
    tables, pdf_comparables = table_candidates(
        ((table.page, table.rows) for table in pdf.tables), source="table"
    )
    if azure is not None:
        primary, comparables = azure_candidates(azure)
        fields, merge_issues = merge(primary, [*regex, *tables])
        comparables = comparables or pdf_comparables
        context_pages = azure.page_texts()
    else:
        fields, merge_issues = merge(regex, tables)
        comparables = pdf_comparables
        context_pages = pdf.pages

    if rejected is not None:
        merge_issues.append(rejected)
    issues = merge_issues + validate_fields(fields)
    failed = failed_critical_keys(fields, issues)
    ctx.heartbeat("fallback")
    outcome = await fallback_fields(
        failed,
        context_pages,
        provider=ctx.provider,
        ctx=ctx.call,
        prompt=ctx.prompt,
        model=ctx.text_model,
        max_tokens=ctx.fallback_max_tokens,
    )
    if outcome.fields:
        filled: dict[str, ExtractedField] = dict(fields)
        filled.update(outcome.fields)
        fields = filled
        issues = merge_issues + validate_fields(fields)

    gate = review_gate(fields, issues, threshold=ctx.review_threshold)
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
        "fallback": outcome.summary(),
        "comparables": comparables,
    }
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
        fallback=outcome.summary(),
        notes=notes,
        raw=raw,
    )
