"""Which photos of a room show the same objects: pairwise judgments and the merge they give.

The per-photo step counts each photo alone, so an object seen in two photos of a room is
counted twice, and a room whose photos show different walls must add its photos' counts
(EVAL_REPORT section 16). The two simple merges are both wrong somewhere: the largest
per-photo count (:func:`basis.vision.reconcile.heuristic_merge`) under-counts photos of
different walls, the sum over-counts photos of the same wall. Multi-view counting needs
the correspondence between views; in the literature that comes from 3-D reconstruction or
local feature matching (SuperPoint/LightGlue, SIFT with RANSAC), which tells whether two
photos overlap but not which *objects* of a label are shared. This module asks a vision
model the object-level question directly, one released prompt (``photo_overlap``) per
pair of photos:

* the two images (``"Image A:"``, ``"Image B:"``) and each one's per-photo components;
* structured output :class:`ModelPairOverlap`: whether the photos show any of the same
  surfaces or objects, and for each label how many of the listed objects are the SAME
  physical objects in both images.

:func:`overlap_merge` turns the judgments into counts, per label, as inclusion-exclusion
over the photo pairs: the sum of the per-photo counts minus, for every pair, the objects
of that label the pair shares (capped at the smaller of the two counts), and never below
the largest single-photo count of a connected group. That is exact when every shared
object is in at most two photos (the common case for a few photos of a room); when an
object is in three or more photos it subtracts too much, and the floor at the group's
largest count limits the error. A pair whose judgment failed is treated as sharing
everything (the conservative max rule), and is reported.

:func:`render_hints` writes the judgments as text for the reconciliation call
(``reconcile_room(..., hints=...)``), so the model can use them instead of re-deriving the
overlap; :func:`overlap_merge` is the deterministic alternative that replaces the call.

Cost: ``n * (n - 1) / 2`` calls for a room of ``n`` photos, two images each (a 12-photo
exterior is 66 calls). Every attempt goes through :func:`basis.llm.call.call_model`, so
the budget and the call records apply as everywhere else.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from basis.llm.call import CallContext, call_model
from basis.llm.cost import cost_usd
from basis.llm.errors import ProviderBadResponse, ProviderRequestRejected
from basis.llm.prompts import Prompt
from basis.llm.provider import ChatProvider, ChatRequest, ImagePart, Message
from basis.vision.reconcile import (
    NOTE_MAX,
    InventoryObject,
    RoomPhoto,
    clip_text,
    heuristic_merge,
)
from basis.vision.schemas import COMPONENT_LABELS, ComponentLabel, PhotoAnalysis

PROMPT_NAME: Final = "photo_overlap"
MAX_ATTEMPTS: Final = 2
MAX_SHARED: Final = 60
MAX_TOKENS: Final = 1500
INSTRUCTION: Final = (
    "Image A and Image B are two photos of the same room. Say whether they show any of "
    "the same surfaces or objects, and for each label how many of the listed objects are "
    "the same physical objects in both images."
)


class SharedObject(BaseModel):
    """Objects of one label that are the same physical objects in both images."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: ComponentLabel
    count: int = Field(ge=1, le=500, description="how many of them are visible in BOTH images")

    @model_validator(mode="before")
    @classmethod
    def _known_label(cls, data: Any) -> Any:
        if isinstance(data, dict):
            label = data.get("label")
            if isinstance(label, str) and label not in COMPONENT_LABELS:
                key = label.strip().lower().replace("-", "_").replace(" ", "_")
                data = {**data, "label": key if key in COMPONENT_LABELS else "other"}
        return data


class ModelPairOverlap(BaseModel):
    """What the model fills for one pair of photos (strict JSON schema)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    same_area: bool = Field(description="the photos show some of the same surfaces or objects")
    shared: list[SharedObject] = Field(max_length=MAX_SHARED)
    note: str | None = Field(max_length=NOTE_MAX)

    @field_validator("note", mode="before")
    @classmethod
    def _clip_note(cls, value: Any) -> Any:
        return clip_text(value)


@dataclass
class OverlapContext:
    call: CallContext
    provider: ChatProvider
    prompt: Prompt
    model: str
    max_tokens: int = MAX_TOKENS
    reasoning_effort: str | None = None


@dataclass
class PairJudgment:
    """One pair of the room's photos (0-based positions in the room, ``a < b``)."""

    a: int
    b: int
    overlap: ModelPairOverlap | None = None
    attempts: int = 0
    model: str | None = None
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def shared(self, label: ComponentLabel) -> int | None:
        """Objects of ``label`` the pair shares; ``None`` when the judgment failed."""
        if self.overlap is None:
            return None
        return sum(s.count for s in self.overlap.shared if s.label is label)

    def record(self, files: Sequence[str]) -> dict[str, Any]:
        return {
            "a": files[self.a],
            "b": files[self.b],
            "overlap": None if self.overlap is None else self.overlap.model_dump(mode="json"),
            "attempts": self.attempts,
            "model": self.model,
            "cost_usd": round(self.cost_usd, 6),
            "errors": self.errors,
        }


@dataclass
class RoomOverlap:
    photos: int
    pairs: list[PairJudgment] = field(default_factory=list)

    @property
    def failed(self) -> list[PairJudgment]:
        return [p for p in self.pairs if p.overlap is None]

    @property
    def cost_usd(self) -> float:
        return sum(p.cost_usd for p in self.pairs)


def _components_line(analysis: PhotoAnalysis | None) -> str:
    if analysis is None:
        return "no per-photo analysis"
    totals: dict[str, int] = {}
    for c in analysis.components:
        if c.label is not ComponentLabel.OTHER:
            totals[c.label.value] = totals.get(c.label.value, 0) + c.count
    return "; ".join(f"{label} x{count}" for label, count in totals.items()) or "no components"


def build_request(a: RoomPhoto, b: RoomPhoto, ctx: OverlapContext) -> ChatRequest:
    images = tuple(
        Message(
            role="user",
            content=f"Image {name}:",
            images=(ImagePart(media_type="image/jpeg", data_base64=p.image.base64, detail="high"),),
        )
        for name, p in (("A", a), ("B", b))
    )
    text = (
        f"{INSTRUCTION}\n\nPer-photo analysis (each photo analyzed alone):\n"
        f"Image A: {_components_line(a.analysis)}\nImage B: {_components_line(b.analysis)}"
    )
    return ChatRequest(
        model=ctx.model,
        system=ctx.prompt.text,
        messages=(*images, Message(role="user", content=text)),
        response_model=ModelPairOverlap,
        max_tokens=ctx.max_tokens,
        temperature=0.0,
        reasoning_effort=ctx.reasoning_effort,
        prompt_version=ctx.prompt.version_id,
        study_id=str(ctx.call.study_id),
        activity=ctx.call.activity,
    )


async def judge_pair(
    photos: Sequence[RoomPhoto], a: int, b: int, ctx: OverlapContext
) -> PairJudgment:
    """One pair; an unusable answer is retried once, then the judgment stays ``None``.
    Budget and transient provider errors propagate (as in ``reconcile_room``)."""
    judgment = PairJudgment(a=a, b=b)
    request = build_request(photos[a], photos[b], ctx)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        judgment.attempts = attempt
        try:
            response = await call_model(ctx.provider, request, ctx.call)
        except ProviderBadResponse as exc:
            judgment.errors.append(f"attempt {attempt}: {exc}")
            if exc.usage is not None:
                judgment.cost_usd += cost_usd(exc.model or request.model, exc.usage)
            continue
        except ProviderRequestRejected as exc:
            # This pair refused: no judgment, not retried (the same input gets the same
            # answer). Configuration and account errors propagate.
            judgment.errors.append(f"attempt {attempt}: {exc}")
            return judgment
        judgment.cost_usd += cost_usd(response.model, response.usage)
        judgment.model = response.model
        if isinstance(response.parsed, ModelPairOverlap):
            judgment.overlap = response.parsed
            return judgment
        judgment.errors.append(f"attempt {attempt}: no structured overlap in the response")
    return judgment


Judge = Callable[[Sequence[RoomPhoto], int, int, OverlapContext], Awaitable[PairJudgment]]


async def room_overlap(
    photos: Sequence[RoomPhoto], ctx: OverlapContext, judge: Judge | None = None
) -> RoomOverlap:
    """Every pair of the room's photos (none for a one-photo room), concurrently; the
    provider pool bounds the concurrency. ``judge`` replaces :func:`judge_pair` (the eval
    runner wraps it in its per-call retry, so one rate limit does not redo every pair)."""
    pairs = list(itertools.combinations(range(len(photos)), 2))
    run = judge or judge_pair
    judged = await asyncio.gather(*(run(photos, a, b, ctx) for a, b in pairs))
    return RoomOverlap(photos=len(photos), pairs=list(judged))


def _per_photo(analysis: PhotoAnalysis | None) -> dict[ComponentLabel, int]:
    totals: dict[ComponentLabel, int] = {}
    for c in analysis.components if analysis is not None else []:
        if c.label is not ComponentLabel.OTHER:
            totals[c.label] = totals.get(c.label, 0) + c.count
    return totals


def merged_count(counts: dict[int, int], shared: dict[tuple[int, int], int | None]) -> int:
    """One label's room count from its per-photo ``counts`` (photo -> count, photos that
    list it) and the pairs' ``shared`` objects (``None`` = judgment failed = shares all):
    the inclusion-exclusion sum over pairs, floored per connected group at its largest
    single-photo count (module doc)."""
    photos = sorted(counts)
    parent = {p: p for p in photos}

    def find(p: int) -> int:
        while parent[p] != p:
            p = parent[p]
        return p

    subtract: dict[int, int] = {}
    for a, b in itertools.combinations(photos, 2):
        value = shared.get((a, b))
        overlap = min(counts[a], counts[b]) if value is None else min(value, counts[a], counts[b])
        if overlap > 0:
            parent[find(b)] = find(a)
            subtract[a] = subtract.get(a, 0) + overlap
    groups: dict[int, list[int]] = {}
    for p in photos:
        groups.setdefault(find(p), []).append(p)
    total = 0
    for members in groups.values():
        raw = sum(counts[p] for p in members) - sum(subtract.get(p, 0) for p in members)
        total += max(raw, max(counts[p] for p in members))
    return total


def overlap_merge(
    analyses: Sequence[PhotoAnalysis | None], overlap: RoomOverlap
) -> list[InventoryObject]:
    """The room inventory from the per-photo components and the pair judgments.

    Per label: :func:`merged_count`; indices = the photos listing it; material,
    attachment, confidence and note from the photo with the largest count (earliest on
    ties), as :func:`basis.vision.reconcile.heuristic_merge`. ``other`` components are
    kept as the heuristic keeps them (the judgments are by vocabulary label), one per
    distinct note with the largest per-photo count."""
    per_photo = [_per_photo(a) for a in analyses]
    by_pair = {(p.a, p.b): p for p in overlap.pairs}
    objects: list[InventoryObject] = []
    for obj in heuristic_merge(analyses):
        if obj.label is ComponentLabel.OTHER:
            objects.append(obj)
            continue
        counts = {
            i - 1: per_photo[i - 1][obj.label]
            for i in obj.image_indices
            if per_photo[i - 1].get(obj.label)
        }
        shared = {
            (a, b): (by_pair[(a, b)].shared(obj.label) if (a, b) in by_pair else None)
            for a, b in itertools.combinations(sorted(counts), 2)
        }
        objects.append(obj.model_copy(update={"count": merged_count(counts, shared)}))
    return objects


def render_hints(overlap: RoomOverlap, indices: range) -> str | None:
    """The judgments of the pairs inside one reconciliation call, numbered as that call's
    images (``indices`` = the call's 0-based slice of the room's photos)."""
    lines: list[str] = []
    for pair in overlap.pairs:
        if pair.a not in indices or pair.b not in indices:
            continue
        a, b = pair.a - indices.start + 1, pair.b - indices.start + 1
        if pair.overlap is None:
            lines.append(f"Images {a} and {b}: no judgment.")
        elif not pair.overlap.same_area and not pair.overlap.shared:
            lines.append(f"Images {a} and {b}: no common surfaces or objects.")
        else:
            shared = ", ".join(f"{s.label.value} x{s.count}" for s in pair.overlap.shared)
            lines.append(f"Images {a} and {b}: same objects in both: {shared or 'surfaces only'}.")
    if not lines:
        return None
    return (
        "A separate pairwise check of these images found which objects are the same "
        "physical objects in two images (use it, and check it against the images):\n"
        + "\n".join(lines)
    )
