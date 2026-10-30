"""The firm's conventions (``classification_cache``): engineer-approved answers by context.

Key: ``label|material|attachment|room|property_class|rules_version`` with every part
normalized like rule labels and the room by :func:`basis.domain.rules.normalize_room_type`
(``""`` when unknown): the same context the rules table picks a variant by
(docs/review_2026-10-15/CLASSIFICATION_CONVENTIONS.md section 3). Including
``rules_version`` means a rules change never serves an answer made under older rules.

Rows are scoped by firm (``org_id``; the primary key is ``(org_id, key)``): one firm's
rulings never classify another firm's study. They are written only by the
``promote_conventions`` activity after an ``assets_review`` approval (section 5): an
engineer's correction (``manual``) or an engineer-approved model answer with a verified
reference citation (``llm``); an approved asset classified from a convention (``cache``)
adds an approval to it. A row records the study whose approval seeded it, the
reviewer and when, what the rules table says for the same key and whether the convention
agrees, and ``approvals``: how many approved studies at the firm produced the same answer.
A different answer for the same key replaces the row (``approvals`` back to 1); the
activity keeps the replaced row in ``study_events``.

A convention without citations (an engineer's ruling needs none) is served; one with
citations has them re-verified against the reference corpus in use (``basis.classify.router``).
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from basis.classify.schemas import CitationClaim
from basis.db.enums import Bucket
from basis.db.models import ClassificationCacheEntry
from basis.domain.rules import (
    RECOVERY_YEARS,
    RULES_VERSION,
    check_property_class,
    normalize_label,
    normalize_room_type,
)

# The ``citation`` member of an uncited convention's provenance (section 5).
UNCITED: Final = "firm convention, uncited"

PutOutcome = Literal["created", "confirmed", "replaced"]
_BUCKETS: Final = frozenset(b.value for b in Bucket)


def cache_key(
    label: str | None,
    material: str | None,
    attachment: str | None,
    property_class: str,
    room_type: str | None = None,
    rules_version: str = RULES_VERSION,
) -> str:
    """The context key (six parts, section 3)."""
    parts = (
        normalize_label(label),
        normalize_label(material),
        normalize_label(attachment),
        normalize_room_type(room_type),
        check_property_class(property_class),
        rules_version,
    )
    return "|".join(parts)


def answer_of(result: Mapping[str, Any]) -> tuple[str, Decimal | None, str | None]:
    """What makes two rulings the same: bucket, recovery period and section (the category
    is free text and the citations are the evidence, not the answer). A missing recovery
    period is the bucket's standard one."""
    bucket = str(result.get("bucket"))
    period = result.get("recovery_period")
    recovery: Decimal | None
    if period is not None:
        try:
            recovery = Decimal(str(period))
        except InvalidOperation:
            recovery = None
    else:
        recovery = RECOVERY_YEARS.get(Bucket(bucket)) if bucket in _BUCKETS else None
    section = result.get("section")
    return bucket, recovery, None if section is None else str(section)


@dataclass(frozen=True, slots=True)
class CachedClassification:
    key: str
    category: str
    section: str | None
    bucket: Bucket
    asset_class: str | None
    citations: tuple[CitationClaim, ...]
    source: str
    org_id: uuid.UUID | None = None
    study_id: uuid.UUID | None = None
    reviewer_id: uuid.UUID | None = None
    approved_at: datetime | None = None
    approvals: int = 1
    rule_key: str | None = None
    rule_bucket: Bucket | None = None
    agrees_with_rules: bool | None = None
    rationale: str = ""
    recovery_period: Decimal | None = None

    @property
    def period(self) -> Decimal:
        """The recovery period served: the ruling's own, else the bucket's standard one
        (0 for land, which is not depreciated)."""
        if self.recovery_period is not None:
            return self.recovery_period
        return RECOVERY_YEARS.get(self.bucket, Decimal(0))

    def provenance(self) -> dict[str, Any]:
        """The ``convention`` member of an asset classified from this row (the asset keeps
        it as of its classification: a later approval or replacement does not rewrite it)."""
        return {
            "key": self.key,
            "study_id": None if self.study_id is None else str(self.study_id),
            "reviewer_id": None if self.reviewer_id is None else str(self.reviewer_id),
            "approved_at": None if self.approved_at is None else self.approved_at.isoformat(),
            "approvals": self.approvals,
            "rule_key": self.rule_key,
            "agrees_with_rules": self.agrees_with_rules,
            "citation": None if self.citations else UNCITED,
        }

    @classmethod
    def from_entry(cls, entry: ClassificationCacheEntry) -> CachedClassification | None:
        """``None`` for a malformed entry (never served)."""
        result: Any = entry.result
        if not isinstance(result, dict):
            return None
        try:
            bucket = Bucket(str(result["bucket"]))
            citations = tuple(
                CitationClaim.model_validate(c) for c in result.get("citations") or []
            )
            category = str(result["category"])
            rule_bucket = result.get("rule_bucket")
            period = result.get("recovery_period")
            recovery = None if period is None else Decimal(str(period))
        except (KeyError, ValueError, InvalidOperation):
            return None
        section = result.get("section")
        asset_class = result.get("asset_class")
        rationale = result.get("rationale")
        return cls(
            key=entry.key,
            category=category,
            section=None if section is None else str(section),
            bucket=bucket,
            asset_class=None if asset_class is None else str(asset_class),
            citations=citations,
            source=entry.source,
            org_id=entry.org_id,
            study_id=entry.study_id,
            reviewer_id=entry.reviewer_id,
            approved_at=entry.approved_at,
            approvals=entry.approvals,
            rule_key=entry.rule_key,
            rule_bucket=Bucket(rule_bucket) if rule_bucket in _BUCKETS else None,
            agrees_with_rules=entry.agrees_with_rules,
            rationale=rationale if isinstance(rationale, str) else "",
            recovery_period=recovery,
        )


@dataclass(frozen=True, slots=True)
class PutResult:
    """What :meth:`ClassificationCacheRepository.put` did: a new row, one more approval of
    the same answer, or a different answer replacing the row (``previous`` is the replaced
    row's values)."""

    outcome: PutOutcome
    approvals: int
    previous: dict[str, Any] | None = None


def _snapshot(entry: ClassificationCacheEntry) -> dict[str, Any]:
    return {
        "result": entry.result,
        "source": entry.source,
        "study_id": str(entry.study_id),
        "reviewer_id": str(entry.reviewer_id),
        "approved_at": entry.approved_at.isoformat(),
        "rule_key": entry.rule_key,
        "agrees_with_rules": entry.agrees_with_rules,
        "approvals": entry.approvals,
        "hits": entry.hits,
    }


class ClassificationCacheRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_many(self, org_id: uuid.UUID, keys: set[str]) -> dict[str, CachedClassification]:
        """The firm's conventions for ``keys`` (keys with no row, or a malformed one, are
        absent)."""
        if not keys:
            return {}
        rows = await self._session.scalars(
            select(ClassificationCacheEntry).where(
                ClassificationCacheEntry.org_id == org_id,
                ClassificationCacheEntry.key.in_(sorted(keys)),
            )
        )
        found: dict[str, CachedClassification] = {}
        for row in rows:
            cached = CachedClassification.from_entry(row)
            if cached is not None:
                found[row.key] = cached
        return found

    async def record_hits(self, org_id: uuid.UUID, hits: Counter[str]) -> None:
        for key, count in sorted(hits.items()):
            await self._session.execute(
                update(ClassificationCacheEntry)
                .where(
                    ClassificationCacheEntry.org_id == org_id,
                    ClassificationCacheEntry.key == key,
                )
                .values(hits=ClassificationCacheEntry.hits + count)
            )

    async def put(
        self,
        org_id: uuid.UUID,
        key: str,
        result: dict[str, Any],
        *,
        source: str,
        study_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        approved_at: datetime,
        rule_key: str | None,
        agrees_with_rules: bool | None,
    ) -> PutResult:
        """Write one approval of ``result`` for ``(org_id, key)``.

        No row: inserted with ``approvals = 1``. A row with the same answer
        (:func:`answer_of`): ``approvals + 1``, the row otherwise unchanged (it keeps the
        ruling that seeded it). A row with a different answer: replaced by this one,
        ``approvals = 1`` and ``hits = 0``, and the replaced values are returned. The row is
        locked (``FOR UPDATE``) so two approvals at once cannot lose a count; the caller
        decides idempotency (``promote_conventions`` skips a key its study already promoted).
        """
        values = {
            "org_id": org_id,
            "key": key,
            "result": result,
            "source": source,
            "rules_version": key.rsplit("|", 1)[-1],
            "study_id": study_id,
            "reviewer_id": reviewer_id,
            "approved_at": approved_at,
            "rule_key": rule_key,
            "agrees_with_rules": agrees_with_rules,
            "approvals": 1,
        }
        inserted = await self._session.scalar(
            pg_insert(ClassificationCacheEntry)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[ClassificationCacheEntry.org_id, ClassificationCacheEntry.key]
            )
            .returning(ClassificationCacheEntry.key)
        )
        if inserted is not None:
            return PutResult("created", 1)
        entry = (
            await self._session.scalars(
                select(ClassificationCacheEntry)
                .where(
                    ClassificationCacheEntry.org_id == org_id,
                    ClassificationCacheEntry.key == key,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one()
        if answer_of(entry.result) == answer_of(result):
            entry.approvals += 1
            await self._session.flush()
            return PutResult("confirmed", entry.approvals)
        previous = _snapshot(entry)
        for name, value in values.items():
            setattr(entry, name, value)
        entry.hits = 0
        await self._session.flush()
        return PutResult("replaced", 1, previous)

    async def confirm(
        self, org_id: uuid.UUID, key: str, result: Mapping[str, Any]
    ) -> PutResult | None:
        """One more approval of the convention for ``(org_id, key)`` when it gives the same
        answer as ``result`` (an approved asset classified from it); ``None`` when there is
        no such row or it now gives another answer (nothing is written)."""
        entry = (
            await self._session.scalars(
                select(ClassificationCacheEntry)
                .where(
                    ClassificationCacheEntry.org_id == org_id,
                    ClassificationCacheEntry.key == key,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if entry is None or answer_of(entry.result) != answer_of(result):
            return None
        entry.approvals += 1
        await self._session.flush()
        return PutResult("confirmed", entry.approvals)


__all__ = [
    "UNCITED",
    "CachedClassification",
    "ClassificationCacheRepository",
    "PutResult",
    "answer_of",
    "cache_key",
]
