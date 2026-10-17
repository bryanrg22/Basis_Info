"""Many photos in one call: a room, the whole property, or the schedule directly.

EVAL_REPORT section 26 tests architectures that replace "per photo, then reconcile per
room" (:mod:`basis.vision.analyze` + :mod:`basis.vision.reconcile`) with calls that see
several photos at once and count from the pixels, with no per-photo analysis in between:

* ``room`` (prompt ``room_photos_inventory``): every photo of one room in one call ->
  that room's inventory (:class:`~basis.vision.reconcile.ModelRoomInventory`, the shape
  the reconciliation call already returns, so the result scores and stores the same way).
* ``house`` (prompt ``house_inventory``): every photo of the property in one call, each
  captioned with its room, plus the room list -> every room's inventory
  (:class:`ModelHouseInventory`). The rooms are given (file names and aliases,
  :mod:`basis.vision.rooms`); the call only counts.
* ``schedule`` (prompt ``house_schedule``): every photo in one call plus a schedule
  template (the row names a firm's schedule may use) -> the schedule rows directly
  (:class:`ModelSchedule`): system, bucket, quantity and unit, evidence images.

Requests follow the reconciliation call's layout: one user message per image, ``Image N``
(with the room id for the property-wide calls), then one text message. Answers are checked
against the request: an object or row whose image indices fall outside the request, a
room id not in the list, or a system name not in the template is dropped with its reason
(kept in the result for review), never guessed into place.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from basis.llm.prompts import Prompt
from basis.llm.provider import ChatRequest, ImagePart, Message
from basis.vision.prep import PreparedImage
from basis.vision.reconcile import (
    NOTE_MAX,
    InventoryObject,
    ModelInventoryObject,
    ModelRoomInventory,
    clip_text,
)

Scope = Literal["room", "house", "schedule"]
PROMPTS: Final[dict[str, str]] = {
    "room": "room_photos_inventory",
    "house": "house_inventory",
    "schedule": "house_schedule",
}
MAX_IMAGES: Final = 100  # Anthropic's per-request image limit is the tighter one
MAX_ROOMS: Final = 60
MAX_OBJECTS_PER_ROOM: Final = 80
MAX_ROWS: Final = 80
# Output budgets: a room ~ 40 objects x 60 tokens; the property ~ 35 rooms x 15 objects.
MAX_TOKENS: Final[dict[str, int]] = {"room": 6000, "house": 32000, "schedule": 16000}


# --------------------------------------------------------------------------- schemas


class HouseObject(ModelInventoryObject):
    """An inventory object whose image indices are the property-wide numbers."""

    image_indices: list[int] = Field(
        max_length=MAX_IMAGES,
        description="1-based numbers of the images in which this object is visible",
    )


class HouseRoom(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    room_id: str = Field(description="A room id from the room list")
    objects: list[HouseObject] = Field(max_length=MAX_OBJECTS_PER_ROOM)


class ModelHouseInventory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rooms: list[HouseRoom] = Field(max_length=MAX_ROOMS)


class BucketYears(StrEnum):
    Y5 = "5"
    Y7 = "7"
    Y15 = "15"
    Y27_5 = "27.5"
    Y39 = "39"


class Unit(StrEnum):
    EACH = "each"
    SQUARE_FEET = "square_feet"
    LINEAR_FEET = "linear_feet"
    LUMP_SUM = "lump_sum"


class ScheduleRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    system: str = Field(description="A row name from the schedule template, exactly")
    bucket: BucketYears
    quantity: float = Field(ge=0.0)
    unit: Unit
    image_indices: list[int] = Field(max_length=MAX_IMAGES)
    rooms: list[str] = Field(max_length=MAX_ROOMS)
    note: str | None = Field(max_length=NOTE_MAX)

    @field_validator("note", mode="before")
    @classmethod
    def _clip_note(cls, value: Any) -> Any:
        return clip_text(value)


class ModelSchedule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: list[ScheduleRow] = Field(max_length=MAX_ROWS)


RESPONSE_MODELS: Final[dict[str, type[BaseModel]]] = {
    "room": ModelRoomInventory,
    "house": ModelHouseInventory,
    "schedule": ModelSchedule,
}


# --------------------------------------------------------------------------- requests


@dataclass(frozen=True, slots=True)
class Photo:
    file: str
    room_id: str
    image: PreparedImage


@dataclass(frozen=True)
class CallSettings:
    model: str
    prompt: Prompt
    study_id: str
    activity: str
    max_tokens: int
    reasoning_effort: str | None = None


def normalize_system(name: str) -> str:
    """Template row names compared without case, punctuation spacing or accents that
    models drop (``Facade`` / ``Façade``)."""
    folded = name.replace("ç", "c").replace("Ç", "C").lower()
    return " ".join(folded.replace("&", " and ").replace("/", " / ").replace("-", " - ").split())


def room_listing(photos: Sequence[Photo]) -> str:
    """``1_kitchen: images 3, 4`` per room, in order of first image."""
    rooms: dict[str, list[int]] = {}
    for number, photo in enumerate(photos, start=1):
        rooms.setdefault(photo.room_id, []).append(number)
    return "\n".join(
        f"{room}: image{'s' if len(nums) > 1 else ''} {', '.join(map(str, nums))}"
        for room, nums in rooms.items()
    )


def build_request(
    scope: Scope,
    photos: Sequence[Photo],
    settings: CallSettings,
    template: Sequence[str] = (),
) -> ChatRequest:
    """The call for ``scope`` over ``photos`` (a room's photos for ``room``, the
    property's for ``house`` and ``schedule``)."""
    if not photos:
        raise ValueError("a multi-image call needs at least one photo")
    if len(photos) > MAX_IMAGES:
        raise ValueError(f"at most {MAX_IMAGES} images per call, got {len(photos)}")
    if scope == "schedule" and not template:
        raise ValueError("the schedule call needs a template")
    captioned = scope != "room"
    images = tuple(
        Message(
            role="user",
            content=f"Image {n} (room {p.room_id}):" if captioned else f"Image {n}:",
            images=(ImagePart(media_type="image/jpeg", data_base64=p.image.base64, detail="high"),),
        )
        for n, p in enumerate(photos, start=1)
    )
    if scope == "room":
        text = (
            f"The {len(photos)} images above all show the same room. List each distinct "
            "physical object once, with its count and the numbers of the images where it "
            "is visible."
        )
    elif scope == "house":
        text = (
            f"The {len(photos)} images above are every site photo of the property. Rooms "
            f"and their images:\n{room_listing(photos)}\n\nReport every room in this "
            "list with each distinct physical object once."
        )
    else:
        rows = "\n".join(f"- {name}" for name in template)
        text = (
            f"The {len(photos)} images above are every site photo of the property. Rooms "
            f"and their images:\n{room_listing(photos)}\n\nSchedule template (use these "
            f"row names exactly):\n{rows}\n\nList the rows the photos give evidence for."
        )
    return ChatRequest(
        model=settings.model,
        system=settings.prompt.text,
        messages=(*images, Message(role="user", content=text)),
        response_model=RESPONSE_MODELS[scope],
        max_tokens=settings.max_tokens,
        temperature=0.0,
        reasoning_effort=settings.reasoning_effort,
        prompt_version=settings.prompt.version_id,
        study_id=settings.study_id,
        activity=settings.activity,
    )


# --------------------------------------------------------------------------- answers


@dataclass
class Checked:
    """A checked answer: inventories per room (``room`` / ``house``) or schedule rows,
    with every dropped entry and its reason."""

    rooms: dict[str, list[InventoryObject]] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)


def _indices_ok(indices: Sequence[int], images: int) -> bool:
    return bool(indices) and all(1 <= i <= images for i in indices)


def _object(obj: ModelInventoryObject) -> InventoryObject:
    return InventoryObject(
        label=obj.label,
        count=obj.count,
        image_indices=list(obj.image_indices),
        material=obj.material,
        attachment=obj.attachment,
        confidence=obj.confidence,
        note=obj.note,
    )


def check_room(parsed: ModelRoomInventory, room_id: str, images: int) -> Checked:
    out = Checked(rooms={room_id: []})
    for obj in parsed.objects:
        if not _indices_ok(obj.image_indices, images):
            out.dropped.append({"reason": "image_indices", "object": obj.model_dump(mode="json")})
            continue
        out.rooms[room_id].append(_object(obj))
    return out


def check_house(parsed: ModelHouseInventory, photos: Sequence[Photo]) -> Checked:
    """Objects whose images exist; a room listed twice is merged (its entries add up,
    the model's own rule); rooms not in the list are dropped."""
    known = {p.room_id for p in photos}
    out = Checked(rooms={room: [] for room in dict.fromkeys(p.room_id for p in photos)})
    for room in parsed.rooms:
        if room.room_id not in known:
            out.dropped.append({"reason": "unknown_room", "room_id": room.room_id})
            continue
        for obj in room.objects:
            if not _indices_ok(obj.image_indices, len(photos)):
                out.dropped.append(
                    {
                        "reason": "image_indices",
                        "room_id": room.room_id,
                        "object": obj.model_dump(mode="json"),
                    }
                )
                continue
            out.rooms[room.room_id].append(_object(obj))
    return out


def check_schedule(
    parsed: ModelSchedule, photos: Sequence[Photo], template: Sequence[str]
) -> Checked:
    """Rows named in the template (matched by :func:`normalize_system`) with existing
    images; the template's own spelling is kept; files replace image numbers."""
    names = {normalize_system(t): t for t in template}
    out = Checked()
    for row in parsed.rows:
        name = names.get(normalize_system(row.system))
        if name is None:
            out.dropped.append({"reason": "unknown_system", "row": row.model_dump(mode="json")})
            continue
        if not _indices_ok(row.image_indices, len(photos)):
            out.dropped.append({"reason": "image_indices", "row": row.model_dump(mode="json")})
            continue
        record = row.model_dump(mode="json")
        record["system"] = name
        record["files"] = [photos[i - 1].file for i in sorted(set(row.image_indices))]
        out.rows.append(record)
    return out
