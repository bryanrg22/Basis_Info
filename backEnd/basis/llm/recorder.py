"""Writes one ``llm_calls`` row per model call, in its own short transaction.

The row is written right after the call returns (or fails after reaching the model),
independently of the activity's work transaction: the tokens were spent even if the
activity later rolls back or is retried, and the study budget is a SUM over these rows
(BUILD_PLAN section 3), so a rolled-back record would let a retried activity overspend.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.repositories.llm_call import LlmCallRepository
from basis.db.session import session_scope
from basis.llm.cost import cost_decimal
from basis.llm.provider import Usage


@dataclass(frozen=True, slots=True)
class CallRecord:
    study_id: uuid.UUID
    run_id: uuid.UUID | None
    activity: str
    prompt_version: str | None
    provider: str
    model: str
    usage: Usage
    cost_usd: Decimal
    latency_ms: int | None


class Recorder(Protocol):
    async def record(
        self,
        *,
        study_id: uuid.UUID,
        run_id: uuid.UUID | None,
        activity: str,
        prompt_version: str | None,
        provider: str,
        model: str,
        usage: Usage,
        latency_ms: int | None,
    ) -> Decimal:
        """Persist the call and return its cost in dollars."""
        ...


class LlmCallRecorder:
    """The production recorder: an ``llm_calls`` row through :class:`LlmCallRepository`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def record(
        self,
        *,
        study_id: uuid.UUID,
        run_id: uuid.UUID | None,
        activity: str,
        prompt_version: str | None,
        provider: str,
        model: str,
        usage: Usage,
        latency_ms: int | None,
    ) -> Decimal:
        cost = cost_decimal(model, usage)
        async with session_scope(self._sessionmaker) as session:
            await LlmCallRepository(session).add(
                study_id=study_id,
                run_id=run_id,
                activity=activity,
                provider=provider,
                model=model,
                prompt_version=prompt_version,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        return cost

    async def spent_usd(self, study_id: uuid.UUID) -> Decimal:
        async with session_scope(self._sessionmaker) as session:
            return await LlmCallRepository(session).spent_usd(study_id)


@dataclass
class MemoryRecorder:
    """Keeps records in a list (unit tests that need no database)."""

    records: list[CallRecord] = field(default_factory=list)

    async def record(
        self,
        *,
        study_id: uuid.UUID,
        run_id: uuid.UUID | None,
        activity: str,
        prompt_version: str | None,
        provider: str,
        model: str,
        usage: Usage,
        latency_ms: int | None,
    ) -> Decimal:
        cost = cost_decimal(model, usage)
        self.records.append(
            CallRecord(
                study_id=study_id,
                run_id=run_id,
                activity=activity,
                prompt_version=prompt_version,
                provider=provider,
                model=model,
                usage=usage,
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        )
        return cost
