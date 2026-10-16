"""What evidences each asset of a revision: its room and photos, the localized instances of
its label in those photos, and its rule citations (``GET /v1/studies/{id}/evidence``).

One page of assets is read by keyset on ``assets.id``; everything else is fetched for the
whole page in one query per table (no per-asset queries). The cursor carries the revision
it was issued for, so paging through a revision keeps returning that revision's rows even
if a rejection starts a new one meanwhile (decided revisions never change).

Evidence of an asset, through its component (``assets.component_id``):

* the component's room, and the photos assigned to that room in the revision
  (``photo_rooms``); the component's own photo is ``source``, the room's other photos are
  ``room``;
* in each of those photos, the active ``photo_objects`` with the component's label and the
  photo's localization status;
* the asset's ``citations`` with the cited chunk's document, pages and section, and the
  verification (``verified_at``, ``verification_method``).

An asset without a component (none are written today) has no room or photos, only its
citations.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.errors import InvalidCursorError
from basis.db.models import (
    Asset,
    Chunk,
    Citation,
    Component,
    Document,
    PhotoLocalization,
    PhotoObject,
    PhotoRoom,
    Room,
)
from basis.db.repositories.photo_object import PhotoObjectRepository

MAX_EVIDENCE_PAGE: Final = 100


@dataclass(frozen=True, slots=True)
class PhotoEvidence:
    document: Document
    role: str  # "source" | "room"
    localization: PhotoLocalization | None
    instances: list[PhotoObject]


@dataclass(frozen=True, slots=True)
class CitationEvidence:
    citation: Citation
    chunk: Chunk


@dataclass(frozen=True, slots=True)
class AssetEvidence:
    asset: Asset
    component: Component | None
    room: Room | None
    photos: list[PhotoEvidence] = field(default_factory=list)
    citations: list[CitationEvidence] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EvidencePage:
    revision: int
    items: list[AssetEvidence]
    next_cursor: str | None


def encode_cursor(revision: int, asset_id: uuid.UUID) -> str:
    raw = json.dumps({"r": revision, "id": str(asset_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        revision, asset_id = data["r"], uuid.UUID(data["id"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise InvalidCursorError("invalid pagination cursor") from exc
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise InvalidCursorError("invalid pagination cursor")
    return revision, asset_id


class EvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def page(
        self,
        study_id: uuid.UUID,
        revision: int,
        *,
        limit: int,
        cursor: str | None = None,
        asset_id: uuid.UUID | None = None,
    ) -> EvidencePage:
        if cursor is not None:
            revision, after = decode_cursor(cursor)
        else:
            after = None
        stmt = select(Asset).where(Asset.study_id == study_id, Asset.revision == revision)
        if asset_id is not None:
            stmt = stmt.where(Asset.id == asset_id)
        if after is not None:
            stmt = stmt.where(Asset.id > after)
        rows = list((await self._session.scalars(stmt.order_by(Asset.id).limit(limit + 1))).all())
        assets = rows[:limit]
        next_cursor = encode_cursor(revision, assets[-1].id) if len(rows) > limit else None
        items = await self._evidence(study_id, revision, assets)
        return EvidencePage(revision=revision, items=items, next_cursor=next_cursor)

    async def _evidence(
        self, study_id: uuid.UUID, revision: int, assets: list[Asset]
    ) -> list[AssetEvidence]:
        if not assets:
            return []
        component_ids = [a.component_id for a in assets if a.component_id is not None]
        components = {
            c.id: c
            for c in await self._session.scalars(
                select(Component).where(
                    Component.study_id == study_id, Component.id.in_(component_ids)
                )
            )
        }
        room_ids = {c.room_id for c in components.values() if c.room_id is not None}
        rooms = {
            r.id: r
            for r in await self._session.scalars(
                select(Room).where(Room.study_id == study_id, Room.id.in_(room_ids))
            )
        }
        room_photos: dict[uuid.UUID, list[uuid.UUID]] = {}
        for assignment in await self._session.scalars(
            select(PhotoRoom)
            .where(
                PhotoRoom.study_id == study_id,
                PhotoRoom.revision == revision,
                PhotoRoom.room_id.in_(room_ids),
            )
            .order_by(PhotoRoom.document_id)
        ):
            room_photos.setdefault(assignment.room_id, []).append(assignment.document_id)
        photo_ids = sorted(
            {c.document_id for c in components.values() if c.document_id is not None}
            | {d for docs in room_photos.values() for d in docs}
        )
        documents = {
            d.id: d
            for d in await self._session.scalars(
                select(Document).where(Document.study_id == study_id, Document.id.in_(photo_ids))
            )
        }
        objects = PhotoObjectRepository(self._session)
        localizations = await objects.localizations(study_id, revision, photo_ids)
        instances: dict[tuple[uuid.UUID, str], list[PhotoObject]] = {}
        for row in await objects.active(study_id, revision, photo_ids):
            instances.setdefault((row.document_id, row.label), []).append(row)
        cited: dict[uuid.UUID, list[CitationEvidence]] = {}
        for citation, chunk in await self._session.execute(
            select(Citation, Chunk)
            .join(Chunk, Chunk.id == Citation.chunk_id)
            .where(
                Citation.study_id == study_id,
                Citation.asset_id.in_([a.id for a in assets]),
            )
            .order_by(Citation.asset_id, Chunk.doc_id, Chunk.page_start, Citation.id)
        ):
            cited.setdefault(citation.asset_id, []).append(CitationEvidence(citation, chunk))

        items: list[AssetEvidence] = []
        for asset in assets:
            component = components.get(asset.component_id) if asset.component_id else None
            room = rooms.get(component.room_id) if component and component.room_id else None
            photos: list[PhotoEvidence] = []
            if component is not None:
                ordered: list[tuple[uuid.UUID, str]] = []
                if component.document_id is not None:
                    ordered.append((component.document_id, "source"))
                for document_id in room_photos.get(room.id, []) if room else []:
                    if document_id != component.document_id:
                        ordered.append((document_id, "room"))
                for document_id, role in ordered:
                    document = documents.get(document_id)
                    if document is None:  # pragma: no cover - tenancy keys forbid it
                        continue
                    photos.append(
                        PhotoEvidence(
                            document=document,
                            role=role,
                            localization=localizations.get(document_id),
                            instances=instances.get((document_id, component.label), []),
                        )
                    )
            items.append(
                AssetEvidence(
                    asset=asset,
                    component=component,
                    room=room,
                    photos=photos,
                    citations=cited.get(asset.id, []),
                )
            )
        return items
