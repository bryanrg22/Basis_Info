"""The engineer's line annotations ``process_assets`` carries from one revision to the next
(EVAL_REPORT 102, docs/review_2026-10-15/RENOVATIONS.md section 4.6).

Two decisions an engineer records on an acquisition line at the assets review that are neither
a classification nor a price: the line's own age (:mod:`basis.domain.component_age`) and its
removal by a renovation project (:mod:`basis.domain.renovations`). Each is an ``asset``
correction's fields; the record lives on the row's takeoff (``takeoffs.inputs``), and every
later revision's row of the same line gets it again here, whatever else changes:

* the line is found by its lineage: a component row by the ``carried_from`` chain of its
  component (``apply_corrections`` and ``carry_revision`` write it on every copy), a concealed
  row of the structure model by its label;
* the annotations come from the latest earlier revision that has assets (an assets rejection's
  is the one before; after a reopen at an earlier gate it can be further back), so a rejection
  at any gate keeps them;
* this rejection's corrections are applied on top (a record replaces, a clearing correction
  removes).

Which renovation project a component belongs to is not carried: it follows the renovations
document's photograph tags each time the assets are built
(:func:`basis.domain.renovations.membership`).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.models import Asset, Component, Takeoff
from basis.domain import component_age, renovations
from basis.evidence.result import concealed_label
from basis.workflow.contracts import CorrectionInput

CARRIED_FROM: Final = "carried_from"
KEYS: Final = (component_age.INPUT_KEY, renovations.REMOVAL_INPUT)
# How many revisions back a lineage is followed (a study has far fewer).
MAX_HOPS: Final = 100


@dataclass(frozen=True, slots=True)
class Annotations:
    """The records each row of this revision is written with."""

    by_component: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    by_label: dict[str, dict[str, Any]] = field(default_factory=dict)

    def for_component(self, component_id: uuid.UUID) -> dict[str, Any]:
        return dict(self.by_component.get(component_id, {}))

    def for_label(self, label: str) -> dict[str, Any]:
        return dict(self.by_label.get(label, {}))


def changes(correction: CorrectionInput, revision: int) -> dict[str, dict[str, Any] | None]:
    """The records a correction sets (a dict) or clears (None), by input key; empty when it
    carries neither an age nor a removal. ``after`` must already be checked."""
    out: dict[str, dict[str, Any] | None] = {}
    reason = correction.reason or ""
    if component_age.has_age(correction.after):
        out[component_age.INPUT_KEY] = component_age.record(
            correction.after, reason, from_asset_id=correction.entity_id, revision=revision
        )
    if renovations.has_removal(correction.after):
        out[renovations.REMOVAL_INPUT] = renovations.removal_record(
            correction.after, reason, from_asset_id=correction.entity_id, revision=revision
        )
    return out


def _kept(inputs: Any) -> dict[str, Any]:
    if not isinstance(inputs, Mapping):
        return {}
    return {k: inputs[k] for k in KEYS if isinstance(inputs.get(k), Mapping)}


async def load(
    session: AsyncSession,
    study_id: uuid.UUID,
    revision: int,
    components: Sequence[Component],
    corrections: Sequence[CorrectionInput],
) -> Annotations:
    """The annotations of this revision's rows: the latest earlier revision's (by lineage),
    with this rejection's corrections applied; empty for the first revision with assets and
    when nothing earlier or now carries one."""
    edits: dict[str, dict[str, dict[str, Any] | None]] = {}
    for correction in corrections:
        if correction.entity_type != "asset":
            continue
        found = changes(correction, revision)
        if found:
            edits.setdefault(correction.entity_id, {}).update(found)
    source = await session.scalar(
        select(func.max(Asset.revision)).where(
            Asset.study_id == study_id, Asset.revision < revision
        )
    )
    if source is None:
        return Annotations()
    old_assets = (
        await session.scalars(
            select(Asset).where(Asset.study_id == study_id, Asset.revision == source)
        )
    ).all()
    takeoffs = {
        t.asset_id: t
        for t in (
            await session.scalars(
                select(Takeoff).where(Takeoff.asset_id.in_([a.id for a in old_assets]))
            )
        ).all()
    }
    # Each old row's records, and this rejection's edits on top (keyed by the old asset id).
    records: dict[uuid.UUID, dict[str, Any]] = {}
    for asset in old_assets:
        takeoff = takeoffs.get(asset.id)
        kept = _kept(takeoff.inputs if takeoff else None)
        for key, value in edits.get(str(asset.id), {}).items():
            if value is None:
                kept.pop(key, None)
            else:
                kept[key] = value
        if kept:
            records[asset.id] = kept
    if not records:
        return Annotations()
    # The old component each current component descends from, through the revisions between.
    lineage = await _lineage(session, study_id, source, components)
    by_old_component = {
        a.component_id: records[a.id]
        for a in old_assets
        if a.id in records and a.component_id is not None
    }
    by_component = {
        current: dict(by_old_component[old])
        for current, old in lineage.items()
        if old in by_old_component
    }
    by_label: dict[str, dict[str, Any]] = {}
    for asset in old_assets:
        if asset.component_id is not None or asset.id not in records:
            continue
        takeoff = takeoffs.get(asset.id)
        label, concealed = concealed_label(takeoff.inputs if takeoff else None)
        if concealed and label is not None:
            by_label[label] = dict(records[asset.id])
    return Annotations(by_component, by_label)


async def _lineage(
    session: AsyncSession, study_id: uuid.UUID, source: int, components: Sequence[Component]
) -> dict[uuid.UUID, uuid.UUID]:
    """``{current component id: its ancestor in revision source}`` along ``carried_from``
    (one query per revision between them)."""
    out: dict[uuid.UUID, uuid.UUID] = {}
    frontier = {c.id: p for c in components if (p := _parent(c.raw)) is not None}
    for _ in range(MAX_HOPS):
        if not frontier:
            break
        rows = (
            await session.scalars(
                select(Component).where(
                    Component.study_id == study_id, Component.id.in_(set(frontier.values()))
                )
            )
        ).all()
        found = {r.id: r for r in rows}
        nxt: dict[uuid.UUID, uuid.UUID] = {}
        for cid, parent in frontier.items():
            row = found.get(parent)
            if row is None or row.revision < source:
                continue
            if row.revision == source:
                out[cid] = row.id
            elif (grand := _parent(row.raw)) is not None:
                nxt[cid] = grand
        frontier = nxt
    return out


def _parent(raw: Any) -> uuid.UUID | None:
    if not isinstance(raw, Mapping):
        return None
    value = raw.get(CARRIED_FROM)
    try:
        return uuid.UUID(str(value)) if value is not None else None
    except ValueError:
        return None


__all__ = ["KEYS", "Annotations", "changes", "load"]
