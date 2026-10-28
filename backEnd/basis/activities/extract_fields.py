"""``extract_fields``: the appraisal's fields for one revision (S3 body).

Runs :func:`basis.extraction.pipeline.extract_appraisal_upload` on the uploaded appraisal. A
UAD 3.6 delivery (MISMO 3.6 XML, bare or in its ZIP) takes its values from the XML and its
highlight regions from the PDF (the ZIP's, else the study's latest other appraisal PDF);
docs/api/UAD36_INTAKE.md. A PDF runs :func:`basis.extraction.pipeline.extract_appraisal`: Azure DI
(when configured) merged with the text layer, cleaned, validated, with one LLM call for
critical fields that are missing or failed, and the review gate. The result is written
to ``extractions`` (``raw`` keeps the Azure DI analysis, candidates, issues and the
fallback trace) and one typed ``extraction_fields`` row per field.

Order of work, so that money is not spent twice and rows are written once:

1. A read-only look: the document (must be an uploaded appraisal of this study), an
   extraction already written for this revision (the business key: return it), and the
   Azure DI analysis of an earlier revision of the same document (reused, not re-paid).
2. The pipeline, outside any database transaction (it can take minutes); model calls
   record their ``llm_calls`` rows as they happen. The study budget for the fallback is
   ``settings.study_budget_usd`` minus the recorded spend (this activity's contract has no
   budget field; the workflow's own budget does not see the fallback's few cents).
3. ``run_once``: in one transaction, write the extraction, apply the reviewer's
   corrections (``extraction_field`` corrections from an appraisal rejection overwrite the
   same ``(section, field)`` with ``source="manual"``, confidence 1), recompute the review
   gate over the corrected fields, and complete the idempotency key.

Errors: no appraisal uploaded -> ``needs_review`` with no extraction (unchanged from S2);
a missing file, a non-PDF, a PDF with no pages, or a document Azure DI rejects ->
non-retryable ``ValidationError``; Azure DI 429/5xx/timeouts -> retryable.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._model_calls import heartbeating, provider_failure, run_pk
from basis.activities._support import heartbeat, parse_uuid, run_once, validation_error
from basis.activities.corrections import (
    check_extraction_field,
    parse_date,
    parse_number,
    parse_text,
)
from basis.activities.deps import Deps
from basis.db.enums import DocumentKind
from basis.db.models import Extraction
from basis.db.repositories.activity_result import COMPLETED, ActivityResultRepository
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.extraction import ExtractionRepository
from basis.db.repositories.llm_call import LlmCallRepository
from basis.db.session import session_scope
from basis.extraction.azure_di import AzureDiResult
from basis.extraction.confidence import review_gate
from basis.extraction.fallback import PROMPT_NAME
from basis.extraction.package import is_pdf, is_zip
from basis.extraction.pdf_text import InvalidDocument
from basis.extraction.persist import persist_extraction, stored_azure_result
from basis.extraction.pipeline import ExtractionContext, extract_appraisal_upload
from basis.extraction.results import ExtractedField, ExtractionResult
from basis.extraction.validation import validate_fields
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.errors import ProviderError
from basis.llm.provider import Budget
from basis.llm.recorder import LlmCallRecorder
from basis.storage import ObjectNotFound
from basis.workflow.contracts import CorrectionInput, ExtractFieldsInput, ExtractFieldsOutput

ACTIVITY: Final = "extract_fields"
MANUAL: Final = "manual"
READER_ISSUES: Final = frozenset({"sources_disagree", "azure_di_rejected"})


class ExtractFields:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="extract_fields")
    async def run(self, input: ExtractFieldsInput) -> ExtractFieldsOutput:
        study_id = parse_uuid(input.study_id, "study_id")
        if input.document_id is None:
            # No appraisal was uploaded: nothing to extract, the reviewer must look.
            async def nothing(session: AsyncSession) -> ExtractFieldsOutput:
                return ExtractFieldsOutput(extraction_id=None, fields=0, needs_review=True)

            return await self._run_once(input, nothing)
        document_id = parse_uuid(input.document_id, "document_id")
        heartbeat("start")
        for correction in input.corrections:  # cheap checks before paying for anything
            _check_correction(correction)

        async with session_scope(self._deps.sessionmaker) as session:
            document = await DocumentRepository(session).get(study_id, document_id)
            if document is None or document.kind is not DocumentKind.APPRAISAL:
                raise validation_error(f"appraisal {document_id} not found in study {study_id}")
            repo = ExtractionRepository(session)
            done = await repo.get_for_revision(document_id, input.revision) is not None
            claimed = await ActivityResultRepository(session).get(input.idempotency_key)
            done = done or (claimed is not None and claimed.status == COMPLETED)
            previous = await repo.latest_before(document_id, input.revision)
            reuse = stored_azure_result(previous.raw) if previous is not None else None
            run_id = await run_pk(session)
            spent = await LlmCallRepository(session).spent_usd(study_id)
            gcs_uri = document.gcs_uri
            companion_uri = await _companion_pdf_uri(session, study_id, document_id)

        result: ExtractionResult | None = None
        if not done:
            result = await self._extract(
                study_id, document_id, gcs_uri, run_id, spent, reuse, companion_uri
            )

        async def work(session: AsyncSession) -> ExtractFieldsOutput:
            repo = ExtractionRepository(session)
            existing = await repo.get_for_revision(document_id, input.revision)
            if existing is not None:
                # Written by an earlier execution for the same revision (e.g. after a
                # workflow reset); the business key wins over the activity key.
                return await _output(repo, existing)
            if result is None:  # pragma: no cover - only if the row vanished meanwhile
                raise RuntimeError("extraction disappeared between the check and the write")
            final = await _with_corrections(repo, study_id, result, input.corrections)
            extraction = await persist_extraction(
                session,
                study_id=study_id,
                document_id=document_id,
                revision=input.revision,
                result=final,
            )
            return await _output(repo, extraction)

        return await self._run_once(input, work)

    async def _run_once(
        self,
        input: ExtractFieldsInput,
        work: Callable[[AsyncSession], Awaitable[ExtractFieldsOutput]],
    ) -> ExtractFieldsOutput:
        return await run_once(
            self._deps,
            name=ACTIVITY,
            key=input.idempotency_key,
            output_type=ExtractFieldsOutput,
            work=work,
        )

    async def _extract(
        self,
        study_id: uuid.UUID,
        document_id: uuid.UUID,
        gcs_uri: str,
        run_id: uuid.UUID | None,
        spent: Decimal,
        reuse: AzureDiResult | None,
        companion_uri: str | None = None,
    ) -> ExtractionResult:
        settings = self._deps.settings
        try:
            data = await self._deps.storage.read(gcs_uri)
        except ObjectNotFound:
            raise validation_error(f"appraisal file {gcs_uri} is missing from storage") from None
        companion: bytes | None = None
        if companion_uri is not None and not is_pdf(data) and not is_zip(data):
            try:  # a bare UAD 3.6 XML: its PDF was uploaded as another appraisal document
                companion = await self._deps.storage.read(companion_uri)
            except ObjectNotFound:
                companion = None
        ctx = ExtractionContext(
            call=CallContext(
                study_id=study_id,
                run_id=run_id,
                activity=ACTIVITY,
                budget=Budget(remaining_usd=max(settings.study_budget_usd - float(spent), 0.0)),
                recorder=LlmCallRecorder(self._deps.sessionmaker),
            ),
            provider=self._deps.llm,
            azure=self._deps.azure_di,
            prompt=prompts.latest(PROMPT_NAME),
            text_model=settings.text_model,
            review_threshold=settings.extraction_review_threshold,
            fallback_max_tokens=settings.extraction_output_budget,
            fallback_reasoning_effort=settings.extraction_reasoning_effort,
            fallback_scope=settings.extraction_fallback_scope,
            fallback_context_chars=settings.extraction_fallback_context_chars,
            previous_azure=reuse,
            heartbeat=heartbeat,
        )
        try:
            async with heartbeating("extracting"):
                return await extract_appraisal_upload(
                    data, str(document_id), ctx, companion_pdf=companion
                )
        except InvalidDocument as exc:
            raise validation_error(f"appraisal {document_id}: {exc}") from exc
        except ProviderError as exc:
            raise provider_failure(exc, f"appraisal {document_id}") from exc


async def _companion_pdf_uri(
    session: AsyncSession, study_id: uuid.UUID, document_id: uuid.UUID
) -> str | None:
    """The study's latest uploaded appraisal PDF other than ``document_id``: the report PDF of
    a UAD 3.6 XML uploaded on its own."""
    documents = await DocumentRepository(session).list_for_study(study_id)
    pdfs = [
        d
        for d in documents
        if d.id != document_id
        and d.kind is DocumentKind.APPRAISAL
        and d.content_type == "application/pdf"
        and d.uploaded_at is not None
    ]
    if not pdfs:
        return None
    return max(pdfs, key=lambda d: (d.uploaded_at or d.created_at, d.created_at)).gcs_uri


def _check_correction(correction: CorrectionInput) -> None:
    if correction.entity_type != "extraction_field":
        raise validation_error(f"extract_fields cannot apply a {correction.entity_type} correction")
    check_extraction_field(correction.after)
    parse_uuid(correction.entity_id, "entity_id")


async def _with_corrections(
    repo: ExtractionRepository,
    study_id: uuid.UUID,
    result: ExtractionResult,
    corrections: tuple[CorrectionInput, ...],
) -> ExtractionResult:
    """``result`` with the reviewer's corrections applied and the gate recomputed."""
    if not corrections:
        return result
    fields = dict(result.fields)
    corrected: set[str] = set()
    for correction in corrections:
        _check_correction(correction)
        target = await repo.get_field(study_id, parse_uuid(correction.entity_id, "entity_id"))
        if target is None:
            raise validation_error(f"extraction field {correction.entity_id} not found")
        key = f"{target.section}.{target.field}"
        fields[key] = ExtractedField(
            key=key,
            raw_text=None,
            value_text=parse_text(correction.after.get("value_text")),
            value_num=parse_number(correction.after.get("value_num")),
            value_date=parse_date(correction.after.get("value_date")),
            confidence=1.0,
            source=MANUAL,
            page=target.page,
        )
        corrected.add(key)
    # Issues the validator does not recompute (reader disagreement, Azure DI refusal) are
    # kept unless the reviewer corrected every field they name.
    kept = [
        issue
        for issue in result.issues
        if issue.rule in READER_ISSUES and not (issue.keys and set(issue.keys) <= corrected)
    ]
    issues = kept + validate_fields(fields)
    threshold = float(result.raw.get("review_threshold", 0.90))
    gate = review_gate(fields, issues, threshold=threshold)
    raw = {
        **result.raw,
        "issues": [i.model_dump(mode="json") for i in issues],
        "review_reasons": list(gate.reasons),
        "corrected": sorted(corrected),
    }
    return result.model_copy(
        update={
            "fields": fields,
            "issues": issues,
            "overall_confidence": gate.overall_confidence,
            "needs_review": gate.needs_review,
            "review_reasons": list(gate.reasons),
            "raw": raw,
        }
    )


async def _output(repo: ExtractionRepository, extraction: Extraction) -> ExtractFieldsOutput:
    fields = await repo.fields(extraction.id)
    return ExtractFieldsOutput(
        extraction_id=str(extraction.id),
        fields=len(fields),
        needs_review=extraction.needs_review,
    )
