"""Map a :class:`PhotoAnalysis` to ``rooms``, ``photo_rooms`` and ``components`` rows.

Room identity within a study revision is ``(room_type, floor, ordinal)``:

* ``room_type`` is the normalized enum value; ``floor`` is the model's hint as text
  (``None`` when not visible); ``ordinal`` is 1 unless the photo itself shows otherwise.
* The room's display name encodes the ordinal ("Bedroom", "Bedroom 2"); the floor is in
  its own column, so the kitchen on floor 1 and the one on floor 2 are two rows both
  named "Kitchen".
* Every photo with the same identity in the revision shares one row (several photos of
  one kitchen are one room). A room a reviewer corrected (``source="manual"``, e.g.
  renamed "Main Kitchen") still matches its identity, so re-analyzing a flagged photo
  after a rooms rejection joins that room instead of creating a duplicate.
* An ``ambiguous`` photo goes to the revision's "Unassigned" room so it still has a
  ``photo_rooms`` row the reviewer can move.

A photo can only identify its own room; telling two bedrooms on one floor apart needs the
engineer (rooms review) unless a visible sign gives the ordinal. This is a documented
limitation, not a guess.

Grouping a package's photos into rooms (VISION_RESEARCH.md, recommendation step 2) is the
second half of this module: :func:`name_hint` reads the room from the file name with a
fixed rule (a labeller applies the same rule by hand to build the ``room_inventory``
dataset, so the rule must not drift), and :func:`group_photos` groups by that room id,
after the engineer's aliases, with the room type voted by the per-photo model. It is
deterministic and makes no model call; the per-room reconciliation call
(:mod:`basis.vision.reconcile`) runs on its groups.

Production grouping (EVAL_REPORT sections 23.3 and 27): with ``ROOM_GROUPING=filename``
(the default) ``analyze_photo`` puts a photo whose file name names a room
(:func:`is_room_hint`) in the room :func:`group_photos` gives it among the study's photos,
after the study's ``room_aliases`` (:func:`assign_room_by_name`; the room's ``group_key``
is the canonical file-name room id); a photo whose name says nothing (``IMG_1234.JPG``)
falls back to the model's room identity (:func:`assign_room`). ``reconcile_rooms`` then
re-votes each file-name room's type and floor from its photos (:func:`vote_identity`), so
the rooms are :func:`group_photos`'s. Merging rooms at the rooms review (a ``room``
correction with ``merge_into``) records the merge as an alias (:func:`merge_alias`), so a
photo re-analyzed later joins the merged room.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.repositories.room import RoomRepository
from basis.vision.schemas import Attachment, Material, PhotoAnalysis, RoomType

SOURCE: Final = "vision"
UNASSIGNED: Final = "Unassigned"
# ``components.raw["source"]`` of a room's reconciled inventory (``reconcile_rooms``): rows
# with no document, one per inventory object. When a room has them, assets are built from
# them instead of from the room's per-photo rows (``process_assets``).
ROOM_INVENTORY_SOURCE: Final = "room_inventory"


# ``components.raw["source"]`` of a component an engineer added to a room at the rooms review
# (a ``room_component`` correction, ``apply_corrections``): no document, in the room. Assets
# are built from it whether or not the room has a reconciled inventory (``process_assets``),
# and the room step never rebuilds or deletes it (it is not a model output).
ROOM_MANUAL_SOURCE: Final = "room_manual"


def is_room_inventory(raw: Any, document_id: uuid.UUID | None) -> bool:
    """Whether a ``components`` row is a room inventory row (by its ``raw`` and document)."""
    return (
        document_id is None and isinstance(raw, dict) and raw.get("source") == ROOM_INVENTORY_SOURCE
    )


def is_room_manual(raw: Any, document_id: uuid.UUID | None) -> bool:
    """Whether a ``components`` row is a component an engineer added to a room."""
    return document_id is None and isinstance(raw, dict) and raw.get("source") == ROOM_MANUAL_SOURCE


@dataclass(frozen=True, slots=True)
class RoomKey:
    room_type: str
    floor: str | None
    ordinal: int

    @property
    def name(self) -> str:
        if self.room_type == RoomType.AMBIGUOUS.value:
            return UNASSIGNED
        title = self.room_type.replace("_", " ").title()
        return title if self.ordinal == 1 else f"{title} {self.ordinal}"


def room_key(analysis: PhotoAnalysis) -> RoomKey:
    if analysis.room_type is RoomType.AMBIGUOUS:
        return RoomKey(RoomType.AMBIGUOUS.value, None, 1)
    floor = None if analysis.floor is None else str(analysis.floor)
    return RoomKey(analysis.room_type.value, floor, analysis.room_ordinal)


def optional_value(value: str) -> str | None:
    """A material or attachment as stored: ``unknown`` is ``None``."""
    return None if value in (Material.UNKNOWN.value, Attachment.UNKNOWN.value) else value


@dataclass(frozen=True, slots=True)
class PersistedPhoto:
    room_id: uuid.UUID
    room_created: bool
    components: int


async def assign_room(
    session: AsyncSession,
    *,
    study_id: uuid.UUID,
    revision: int,
    analysis: PhotoAnalysis,
) -> tuple[uuid.UUID, bool]:
    key = room_key(analysis)
    room, created = await RoomRepository(session).get_or_create_by_identity(
        study_id=study_id,
        revision=revision,
        room_type=key.room_type,
        floor=key.floor,
        name=key.name,
        source=SOURCE,
        reuse_renamed=key.ordinal == 1 and key.room_type != RoomType.AMBIGUOUS.value,
    )
    return room.id, created


async def write_photo(
    session: AsyncSession,
    *,
    study_id: uuid.UUID,
    document_id: uuid.UUID,
    revision: int,
    room_id: uuid.UUID,
    analysis: PhotoAnalysis,
    context: dict[str, Any],
    assign: bool = True,
) -> int:
    """The ``photo_rooms`` row (unless ``assign`` is false: the photo is in ``room_id``
    already) and one ``components`` row per component kind."""
    rooms = RoomRepository(session)
    if assign:
        await rooms.assign_photo(
            study_id=study_id, document_id=document_id, room_id=room_id, revision=revision
        )
    for component in analysis.components:
        await rooms.add_component(
            study_id=study_id,
            revision=revision,
            document_id=document_id,
            room_id=room_id,
            label=component.label.value,
            material=optional_value(component.material.value),
            attachment=optional_value(component.attachment.value),
            confidence=component.confidence,
            raw={
                "source": SOURCE,
                "count": component.count,
                "note": component.note,
                "material": component.material.value,
                "attachment": component.attachment.value,
                **context,
            },
        )
    return len(analysis.components)


# --------------------------------------------------------------------------- grouping

_INDEX_PREFIX: Final = re.compile(r"^\d+_")
_PHOTO_SUFFIX: Final = re.compile(r"\s*\bphoto\s*\d+\s*$", re.IGNORECASE)
_FLOOR_PREFIX: Final = re.compile(r"^(\d+)(?:st|nd|rd|th)\s+floor\b", re.IGNORECASE)
_BASEMENT_PREFIX: Final = re.compile(r"^basement\b", re.IGNORECASE)
_NON_ALNUM: Final = re.compile(r"[^a-z0-9]+")
_SPACES: Final = re.compile(r"[\s_]+")
BASEMENT_TOKEN: Final = "b"
NO_FLOOR_TOKEN: Final = "x"
UNNAMED: Final = "unnamed"


class AliasCycleError(ValueError):
    """The room aliases map a room id back to itself (directly or through others)."""


@dataclass(frozen=True, slots=True)
class NameHint:
    """What a photo's file name says about its room."""

    floor: int | None
    floor_token: str
    slug: str
    room_id: str


def _remainder(filename: str) -> str:
    """The file name without directory, extension, ``NN_`` index and trailing ``photo N``."""
    stem = PurePath(filename).name
    dot = stem.rfind(".")
    if dot > 0:
        stem = stem[:dot]
    stem = _INDEX_PREFIX.sub("", stem, count=1)
    return _PHOTO_SUFFIX.sub("", stem)


def name_hint(filename: str) -> NameHint:
    """The room a file name points to, by the labelling rule (do not change it).

    Directory components are ignored (``photos/01_kitchen.jpg`` is ``01_kitchen.jpg``).
    Then: drop the extension; drop a leading ``NN_`` index; drop a trailing ``photo N``
    (case-insensitive); a remainder starting ``<n>st/nd/rd/th Floor`` has floor ``n`` and
    token ``str(n)``, one starting ``Basement`` floor 0 and token ``b``, anything else floor
    ``None`` and token ``x``; the slug is the rest lowercased, each run of non-alphanumerics
    replaced by ``_`` and trimmed (``unnamed`` when empty); ``room_id`` is
    ``<token>_<slug>``. ``"21_1st Floor Kitchen photo 1.JPG"`` -> ``1_kitchen``,
    ``"45_Basement Bath.JPG"`` -> ``b_bath``, ``"49_Mechanicals.JPG"`` -> ``x_mechanicals``.
    The floor and basement words are matched case-insensitively.
    """
    rest = _remainder(filename).strip()
    floor: int | None
    match = _FLOOR_PREFIX.match(rest)
    if match is not None:
        floor = int(match.group(1))
        token = str(floor)
        rest = rest[match.end() :]
    elif (basement := _BASEMENT_PREFIX.match(rest)) is not None:
        floor, token = 0, BASEMENT_TOKEN
        rest = rest[basement.end() :]
    else:
        floor, token = None, NO_FLOOR_TOKEN
    slug = _NON_ALNUM.sub("_", rest.lower()).strip("_") or UNNAMED
    return NameHint(floor=floor, floor_token=token, slug=slug, room_id=f"{token}_{slug}")


def display_name(filename: str) -> str:
    """A readable room name from a file name: the remainder :func:`name_hint` reads, with
    whitespace and underscores collapsed (``"1st Floor Kitchen"``, ``"kitchen 1"``)."""
    return _SPACES.sub(" ", _remainder(filename)).strip() or UNNAMED.title()


@dataclass(frozen=True, slots=True)
class PhotoInput:
    """One photo to group: its file name and the per-photo model's analysis, if any."""

    file: str
    analysis: PhotoAnalysis | None = None


@dataclass(frozen=True, slots=True)
class RoomGroup:
    room_id: str
    name: str
    floor: int | None
    room_type: RoomType
    photos: tuple[PhotoInput, ...]


def resolve_aliases(aliases: Mapping[str, str]) -> dict[str, str]:
    """``{room id: canonical room id}`` with chains followed (``a -> b -> c`` gives ``a ->
    c``). Any cycle, including ``a -> a``, raises :class:`AliasCycleError`: an alias file
    that loops is a labelling mistake, and silently picking one end would merge the wrong
    rooms."""
    resolved: dict[str, str] = {}
    for start in aliases:
        seen = [start]
        current = aliases[start]
        while current in aliases:
            if current in seen:
                raise AliasCycleError(f"room aliases loop: {' -> '.join([*seen, current])}")
            seen.append(current)
            current = aliases[current]
        resolved[start] = current
    return resolved


def _token_order(token: str) -> tuple[int, int]:
    """Basement first, then numbered floors in numeric order, then no floor."""
    if token == BASEMENT_TOKEN:
        return (0, 0)
    if token.isdigit():
        return (1, int(token))
    return (2, 0)


def _vote_room_type(photos: Sequence[PhotoInput]) -> RoomType:
    """Majority of the model's room types; ties go to the earliest photo among the tied.

    ``ambiguous`` is an abstention, not a vote: it wins only when nothing else was
    answered. No analyses at all -> ``other``.
    """
    return _vote_types([p.analysis.room_type for p in photos if p.analysis is not None])


def _vote_types(answers: Sequence[RoomType]) -> RoomType:
    if not answers:
        return RoomType.OTHER
    votes: list[RoomType] = [a for a in answers if a is not RoomType.AMBIGUOUS] or list(answers)
    counts = Counter(votes)
    best = max(counts.values())
    return next(a for a in votes if counts[a] == best)


def _model_floor(photos: Sequence[PhotoInput]) -> int | None:
    return _vote_floor([p.analysis.floor for p in photos if p.analysis is not None])


def _vote_floor(answers: Sequence[int | None]) -> int | None:
    floors = [f for f in answers if f is not None]
    if not floors:
        return None
    counts = Counter(floors)
    best = max(counts.values())
    return next(f for f in floors if counts[f] == best)


def group_photos(
    photos: Sequence[PhotoInput], aliases: Mapping[str, str] | None = None
) -> list[RoomGroup]:
    """Group photos into rooms by :func:`name_hint` room id after ``aliases``.

    ``aliases`` maps a view's room id to the room it belongs to (``2_bed_2_seating_area``
    -> ``2_bed_2``), resolved transitively by :func:`resolve_aliases` (a cycle raises).
    Per room:

    * ``photos`` keep the input order (the reconciliation call numbers images in it);
    * ``room_type`` is the majority of the per-photo model's room types
      (:func:`_vote_room_type`);
    * ``floor`` is the first floor a file name gives, else the most common floor the model
      reported (ties: the earliest photo), else ``None``;
    * ``name`` is the readable file name of the first photo whose own id is the room id
      (so an aliased view does not rename the room), else of the first photo.

    Rooms are ordered by floor token (basement, 1, 2, ..., no floor) then by slug.
    """
    canonical = resolve_aliases(aliases or {})
    members: dict[str, list[tuple[PhotoInput, NameHint]]] = {}
    for photo in photos:
        hint = name_hint(photo.file)
        room_id = canonical.get(hint.room_id, hint.room_id)
        members.setdefault(room_id, []).append((photo, hint))
    groups: list[RoomGroup] = []
    for room_id, entries in members.items():
        inputs = tuple(p for p, _ in entries)
        own = next((p for p, h in entries if h.room_id == room_id), inputs[0])
        hint_floor = next((h.floor for _, h in entries if h.floor is not None), None)
        groups.append(
            RoomGroup(
                room_id=room_id,
                name=display_name(own.file),
                floor=hint_floor if hint_floor is not None else _model_floor(inputs),
                room_type=_vote_room_type(inputs),
                photos=inputs,
            )
        )

    def order(group: RoomGroup) -> tuple[tuple[int, int], str]:
        token, _, slug = group.room_id.partition("_")
        return _token_order(token), slug

    return sorted(groups, key=order)


# --------------------------------------------------------------------------- production grouping

# Words cameras, phones and messaging apps put in file names. A name made only of these,
# digits and hex says nothing about the room ("IMG_1234.JPG", "PXL_20250101_120000.jpg",
# "WhatsApp Image 2025-01-29 at 10.02.11.jpeg", a UUID).
CAMERA_WORDS: Final = frozenset(
    {
        "img", "image", "images", "dsc", "dscn", "dscf", "dcim", "pxl", "mvimg", "vid", "photo",
        "photos", "pic", "picture", "screenshot", "screen", "shot", "whatsapp", "signal",
        "telegram", "snapchat", "edited", "edit", "copy", "final", "scan", "scanned", "jpeg",
        "jpg", "heic", "heif", "png", "raw", "burst", "cover", "portrait", "pano", "panorama",
        "at", "am", "pm", "file", "upload", "unnamed", "untitled", "new", "and",
    }
)  # fmt: skip
_WORDS: Final = re.compile(r"[a-z]+")
_HEX_RUN: Final = re.compile(r"^[0-9a-f]{6,}$")


def is_room_hint(filename: str) -> bool:
    """Whether a file name names a room, so :func:`name_hint` may group by it.

    True when the name, after :func:`_remainder` (no directory, extension, ``NN_`` index or
    ``photo N``), holds a word of three or more letters that is not a camera or app word
    (:data:`CAMERA_WORDS`) and not a run of hex digits (a UUID part). ``"21_1st Floor
    Kitchen photo 1.JPG"`` and ``"49_Mechanicals.JPG"`` are hints; ``"IMG_1234.JPG"``,
    ``"PXL_20250101_120000.jpg"`` and a UUID are not, and fall back to the model's room
    identity (:func:`assign_room`).
    """
    rest = _remainder(filename).lower()
    for token in re.split(r"[^a-z0-9]+", rest):
        if not token or _HEX_RUN.match(token):
            continue
        for word in _WORDS.findall(token):
            if len(word) >= 3 and word not in CAMERA_WORDS:
                return True
    return False


def name_group(
    filename: str, photo_files: Sequence[str], aliases: Mapping[str, str] | None = None
) -> RoomGroup:
    """The room :func:`group_photos` puts ``filename`` in, among the study's ``photo_files``.

    The group is computed from the file names alone (no analyses), so its ``room_id``,
    ``name`` and file-name ``floor`` are exactly :func:`group_photos`'s; the room type and
    a floor no file name gives are voted later from the photos' analyses
    (:func:`vote_identity`). ``filename`` is added to ``photo_files`` if missing.
    """
    files = list(photo_files)
    if filename not in files:
        files.append(filename)
    inputs = [PhotoInput(file=f) for f in sorted(files)]
    room_id = name_hint(filename).room_id
    canonical = resolve_aliases(aliases or {}).get(room_id, room_id)
    for group in group_photos(inputs, aliases):
        if group.room_id == canonical:
            return group
    raise AssertionError(f"{filename} is in no group")  # pragma: no cover - by construction


def vote_identity(
    room_types: Sequence[str], floors: Sequence[int | None]
) -> tuple[RoomType, int | None]:
    """A file-name room's type and model floor from its photos' stored answers (in photo
    order), by :func:`group_photos`'s rules; an unknown type string votes ``other``."""
    types = []
    for room_type in room_types:
        try:
            types.append(RoomType(room_type))
        except ValueError:
            types.append(RoomType.OTHER)
    return _vote_types(types), _vote_floor(floors)


async def assign_room_by_name(
    session: AsyncSession,
    *,
    study_id: uuid.UUID,
    revision: int,
    filename: str,
    photo_files: Sequence[str],
    aliases: Mapping[str, str],
    analysis: PhotoAnalysis,
) -> tuple[uuid.UUID, bool]:
    """The room of the revision whose ``group_key`` is the photo's file-name room id after
    the study's aliases, created if missing (named and floored as :func:`group_photos`
    would; typed by this photo's answer until :func:`vote_identity` re-votes it)."""
    group = name_group(filename, photo_files, aliases)
    floor = group.floor if group.floor is not None else analysis.floor
    room, created = await RoomRepository(session).get_or_create_by_group(
        study_id=study_id,
        revision=revision,
        group_key=group.room_id,
        name=group.name,
        room_type=analysis.room_type.value,
        floor=None if floor is None else str(floor),
        source=SOURCE,
    )
    return room.id, created


def merge_alias(aliases: Mapping[str, str], source: str, target: str) -> dict[str, str]:
    """``aliases`` with the room ``source`` merged into ``target`` (both canonical ids).

    ``target`` becomes canonical (an alias that maps it elsewhere is dropped) and
    ``source`` maps to it; aliases that pointed at ``source`` now resolve through it to
    ``target``. The result never loops: ``target`` has no outgoing alias.
    """
    if source == target:
        raise AliasCycleError(f"room {source} cannot be merged into itself")
    merged = {k: v for k, v in aliases.items() if k != target}
    merged[source] = target
    resolve_aliases(merged)
    return merged
