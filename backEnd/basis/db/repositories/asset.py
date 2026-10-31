"""Classified assets with their takeoffs, cost estimates and citations."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import Bucket, ClassificationMethod
from basis.db.models import Asset, Citation, CostEstimate, Takeoff


class AssetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        study_id: uuid.UUID,
        revision: int,
        component_id: uuid.UUID | None,
        category: str,
        bucket: Bucket,
        recovery_period: Decimal,
        irs_section: str | None,
        method: ClassificationMethod,
        confidence: float | None,
        needs_review: bool = False,
        review_reason: str | None = None,
        convention: dict[str, Any] | None = None,
    ) -> Asset:
        asset = Asset(
            study_id=study_id,
            revision=revision,
            component_id=component_id,
            category=category,
            bucket=bucket,
            recovery_period=recovery_period,
            irs_section=irs_section,
            method=method,
            confidence=confidence,
            needs_review=needs_review,
            review_reason=review_reason,
            convention=convention,
        )
        self._session.add(asset)
        await self._session.flush()
        await self._session.refresh(asset)
        return asset

    async def add_takeoff(
        self,
        *,
        asset: Asset,
        quantity: Decimal,
        unit: str,
        formula: str | None,
        inputs: Any = None,
    ) -> Takeoff:
        takeoff = Takeoff(
            study_id=asset.study_id,
            asset_id=asset.id,
            quantity=quantity,
            unit=unit,
            formula=formula,
            inputs=inputs,
        )
        self._session.add(takeoff)
        await self._session.flush()
        return takeoff

    async def add_cost_estimate(
        self,
        *,
        asset: Asset,
        unit_cost: Decimal | None,
        total: Decimal | None,
        source_row: str | None,
        location_factor: Decimal | None = None,
        cost_year: int | None = None,
        trail: dict[str, Any] | None = None,
    ) -> CostEstimate:
        """``trail``: how the amounts were reached (``basis.domain.cost_trail``), written
        with the row and never updated; None for a row written without one."""
        estimate = CostEstimate(
            study_id=asset.study_id,
            asset_id=asset.id,
            unit_cost=unit_cost,
            total=total,
            source_row=source_row,
            location_factor=location_factor,
            cost_year=cost_year,
            trail=trail,
        )
        self._session.add(estimate)
        await self._session.flush()
        return estimate

    async def add_citation(
        self,
        *,
        asset: Asset,
        chunk_id: uuid.UUID,
        excerpt: str,
        verification_method: str | None = None,
    ) -> Citation:
        citation = Citation(
            study_id=asset.study_id,
            asset_id=asset.id,
            chunk_id=chunk_id,
            excerpt=excerpt,
            verification_method=verification_method,
        )
        self._session.add(citation)
        await self._session.flush()
        return citation

    async def get(self, study_id: uuid.UUID, asset_id: uuid.UUID) -> Asset | None:
        stmt = select(Asset).where(Asset.id == asset_id, Asset.study_id == study_id)
        return (await self._session.scalars(stmt)).one_or_none()

    async def list_for_revision(self, study_id: uuid.UUID, revision: int) -> list[Asset]:
        stmt = (
            select(Asset)
            .where(Asset.study_id == study_id, Asset.revision == revision)
            .order_by(Asset.component_id, Asset.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def takeoffs(self, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, Takeoff]:
        if not asset_ids:
            return {}
        rows = await self._session.scalars(select(Takeoff).where(Takeoff.asset_id.in_(asset_ids)))
        return {row.asset_id: row for row in rows}

    async def cost_estimates(self, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, CostEstimate]:
        if not asset_ids:
            return {}
        rows = await self._session.scalars(
            select(CostEstimate).where(CostEstimate.asset_id.in_(asset_ids))
        )
        return {row.asset_id: row for row in rows}

    async def citations(self, asset_ids: list[uuid.UUID]) -> list[Citation]:
        if not asset_ids:
            return []
        stmt = (
            select(Citation)
            .where(Citation.asset_id.in_(asset_ids))
            .order_by(Citation.asset_id, Citation.chunk_id)
        )
        return list((await self._session.scalars(stmt)).all())
