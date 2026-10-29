"""``extract_fields``: the appraisal's fields for one revision (S3 body).

Runs :func:`basis.extraction.pipeline.extract_appraisal_upload` on the uploaded appraisal. A
UAD 3.6 delivery (MISMO 3.6 XML, bare or in its ZIP) takes its values from the XML and its
highlight regions from the PDF (the ZIP's, else the report PDF ``ingest_appraisal`` chose,
``input.report_pdf``; :mod:`basis.activities.appraisal_sources`); docs/api/UAD36_INTAKE.md.
``raw["source"]`` records the source document, its kind and the report PDF's document.
A PDF runs :func:`basis.extraction.pipeline.extract_appraisal` with ``EXTRACTION_METHOD``:
``grounded`` (the default, EVAL_REPORT section 33.5) reads every field in one grounded call on
``EXTRACTION_GROUNDED_MODEL`` over Azure DI's page text, each value checked against its
verbatim quote; ``pipeline`` merges Azure DI with the text layer, with one ``TEXT_MODEL`` call
for the fields still missing or failed (also what a grounded reading that cannot run or whose
call fails falls back to, on the same Azure DI analysis). Then the validation and the review
gate. The result is written to ``extractions`` (``raw`` keeps the Azure DI analysis, the
issues, the model's trace and, for the grounded reading, its quotes) and one typed
``extraction_fields`` row per field.

Order of work, so that money is not spent twice and rows are written once:

1. A read-only look: the document (must be an uploaded appraisal of this study), an
   extraction already written for this revision (the business key: return it), and the
   Azure DI analysis of an earlier revision of the same document (reused, not re-paid).
2. The extraction, outside any database transaction (it can take minutes); model calls
   record their ``llm_calls`` rows as they happen. The study budget for its model calls is
   ``settings.study_budget_usd`` minus the recorded spend (this activity's contract has no
   budget field; the workflow's own budget does not see the extraction's cents).
3. ``run_once``: in one transaction, write the extraction, apply the reviewer's
   corrections (``extraction_field`` corrections from an appraisal rejection overwrite the
   same ``(section, field)`` with ``source="manual"``, confidence 1), recompute the review
   gate over the corrected fields, and complete the idempotency key.

Errors: no appraisal uploaded -> ``needs_review`` with no extraction (unchanged from S2);
a missing file, a non-PDF, a PDF with no pages, or a document Azure DI rejects ->
non-retryable ``ValidationError``; Azure DI 429/5xx/timeouts -> retryable. A model call that
fails is best effort (the grounded reading falls back to the pipeline, whose own call leaves
its fields empty), except a provider account or configuration error -> non-retryable
``ProviderAccountError`` (the workflow stops the study with the reason).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._model_calls import (
    CallOrigin,
    call_origin,
    heartbeating,
    provider_failure,
)
from basis.activities._support import heartbeat, parse_uuid, run_once, validation_error
from basis.activities.appraisal_sources import (
    NO_REPORT_PDF,
    companion_pdf,
    recorded_pdf,
    upload_kind,
    with_source,
)
from basis.activities.corrections import (
    check_extraction_field,
    parse_date,
    parse_number,
    parse_text,
)
from basis.activities.deps import Deps
from basis.db.enums import DocumentKind
from basis.db.models import Document, Extraction
from basis.db.repositories.activity_result import COMPLETED, ActivityResultRepository
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.extraction import ExtractionRepository
from basis.db.repositories.llm_call import LlmCallRepository
from basis.db.session import session_scope
from basis.extraction.azure_di import AzureDiResult
from basis.extraction.confidence import review_gate
from basis.extraction.fallback import PROMPT_NAME
from basis.extraction.grounded import GroundedModel
from basis.extraction.package import is_pdf
from basis.extraction.pdf_text import InvalidDocument
from basis.extraction.persist import persist_extraction, stored_azure_result
from basis.extraction.pipeline import ExtractionContext, extract_appraisal_upload
from basis.extraction.results import ExtractedField, ExtractionResult
from basis.extraction.validation import validate_fields
from basis.llm import prompts
from basis.llm.errors import ProviderError
from basis.llm.provider import Budget
from basis.storage import ObjectNotFound
from basis.workflow.contracts import CorrectionInput, ExtractFieldsInput, ExtractFieldsOutput

ACTIVITY: Final = "extract_fields"
MANUAL: Final = "manual"
# Issues the validator does not recompute: kept through a rejection's corrections.
READER_ISSUES: Final = frozenset(
    {"sources_disagree", "azure_di_rejected", "xml_pdf_mismatch", NO_REPORT_PDF}
)


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
            report = await _report_pdf(session, study_id, document_id, input)
            previous = await repo.latest_before(document_id, input.revision)
            reuse = None
            if previous is not None and _same_pdf(previous.raw, report):
                reuse = stored_azure_result(previous.raw)
            origin = await call_origin(session, study_id)
            spent = await LlmCallRepository(session).spent_usd(study_id)
            gcs_uri = document.gcs_uri
            companion = (report.id, report.gcs_uri) if report is not None else None

        result: ExtractionResult | None = None
        if not done:
            result = await self._extract(
                study_id, document_id, gcs_uri, origin, input.revision, spent, reuse, companion
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
        origin: CallOrigin,
        revision: int,
        spent: Decimal,
        reuse: AzureDiResult | None,
        report: tuple[uuid.UUID, str] | None = None,
    ) -> ExtractionResult:
        settings = self._deps.settings
        try:
            data = await self._deps.storage.read(gcs_uri)
        except ObjectNotFound:
            raise validation_error(f"appraisal file {gcs_uri} is missing from storage") from None
        kind = upload_kind(data)
        companion: bytes | None = None
        if report is not None and kind == "xml":
            try:  # a bare UAD 3.6 XML: its PDF was uploaded as another appraisal document
                companion = await self._deps.storage.read(report[1])
            except ObjectNotFound:
                raise validation_error(
                    f"report PDF {report[0]} of appraisal {document_id} is missing from storage"
                ) from None
            if not is_pdf(companion):
                raise validation_error(
                    f"report PDF {report[0]} of appraisal {document_id} is not a PDF"
                )
        ctx = ExtractionContext(
            call=origin.context(
                study_id,
                ACTIVITY,
                Budget(remaining_usd=max(settings.study_budget_usd - float(spent), 0.0)),
                self._deps.ledger(),
                revision=revision,
                entity_type="appraisal",
                entity_id=document_id,
            ),
            provider=self._deps.llm,
            azure=self._deps.azure_di,
            azure_usd_per_page=settings.azure_di_usd_per_page,
            prompt=prompts.latest(PROMPT_NAME),
            text_model=settings.text_model,
            review_threshold=settings.extraction_review_threshold,
            fallback_max_tokens=settings.extraction_output_budget,
            fallback_reasoning_effort=settings.extraction_reasoning_effort,
            fallback_scope=settings.extraction_fallback_scope,
            fallback_context_chars=settings.extraction_fallback_context_chars,
            previous_azure=reuse,
            heartbeat=heartbeat,
            method=settings.extraction_method,
            grounded=GroundedModel(
                model=settings.extraction_grounded_model,
                reasoning_effort=settings.extraction_grounded_reasoning_effort,
                max_tokens=settings.extraction_grounded_max_tokens,
            ),
        )
        try:
            async with heartbeating("extracting"):
                result = await extract_appraisal_upload(
                    data, str(document_id), ctx, companion_pdf=companion
                )
            pdf_id = report[0] if report is not None and companion is not None else None
            if kind != "xml":
                pdf_id = document_id
            return with_source(result, document_id=document_id, kind=kind, pdf_document_id=pdf_id)
        except InvalidDocument as exc:
            raise validation_error(f"appraisal {document_id}: {exc}") from exc
        except ProviderError as exc:
            raise provider_failure(exc, f"appraisal {document_id}") from exc


async def _report_pdf(
    session: AsyncSession, study_id: uuid.UUID, document_id: uuid.UUID, input: ExtractFieldsInput
) -> Document | None:
    """The report PDF for a bare XML: the one ``ingest_appraisal`` chose (``input.report_pdf``),
    which must be a confirmed appraisal of the study; for an input recorded before that field,
    the companion rule (:func:`basis.activities.appraisal_sources.companion_pdf`)."""
    repo = DocumentRepository(session)
    if input.report_pdf is None:
        source = await repo.get(study_id, document_id)
        assert source is not None  # checked by the caller
        return companion_pdf(await repo.list_for_study(study_id), source)
    if input.report_pdf.document_id is None:
        return None
    pdf_id = parse_uuid(input.report_pdf.document_id, "report_pdf.document_id")
    if pdf_id == document_id:
        return None  # a PDF or ZIP is its own report
    found = await repo.get(study_id, pdf_id)
    if found is None or found.kind is not DocumentKind.APPRAISAL or found.uploaded_at is None:
        raise validation_error(f"report PDF {pdf_id} is not a confirmed appraisal of {study_id}")
    return found


def _same_pdf(raw: object, report: Document | None) -> bool:
    """An earlier revision's Azure DI analysis is reused only for the same report PDF (a bare
    XML's companion could differ between revisions recorded before it was pinned)."""
    known, pdf_id = recorded_pdf(raw)
    if not known or report is None:
        return True  # the document itself (a PDF or ZIP), or a legacy extraction
    return pdf_id == str(report.id)


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
    # A UAD 3.6 PDF's form carries fewer keys: they stay not required after a correction.
    form = result.raw.get("form")
    off = form.get("not_on_form") if isinstance(form, dict) else None
    off_form = frozenset(off) if isinstance(off, list) else frozenset()
    issues = kept + validate_fields(fields, not_on_form=off_form)
    threshold = float(result.raw.get("review_threshold", 0.90))
    gate = review_gate(fields, issues, threshold=threshold, not_on_form=off_form)
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
