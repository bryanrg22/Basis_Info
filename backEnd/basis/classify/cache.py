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

The table holds exact keys only; the reader derives a hierarchy from them (section 9):
``exact`` (the component's own key), ``label_room`` (the same label and room, whatever
material and attachment the photo step read), ``label`` (the same label in any room). A
generic floor or surface finish keeps its material at every tier (the material is the
item), and the catch-all ``other`` matches at the exact tier only. :class:`ConventionIndex` groups a
firm's rows by tier; :meth:`ConventionIndex.match` serves the most specific tier that has a
candidate and settles a disagreement inside a tier by approvals (a tie is a conflict).
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from basis.classify.schemas import CitationClaim
from basis.db.enums import Bucket
from basis.db.models import ClassificationCacheEntry
from basis.domain.rules import (
    GENERIC_FLOOR_LABELS,
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

Tier = Literal["exact", "label_room", "label"]
TIERS: Final[tuple[Tier, ...]] = ("exact", "label_room", "label")
# Labels that name nothing: a ruling on one says nothing about the next (section 9.1).
CATCH_ALL_LABELS: Final = frozenset({"other"})
# Labels whose material is the item, so every tier keeps it (section 9.1): the generic
# floors the rules table resolves by material, and the surface finishes (wallpaper and wood
# paneling are wall coverings; drywall and painted masonry are the wall).
MATERIAL_LABELS: Final = GENERIC_FLOOR_LABELS | {"wall_finish", "ceiling_finish"}
KEY_PARTS: Final = 6


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


def tier_key(key: str, tier: Tier) -> str:
    """The exact ``key`` projected onto ``tier`` (section 9.1): ``exact`` is the key itself;
    ``label_room`` blanks the material and attachment; ``label`` blanks the room too. A
    label whose material is the item (:data:`MATERIAL_LABELS`) keeps its material."""
    parts = key.split("|")
    if len(parts) != KEY_PARTS:
        raise ValueError(f"not a {KEY_PARTS}-part context key: {key!r}")
    if tier == "exact":
        return key
    label, material, _attachment, room, property_class, version = parts
    kept = material if label in MATERIAL_LABELS else ""
    if tier == "label_room":
        return "|".join((label, kept, "", room, property_class, version))
    return "|".join((label, kept, "", "", property_class, version))


def tier_keys(
    key: str, *, catch_all: frozenset[str] = CATCH_ALL_LABELS
) -> tuple[tuple[Tier, str], ...]:
    """The tiers a component with exact ``key`` is looked up at, most specific first, each
    with the projected key: ``exact``, then ``label_room`` (skipped when the room is
    unknown: it would be the ``label`` tier), then ``label``; a catch-all label
    (``catch_all``, :data:`CATCH_ALL_LABELS` in production) has the exact tier only."""
    parts = key.split("|")
    if len(parts) != KEY_PARTS:
        raise ValueError(f"not a {KEY_PARTS}-part context key: {key!r}")
    found: list[tuple[Tier, str]] = [("exact", key)]
    if parts[0] in catch_all:
        return tuple(found)
    if parts[3]:
        found.append(("label_room", tier_key(key, "label_room")))
    found.append(("label", tier_key(key, "label")))
    return tuple(found)


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

    def provenance(self, matched_tier: Tier = "exact") -> dict[str, Any]:
        """The ``convention`` member of an asset classified from this row (the asset keeps
        it as of its classification: a later approval or replacement does not rewrite it);
        ``matched_tier`` says how the component's key met the row's (section 9.4)."""
        return {
            "key": self.key,
            "matched_tier": matched_tier,
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
class ConventionMatch:
    """The convention that serves a component: the row and the tier it matched at."""

    cached: CachedClassification
    tier: Tier


@dataclass(frozen=True, slots=True)
class ConventionConflict:
    """The firm's rulings disagree at ``tier`` with equal weight (section 9.3): no
    convention serves, and the component carries ``convention_conflict:<tier>``."""

    tier: Tier
    rows: tuple[CachedClassification, ...]


class ConventionIndex:
    """A firm's conventions grouped by tier (section 9.1), built from the rows
    :meth:`ClassificationCacheRepository.get_for_labels` returns, or from a mapping of
    exact key to row (what the router's tests pass)."""

    def __init__(
        self,
        rows: Iterable[CachedClassification] | Mapping[str, CachedClassification] = (),
        *,
        catch_all: frozenset[str] = CATCH_ALL_LABELS,
    ) -> None:
        values = rows.values() if isinstance(rows, Mapping) else rows
        # Which labels never generalize; the replay evaluation passes an empty set to
        # measure the plain hierarchy (section 9.5).
        self.catch_all = catch_all
        self._rows: dict[str, CachedClassification] = {}
        self._tiers: dict[Tier, dict[str, list[CachedClassification]]] = {t: {} for t in TIERS}
        for row in values:
            self._rows[row.key] = row
            for tier in TIERS:
                try:
                    projected = tier_key(row.key, tier)
                except ValueError:
                    break  # a malformed key is never served
                self._tiers[tier].setdefault(projected, []).append(row)

    def __len__(self) -> int:
        return len(self._rows)

    def __contains__(self, key: object) -> bool:
        return key in self._rows

    def get(self, key: str) -> CachedClassification | None:
        """The row with this exact key, if any."""
        return self._rows.get(key)

    def match(
        self, key: str, *, org_id: uuid.UUID | None
    ) -> ConventionMatch | ConventionConflict | None:
        """The convention for a component with exact ``key``: the most specific tier
        (:func:`tier_keys`) with a candidate of the firm ``org_id`` (a row of another
        firm, or any row when the study has no firm, never serves). Inside a tier the
        candidates are grouped by answer; the answer whose strongest ruling has the most
        approvals wins, and among its rows the most approved serves (then the smallest
        key). Equally strong different answers are a :class:`ConventionConflict`: no
        coarser tier is consulted."""
        if org_id is None:
            return None
        for tier, projected in tier_keys(key, catch_all=self.catch_all):
            candidates = [r for r in self._tiers[tier].get(projected, ()) if r.org_id == org_id]
            if not candidates:
                continue
            by_answer: dict[tuple[str, Decimal | None, str | None], list[CachedClassification]] = {}
            for row in candidates:
                by_answer.setdefault(_answer(row), []).append(row)
            ranked = sorted(
                by_answer.values(),
                key=lambda rows: (-max(r.approvals for r in rows), min(r.key for r in rows)),
            )
            if len(ranked) > 1 and max(r.approvals for r in ranked[0]) == max(
                r.approvals for r in ranked[1]
            ):
                return ConventionConflict(
                    tier, tuple(sorted(candidates, key=lambda r: (-r.approvals, r.key)))
                )
            served = min(ranked[0], key=lambda r: (-r.approvals, r.key))
            return ConventionMatch(served, tier)
        return None


def _answer(row: CachedClassification) -> tuple[str, Decimal | None, str | None]:
    return row.bucket.value, row.period, row.section


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

    async def get_for_labels(
        self, org_id: uuid.UUID, labels: set[str], rules_version: str = RULES_VERSION
    ) -> list[CachedClassification]:
        """The firm's conventions at ``rules_version`` whose label (the key's first part,
        already normalized) is one of ``labels``: every row a tier of section 9 can serve
        for components with those labels. Malformed rows are left out."""
        wanted = {normalize_label(label) for label in labels} - {""}
        if not wanted:
            return []
        rows = await self._session.scalars(
            select(ClassificationCacheEntry)
            .where(
                ClassificationCacheEntry.org_id == org_id,
                ClassificationCacheEntry.rules_version == rules_version,
                func.split_part(ClassificationCacheEntry.key, "|", 1).in_(sorted(wanted)),
            )
            .order_by(ClassificationCacheEntry.key)
        )
        found: list[CachedClassification] = []
        for row in rows:
            cached = CachedClassification.from_entry(row)
            if cached is not None:
                found.append(cached)
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
    "CATCH_ALL_LABELS",
    "MATERIAL_LABELS",
    "TIERS",
    "UNCITED",
    "CachedClassification",
    "ClassificationCacheRepository",
    "ConventionConflict",
    "ConventionIndex",
    "ConventionMatch",
    "PutResult",
    "Tier",
    "answer_of",
    "cache_key",
    "tier_key",
    "tier_keys",
]
