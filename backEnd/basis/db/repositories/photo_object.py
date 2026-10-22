"""Localized objects per photo and revision (``photo_objects``, ``photo_localizations``)
and the SAM 3 calls prompted with an engineer's box (``object_prompts``).

Grouped by revision like rooms and components: a rejection copies the rows of the photos it
does not re-analyze into the new revision (:meth:`PhotoObjectRepository.carry`), so the
decided revision's rows stay as history. Deleted rows are soft deleted (``deleted_at``)
and every read here returns active rows only unless it says otherwise.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import ObjectPrompt, PhotoLocalization, PhotoObject


@dataclass(frozen=True, slots=True)
class NewObject:
    """One instance to write; the geometry is already normalized and validated."""

    label: str
    x1: float
    y1: float
    x2: float
    y2: float
    area: float
    source: str
    score: float | None = None
    phrase: str | None = None
    mask: dict[str, Any] | None = None
    counted: bool = True
    derived_from: uuid.UUID | None = None
    created_by: uuid.UUID | None = None
    mask_source: str | None = None
    mask_reason: str | None = None
    prompt_id: uuid.UUID | None = None


class PhotoObjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ localizations

    async def localization(self, document_id: uuid.UUID, revision: int) -> PhotoLocalization | None:
        return await self._session.get(PhotoLocalization, (document_id, revision))

    async def localizations(
        self, study_id: uuid.UUID, revision: int, document_ids: Iterable[uuid.UUID] | None = None
    ) -> dict[uuid.UUID, PhotoLocalization]:
        stmt = select(PhotoLocalization).where(
            PhotoLocalization.study_id == study_id, PhotoLocalization.revision == revision
        )
        if document_ids is not None:
            stmt = stmt.where(PhotoLocalization.document_id.in_(list(document_ids)))
        return {row.document_id: row for row in await self._session.scalars(stmt)}

    async def record_localization(
        self,
        *,
        study_id: uuid.UUID,
        document_id: uuid.UUID,
        revision: int,
        status: str,
        reason: str | None = None,
        instances: int = 0,
        model_version: str | None = None,
        params: dict[str, Any] | None = None,
        image_width: int | None = None,
        image_height: int | None = None,
        predict_seconds: float | None = None,
        cost_usd: float | None = None,
        carried_from_revision: int | None = None,
    ) -> PhotoLocalization:
        row = PhotoLocalization(
            study_id=study_id,
            document_id=document_id,
            revision=revision,
            status=status,
            reason=reason,
            instances=instances,
            model_version=model_version,
            params=params,
            image_width=image_width,
            image_height=image_height,
            predict_seconds=predict_seconds,
            cost_usd=cost_usd,
            carried_from_revision=carried_from_revision,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    # ------------------------------------------------------------------ objects

    async def add(
        self,
        *,
        study_id: uuid.UUID,
        document_id: uuid.UUID,
        revision: int,
        new: NewObject,
        carried_from: uuid.UUID | None = None,
    ) -> PhotoObject:
        row = PhotoObject(
            study_id=study_id,
            document_id=document_id,
            revision=revision,
            label=new.label,
            phrase=new.phrase,
            score=new.score,
            x1=new.x1,
            y1=new.y1,
            x2=new.x2,
            y2=new.y2,
            mask=new.mask,
            area=new.area,
            source=new.source,
            counted=new.counted,
            derived_from=new.derived_from,
            carried_from=carried_from,
            created_by=new.created_by,
            mask_source=new.mask_source,
            mask_reason=new.mask_reason,
            prompt_id=new.prompt_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get(self, study_id: uuid.UUID, object_id: uuid.UUID) -> PhotoObject | None:
        stmt = select(PhotoObject).where(
            PhotoObject.id == object_id, PhotoObject.study_id == study_id
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def active(
        self, study_id: uuid.UUID, revision: int, document_ids: Sequence[uuid.UUID]
    ) -> list[PhotoObject]:
        """Active rows of these photos at ``revision``: by photo, label, score (highest
        first, manual boxes without a score last), then id."""
        if not document_ids:
            return []
        stmt = (
            select(PhotoObject)
            .where(
                PhotoObject.study_id == study_id,
                PhotoObject.revision == revision,
                PhotoObject.document_id.in_(list(document_ids)),
                PhotoObject.deleted_at.is_(None),
            )
            .order_by(
                PhotoObject.document_id,
                PhotoObject.label,
                PhotoObject.score.desc().nulls_last(),
                PhotoObject.id,
            )
        )
        return list((await self._session.scalars(stmt)).all())

    async def record_prompt(
        self,
        *,
        study_id: uuid.UUID,
        document_id: uuid.UUID,
        revision: int,
        label: str,
        params: dict[str, Any],
        status: str,
        created_by: uuid.UUID,
        reason: str | None = None,
        returned: int = 0,
        kept_score: float | None = None,
        model_version: str | None = None,
        predict_seconds: float | None = None,
        cost_usd: float | None = None,
    ) -> ObjectPrompt:
        row = ObjectPrompt(
            study_id=study_id,
            document_id=document_id,
            revision=revision,
            label=label,
            params=params,
            status=status,
            reason=reason,
            returned=returned,
            kept_score=kept_score,
            model_version=model_version,
            predict_seconds=predict_seconds,
            cost_usd=cost_usd,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def prompts(self, study_id: uuid.UUID, document_id: uuid.UUID) -> list[ObjectPrompt]:
        """Every box prompt of the photo, oldest first."""
        stmt = (
            select(ObjectPrompt)
            .where(ObjectPrompt.study_id == study_id, ObjectPrompt.document_id == document_id)
            .order_by(ObjectPrompt.created_at, ObjectPrompt.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def soft_delete(self, row: PhotoObject, user_id: uuid.UUID) -> PhotoObject:
        row.deleted_at = datetime.now(UTC)
        row.deleted_by = user_id
        await self._session.flush()
        return row

    # ------------------------------------------------------------------ revisions

    async def carry(
        self,
        study_id: uuid.UUID,
        from_revision: int,
        to_revision: int,
        *,
        skip_photos: set[uuid.UUID],
    ) -> int:
        """Copy the localization rows and active objects of every photo not in
        ``skip_photos`` from ``from_revision`` into ``to_revision``; returns objects copied.

        A copied object keeps its geometry, score, source, author and mask source and
        names the row it came from (``carried_from``); a copied ``derived_from`` still names
        the candidate of ``from_revision`` (the selection's history), not its copy, and a
        copied ``prompt_id`` the call that outlined it.
        """
        localizations = await self.localizations(study_id, from_revision)
        for document_id in sorted(localizations):
            if document_id in skip_photos:
                continue
            old = localizations[document_id]
            await self.record_localization(
                study_id=study_id,
                document_id=document_id,
                revision=to_revision,
                status=old.status,
                reason=old.reason,
                instances=old.instances,
                model_version=old.model_version,
                params=old.params,
                image_width=old.image_width,
                image_height=old.image_height,
                predict_seconds=old.predict_seconds,
                cost_usd=old.cost_usd,
                carried_from_revision=from_revision,
            )
        stmt = (
            select(PhotoObject)
            .where(
                PhotoObject.study_id == study_id,
                PhotoObject.revision == from_revision,
                PhotoObject.deleted_at.is_(None),
            )
            .order_by(PhotoObject.document_id, PhotoObject.created_at, PhotoObject.id)
        )
        copied = 0
        for old_row in (await self._session.scalars(stmt)).all():
            if old_row.document_id in skip_photos:
                continue
            self._session.add(
                PhotoObject(
                    study_id=study_id,
                    document_id=old_row.document_id,
                    revision=to_revision,
                    label=old_row.label,
                    phrase=old_row.phrase,
                    score=old_row.score,
                    x1=old_row.x1,
                    y1=old_row.y1,
                    x2=old_row.x2,
                    y2=old_row.y2,
                    mask=old_row.mask,
                    area=old_row.area,
                    source=old_row.source,
                    counted=old_row.counted,
                    derived_from=old_row.derived_from,
                    carried_from=old_row.id,
                    created_by=old_row.created_by,
                    mask_source=old_row.mask_source,
                    mask_reason=old_row.mask_reason,
                    prompt_id=old_row.prompt_id,
                )
            )
            copied += 1
        await self._session.flush()
        return copied


def counts(rows: Iterable[PhotoObject]) -> dict[str, int]:
    """``{label: counted instances}``, in label order (rows are assumed active)."""
    totals: dict[str, int] = {}
    for row in rows:
        if row.counted:
            totals[row.label] = totals.get(row.label, 0) + 1
    return dict(sorted(totals.items()))
