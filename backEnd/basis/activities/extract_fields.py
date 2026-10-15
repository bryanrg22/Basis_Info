"""``extract_fields`` (Stage 2 stub): the appraisal's fields for one revision.

Stage 2 body: an ``extractions`` row (``method="stub"``) with five ``extraction_fields``
derived from the document's own metadata (file name, format, a value derived from its
sha256, its upload date). Stage 3 replaces this with Azure Document Intelligence plus the
regex fallback; the contract stays.

Corrections (an appraisal rejection) arrive as ``extraction_field`` corrections that name
a field of the rejected revision; their ``after`` values overwrite the same
``(section, field)`` in the new revision with ``source="manual"`` and confidence 1.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import heartbeat, parse_uuid, run_once, validation_error
from basis.activities.corrections import (
    check_extraction_field,
    parse_date,
    parse_number,
    parse_text,
)
from basis.activities.deps import Deps
from basis.db.enums import DocumentKind
from basis.db.models import Document, Extraction
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.extraction import ExtractionRepository
from basis.workflow.contracts import CorrectionInput, ExtractFieldsInput, ExtractFieldsOutput

METHOD: Final = "stub"
SOURCE: Final = "stub"
MANUAL: Final = "manual"
STUB_CONFIDENCE: Final = 0.5


StubField = tuple[str, str, str | None, Decimal | None, date | None]


def stub_fields(document: Document) -> list[StubField]:
    """``(section, field, value_text, value_num, value_date)`` from the document's metadata."""
    path = PurePosixPath(document.filename)
    name = path.stem.replace("_", " ").replace("-", " ").strip() or document.filename
    appraised = Decimal((int(document.sha256[:8], 16) % 9000 + 1000) * 1000)
    uploaded = (document.uploaded_at or document.created_at).date()
    return [
        ("subject", "file_name", document.filename, None, None),
        ("subject", "property_name", name.title(), None, None),
        ("subject", "document_format", path.suffix.lstrip(".").upper() or None, None, None),
        ("valuation", "appraised_value", None, appraised, None),
        ("valuation", "effective_date", None, None, uploaded),
    ]


class ExtractFields:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="extract_fields")
    async def run(self, input: ExtractFieldsInput) -> ExtractFieldsOutput:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> ExtractFieldsOutput:
            if input.document_id is None:
                # No appraisal was uploaded: nothing to extract, the reviewer must look.
                return ExtractFieldsOutput(extraction_id=None, fields=0, needs_review=True)
            document_id = parse_uuid(input.document_id, "document_id")
            document = await DocumentRepository(session).get(study_id, document_id)
            if document is None or document.kind is not DocumentKind.APPRAISAL:
                raise validation_error(f"appraisal {document_id} not found in study {study_id}")
            repo = ExtractionRepository(session)
            existing = await repo.get_for_revision(document_id, input.revision)
            if existing is not None:
                # Written by an earlier execution for the same revision (e.g. after a
                # workflow reset); the business key wins over the activity key.
                return await _output(repo, existing)
            extraction = await repo.create(
                study_id=study_id,
                document_id=document_id,
                revision=input.revision,
                method=METHOD,
                overall_confidence=STUB_CONFIDENCE,
                needs_review=False,
                raw={"method": METHOD, "filename": document.filename},
            )
            for section, field, text, num, day in stub_fields(document):
                heartbeat(field)
                await repo.set_field(
                    extraction=extraction,
                    section=section,
                    field=field,
                    value_text=text,
                    value_num=num,
                    value_date=day,
                    confidence=STUB_CONFIDENCE,
                    source=SOURCE,
                )
            for correction in input.corrections:
                await _apply(repo, extraction, study_id, correction)
            return await _output(repo, extraction)

        return await run_once(
            self._deps,
            name="extract_fields",
            key=input.idempotency_key,
            output_type=ExtractFieldsOutput,
            work=work,
        )


async def _apply(
    repo: ExtractionRepository,
    extraction: Extraction,
    study_id: uuid.UUID,
    correction: CorrectionInput,
) -> None:
    if correction.entity_type != "extraction_field":
        raise validation_error(f"extract_fields cannot apply a {correction.entity_type} correction")
    check_extraction_field(correction.after)
    target = await repo.get_field(study_id, parse_uuid(correction.entity_id, "entity_id"))
    if target is None:
        raise validation_error(f"extraction field {correction.entity_id} not found")
    await repo.set_field(
        extraction=extraction,
        section=target.section,
        field=target.field,
        value_text=parse_text(correction.after.get("value_text")),
        value_num=parse_number(correction.after.get("value_num")),
        value_date=parse_date(correction.after.get("value_date")),
        confidence=1.0,
        source=MANUAL,
    )


async def _output(repo: ExtractionRepository, extraction: Extraction) -> ExtractFieldsOutput:
    fields = await repo.fields(extraction.id)
    return ExtractFieldsOutput(
        extraction_id=str(extraction.id),
        fields=len(fields),
        needs_review=extraction.needs_review,
    )
