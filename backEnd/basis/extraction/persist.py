"""Write an :class:`ExtractionResult` to ``extractions`` and ``extraction_fields``."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import Extraction
from basis.db.repositories.extraction import ExtractionRepository
from basis.extraction.azure_di import AzureDiResult
from basis.extraction.regions import region_json
from basis.extraction.results import ExtractionResult


async def persist_extraction(
    session: AsyncSession,
    *,
    study_id: uuid.UUID,
    document_id: uuid.UUID,
    revision: int,
    result: ExtractionResult,
) -> Extraction:
    """One ``extractions`` row and one typed ``extraction_fields`` row per field."""
    repo = ExtractionRepository(session)
    extraction = await repo.create(
        study_id=study_id,
        document_id=document_id,
        revision=revision,
        method=result.method,
        overall_confidence=result.overall_confidence,
        needs_review=result.needs_review,
        raw=result.raw,
    )
    for key in sorted(result.fields):
        item = result.fields[key]
        await repo.set_field(
            extraction=extraction,
            section=item.section,
            field=item.field,
            value_text=item.value_text,
            value_num=item.value_num,
            value_date=item.value_date,
            confidence=item.confidence,
            source=item.source,
            page=item.page,
            regions=region_json(item.regions),
        )
    return extraction


def stored_azure_result(raw: object) -> AzureDiResult | None:
    """The Azure DI analysis stored in an earlier extraction's ``raw``, if any. A real analysis
    stored before the geometry was kept (pages without words) is not reused: the new revision's
    fields would get no highlight regions, so the document is analyzed again (once)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("azure_di"), dict):
        return None
    try:
        result = AzureDiResult.model_validate(raw["azure_di"])
    except ValueError:
        return None
    if not result.synthetic and result.pages and not any(p.words for p in result.pages):
        return None
    return result
