"""M4d of AI_MEASUREMENT_PLAN.md: a vision model asked for each interior quantity directly, with
objects of known size as rulers (EVAL_REPORT 107.5), the floor the depth methods must beat.

One call per room per setup: the room's photos (:mod:`evals.interior_data`, the same frames the
depth methods measure) prepared as production prepares any photo
(:func:`basis.vision.prep.prepare_image`), the room's type, and the reference prompt
``measure_interior_reference/v1`` (doors 80 in, counters 36 in, outlets 4.5 in, ...); one
structured answer (:class:`InteriorAnswer`). A setup is one model at a production call's exact
settings (:func:`evals.measure_vlm.models_from_settings`, the M5 models). Every call goes through
:func:`basis.llm.call.call_model` with the run's ledger, under one budget; a room's dollars and
tokens are the ledger rows of its calls.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.cost import estimate_usd
from basis.llm.provider import ChatProvider, ChatRequest
from basis.llm.recorder import CallRecord
from basis.vision import measure_interior
from basis.vision.measure_interior import (
    NOTE_MAX,
    PROMPT,
    PROMPT_VERSION,
    QUANTITIES,
    ROOM_NAMES,
    InteriorAnswer,
)
from basis.vision.prep import prepare_image
from evals.measure_vlm import ModelSetup, ledger_totals

# The schema, prompt and message are production's (``basis.vision.measure_interior``, which
# ``basis.activities.measure_rooms`` calls with), so the eval measures the request shipped.


@dataclass(frozen=True, slots=True)
class Setup:
    model: ModelSetup

    @property
    def id(self) -> str:
        return f"M4d.{self.model.key}"

    @property
    def name(self) -> str:
        return f"Vision model asked, objects of known size ({self.model.model})"

    def settings(self) -> dict[str, Any]:
        prompt = prompts.load(PROMPT, PROMPT_VERSION)
        return {
            "model": self.model.model,
            "reasoning_effort": self.model.effort,
            "answer_budget_tokens": self.model.max_tokens,
            "temperature": "not sent (reasoning models refuse it)",
            "prompt": prompt.version_id,
            "prompt_sha256": prompt.sha256,
            "production_call": self.model.production_call,
            "inputs": "the room's photos (640 x 480, prepared as production prepares a photo) "
            "and its type",
        }


@dataclass(frozen=True, slots=True)
class RoomPhotos:
    room: str
    room_type: str
    jpegs: tuple[bytes, ...]


def load_photos(folder: Path, room_type: str) -> RoomPhotos:
    """The room's photos as production prepares a photo (CPU work: a thread from async code)."""
    meta = json.loads((folder / "photos.json").read_text())
    jpegs = tuple(prepare_image((folder / p["file"]).read_bytes()).jpeg for p in meta["photos"])
    if not jpegs:
        raise LookupError("no photos")
    return RoomPhotos(folder.name, room_type, jpegs)


def message_text(photos: RoomPhotos) -> str:
    return measure_interior.message_text(photos.room_type, len(photos.jpegs))


def build_request(setup: Setup, photos: RoomPhotos, *, study_id: str, activity: str) -> ChatRequest:
    return measure_interior.build_request(
        model=setup.model.model,
        reasoning_effort=setup.model.effort,
        max_tokens=setup.model.max_tokens,
        room_type=photos.room_type,
        jpegs=photos.jpegs,
        ref=photos.room,
        study_id=study_id,
        activity=activity,
    )


def estimate_job_usd(setup: Setup, photos: RoomPhotos) -> float:
    """The pre-call estimate :func:`basis.llm.call.call_model` reserves (worst case)."""
    request = build_request(setup, photos, study_id="estimate", activity="estimate")
    chars = len(request.system) + sum(len(m.content) for m in request.messages)
    return estimate_usd(request.model, request.max_tokens, chars, len(photos.jpegs))


def answer_quantities(answer: InteriorAnswer, kitchen: bool) -> dict[str, float]:
    q = {k: float(v) for k in QUANTITIES if (v := getattr(answer, k)) is not None}
    if not kitchen:
        q.pop("countertop_sf", None)
        q.pop("cabinet_lf", None)
    return q


@dataclass(slots=True)
class Outcome:
    setup: str
    room: str
    repeat: int
    answer: dict[str, Any] | None = None
    quantities: dict[str, float] = field(default_factory=dict)
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    calls: int = 0
    seconds: float = 0.0
    models: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def run_one(
    provider: ChatProvider,
    ctx: CallContext,
    setup: Setup,
    photos: RoomPhotos,
    repeat: int,
    records: Sequence[CallRecord],
) -> Outcome:
    from evals.predict_multiview import call_with_retries

    entity = uuid.uuid4()
    job_ctx = ctx.about("measure_room", entity)
    request = build_request(setup, photos, study_id=str(ctx.study_id), activity=ctx.activity)
    started = time.monotonic()
    parsed, attempts = await call_with_retries(provider, request, job_ctx)
    out = Outcome(setup.id, photos.room, repeat, seconds=time.monotonic() - started)
    out.errors = list(attempts.get("errors") or [])
    for key, value in ledger_totals(records, entity).items():
        setattr(out, key, value)
    if isinstance(parsed, InteriorAnswer):
        out.answer = parsed.model_dump(mode="json")
        out.quantities = answer_quantities(parsed, photos.room_type == "kitchen")
    return out


async def run_jobs(
    provider: ChatProvider,
    ctx: CallContext,
    records: Sequence[CallRecord],
    jobs: Sequence[tuple[Setup, RoomPhotos, int]],
    *,
    concurrency: int = 4,
) -> list[Outcome]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(job: tuple[Setup, RoomPhotos, int]) -> Outcome:
        setup, photos, repeat = job
        async with semaphore:
            return await run_one(provider, ctx, setup, photos, repeat, records)

    return list(await asyncio.gather(*(one(j) for j in jobs)))


__all__ = [
    "NOTE_MAX",
    "PROMPT_VERSION",
    "QUANTITIES",
    "ROOM_NAMES",
    "InteriorAnswer",
    "Outcome",
    "PROMPT",
    "RoomPhotos",
    "Setup",
    "answer_quantities",
    "build_request",
    "estimate_job_usd",
    "load_photos",
    "message_text",
    "run_jobs",
]
