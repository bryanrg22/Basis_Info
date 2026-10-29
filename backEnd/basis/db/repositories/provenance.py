"""Who produced a row: the model, or an engineer who added or modified it, and from what.

Every revision-scoped output row (an extraction field, a room, a photo's room assignment, a
component, an asset with its takeoff and cost) gets a :class:`Provenance`
(docs/api/TIMELINE_AND_AUDIT.md): ``model`` (the pipeline wrote it and no engineer changed
it on the way), ``engineer_added`` (the row exists because an engineer added it: a
``room_component`` correction) or ``engineer_modified`` (an engineer's correction was applied
when the row was copied into its revision; ``before``/``after`` say what changed, from the
copied row's own values and the correction).

The source of truth is the append-only ``corrections`` table (the engineer's input, with the
reviewer and the time) joined to the row's **lineage**: ``apply_corrections`` copies a
revision's rows into the next and names the copy's parent (``components.raw["carried_from"]``,
``rooms.carried_from``, an extraction's ``raw["carried_from"]``; a photo assignment is keyed by
its document, an asset reaches its parent through its component). A correction at revision
``r`` targets a row of revision ``r`` and is applied to that row's copy at ``r + 1``, so
walking a row's ancestors and looking each one up in ``corrections`` finds the correction
that produced the row; the nearest ancestor with one wins (the latest change). A row carried
unchanged after that keeps the same provenance, which is what the engineer would expect: the
value is still theirs. In PROV terms the row ``wasDerivedFrom`` its parent and the
correction's application ``wasAssociatedWith`` the reviewer.

The ``study_events`` rows ``entity.corrected`` / ``entity.added`` / ``entity.moved`` record
the same link forward (new row -> correction) on the timeline; this module does not need
them, so studies whose rows predate the events get the same answers.

One resolver per request: it loads the study's corrections and reviews once (hundreds at
most) and the ancestors of the rows it is asked about in batches, one query per generation.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import Decision
from basis.db.models import (
    Asset,
    Component,
    Correction,
    Extraction,
    ExtractionField,
    PhotoRoom,
    Review,
    Room,
)
from basis.vision.rooms import is_room_manual

Origin = Literal["model", "engineer_added", "engineer_modified"]
CARRIED_FROM: Final = "carried_from"
MANUAL: Final = "manual"


@dataclass(frozen=True, slots=True)
class Provenance:
    origin: Origin
    correction_id: uuid.UUID | None = None
    review_id: uuid.UUID | None = None
    stage: str | None = None  # the review stage the correction was made at
    revision: int | None = None  # the revision the correction was made at (the decided one)
    at: datetime | None = None
    by: uuid.UUID | None = None
    before: Any | None = None
    after: Any | None = None
    changed: tuple[str, ...] = ()
    carried_from: uuid.UUID | None = None  # the parent row the correction was applied to


MODEL: Final = Provenance("model")


@dataclass(slots=True)
class _Corrections:
    by_target: dict[tuple[str, uuid.UUID], list[Correction]] = field(default_factory=dict)
    reject_reviews: dict[int, Review] = field(default_factory=dict)

    def of(self, entity_type: str, entity_id: uuid.UUID, revision: int | None) -> list[Correction]:
        rows = self.by_target.get((entity_type, entity_id), [])
        return [r for r in rows if revision is None or r.revision == revision]


class ProvenanceResolver:
    def __init__(self, session: AsyncSession, study_id: uuid.UUID) -> None:
        self._session = session
        self._study_id = study_id
        self._corrections: _Corrections | None = None

    # ------------------------------------------------------------------ loading

    async def _load(self) -> _Corrections:
        if self._corrections is not None:
            return self._corrections
        loaded = _Corrections()
        rows = await self._session.scalars(
            select(Correction)
            .where(Correction.study_id == self._study_id)
            .order_by(Correction.created_at, Correction.id)
        )
        for row in rows.all():
            loaded.by_target.setdefault((row.entity_type, row.entity_id), []).append(row)
        reviews = await self._session.scalars(
            select(Review).where(
                Review.study_id == self._study_id, Review.decision == Decision.REJECT
            )
        )
        for review in reviews.all():
            loaded.reject_reviews[review.revision] = review
        self._corrections = loaded
        return loaded

    def _from(
        self,
        origin: Origin,
        corrections: Sequence[Correction],
        *,
        before: Any | None,
        carried_from: uuid.UUID | None,
    ) -> Provenance:
        """The provenance a (non-empty) list of corrections of one target gives: the last
        one made is the one reported (several on one target merge into one copy)."""
        assert self._corrections is not None
        latest = corrections[-1]
        after: dict[str, Any] = {}
        for row in corrections:
            if isinstance(row.after, dict):
                after.update(row.after)
        review = self._corrections.reject_reviews.get(latest.revision)
        return Provenance(
            origin=origin,
            correction_id=latest.id,
            review_id=review.id if review is not None else None,
            stage=review.stage.value if review is not None else None,
            revision=latest.revision,
            at=latest.created_at,
            by=latest.reviewer_id,
            before=before,
            after=after or latest.after,
            changed=tuple(sorted(after)) if isinstance(after, dict) else (),
            carried_from=carried_from,
        )

    async def _components_by_id(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Component]:
        wanted = [i for i in set(ids)]
        if not wanted:
            return {}
        rows = await self._session.scalars(
            select(Component).where(Component.study_id == self._study_id, Component.id.in_(wanted))
        )
        return {row.id: row for row in rows.all()}

    async def _component_lineage(self, rows: Sequence[Component]) -> dict[uuid.UUID, Component]:
        """Every ancestor of ``rows`` (through ``raw["carried_from"]``), by id."""
        known = {row.id: row for row in rows}
        frontier = [p for row in rows if (p := _parent_of(row)) is not None]
        while frontier:
            fetched = await self._components_by_id(p for p in frontier if p not in known)
            known.update(fetched)
            frontier = [
                p for c in fetched.values() if (p := _parent_of(c)) is not None and p not in known
            ]
        return known

    # ------------------------------------------------------------------ components

    async def components(self, rows: Sequence[Component]) -> dict[uuid.UUID, Provenance]:
        corrections = await self._load()
        lineage = await self._component_lineage(rows)
        found: dict[uuid.UUID, Provenance] = {}
        for row in rows:
            found[row.id] = self._component(row, lineage, corrections)
        return found

    def _component(
        self,
        row: Component,
        lineage: dict[uuid.UUID, Component],
        corrections: _Corrections,
    ) -> Provenance:
        current = row
        while True:
            parent_id = _parent_of(current)
            parent = lineage.get(parent_id) if parent_id is not None else None
            if parent is None:
                break
            applied = corrections.of("component", parent.id, parent.revision)
            if applied:
                parent_raw = parent.raw if isinstance(parent.raw, dict) else {}
                keys = sorted({k for c in applied if isinstance(c.after, dict) for k in c.after})
                before = {
                    key: parent_raw.get("count") if key == "count" else getattr(parent, key, None)
                    for key in keys
                }
                return self._from(
                    "engineer_modified", applied, before=before, carried_from=parent.id
                )
            current = parent
        # ``current`` is the origin of the lineage: a model row, or one an engineer added.
        if is_room_manual(current.raw, current.document_id):
            raw = current.raw if isinstance(current.raw, dict) else {}
            room_from = _uuid(raw.get("room_from"))
            applied = (
                corrections.of("room_component", room_from, current.revision - 1)
                if room_from is not None
                else []
            )
            matching = [
                c
                for c in applied
                if isinstance(c.after, dict) and c.after.get("label") == current.label
            ] or applied
            if matching:
                return self._from("engineer_added", matching, before=None, carried_from=None)
            return Provenance("engineer_added")
        return MODEL

    # ------------------------------------------------------------------ rooms

    async def rooms(self, rows: Sequence[Room]) -> dict[uuid.UUID, Provenance]:
        corrections = await self._load()
        lineage = {row.id: row for row in rows}
        frontier = [row.carried_from for row in rows if row.carried_from is not None]
        while frontier:
            wanted = [i for i in set(frontier) if i not in lineage]
            if not wanted:
                break
            fetched = await self._session.scalars(
                select(Room).where(Room.study_id == self._study_id, Room.id.in_(wanted))
            )
            new = {r.id: r for r in fetched.all()}
            lineage.update(new)
            frontier = [r.carried_from for r in new.values() if r.carried_from is not None]
        found: dict[uuid.UUID, Provenance] = {}
        for row in rows:
            found[row.id] = self._room(row, lineage, corrections)
        return found

    def _room(
        self, row: Room, lineage: dict[uuid.UUID, Room], corrections: _Corrections
    ) -> Provenance:
        current = row
        while current.carried_from is not None and current.carried_from in lineage:
            parent = lineage[current.carried_from]
            applied = [
                c
                for c in corrections.of("room", parent.id, parent.revision)
                if not (isinstance(c.after, dict) and "merge_into" in c.after)
            ]
            if applied:
                keys = sorted({k for c in applied if isinstance(c.after, dict) for k in c.after})
                before = {key: getattr(parent, key, None) for key in keys}
                return self._from(
                    "engineer_modified", applied, before=before, carried_from=parent.id
                )
            current = parent
        if row.source == MANUAL:
            # A copy made before rooms recorded their lineage: the engineer changed it, the
            # correction cannot be named.
            return Provenance("engineer_modified")
        return MODEL

    # ------------------------------------------------------------------ extraction fields

    async def fields(self, rows: Sequence[ExtractionField]) -> dict[uuid.UUID, Provenance]:
        corrections = await self._load()
        extractions = await self._extractions([row.extraction_id for row in rows])
        found: dict[uuid.UUID, Provenance] = {}
        cache: dict[uuid.UUID, dict[str, ExtractionField]] = {}
        for row in rows:
            extraction = extractions.get(row.extraction_id)
            found[row.id] = await self._field(row, extraction, extractions, cache, corrections)
        return found

    async def _extractions(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Extraction]:
        wanted = list(set(ids))
        if not wanted:
            return {}
        rows = await self._session.scalars(
            select(Extraction).where(
                Extraction.study_id == self._study_id, Extraction.id.in_(wanted)
            )
        )
        return {row.id: row for row in rows.all()}

    async def _parent_extraction(
        self, extraction: Extraction, known: dict[uuid.UUID, Extraction]
    ) -> Extraction | None:
        """The extraction this one was copied from (``raw["carried_from"]``) or, for a
        re-extraction after an appraisal rejection, the same document's at the previous
        revision."""
        raw = extraction.raw if isinstance(extraction.raw, dict) else {}
        parent_id = _uuid(raw.get(CARRIED_FROM))
        if parent_id is not None:
            if parent_id not in known:
                known.update(await self._extractions([parent_id]))
            return known.get(parent_id)
        if extraction.revision <= 1:
            return None
        parent = await self._session.scalar(
            select(Extraction).where(
                Extraction.study_id == self._study_id,
                Extraction.document_id == extraction.document_id,
                Extraction.revision == extraction.revision - 1,
            )
        )
        if parent is not None:
            known[parent.id] = parent
        return parent

    async def _fields_of(
        self, extraction_id: uuid.UUID, cache: dict[uuid.UUID, dict[str, ExtractionField]]
    ) -> dict[str, ExtractionField]:
        if extraction_id not in cache:
            rows = await self._session.scalars(
                select(ExtractionField).where(ExtractionField.extraction_id == extraction_id)
            )
            cache[extraction_id] = {f"{f.section}.{f.field}": f for f in rows.all()}
        return cache[extraction_id]

    async def _field(
        self,
        row: ExtractionField,
        extraction: Extraction | None,
        extractions: dict[uuid.UUID, Extraction],
        cache: dict[uuid.UUID, dict[str, ExtractionField]],
        corrections: _Corrections,
    ) -> Provenance:
        key = f"{row.section}.{row.field}"
        current_extraction = extraction
        while current_extraction is not None:
            parent = await self._parent_extraction(current_extraction, extractions)
            if parent is None:
                break
            parent_field = (await self._fields_of(parent.id, cache)).get(key)
            if parent_field is not None:
                applied = corrections.of("extraction_field", parent_field.id, parent.revision)
                if applied:
                    before = {
                        "value_text": parent_field.value_text,
                        "value_num": _text(parent_field.value_num),
                        "value_date": _text(parent_field.value_date),
                    }
                    return self._from(
                        "engineer_modified", applied, before=before, carried_from=parent_field.id
                    )
            current_extraction = parent
        if row.source == MANUAL:
            return Provenance("engineer_modified")
        return MODEL

    # ------------------------------------------------------------------ photo assignments

    async def photo_rooms(self, rows: Sequence[PhotoRoom]) -> dict[uuid.UUID, Provenance]:
        """By document id: the latest ``photo_room`` move of the photo at a revision before
        the row's, else the model's grouping."""
        corrections = await self._load()
        found: dict[uuid.UUID, Provenance] = {}
        moves: dict[uuid.UUID, Correction] = {}
        for row in rows:
            applied = [
                c
                for c in corrections.of("photo_room", row.document_id, None)
                if c.revision < row.revision
            ]
            if applied:
                moves[row.document_id] = applied[-1]
        previous: dict[tuple[uuid.UUID, int], uuid.UUID] = {}
        if moves:
            pairs = await self._session.execute(
                select(PhotoRoom.document_id, PhotoRoom.revision, PhotoRoom.room_id).where(
                    PhotoRoom.study_id == self._study_id,
                    PhotoRoom.document_id.in_(list(moves)),
                )
            )
            for document_id, revision, room_id in pairs.all():
                previous[(document_id, revision)] = room_id
        for row in rows:
            move = moves.get(row.document_id)
            if move is None:
                found[row.document_id] = MODEL
                continue
            was = previous.get((row.document_id, move.revision))
            found[row.document_id] = self._from(
                "engineer_modified",
                [move],
                before={"room_id": _text(was)},
                carried_from=None,
            )
        return found

    # ------------------------------------------------------------------ assets

    async def assets(
        self, rows: Sequence[Asset], components: dict[uuid.UUID, Component] | None = None
    ) -> dict[uuid.UUID, Provenance]:
        """An asset is the engineer's when an ``asset`` correction was applied to it or to
        the asset of an ancestor component; else it inherits its component's provenance
        (an engineer-added component's asset is ``engineer_added``); else the model's. Its
        takeoff and cost estimate share this provenance (an engineer cannot edit them
        directly; they are recomputed from the asset's classification)."""
        corrections = await self._load()
        component_ids = [a.component_id for a in rows if a.component_id is not None]
        known = dict(components or {})
        missing = [i for i in component_ids if i not in known]
        known.update(await self._components_by_id(missing))
        lineage = await self._component_lineage([known[i] for i in component_ids if i in known])
        ancestor_ids = list(lineage)
        assets_of: dict[uuid.UUID, list[Asset]] = {}
        if ancestor_ids:
            older = await self._session.scalars(
                select(Asset).where(
                    Asset.study_id == self._study_id, Asset.component_id.in_(ancestor_ids)
                )
            )
            for asset in older.all():
                assert asset.component_id is not None
                assets_of.setdefault(asset.component_id, []).append(asset)
        found: dict[uuid.UUID, Provenance] = {}
        for asset in rows:
            component = lineage.get(asset.component_id) if asset.component_id else None
            found[asset.id] = self._asset(asset, component, lineage, assets_of, corrections)
        return found

    def _asset(
        self,
        asset: Asset,
        component: Component | None,
        lineage: dict[uuid.UUID, Component],
        assets_of: dict[uuid.UUID, list[Asset]],
        corrections: _Corrections,
    ) -> Provenance:
        current = component
        while current is not None:
            parent_id = _parent_of(current)
            parent = lineage.get(parent_id) if parent_id is not None else None
            if parent is None:
                break
            for older in assets_of.get(parent.id, []):
                if older.revision != parent.revision:
                    continue
                applied = corrections.of("asset", older.id, older.revision)
                if applied:
                    before = {
                        "category": older.category,
                        "bucket": older.bucket.value,
                        "recovery_period": _text(older.recovery_period),
                        "irs_section": older.irs_section,
                    }
                    return self._from(
                        "engineer_modified", applied, before=before, carried_from=older.id
                    )
            current = parent
        if component is not None:
            inherited = self._component(component, lineage, corrections)
            if inherited.origin == "engineer_added":
                return inherited
        return MODEL


def _parent_of(component: Component) -> uuid.UUID | None:
    raw = component.raw if isinstance(component.raw, dict) else {}
    return _uuid(raw.get(CARRIED_FROM))


def _uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _text(value: object | None) -> str | None:
    return None if value is None else str(value)


def provenance_json(provenance: Provenance) -> dict[str, Any]:
    """The ``provenance`` member of the API's rows and the export (one shape)."""
    return {
        "origin": provenance.origin,
        "correction_id": _text(provenance.correction_id),
        "review_id": _text(provenance.review_id),
        "stage": provenance.stage,
        "revision": provenance.revision,
        "at": None if provenance.at is None else provenance.at.isoformat(),
        "by": _text(provenance.by),
        "before": provenance.before,
        "after": provenance.after,
        "changed": list(provenance.changed),
        "carried_from": _text(provenance.carried_from),
    }
