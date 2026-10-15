"""The classification cache (``classification_cache``): engineer-approved answers by key.

Key: ``label|material|attachment|property_class|rules_version`` with every part
normalized like rule labels (S3_SPEC section 5). Including ``rules_version`` means a
rules change never serves an answer made under older rules.

Entries are written only from engineer-approved assets: :func:`promote_to_cache`
refuses an asset whose revision has no ``approve`` review at ``assets_review`` (the S4
review flow calls it). Only citations into a *reference* corpus are cached; a study's own
appraisal is evidence for that study only. Legacy (``firestore/classification_cache.py``)
required citations and at least one approval; both rules are kept: an entry without
citations is not served.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from basis.classify.schemas import CitationClaim
from basis.db.enums import Bucket, Corpus, Decision, Stage
from basis.db.models import (
    Asset,
    Chunk,
    Citation,
    ClassificationCacheEntry,
    Component,
    CorpusVersion,
    Review,
)
from basis.domain.rules import RULES_VERSION, check_property_class, normalize_label


class NotApprovedError(ValueError):
    """The asset is not engineer-approved, so it cannot seed the cache."""


def cache_key(
    label: str | None,
    material: str | None,
    attachment: str | None,
    property_class: str,
    rules_version: str = RULES_VERSION,
) -> str:
    parts = (
        normalize_label(label),
        normalize_label(material),
        normalize_label(attachment),
        check_property_class(property_class),
        rules_version,
    )
    return "|".join(parts)


@dataclass(frozen=True, slots=True)
class CachedClassification:
    key: str
    category: str
    section: str | None
    bucket: Bucket
    asset_class: str | None
    citations: tuple[CitationClaim, ...]
    source: str

    @classmethod
    def from_entry(cls, entry: ClassificationCacheEntry) -> CachedClassification | None:
        """``None`` for a malformed entry or one without citations (never served)."""
        result: Any = entry.result
        if not isinstance(result, dict):
            return None
        try:
            bucket = Bucket(str(result["bucket"]))
            citations = tuple(
                CitationClaim.model_validate(c) for c in result.get("citations") or []
            )
            category = str(result["category"])
        except (KeyError, ValueError):
            return None
        if not citations:
            return None
        section = result.get("section")
        asset_class = result.get("asset_class")
        return cls(
            key=entry.key,
            category=category,
            section=None if section is None else str(section),
            bucket=bucket,
            asset_class=None if asset_class is None else str(asset_class),
            citations=citations,
            source=entry.source,
        )


class ClassificationCacheRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_many(self, keys: set[str]) -> dict[str, CachedClassification]:
        if not keys:
            return {}
        rows = await self._session.scalars(
            select(ClassificationCacheEntry).where(ClassificationCacheEntry.key.in_(keys))
        )
        found: dict[str, CachedClassification] = {}
        for row in rows:
            cached = CachedClassification.from_entry(row)
            if cached is not None:
                found[row.key] = cached
        return found

    async def record_hits(self, hits: Counter[str]) -> None:
        for key, count in sorted(hits.items()):
            await self._session.execute(
                update(ClassificationCacheEntry)
                .where(ClassificationCacheEntry.key == key)
                .values(hits=ClassificationCacheEntry.hits + count)
            )

    async def put(self, key: str, result: dict[str, Any], source: str) -> None:
        rules_version = key.rsplit("|", 1)[-1]
        stmt = pg_insert(ClassificationCacheEntry).values(
            key=key, result=result, source=source, rules_version=rules_version
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[ClassificationCacheEntry.key],
                set_={"result": stmt.excluded.result, "source": stmt.excluded.source},
            )
        )


async def promote_to_cache(
    session: AsyncSession, *, study_id: uuid.UUID, asset_id: uuid.UUID, property_class: str
) -> str:
    """Seed the cache from an engineer-approved asset; returns the key written.

    Raises :class:`NotApprovedError` when the asset is unknown, has no component, its
    revision was not approved at ``assets_review``, or it has no reference citation.
    """
    asset = (
        await session.scalars(select(Asset).where(Asset.id == asset_id, Asset.study_id == study_id))
    ).one_or_none()
    if asset is None or asset.component_id is None:
        raise NotApprovedError(f"asset {asset_id} not found in study {study_id}")
    approved = await session.scalar(
        select(Review.id).where(
            Review.study_id == study_id,
            Review.stage == Stage.ASSETS_REVIEW,
            Review.revision == asset.revision,
            Review.decision == Decision.APPROVE,
        )
    )
    if approved is None:
        raise NotApprovedError(f"revision {asset.revision} of study {study_id} is not approved")
    component = await session.get(Component, asset.component_id)
    if component is None:
        raise NotApprovedError(f"component {asset.component_id} not found")
    citation_rows = await session.execute(
        select(Citation.chunk_id, Citation.excerpt)
        .join(Chunk, Chunk.id == Citation.chunk_id)
        .join(CorpusVersion, CorpusVersion.id == Chunk.corpus_version_id)
        .where(Citation.asset_id == asset.id, CorpusVersion.corpus == Corpus.REFERENCE.value)
        .order_by(Citation.chunk_id, Citation.excerpt)
    )
    citations = [{"chunk_id": str(c), "excerpt": e} for c, e in citation_rows.all()]
    if not citations:
        raise NotApprovedError(f"asset {asset_id} has no reference citation to cache")
    key = cache_key(component.label, component.material, component.attachment, property_class)
    await ClassificationCacheRepository(session).put(
        key,
        {
            "category": asset.category,
            "section": asset.irs_section,
            "bucket": asset.bucket.value,
            "asset_class": None,
            "citations": citations,
            "source_asset_id": str(asset.id),
        },
        source=f"approved_asset:{asset.id}",
    )
    return key
