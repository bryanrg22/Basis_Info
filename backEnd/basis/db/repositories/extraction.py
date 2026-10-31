"""Appraisal extractions and their typed fields (``extractions``, ``extraction_fields``)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import Extraction, ExtractionField


class ExtractionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        study_id: uuid.UUID,
        document_id: uuid.UUID,
        revision: int,
        method: str,
        overall_confidence: float | None,
        needs_review: bool,
        raw: Any,
    ) -> Extraction:
        extraction = Extraction(
            study_id=study_id,
            document_id=document_id,
            revision=revision,
            method=method,
            overall_confidence=overall_confidence,
            needs_review=needs_review,
            raw=raw,
        )
        self._session.add(extraction)
        await self._session.flush()
        await self._session.refresh(extraction)
        return extraction

    async def get(self, study_id: uuid.UUID, extraction_id: uuid.UUID) -> Extraction | None:
        """The study's extraction with that id (None for another study's)."""
        stmt = select(Extraction).where(
            Extraction.id == extraction_id, Extraction.study_id == study_id
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def get_for_revision(self, document_id: uuid.UUID, revision: int) -> Extraction | None:
        stmt = select(Extraction).where(
            Extraction.document_id == document_id, Extraction.revision == revision
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def latest_before(self, document_id: uuid.UUID, revision: int) -> Extraction | None:
        """The document's extraction with the highest revision below ``revision``."""
        stmt = (
            select(Extraction)
            .where(Extraction.document_id == document_id, Extraction.revision < revision)
            .order_by(Extraction.revision.desc())
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()

    async def list_for_revision(self, study_id: uuid.UUID, revision: int) -> list[Extraction]:
        stmt = (
            select(Extraction)
            .where(Extraction.study_id == study_id, Extraction.revision == revision)
            .order_by(Extraction.document_id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def set_field(
        self,
        *,
        extraction: Extraction,
        section: str,
        field: str,
        value_text: str | None,
        value_num: Decimal | None = None,
        value_date: date | None = None,
        confidence: float | None,
        source: str,
        page: int | None = None,
        regions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Insert the field, or overwrite it (unique per extraction, section, field).
        ``regions=None`` leaves a row's regions as they are (a correction changes the value,
        not where the document printed it)."""
        values: dict[str, Any] = {
            "extraction_id": extraction.id,
            "study_id": extraction.study_id,
            "section": section,
            "field": field,
            "value_text": value_text,
            "value_num": value_num,
            "value_date": value_date,
            "confidence": confidence,
            "source": source,
            "page": page,
        }
        updated = ["value_text", "value_num", "value_date", "confidence", "source"]
        if regions is not None:
            values["regions"] = regions
            updated.append("regions")
        stmt = pg_insert(ExtractionField).values(values)
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    ExtractionField.extraction_id,
                    ExtractionField.section,
                    ExtractionField.field,
                ],
                set_={name: stmt.excluded[name] for name in updated},
            )
        )

    async def fields(self, extraction_id: uuid.UUID) -> list[ExtractionField]:
        stmt = (
            select(ExtractionField)
            .where(ExtractionField.extraction_id == extraction_id)
            .order_by(ExtractionField.section, ExtractionField.field)
            .execution_options(populate_existing=True)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get_field(self, study_id: uuid.UUID, field_id: uuid.UUID) -> ExtractionField | None:
        stmt = select(ExtractionField).where(
            ExtractionField.id == field_id, ExtractionField.study_id == study_id
        )
        return (await self._session.scalars(stmt)).one_or_none()
