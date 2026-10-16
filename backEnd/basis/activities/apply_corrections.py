"""``apply_corrections``: start the next revision after a rejection.

The API has already stored the reviewer's ``corrections`` rows (append-only audit). This
activity builds the new revision's outputs: it copies every revision-scoped output that
the rejected stage does not recompute from ``from_revision`` into ``to_revision`` and
applies the corrections' ``after`` values to the copies. The rows of ``from_revision`` are
never changed, so the decided revision stays as history.

==================  =========================================  ======================
rejected stage      copied to the new revision                 recomputed by
==================  =========================================  ======================
appraisal_review    rooms, photo assignments, components,      extract_fields
                    localized objects
rooms_review        extraction + fields; rooms, assignments,   analyze_photo and
                    components and localized objects of        localize_photo (only
                    photos not flagged (room/component         the photos flagged by
                    corrections applied)                       a ``photo`` correction)
assets_review       extraction + fields; rooms, assignments,   process_assets
                    components, localized objects
==================  =========================================  ======================

Localized objects are the ``photo_localizations`` and active ``photo_objects`` rows of a
photo (an engineer's manual selections included, deleted rows left behind).

``extraction_field`` and ``asset`` corrections are applied by the activity that
recomputes those rows (they receive the corrections in their input), so ``applied``
counts only the room and component corrections applied here. Every correction is
validated here first, before anything is written: a type the stage does not accept, a
target that is not in ``from_revision`` of this study, an unknown field or a value that
does not parse is a ``ValidationError`` (the workflow then starts the revision without
corrections and flags the stage; see ``StudyWorkflow._reject``).
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import (
    activity_key,
    heartbeat,
    parse_uuid,
    run_once,
    validation_error,
)
from basis.activities.corrections import (
    COMPONENT_FIELDS,
    PHOTO_FIELDS,
    ROOM_FIELDS,
    check_asset,
    check_extraction_field,
    check_fields,
)
from basis.activities.deps import Deps
from basis.db.enums import DocumentKind
from basis.db.models import Component, Extraction, Room
from basis.db.repositories.asset import AssetRepository
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.extraction import ExtractionRepository
from basis.db.repositories.photo_object import PhotoObjectRepository
from basis.db.repositories.room import RoomRepository
from basis.workflow.contracts import (
    CORRECTION_TYPES,
    ApplyCorrectionsInput,
    ApplyCorrectionsOutput,
    CorrectionInput,
)

CARRIED_FROM: Final = "carried_from"


class ApplyCorrections:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="apply_corrections")
    async def run(self, input: ApplyCorrectionsInput) -> ApplyCorrectionsOutput:
        study_id = parse_uuid(input.study_id, "study_id")
        if input.to_revision != input.from_revision + 1:
            raise validation_error("to_revision must be from_revision + 1")

        async def work(session: AsyncSession) -> ApplyCorrectionsOutput:
            by_type = _group(input.corrections)
            await _validate(session, study_id, input.stage, input.from_revision, by_type)
            if input.stage != "appraisal_review":
                await _carry_extractions(session, study_id, input.from_revision, input.to_revision)
            flagged = {parse_uuid(c.entity_id, "entity_id") for c in by_type.get("photo", [])}
            applied = await _carry_rooms(
                session,
                study_id,
                input.from_revision,
                input.to_revision,
                skip_photos=flagged,
                room_corrections=by_type.get("room", []),
                component_corrections=by_type.get("component", []),
            )
            return ApplyCorrectionsOutput(applied=applied)

        return await run_once(
            self._deps,
            name="apply_corrections",
            key=activity_key(),
            output_type=ApplyCorrectionsOutput,
            work=work,
        )


async def _validate(
    session: AsyncSession,
    study_id: uuid.UUID,
    stage: str,
    from_revision: int,
    by_type: dict[str, list[CorrectionInput]],
) -> None:
    """Refuse the whole rejection's corrections before anything is written.

    Room and component targets are checked while copying (``_targets``); the targets that
    later activities apply (``extraction_field``, ``asset``) and the flagged photos are
    checked here, so a bad correction never fails a recompute halfway through a revision.
    """
    allowed = CORRECTION_TYPES[stage]
    unknown = set(by_type) - allowed
    if unknown:
        raise validation_error(f"{stage} does not accept {', '.join(sorted(unknown))} corrections")
    extractions = ExtractionRepository(session)
    for correction in by_type.get("extraction_field", []):
        check_extraction_field(correction.after)
        field = await extractions.get_field(study_id, parse_uuid(correction.entity_id, "entity_id"))
        extraction = None if field is None else await session.get(Extraction, field.extraction_id)
        if extraction is None or extraction.revision != from_revision:
            raise validation_error(
                f"extraction field {correction.entity_id} is not in the rejected revision"
            )
    assets = AssetRepository(session)
    for correction in by_type.get("asset", []):
        check_asset(correction.after)
        asset = await assets.get(study_id, parse_uuid(correction.entity_id, "entity_id"))
        if asset is None or asset.revision != from_revision:
            raise validation_error(f"asset {correction.entity_id} is not in the rejected revision")
    documents = DocumentRepository(session)
    for correction in by_type.get("photo", []):
        check_fields("photo", correction.after, PHOTO_FIELDS)
        document = await documents.get(study_id, parse_uuid(correction.entity_id, "entity_id"))
        if document is None or document.kind is not DocumentKind.PHOTO:
            raise validation_error(f"photo {correction.entity_id} is not a photo of this study")


def _group(corrections: tuple[CorrectionInput, ...]) -> dict[str, list[CorrectionInput]]:
    grouped: dict[str, list[CorrectionInput]] = {}
    for correction in corrections:
        grouped.setdefault(correction.entity_type, []).append(correction)
    return grouped


async def _carry_extractions(
    session: AsyncSession, study_id: uuid.UUID, from_revision: int, to_revision: int
) -> None:
    repo = ExtractionRepository(session)
    for old in await repo.list_for_revision(study_id, from_revision):
        heartbeat("extraction")
        new = await repo.create(
            study_id=study_id,
            document_id=old.document_id,
            revision=to_revision,
            method=old.method,
            overall_confidence=old.overall_confidence,
            needs_review=old.needs_review,
            raw={**(old.raw if isinstance(old.raw, dict) else {}), CARRIED_FROM: str(old.id)},
        )
        for field in await repo.fields(old.id):
            await repo.set_field(
                extraction=new,
                section=field.section,
                field=field.field,
                value_text=field.value_text,
                value_num=field.value_num,
                value_date=field.value_date,
                confidence=field.confidence,
                source=field.source,
                page=field.page,
            )


async def _carry_rooms(
    session: AsyncSession,
    study_id: uuid.UUID,
    from_revision: int,
    to_revision: int,
    *,
    skip_photos: set[uuid.UUID],
    room_corrections: list[CorrectionInput],
    component_corrections: list[CorrectionInput],
) -> int:
    repo = RoomRepository(session)
    old_rooms = {room.id: room for room in await repo.list_for_revision(study_id, from_revision)}
    old_components = {c.id: c for c in await repo.components(study_id, from_revision)}
    room_after = _targets(room_corrections, old_rooms, ROOM_FIELDS, "room")
    component_after = _targets(component_corrections, old_components, COMPONENT_FIELDS, "component")

    assignments = [
        pr
        for pr in await repo.photo_rooms(study_id, from_revision)
        if pr.document_id not in skip_photos
    ]
    keep_rooms = {pr.room_id for pr in assignments} | set(room_after)
    new_room_ids: dict[uuid.UUID, uuid.UUID] = {}
    for room_id in sorted(keep_rooms):
        heartbeat("room")
        new_room_ids[room_id] = await _copy_room(repo, old_rooms[room_id], to_revision, room_after)
    for assignment in assignments:
        await repo.assign_photo(
            study_id=study_id,
            document_id=assignment.document_id,
            room_id=new_room_ids[assignment.room_id],
            revision=to_revision,
        )
    for component in old_components.values():
        if component.document_id in skip_photos:
            continue
        heartbeat("component")
        await _copy_component(repo, component, to_revision, new_room_ids, component_after)
    heartbeat("objects")
    await PhotoObjectRepository(session).carry(
        study_id, from_revision, to_revision, skip_photos=skip_photos
    )
    return len(room_after) + len(component_after)


def _targets(
    corrections: list[CorrectionInput],
    rows: dict[uuid.UUID, Any],
    allowed: frozenset[str],
    kind: str,
) -> dict[uuid.UUID, dict[str, Any]]:
    after: dict[uuid.UUID, dict[str, Any]] = {}
    for correction in corrections:
        target = parse_uuid(correction.entity_id, "entity_id")
        if target not in rows:
            raise validation_error(f"{kind} {target} is not in the rejected revision")
        check_fields(kind, correction.after, allowed)
        after.setdefault(target, {}).update(correction.after)
    return after


async def _copy_room(
    repo: RoomRepository,
    old: Room,
    to_revision: int,
    room_after: dict[uuid.UUID, dict[str, Any]],
) -> uuid.UUID:
    changes = room_after.get(old.id, {})
    new = await repo.create(
        study_id=old.study_id,
        revision=to_revision,
        name=str(changes.get("name", old.name)),
        room_type=str(changes.get("room_type", old.room_type)),
        floor=changes.get("floor", old.floor),
        source="manual" if changes else old.source,
    )
    return new.id


async def _copy_component(
    repo: RoomRepository,
    old: Component,
    to_revision: int,
    new_room_ids: dict[uuid.UUID, uuid.UUID],
    component_after: dict[uuid.UUID, dict[str, Any]],
) -> None:
    changes = component_after.get(old.id, {})
    raw = {**(old.raw if isinstance(old.raw, dict) else {}), CARRIED_FROM: str(old.id)}
    if changes:
        raw["corrected"] = sorted(changes)
    await repo.add_component(
        study_id=old.study_id,
        revision=to_revision,
        document_id=old.document_id,
        room_id=new_room_ids.get(old.room_id) if old.room_id is not None else None,
        label=str(changes.get("label", old.label)),
        material=changes.get("material", old.material),
        attachment=changes.get("attachment", old.attachment),
        confidence=1.0 if changes else old.confidence,
        bbox=old.bbox,
        raw=raw,
    )
