"""The revision history and the diff between two revisions (docs/api/REVISIONS.md).

Both are derived from what the study already records, with no bookkeeping of their own:

* the **history** (:meth:`RevisionRepository.history`) reads ``study_events`` (``stage.entered``
  for when a revision began and how far it got; ``study.started``, ``review.decided``,
  ``study.reopened``, ``revision.restored`` and ``study.restarted`` for what made it;
  ``export.written`` for its export), ``reviews`` (the decisions made at it; the rejection
  that made it when the study predates the timeline), ``corrections`` (what the decision that
  made it carried) and ``report_signatures``;
* the **diff** (:meth:`RevisionRepository.diff`) pairs the rows of two revisions through
  their lineage: ``rooms.carried_from``, ``components.raw["carried_from"]``, an asset through
  its component (assets are rebuilt per revision; the component they price carries the
  lineage), a concealed row (no component) through its label. A row of the later revision
  with no ancestor in the earlier one was rewritten by the model (``added``); a row of the
  earlier one with no descendant was dropped or rewritten (``removed``); a pair whose compared
  fields differ is ``changed`` with the before and after values.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import REVIEW_STAGES, Decision, Stage
from basis.db.errors import InvalidCursorError
from basis.db.models import (
    Asset,
    Component,
    Correction,
    CostEstimate,
    Extraction,
    ReportSignature,
    Review,
    Room,
    Study,
    StudyEvent,
    Takeoff,
)

CARRIED_FROM: Final = "carried_from"
OriginKind = Literal["start", "rejection", "reopen", "restore", "restart", "unknown"]
DiffKind = Literal["room", "component", "asset"]
KIND_ORDER: Final[tuple[DiffKind, ...]] = ("room", "component", "asset")
# Stages in workflow order, to say how far a revision got.
STAGE_ORDER: Final[tuple[Stage, ...]] = tuple(Stage)
MAX_DIFF_PAGE: Final = 200
_HISTORY_EVENTS: Final = (
    "stage.entered",
    "study.started",
    "review.decided",
    "study.reopened",
    "revision.restored",
    "study.restarted",
    "export.written",
)


# --------------------------------------------------------------------------- history


@dataclass(frozen=True, slots=True)
class RevisionOrigin:
    kind: OriginKind
    stage: Stage | None = None  # the gate the decision, reopen or restore was made at
    actor_id: uuid.UUID | None = None
    at: datetime | None = None
    review_id: uuid.UUID | None = None
    from_revision: int | None = None


@dataclass(frozen=True, slots=True)
class RevisionDecision:
    stage: Stage
    decision: Decision
    reviewer_id: uuid.UUID
    review_id: uuid.UUID
    at: datetime


@dataclass(frozen=True, slots=True)
class RevisionExport:
    uri: str | None
    sha256: str | None
    at: datetime


@dataclass(frozen=True, slots=True)
class RevisionSignature:
    id: uuid.UUID
    signed_by: uuid.UUID
    signed_at: datetime


@dataclass(slots=True)
class RevisionItem:
    revision: int
    started_at: datetime | None = None
    ended_at: datetime | None = None
    origin: RevisionOrigin = field(default_factory=lambda: RevisionOrigin("unknown"))
    corrections: dict[str, int] = field(default_factory=dict)
    decisions: list[RevisionDecision] = field(default_factory=list)
    stage_reached: Stage | None = None
    export: RevisionExport | None = None
    signature: RevisionSignature | None = None
    current: bool = False


def _stage_max(a: Stage | None, b: Stage) -> Stage:
    if a is None or STAGE_ORDER.index(b) > STAGE_ORDER.index(a):
        return b
    return a


def _uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class RevisionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ history

    async def history(self, study: Study) -> list[RevisionItem]:
        """One item per revision, 1 to the study's current one (module doc)."""
        study_id = study.id
        items = {n: RevisionItem(revision=n) for n in range(1, study.revision + 1)}
        items[study.revision].current = True
        events = (
            await self._session.scalars(
                select(StudyEvent)
                .where(
                    StudyEvent.study_id == study_id,
                    StudyEvent.event_type.in_(_HISTORY_EVENTS),
                )
                .order_by(StudyEvent.seq)
            )
        ).all()
        for event in events:
            detail = event.detail if isinstance(event.detail, dict) else {}
            item = items.get(event.revision) if event.revision is not None else None
            if item is not None and (item.started_at is None or event.created_at < item.started_at):
                item.started_at = event.created_at
            if event.event_type == "stage.entered" and item is not None and event.stage is not None:
                item.stage_reached = _stage_max(item.stage_reached, event.stage)
            elif event.event_type == "study.started" and item is not None:
                item.origin = RevisionOrigin("start", actor_id=event.actor_id, at=event.created_at)
            elif event.event_type == "review.decided" and detail.get("decision") == "reject":
                made = items.get(_int(detail.get("new_revision")) or -1)
                if made is not None:
                    made.origin = RevisionOrigin(
                        "rejection",
                        stage=event.stage,
                        actor_id=event.actor_id,
                        at=event.created_at,
                        review_id=event.entity_id,
                        from_revision=event.revision,
                    )
            elif event.event_type in ("study.reopened", "revision.restored") and item is not None:
                item.origin = RevisionOrigin(
                    "reopen" if event.event_type == "study.reopened" else "restore",
                    stage=event.stage,
                    actor_id=event.actor_id,
                    at=event.created_at,
                    from_revision=_int(detail.get("from_revision")),
                )
            elif event.event_type == "study.restarted" and item is not None:
                before = event.before if isinstance(event.before, dict) else {}
                item.origin = RevisionOrigin(
                    "restart",
                    stage=event.stage,
                    actor_id=event.actor_id,
                    at=event.created_at,
                    from_revision=_int(before.get("revision")),
                )
            elif event.event_type == "export.written" and item is not None:
                uri, sha = detail.get("uri"), detail.get("sha256")
                item.export = RevisionExport(
                    uri=uri if isinstance(uri, str) else None,
                    sha256=sha if isinstance(sha, str) else None,
                    at=event.created_at,
                )
        reviews = (
            await self._session.scalars(
                select(Review)
                .where(Review.study_id == study_id)
                .order_by(Review.created_at, Review.id)
            )
        ).all()
        for review in reviews:
            item = items.get(review.revision)
            if item is None:
                continue
            item.decisions.append(
                RevisionDecision(
                    stage=review.stage,
                    decision=review.decision,
                    reviewer_id=review.reviewer_id,
                    review_id=review.id,
                    at=review.created_at,
                )
            )
            made = items.get(review.revision + 1)
            if (
                review.decision is Decision.REJECT
                and made is not None
                and made.origin.kind == "unknown"
            ):
                # A study older than the timeline: the rejection from the reviews table.
                made.origin = RevisionOrigin(
                    "rejection",
                    stage=review.stage,
                    actor_id=review.reviewer_id,
                    at=review.created_at,
                    review_id=review.id,
                    from_revision=review.revision,
                )
        if items[1].origin.kind == "unknown":
            items[1].origin = RevisionOrigin("start", at=study.created_at)
        counts = (
            await self._session.execute(
                select(Correction.revision, Correction.entity_type, func.count())
                .where(Correction.study_id == study_id)
                .group_by(Correction.revision, Correction.entity_type)
            )
        ).all()
        for revision, entity_type, count in counts:
            made = items.get(int(revision) + 1)
            if made is not None and made.origin.kind == "rejection":
                made.corrections[str(entity_type)] = int(count)
        signatures = (
            await self._session.scalars(
                select(ReportSignature).where(ReportSignature.study_id == study_id)
            )
        ).all()
        for row in signatures:
            item = items.get(row.revision)
            if item is not None:
                item.signature = RevisionSignature(
                    id=row.id, signed_by=row.signed_by, signed_at=row.signed_at
                )
        for item in items.values():
            if item.started_at is None:
                item.started_at = await self._first_row_at(study_id, item.revision)
            if item.stage_reached is None:
                item.stage_reached = await self._stage_from_rows(study_id, item.revision)
        ordered = [items[n] for n in sorted(items)]
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            earlier.ended_at = later.started_at
        if ordered and ordered[-1].stage_reached is None and study.revision == ordered[-1].revision:
            ordered[-1].stage_reached = study.current_stage
        return ordered

    async def _first_row_at(self, study_id: uuid.UUID, revision: int) -> datetime | None:
        """The earliest row of the revision (a study older than the timeline)."""
        found: datetime | None = None
        for model in (Extraction, Room, Component, Asset):
            value = await self._session.scalar(
                select(func.min(model.created_at)).where(
                    model.study_id == study_id, model.revision == revision
                )
            )
            if value is not None and (found is None or value < found):
                found = value
        return found

    async def _stage_from_rows(self, study_id: uuid.UUID, revision: int) -> Stage | None:
        """How far a revision's rows reach (a study older than the timeline): assets mean the
        assets review at least, components the components review, rooms the rooms review, an
        extraction the appraisal review."""
        for model, stage in (
            (Asset, Stage.ASSETS_REVIEW),
            (Component, Stage.COMPONENTS_REVIEW),
            (Room, Stage.ROOMS_REVIEW),
            (Extraction, Stage.APPRAISAL_REVIEW),
        ):
            exists = await self._session.scalar(
                select(model.id)
                .where(model.study_id == study_id, model.revision == revision)
                .limit(1)
            )
            if exists is not None:
                return stage
        return None

    async def gate_reached(self, study: Study, revision: int) -> Stage | None:
        """The last review gate ``revision`` reached: the furthest review stage it entered
        (``assets_review`` for one that went on to export), from the timeline; from its rows
        only when the timeline has no stage for it (a study older than the timeline); None
        when it never reached a gate. A restore opens here (docs/api/REVISIONS.md)."""
        entered = (
            await self._session.scalars(
                select(StudyEvent.stage).where(
                    StudyEvent.study_id == study.id,
                    StudyEvent.event_type == "stage.entered",
                    StudyEvent.revision == revision,
                )
            )
        ).all()
        furthest: Stage | None = None
        for stage in entered:
            if stage is not None:
                furthest = _stage_max(furthest, stage)
        if furthest is None:
            furthest = await self._stage_from_rows(study.id, revision)
        if furthest is None:
            return None
        gates = [g for g in STAGE_ORDER if g in REVIEW_STAGES]
        reached = [g for g in gates if STAGE_ORDER.index(g) <= STAGE_ORDER.index(furthest)]
        return reached[-1] if reached else None

    # ------------------------------------------------------------------ diff

    async def diff(self, study_id: uuid.UUID, from_revision: int, to_revision: int) -> RevisionDiff:
        """The rooms, components and assets added, removed and changed from ``from_revision``
        to ``to_revision`` (module doc); ``from_revision > to_revision`` is the reverse diff."""
        if from_revision == to_revision:
            return RevisionDiff(from_revision, to_revision)
        lo, hi = sorted((from_revision, to_revision))
        forward = await self._forward_diff(study_id, lo, hi)
        if from_revision < to_revision:
            return forward
        return RevisionDiff(
            from_revision=from_revision,
            to_revision=to_revision,
            added=forward.removed,
            removed=forward.added,
            changed=[
                DiffChange(
                    kind=c.kind,
                    from_id=c.to_id,
                    to_id=c.from_id,
                    label=c.label,
                    changes={k: FieldChange(v.after, v.before) for k, v in c.changes.items()},
                )
                for c in forward.changed
            ],
            unchanged=forward.unchanged,
        )

    async def _forward_diff(self, study_id: uuid.UUID, lo: int, hi: int) -> RevisionDiff:
        """``lo < hi``. Rows are paired by their lineage root: the row of the earliest
        revision each descends from (``carried_from`` followed to the end). For successive
        revisions that is the same as the ancestor of ``hi``'s row at ``lo``; for a restored
        revision, whose rows descend from the restored one and skip the revisions between,
        it still pairs each row with its counterpart (the diff 3 -> 4 of a revision 4
        restored from 1 shows revision 3's changes undone)."""
        rooms = (await self._session.scalars(select(Room).where(Room.study_id == study_id))).all()
        components = (
            await self._session.scalars(select(Component).where(Component.study_id == study_id))
        ).all()
        assets_lo = (
            await self._session.scalars(
                select(Asset).where(Asset.study_id == study_id, Asset.revision == lo)
            )
        ).all()
        assets_hi = (
            await self._session.scalars(
                select(Asset).where(Asset.study_id == study_id, Asset.revision == hi)
            )
        ).all()
        asset_ids = [a.id for a in (*assets_lo, *assets_hi)]
        takeoffs: dict[uuid.UUID, Takeoff] = {}
        costs: dict[uuid.UUID, CostEstimate] = {}
        if asset_ids:
            for row in (
                await self._session.scalars(select(Takeoff).where(Takeoff.asset_id.in_(asset_ids)))
            ).all():
                takeoffs[row.asset_id] = row
            for cost in (
                await self._session.scalars(
                    select(CostEstimate).where(CostEstimate.asset_id.in_(asset_ids))
                )
            ).all():
                costs[cost.asset_id] = cost
        room_by_id = {r.id: r for r in rooms}
        component_by_id = {c.id: c for c in components}
        room_roots: dict[uuid.UUID, uuid.UUID] = {}
        component_roots: dict[uuid.UUID, uuid.UUID] = {}

        def parent_room_of(room_id: uuid.UUID) -> uuid.UUID | None:
            return room_by_id[room_id].carried_from

        def parent_component_of(component_id: uuid.UUID) -> uuid.UUID | None:
            raw = component_by_id[component_id].raw
            return _uuid(raw.get(CARRIED_FROM)) if isinstance(raw, dict) else None

        def room_root(room_id: uuid.UUID | None) -> uuid.UUID | None:
            if room_id is None:
                return None
            return _root(room_id, room_roots, parent_room_of, room_by_id)

        def component_root(component_id: uuid.UUID) -> uuid.UUID:
            found = _root(component_id, component_roots, parent_component_of, component_by_id)
            assert found is not None
            return found

        diff = RevisionDiff(from_revision=lo, to_revision=hi)
        # Rooms.
        rooms_lo = {r.id: r for r in rooms if r.revision == lo}
        lo_room_by_root = {room_root(r.id): r for r in sorted(rooms_lo.values(), key=_by_id)}
        matched_rooms: set[uuid.UUID] = set()
        for room in sorted((r for r in rooms if r.revision == hi), key=_by_id):
            room_parent = lo_room_by_root.get(room_root(room.id))
            if room_parent is None or room_parent.id in matched_rooms:
                diff.added.append(DiffRow("room", room.id, room.name))
                continue
            matched_rooms.add(room_parent.id)
            changes = _changes(_room_fields(room_parent), _room_fields(room))
            diff.record("room", room_parent.id, room.id, room.name, changes)
        for room_id in sorted(set(rooms_lo) - matched_rooms, key=str):
            diff.removed.append(DiffRow("room", room_id, rooms_lo[room_id].name))
        # Components.
        components_lo = {c.id: c for c in components if c.revision == lo}
        lo_component_by_root = {
            component_root(c.id): c for c in sorted(components_lo.values(), key=_by_id)
        }
        matched_components: set[uuid.UUID] = set()
        ancestor_of: dict[uuid.UUID, uuid.UUID] = {}
        for component in sorted((c for c in components if c.revision == hi), key=_by_id):
            component_parent = lo_component_by_root.get(component_root(component.id))
            if component_parent is None or component_parent.id in matched_components:
                diff.added.append(DiffRow("component", component.id, component.label))
                continue
            matched_components.add(component_parent.id)
            ancestor_of[component.id] = component_parent.id
            parent_room = (
                room_by_id.get(component_parent.room_id)
                if component_parent.room_id is not None
                else None
            )
            before = _component_fields(component_parent, parent_room)
            room_now = room_by_id.get(component.room_id) if component.room_id else None
            after = _component_fields(component, room_now)
            # The room compared through its lineage: a copy of the same room is no change.
            if room_root(component.room_id) == room_root(component_parent.room_id):
                after["room"] = before["room"]
            changes = _changes(before, after)
            diff.record("component", component_parent.id, component.id, component.label, changes)
        for component_id in sorted(set(components_lo) - matched_components, key=str):
            diff.removed.append(
                DiffRow("component", component_id, components_lo[component_id].label)
            )
        # Assets: through the component's lineage, a concealed row through its label.
        lo_by_component = {a.component_id: a for a in assets_lo if a.component_id is not None}
        lo_concealed = {
            _concealed_label(takeoffs.get(a.id)): a for a in assets_lo if a.component_id is None
        }
        matched_assets: set[uuid.UUID] = set()
        for asset in sorted(assets_hi, key=_by_id):
            asset_parent: Asset | None = None
            if asset.component_id is not None:
                parent_component = ancestor_of.get(asset.component_id)
                asset_parent = lo_by_component.get(parent_component) if parent_component else None
            else:
                asset_parent = lo_concealed.get(_concealed_label(takeoffs.get(asset.id)))
            label = _asset_label(asset, component_by_id, takeoffs)
            if asset_parent is None or asset_parent.id in matched_assets:
                diff.added.append(DiffRow("asset", asset.id, label))
                continue
            matched_assets.add(asset_parent.id)
            changes = _changes(
                _asset_fields(
                    asset_parent, takeoffs.get(asset_parent.id), costs.get(asset_parent.id)
                ),
                _asset_fields(asset, takeoffs.get(asset.id), costs.get(asset.id)),
            )
            diff.record("asset", asset_parent.id, asset.id, label, changes)
        for asset in sorted(assets_lo, key=_by_id):
            if asset.id not in matched_assets:
                label = _asset_label(asset, component_by_id, takeoffs)
                diff.removed.append(DiffRow("asset", asset.id, label))
        diff.changed.sort(key=lambda c: (KIND_ORDER.index(c.kind), str(c.to_id)))
        return diff


def _by_id(row: Any) -> str:
    return str(row.id)


def _root(
    row_id: uuid.UUID,
    cache: dict[uuid.UUID, uuid.UUID],
    parent_of: Callable[[uuid.UUID], uuid.UUID | None],
    rows: dict[uuid.UUID, Any],
) -> uuid.UUID | None:
    """The earliest ancestor of ``row_id`` (itself when it has none), memoized; a parent
    that is not a row of the study ends the chain at the row before it."""
    if row_id not in rows:
        return None
    path: list[uuid.UUID] = []
    current = row_id
    while current not in cache:
        path.append(current)
        parent = parent_of(current)
        if parent is None or parent not in rows or parent in path:
            cache[current] = current
            break
        current = parent
    root = cache[current]
    for visited in path:
        cache[visited] = root
    return root


# --------------------------------------------------------------------------- diff shapes


@dataclass(frozen=True, slots=True)
class FieldChange:
    before: Any
    after: Any


@dataclass(frozen=True, slots=True)
class DiffRow:
    kind: DiffKind
    id: uuid.UUID
    label: str


@dataclass(frozen=True, slots=True)
class DiffChange:
    kind: DiffKind
    from_id: uuid.UUID
    to_id: uuid.UUID
    label: str
    changes: dict[str, FieldChange]


@dataclass(slots=True)
class RevisionDiff:
    from_revision: int
    to_revision: int
    added: list[DiffRow] = field(default_factory=list)
    removed: list[DiffRow] = field(default_factory=list)
    changed: list[DiffChange] = field(default_factory=list)
    unchanged: Counter[str] = field(default_factory=Counter)

    def record(
        self,
        kind: DiffKind,
        from_id: uuid.UUID,
        to_id: uuid.UUID,
        label: str,
        changes: dict[str, FieldChange],
    ) -> None:
        if changes:
            self.changed.append(DiffChange(kind, from_id, to_id, label, changes))
        else:
            self.unchanged[kind] += 1

    def counts(self) -> dict[str, dict[str, int]]:
        found: dict[str, dict[str, int]] = {
            kind: {"added": 0, "removed": 0, "changed": 0, "unchanged": self.unchanged[kind]}
            for kind in KIND_ORDER
        }
        for row in self.added:
            found[row.kind]["added"] += 1
        for row in self.removed:
            found[row.kind]["removed"] += 1
        for change in self.changed:
            found[change.kind]["changed"] += 1
        return found


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, FieldChange]:
    return {
        key: FieldChange(_json(before.get(key)), _json(after.get(key)))
        for key in before
        if _json(before.get(key)) != _json(after.get(key))
    }


def _room_fields(room: Room) -> dict[str, Any]:
    return {"name": room.name, "room_type": room.room_type, "floor": room.floor}


def _component_fields(component: Component, room: Room | None) -> dict[str, Any]:
    raw = component.raw if isinstance(component.raw, dict) else {}
    return {
        "label": component.label,
        "material": component.material,
        "attachment": component.attachment,
        "count": raw.get("count"),
        "room": None if room is None else {"id": str(room.id), "name": room.name},
    }


def _asset_fields(
    asset: Asset, takeoff: Takeoff | None, cost: CostEstimate | None
) -> dict[str, Any]:
    return {
        "category": asset.category,
        "bucket": asset.bucket.value,
        "recovery_period": asset.recovery_period,
        "irs_section": asset.irs_section,
        "method": asset.method.value,
        "quantity": None if takeoff is None else takeoff.quantity,
        "unit": None if takeoff is None else takeoff.unit,
        "unit_cost": None if cost is None else cost.unit_cost,
        "total": None if cost is None else cost.total,
    }


def _concealed_label(takeoff: Takeoff | None) -> str:
    inputs = takeoff.inputs if takeoff is not None and isinstance(takeoff.inputs, dict) else {}
    label = inputs.get("label")
    return label if isinstance(label, str) else ""


def _asset_label(
    asset: Asset, components: dict[uuid.UUID, Component], takeoffs: dict[uuid.UUID, Takeoff]
) -> str:
    component = components.get(asset.component_id) if asset.component_id is not None else None
    if component is not None:
        return component.label
    return _concealed_label(takeoffs.get(asset.id)) or asset.category


# --------------------------------------------------------------------------- paging


def encode_diff_cursor(from_revision: int, to_revision: int, change: DiffChange) -> str:
    raw = json.dumps(
        {"a": from_revision, "b": to_revision, "k": change.kind, "i": str(change.to_id)},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_diff_cursor(cursor: str, from_revision: int, to_revision: int) -> tuple[int, str]:
    """``(kind rank, to_id)`` of the last change of the previous page; ``InvalidCursorError``
    when the cursor does not decode or was issued for another pair of revisions."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        a, b, kind, to_id = data["a"], data["b"], data["k"], data["i"]
        rank = KIND_ORDER.index(kind)
        uuid.UUID(to_id)
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise InvalidCursorError("invalid pagination cursor") from exc
    if (a, b) != (from_revision, to_revision):
        raise InvalidCursorError("the cursor was issued for another pair of revisions")
    return rank, str(to_id)


def page_of_changes(
    diff: RevisionDiff, *, limit: int, cursor: str | None
) -> tuple[list[DiffChange], str | None]:
    """The changes after ``cursor`` (keyset on kind rank and ``to_id``), at most ``limit``,
    and the cursor of the next page (None on the last)."""
    changes: Sequence[DiffChange] = diff.changed
    if cursor is not None:
        rank, to_id = decode_diff_cursor(cursor, diff.from_revision, diff.to_revision)
        changes = [c for c in changes if (KIND_ORDER.index(c.kind), str(c.to_id)) > (rank, to_id)]
    items = list(changes[:limit])
    next_cursor = (
        encode_diff_cursor(diff.from_revision, diff.to_revision, items[-1])
        if len(changes) > limit
        else None
    )
    return items, next_cursor
