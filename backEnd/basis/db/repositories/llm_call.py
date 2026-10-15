"""Model-call records (``llm_calls``): one row per provider call, the budget is their SUM."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import LlmCall


class LlmCallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        study_id: uuid.UUID,
        run_id: uuid.UUID | None,
        activity: str | None,
        provider: str,
        model: str,
        prompt_version: str | None,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal,
        latency_ms: int | None,
    ) -> LlmCall:
        row = LlmCall(
            study_id=study_id,
            run_id=run_id,
            activity=activity,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def spent_usd(self, study_id: uuid.UUID) -> Decimal:
        """Total recorded cost for the study (the budget is checked against this sum)."""
        stmt = select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
            LlmCall.study_id == study_id
        )
        return Decimal((await self._session.scalar(stmt)) or 0)

    async def list_for_study(self, study_id: uuid.UUID) -> list[LlmCall]:
        stmt = (
            select(LlmCall)
            .where(LlmCall.study_id == study_id)
            .order_by(LlmCall.created_at, LlmCall.id)
        )
        return list((await self._session.scalars(stmt)).all())
