"""One room measured from its photos by a vision model (M4d, EVAL_REPORT 107; used by
``basis.activities.measure_rooms``, EVAL_REPORT 110).

The request is exactly the one EVAL_REPORT 107 measured (``evals.measure_interior_vlm`` builds
it from this module): the prompt ``measure_interior_reference/v1`` as the system text, one user
message saying the room's type and how many photos follow, the photos as production prepares any
photo (:func:`basis.vision.prep.prepare_image`, high detail), and one structured answer
(:class:`InteriorAnswer`). Pure: the caller reads and prepares the photos (on a thread) and
makes the call.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from basis.llm import prompts
from basis.llm.provider import ChatRequest, ImagePart, Message

PROMPT: Final = "measure_interior_reference"
PROMPT_VERSION: Final = "v1"
NOTE_MAX: Final = 600
QUANTITIES: Final = (
    "floor_area_sf",
    "length_ft",
    "width_ft",
    "ceiling_height_ft",
    "perimeter_ft",
    "wall_area_sf",
    "countertop_sf",
    "cabinet_lf",
)
# The room-type words the message uses (the prompt's four types); any other type reads "room".
ROOM_NAMES: Final = {
    "kitchen": "kitchen",
    "bedroom": "bedroom",
    "bathroom": "bathroom",
    "living": "living room",
}

NonNegative = Annotated[float, Field(ge=0)]


class InteriorAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    length_ft: NonNegative | None
    width_ft: NonNegative | None
    floor_area_sf: NonNegative | None
    ceiling_height_ft: NonNegative | None
    perimeter_ft: NonNegative | None
    wall_area_sf: NonNegative | None
    countertop_sf: NonNegative | None
    cabinet_lf: NonNegative | None
    room_fully_seen: bool
    outline_shape: Literal["rectangle", "L", "other"]
    confidence: Literal["low", "medium", "high"]
    notes: str = Field(max_length=NOTE_MAX)

    @field_validator("notes", mode="before")
    @classmethod
    def _clip_notes(cls, value: Any) -> Any:
        if isinstance(value, str) and len(value) > NOTE_MAX:
            return value[: NOTE_MAX - 1] + "…"
        return value


def message_text(room_type: str, photos: int) -> str:
    """The user message: the room's type (``kitchen``, ``bedroom``, ``bathroom``, ``living``)
    and how many photos follow."""
    kind = ROOM_NAMES.get(room_type, "room")
    return (
        f"The room is a {kind}. {photos} photos of it follow, taken from different "
        "spots in the room. Measure the room."
    )


def build_request(
    *,
    model: str,
    reasoning_effort: str | None,
    max_tokens: int,
    room_type: str,
    jpegs: Sequence[bytes],
    ref: str,
    study_id: str,
    activity: str,
) -> ChatRequest:
    """The request EVAL_REPORT 107 measured; ``room_type`` as :func:`message_text` takes it,
    ``ref`` names the photos (``<ref>:photo_<n>``)."""
    prompt = prompts.load(PROMPT, PROMPT_VERSION)
    images = tuple(
        ImagePart(
            data_base64=base64.b64encode(j).decode("ascii"),
            detail="high",
            ref=f"{ref}:photo_{k + 1}",
        )
        for k, j in enumerate(jpegs)
    )
    return ChatRequest(
        model=model,
        system=prompt.text,
        messages=(
            Message(role="user", content=message_text(room_type, len(jpegs)), images=images),
        ),
        response_model=InteriorAnswer,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        prompt_version=prompt.version_id,
        study_id=study_id,
        activity=activity,
    )


__all__ = [
    "NOTE_MAX",
    "PROMPT",
    "PROMPT_VERSION",
    "QUANTITIES",
    "ROOM_NAMES",
    "InteriorAnswer",
    "build_request",
    "message_text",
]
