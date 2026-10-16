"""``reconcile_room``: one structured multi-image call per room (VISION_RESEARCH.md step 3).

The per-photo model (:mod:`basis.vision.analyze`) cannot count across photos: two photos
of one kitchen give two countertops (EVAL_REPORT section 6). This module turns a room's
photos (grouped by :func:`basis.vision.rooms.group_photos`) into one inventory of
distinct objects, each tied to the images it was seen in, so the engineer can check every
merge against the photos rather than trust an opaque count.

The call (released prompt ``room_inventory``):

* One user message per image, ``"Image N:"`` followed by that image, in the room's photo
  order, so each image is labelled as the vendors recommend; then one text message with
  the per-photo analyses rendered compactly (label, count, material, attachment per
  image) and the instruction. File names are not sent: the model judges the pixels, and
  indices are mapped back to files in :class:`RoomResult`.
* Structured output (:class:`ModelRoomInventory`, strict JSON schema): each object has a
  vocabulary label, a count >= 1 and the 1-based image indices where it is visible.
  Temperature 0; ``max_tokens`` :data:`MAX_TOKENS` covers about 40 objects (one object is
  about 60 output tokens) with room to spare.
* Budget: every attempt goes through :func:`basis.llm.call.call_model` (reserve before,
  record and charge after); :class:`~basis.llm.BudgetExceededError` and transient
  provider errors propagate, as in ``analyze.py``.
* An unusable answer (invalid JSON for the schema, a refusal, no structured object) is
  retried once; a second unusable answer marks the room ``needs_review`` with reason
  ``invalid_model_output`` and falls back to :func:`heuristic_merge` (never a guess
  presented as the model's).
* An object with no image index, or an index outside ``1..images in the call``, is
  dropped with its reason (the model pointed at a photo that does not exist, so its
  merge cannot be checked); a room with dropped objects is ``needs_review``.

Rooms with more than :data:`MAX_IMAGES_PER_CALL` photos are split, in photo order, into
the fewest calls of at most that many images, sized as evenly as possible (23 photos ->
8, 8, 7). The results are merged by label: within one call the model's entries for a
label are distinct objects and their counts add up; across calls the same object may be
seen in both, so the merged count is the largest per-call count (the
:func:`heuristic_merge` rule, which can under-count an object seen only in different
calls; the result says how many calls ran). The merged object lists the union of the
global image indices.

:func:`heuristic_merge` is the deterministic baseline the call has to beat: merge the
per-photo components by label, count = the largest per-photo count, indices = the photos
listing it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from basis.llm.call import CallContext, call_model
from basis.llm.cost import cost_usd
from basis.llm.errors import ProviderBadResponse
from basis.llm.prompts import Prompt
from basis.llm.provider import ChatProvider, ChatRequest, ImagePart, Message
from basis.vision.prep import PreparedImage
from basis.vision.schemas import (
    COMPONENT_LABELS,
    Attachment,
    ComponentLabel,
    Material,
    PhotoAnalysis,
)

PROMPT_NAME: Final = "room_inventory"
MAX_ATTEMPTS: Final = 2
MAX_IMAGES_PER_CALL: Final = 10
MAX_OBJECTS: Final = 80
# ~40 objects x ~60 output tokens each = 2400; 4000 leaves room for notes.
MAX_TOKENS: Final = 4000
INVALID_OUTPUT: Final = "invalid_model_output"
INSTRUCTION: Final = (
    "The images above all show the same room from different angles. List each distinct "
    "physical object once, with its count and the numbers of the images where it is "
    "visible."
)


# --------------------------------------------------------------------------- schemas


class ModelInventoryObject(BaseModel):
    """One object as the model reports it (indices are checked against the call later)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: ComponentLabel
    count: int = Field(ge=1, le=500, description="How many distinct objects of this kind")
    image_indices: list[int] = Field(
        max_length=MAX_IMAGES_PER_CALL * 2,
        description="1-based numbers of the images in which this object is visible",
    )
    material: Material
    attachment: Attachment
    confidence: float = Field(ge=0.0, le=1.0)
    note: str | None = Field(max_length=200)

    @model_validator(mode="before")
    @classmethod
    def _unknown_label_is_other(cls, data: Any) -> Any:
        """An out-of-vocabulary label becomes ``other`` and keeps the text as the note
        (the same rule as :class:`basis.vision.schemas.Component`)."""
        if isinstance(data, dict):
            label = data.get("label")
            if isinstance(label, str) and label not in COMPONENT_LABELS:
                key = label.strip().lower().replace("-", "_").replace(" ", "_")
                data = dict(data)
                if key in COMPONENT_LABELS:
                    data["label"] = key
                else:
                    data["label"] = ComponentLabel.OTHER.value
                    data["note"] = data.get("note") or label[:200]
        return data


class ModelRoomInventory(BaseModel):
    """What the model fills (the strict JSON schema of the call)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objects: list[ModelInventoryObject] = Field(max_length=MAX_OBJECTS)


class InventoryObject(BaseModel):
    """One distinct object of a room, tied to the images (1-based, room order) it is in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: ComponentLabel
    count: int = Field(ge=1, le=500)
    image_indices: list[int] = Field(min_length=1)
    material: Material = Material.UNKNOWN
    attachment: Attachment = Attachment.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    note: str | None = Field(default=None, max_length=200)

    @field_validator("image_indices")
    @classmethod
    def _sorted_unique_positive(cls, value: list[int]) -> list[int]:
        if any(i < 1 for i in value):
            raise ValueError("image indices are 1-based")
        return sorted(set(value))


class RoomInventory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    objects: list[InventoryObject] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """``{label: count}``: entries with the same label are distinct objects and add up."""
        totals: dict[str, int] = {}
        for obj in self.objects:
            totals[obj.label.value] = totals.get(obj.label.value, 0) + obj.count
        return totals


# --------------------------------------------------------------------------- heuristic


def heuristic_merge(photos: Sequence[PhotoAnalysis | None]) -> list[InventoryObject]:
    """The no-reconciliation baseline: one object per label over the room's photos.

    ``count`` is the largest count any single photo reports (a label that one photo lists
    twice is summed within that photo first); ``image_indices`` are the 1-based positions
    of the photos listing it (a missing analysis keeps its position). Material,
    attachment, confidence and note come from the photo with the largest count (the
    earliest on ties). ``other`` components are kept apart by their note, so two unknown
    things are not merged into one. Objects are ordered by first appearance.
    """
    merged: dict[tuple[str, str | None], dict[str, Any]] = {}
    for index, analysis in enumerate(photos, start=1):
        if analysis is None:
            continue
        per_photo: dict[tuple[str, str | None], dict[str, Any]] = {}
        for component in analysis.components:
            note_key = (
                (component.note or "").strip().lower()
                if component.label is ComponentLabel.OTHER
                else None
            )
            key = (component.label.value, note_key)
            entry = per_photo.get(key)
            if entry is None:
                per_photo[key] = {"component": component, "count": component.count}
            else:
                entry["count"] += component.count
        for key, entry in per_photo.items():
            current = merged.get(key)
            if current is None:
                merged[key] = {**entry, "indices": [index]}
                continue
            current["indices"].append(index)
            if entry["count"] > current["count"]:
                current["count"] = entry["count"]
                current["component"] = entry["component"]
    return [
        InventoryObject(
            label=entry["component"].label,
            count=entry["count"],
            image_indices=entry["indices"],
            material=entry["component"].material,
            attachment=entry["component"].attachment,
            confidence=entry["component"].confidence,
            note=entry["component"].note,
        )
        for entry in merged.values()
    ]


# --------------------------------------------------------------------------- the call


def _no_heartbeat(_: str) -> None:
    return None


@dataclass
class ReconcileContext:
    call: CallContext
    provider: ChatProvider
    prompt: Prompt
    model: str
    max_tokens: int = MAX_TOKENS
    heartbeat: Callable[[str], None] = field(default=_no_heartbeat)


@dataclass(frozen=True, slots=True)
class RoomPhoto:
    """One photo of the room: file name (for the result only), prepared image, analysis."""

    file: str
    image: PreparedImage
    analysis: PhotoAnalysis | None = None


@dataclass
class DroppedObject:
    reason: str
    object: dict[str, Any]
    call: int


@dataclass
class CallRecord:
    """One reconciliation call (all its attempts) over a slice of the room's photos."""

    files: list[str]
    first_index: int  # global 1-based index of the call's Image 1
    attempts: int = 0
    provider: str | None = None
    model: str | None = None
    cost_usd: float = 0.0
    raw: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    response: dict[str, Any] | None = None

    @property
    def images(self) -> int:
        return len(self.files)


@dataclass
class RoomResult:
    room_id: str
    files: list[str]
    inventory: RoomInventory
    source: Literal["model", "heuristic"]
    needs_review: bool = False
    review_reason: str | None = None
    dropped: list[DroppedObject] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)

    def files_for(self, obj: InventoryObject) -> list[str]:
        """The file names of the images an object was seen in."""
        return [self.files[i - 1] for i in obj.image_indices]


def chunks(count: int, limit: int = MAX_IMAGES_PER_CALL) -> list[range]:
    """``count`` photos as the fewest consecutive slices of at most ``limit``, sized as
    evenly as possible (earlier slices get the extra photo): 23 -> 8, 8, 7."""
    if count < 1:
        return []
    calls = -(-count // limit)
    base, extra = divmod(count, calls)
    slices: list[range] = []
    start = 0
    for number in range(calls):
        size = base + (1 if number < extra else 0)
        slices.append(range(start, start + size))
        start += size
    return slices


def render_analysis(index: int, analysis: PhotoAnalysis | None) -> str:
    """One compact line per image: ``Image 2: kitchen; countertop x1 (granite, permanent)``."""
    if analysis is None:
        return f"Image {index}: no per-photo analysis"
    parts = [
        f"{c.label.value}{f' [{c.note}]' if c.note and c.label is ComponentLabel.OTHER else ''}"
        f" x{c.count} ({c.material.value}, {c.attachment.value})"
        for c in analysis.components
    ]
    listed = "; ".join(parts) if parts else "no components"
    return f"Image {index}: {analysis.room_type.value}; {listed}"


def build_request(photos: Sequence[RoomPhoto], ctx: ReconcileContext) -> ChatRequest:
    """The request for one call: ``Image N:`` + image per photo, then the analyses."""
    if not photos:
        raise ValueError("a reconciliation call needs at least one photo")
    if len(photos) > MAX_IMAGES_PER_CALL:
        raise ValueError(f"at most {MAX_IMAGES_PER_CALL} images per call, got {len(photos)}")
    image_messages = tuple(
        Message(
            role="user",
            content=f"Image {number}:",
            images=(ImagePart(media_type="image/jpeg", data_base64=p.image.base64, detail="high"),),
        )
        for number, p in enumerate(photos, start=1)
    )
    analyses = "\n".join(render_analysis(n, p.analysis) for n, p in enumerate(photos, start=1))
    text = (
        f"{INSTRUCTION}\n\nThere are {len(photos)} images. Per-photo analysis (each photo "
        f"analyzed alone):\n{analyses}"
    )
    return ChatRequest(
        model=ctx.model,
        system=ctx.prompt.text,
        messages=(*image_messages, Message(role="user", content=text)),
        response_model=ModelRoomInventory,
        max_tokens=ctx.max_tokens,
        temperature=0.0,
        prompt_version=ctx.prompt.version_id,
        study_id=str(ctx.call.study_id),
        activity=ctx.call.activity,
    )


def _check(
    parsed: ModelRoomInventory, images: int, offset: int, call: int
) -> tuple[list[InventoryObject], list[DroppedObject]]:
    """Valid objects with global indices (``offset`` = photos before this call), and the
    dropped ones with their reason."""
    kept: list[InventoryObject] = []
    dropped: list[DroppedObject] = []
    for obj in parsed.objects:
        if not obj.image_indices:
            dropped.append(DroppedObject("no_image_indices", obj.model_dump(mode="json"), call))
            continue
        outside = sorted({i for i in obj.image_indices if not 1 <= i <= images})
        if outside:
            dropped.append(
                DroppedObject(
                    f"image_index_out_of_range:{outside} (images 1..{images})",
                    obj.model_dump(mode="json"),
                    call,
                )
            )
            continue
        kept.append(
            InventoryObject(
                label=obj.label,
                count=obj.count,
                image_indices=[i + offset for i in obj.image_indices],
                material=obj.material,
                attachment=obj.attachment,
                confidence=obj.confidence,
                note=obj.note,
            )
        )
    return kept, dropped


async def _one_call(
    photos: Sequence[RoomPhoto], offset: int, number: int, ctx: ReconcileContext
) -> tuple[CallRecord, ModelRoomInventory | None]:
    record = CallRecord(files=[p.file for p in photos], first_index=offset + 1)
    request = build_request(photos, ctx)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        ctx.heartbeat(f"room inventory call {number} attempt {attempt}")
        record.attempts = attempt
        try:
            response = await call_model(ctx.provider, request, ctx.call)
        except ProviderBadResponse as exc:
            record.errors.append(f"attempt {attempt}: {exc}")
            if exc.raw is not None:
                record.raw.append(exc.raw)
            if exc.usage is not None:
                record.cost_usd += cost_usd(exc.model or request.model, exc.usage)
            continue
        record.raw.append(response.raw)
        record.cost_usd += cost_usd(response.model, response.usage)
        record.provider, record.model = response.provider, response.model
        if not isinstance(response.parsed, ModelRoomInventory):
            record.errors.append(f"attempt {attempt}: no structured inventory in the response")
            continue
        record.response = response.parsed.model_dump(mode="json")
        return record, response.parsed
    return record, None


def merge_calls(per_call: Sequence[Sequence[InventoryObject]]) -> list[InventoryObject]:
    """Objects of several calls over one room -> one object per label.

    Within a call the entries of a label add up (the model listed distinct objects);
    across calls the merged count is the largest per-call total (the same object can be
    in both slices), indices are the union, and material, attachment, confidence and note
    come from the call with the largest total (the earliest on ties). One call is returned
    as is.
    """
    if len(per_call) == 1:
        return list(per_call[0])
    merged: dict[ComponentLabel, dict[str, Any]] = {}
    for objects in per_call:
        totals: dict[ComponentLabel, dict[str, Any]] = {}
        for obj in objects:
            entry = totals.setdefault(obj.label, {"count": 0, "indices": set(), "first": obj})
            entry["count"] += obj.count
            entry["indices"].update(obj.image_indices)
        for label, entry in totals.items():
            current = merged.get(label)
            if current is None:
                merged[label] = entry
                continue
            current["indices"].update(entry["indices"])
            if entry["count"] > current["count"]:
                current["count"] = entry["count"]
                current["first"] = entry["first"]
    return [
        InventoryObject(
            label=label,
            count=entry["count"],
            image_indices=sorted(entry["indices"]),
            material=entry["first"].material,
            attachment=entry["first"].attachment,
            confidence=entry["first"].confidence,
            note=entry["first"].note,
        )
        for label, entry in merged.items()
    ]


async def reconcile_room(
    room_id: str, photos: Sequence[RoomPhoto], ctx: ReconcileContext
) -> RoomResult:
    """The room's inventory from one call per slice of at most ten photos (module doc)."""
    if not photos:
        raise ValueError(f"room {room_id} has no photos")
    files = [p.file for p in photos]
    result = RoomResult(room_id=room_id, files=files, inventory=RoomInventory(), source="model")
    per_call: list[list[InventoryObject]] = []
    for number, indices in enumerate(chunks(len(photos)), start=1):
        record, parsed = await _one_call([photos[i] for i in indices], indices.start, number, ctx)
        result.calls.append(record)
        if parsed is None:
            result.inventory = RoomInventory(objects=heuristic_merge([p.analysis for p in photos]))
            result.source = "heuristic"
            result.needs_review = True
            result.review_reason = INVALID_OUTPUT
            return result
        kept, dropped = _check(parsed, len(indices), indices.start, number)
        per_call.append(kept)
        result.dropped += dropped
    result.inventory = RoomInventory(objects=merge_calls(per_call))
    if result.dropped:
        result.needs_review = True
        result.review_reason = f"dropped_objects:{len(result.dropped)}"
    return result
