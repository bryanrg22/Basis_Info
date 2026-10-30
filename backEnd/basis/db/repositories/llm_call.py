"""The paid-call ledger (``llm_calls``): one row per call, the budget is their SUM.

:meth:`LlmCallRepository.add_record` writes what :mod:`basis.llm.recorder` priced;
:meth:`spend` rolls a study up by stage, activity, component, model and kind (calls, tokens
of every kind, dollars, latency), and :meth:`page` lists the rows oldest first with a keyset
cursor on ``(created_at, id)`` (EVAL_REPORT section 40; ``GET /v1/studies/{id}/spend`` and
``/calls``, the export's ``summary.spend``).
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.errors import InvalidCursorError
from basis.db.models import LlmCall

if TYPE_CHECKING:
    from basis.llm.recorder import CallRecord

MAX_CALL_PAGE: Final = 200
GROUPINGS: Final = ("stage", "activity", "component", "model", "kind")


@dataclass(frozen=True, slots=True)
class SpendGroup:
    """One line of the rollup: the group's key and its totals."""

    key: str | None
    calls: int
    errors: int
    input_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    cost_usd: Decimal
    latency_ms_total: int
    latency_ms_max: int | None
    quantity: Decimal | None


@dataclass(frozen=True, slots=True)
class Spend:
    """A study's whole spend and its rollups (the lines of each sum to ``total``)."""

    total: SpendGroup
    by_stage: list[SpendGroup]
    by_activity: list[SpendGroup]
    by_component: list[SpendGroup]
    by_model: list[SpendGroup]
    by_kind: list[SpendGroup]


@dataclass(frozen=True, slots=True)
class CallPage:
    items: list[LlmCall]
    next_cursor: str | None


def encode_cursor(created_at: datetime, call_id: uuid.UUID) -> str:
    raw = json.dumps({"t": created_at.isoformat(), "id": str(call_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        created_at = datetime.fromisoformat(data["t"])
        call_id = uuid.UUID(data["id"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise InvalidCursorError("invalid pagination cursor") from exc
    if created_at.tzinfo is None:
        raise InvalidCursorError("invalid pagination cursor")
    return created_at, call_id


_SUMS = (
    func.count().label("calls"),
    func.count().filter(LlmCall.status == "error").label("errors"),
    func.coalesce(func.sum(LlmCall.input_tokens), 0).label("input_tokens"),
    func.coalesce(func.sum(LlmCall.cache_read_input_tokens), 0).label("cache_read"),
    func.coalesce(func.sum(LlmCall.cache_creation_input_tokens), 0).label("cache_creation"),
    func.coalesce(func.sum(LlmCall.output_tokens), 0).label("output_tokens"),
    func.coalesce(func.sum(LlmCall.reasoning_output_tokens), 0).label("reasoning"),
    func.coalesce(func.sum(LlmCall.cost_usd), 0).label("cost_usd"),
    func.coalesce(func.sum(LlmCall.latency_ms), 0).label("latency_total"),
    func.max(LlmCall.latency_ms).label("latency_max"),
    func.sum(LlmCall.quantity).label("quantity"),
)


def _group(key: Any, row: Any) -> SpendGroup:
    return SpendGroup(
        key=None if key is None else str(key),
        calls=int(row.calls),
        errors=int(row.errors),
        input_tokens=int(row.input_tokens),
        cache_read_input_tokens=int(row.cache_read),
        cache_creation_input_tokens=int(row.cache_creation),
        output_tokens=int(row.output_tokens),
        reasoning_output_tokens=int(row.reasoning),
        cost_usd=Decimal(row.cost_usd),
        latency_ms_total=int(row.latency_total),
        latency_ms_max=None if row.latency_max is None else int(row.latency_max),
        quantity=None if row.quantity is None else Decimal(row.quantity),
    )


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
        """A bare row (the pre-ledger shape); production writes go through
        :meth:`add_record`."""
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

    async def add_record(self, record: CallRecord, *, build_sha: str | None = None) -> LlmCall:
        """The row of a priced :class:`~basis.llm.recorder.CallRecord`: every column from
        :func:`basis.llm.recorder.ledger_columns`."""
        from basis.llm.recorder import ledger_columns  # the recorder imports this module

        row = LlmCall(**ledger_columns(record, build_sha=build_sha))
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

    async def spend(self, study_id: uuid.UUID) -> Spend:
        """The study's rollup: the total and one line per stage, activity, component, model
        and kind (lines ordered by cost, largest first, then key)."""
        total_row = (
            await self._session.execute(select(*_SUMS).where(LlmCall.study_id == study_id))
        ).one()
        groups: dict[str, list[SpendGroup]] = {}
        for name in GROUPINGS:
            column = getattr(LlmCall, name)
            stmt = (
                select(column.label("key"), *_SUMS)
                .where(LlmCall.study_id == study_id)
                .group_by(column)
                .order_by(func.sum(LlmCall.cost_usd).desc(), column.nulls_last())
            )
            rows = (await self._session.execute(stmt)).all()
            groups[name] = [_group(row.key, row) for row in rows]
        return Spend(
            total=_group(None, total_row),
            by_stage=groups["stage"],
            by_activity=groups["activity"],
            by_component=groups["component"],
            by_model=groups["model"],
            by_kind=groups["kind"],
        )

    async def page(
        self,
        study_id: uuid.UUID,
        *,
        limit: int,
        cursor: str | None = None,
        activity: str | None = None,
        kind: str | None = None,
        entity_id: uuid.UUID | None = None,
    ) -> CallPage:
        """``limit`` rows oldest first after ``cursor``, optionally of one activity, kind or
        entity."""
        limit = max(1, min(limit, MAX_CALL_PAGE))
        stmt = select(LlmCall).where(LlmCall.study_id == study_id)
        if activity is not None:
            stmt = stmt.where(LlmCall.activity == activity)
        if kind is not None:
            stmt = stmt.where(LlmCall.kind == kind)
        if entity_id is not None:
            stmt = stmt.where(LlmCall.entity_id == entity_id)
        if cursor is not None:
            created_at, call_id = decode_cursor(cursor)
            stmt = stmt.where(
                or_(
                    LlmCall.created_at > created_at,
                    and_(LlmCall.created_at == created_at, LlmCall.id > call_id),
                )
            )
        stmt = stmt.order_by(LlmCall.created_at, LlmCall.id).limit(limit + 1)
        rows: Sequence[LlmCall] = (await self._session.scalars(stmt)).all()
        items = list(rows[:limit])
        next_cursor = (
            encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit else None
        )
        return CallPage(items=items, next_cursor=next_cursor)


__all__ = [
    "CallPage",
    "LlmCallRepository",
    "MAX_CALL_PAGE",
    "Spend",
    "SpendGroup",
]
