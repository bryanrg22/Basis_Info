"""Fake providers that answer like the fixtures and take as long as the real ones.

The load-test worker (``tests/load/fake_worker.py``) composes these into the production
code path so the system can be loaded without spending money (docs/perf/LOAD_TESTING.md
section 3). Everything that is not a provider is real: the workflow, the activities, the
provider pool and its ``LLM_CONCURRENCY`` semaphore, the Postgres writes, the chunk store.

* :class:`LatencyModel` samples latencies from a profile measured on a live study
  (``latency_profile_live_27_8.json``, built by ``build_profile.py``): each model call
  sleeps a latency drawn with replacement from the live calls of the same prompt family
  and model (``photo_countables@gpt-6.1-sol``), falling back to the family when the model
  was not measured (``photo_surfaces`` on gpt-6-luna uses the gpt-6.1-sol samples), and
  reports the live calls' mean tokens, so the recorded cost per study is the real price.
  ``scale`` multiplies every sleep (``LOAD_LATENCY_SCALE``; 0.1 runs a study ten times
  faster with the same proportions).
* :class:`LoadProvider` answers every request the worker sends: the per-photo calls with
  a realistic component mix per room type (:data:`ROOM_TEMPLATES`, about half of the
  labels without a rules-table row, as in the live study, so the classifier sees a live
  share of the assets), the room step from the merge of its photos, the second looks, the
  extraction fallback, and the residual classifier as a tool loop (one search call, then
  the answer citing the first chunk the search returned), so the classifier exercises
  the Postgres chunk store as in production.
* :class:`LatencyAzureDi` returns the synthetic Azure DI fixture after the live analysis
  time; :class:`LatencyLocalizer` bounds predictions with ``LOCALIZER_CONCURRENCY`` like
  the Replicate localizer and sleeps the live ``predict_seconds`` plus Replicate's
  create-and-poll overhead, returning one instance per phrase.
"""

from __future__ import annotations

import asyncio
import json
import random
import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Final

from llm_fakes import (
    _analysis_by_image,
    _image_hashes,
    analysis_for,
    fixture_crop,
    fixture_field_fallback,
    fixture_second_look,
    for_prompt_family,
    load_azure_fixture,
    photo_labels,
)
from pydantic import BaseModel

from basis.classify.schemas import CitationClaim, Classification
from basis.extraction.azure_di import AzureDiResult
from basis.extraction.fallback import FallbackAnswer
from basis.llm.provider import ChatRequest, ChatResponse, ToolCall, Usage
from basis.vision.localize import (
    Box,
    FakeLocalizer,
    Instance,
    Localization,
    Phrase,
)
from basis.vision.prep import PreparedImage
from basis.vision.reconcile import ModelInventoryObject, ModelRoomInventory
from basis.vision.schemas import Component, ModelPhotoAnalysis
from basis.vision.surface_crops import CropAnswer
from basis.vision.surface_material import ModelSurfaceMaterial

PROFILE: Final = Path(__file__).with_name("latency_profile_live_27_8.json")
# Replicate's create + poll on top of predict_seconds (EVAL_REPORT section 30: 52
# predictions at concurrency 3 took 55 to 63 s unthrottled, about 3.4 s each, against a
# median predict time of 2.1 s).
LOCALIZE_OVERHEAD_S: Final = 1.3
# A model call of a family the profile does not have (none in production today).
DEFAULT_CALL_S: Final = 2.0
DEFAULT_USAGE: Final = Usage(input_tokens=2000, output_tokens=200)


class LatencyModel:
    """Draws latencies (seconds, already scaled) and token counts from a profile."""

    def __init__(self, profile: Mapping[str, Any], *, scale: float = 1.0, seed: int = 0) -> None:
        if scale < 0:
            raise ValueError("scale must be >= 0")
        self.profile = profile
        self.scale = scale
        self._rng = random.Random(seed)
        self._lock = threading.Lock()  # activities share one model; random is not async-safe

    @classmethod
    def from_file(cls, path: Path = PROFILE, *, scale: float = 1.0, seed: int = 0) -> LatencyModel:
        return cls(json.loads(path.read_text("utf-8")), scale=scale, seed=seed)

    def _draw(self, samples: Sequence[float]) -> float:
        with self._lock:
            return self._rng.choice(list(samples))

    def entry(self, family: str, model: str) -> Mapping[str, Any] | None:
        """The profile entry for ``family@model``, else the family's first, else None."""
        llm: Mapping[str, Any] = self.profile["llm"]
        exact = llm.get(f"{family}@{model}")
        if exact is not None:
            return exact
        for key in sorted(llm):
            if key.split("@", 1)[0] == family:
                entry: Mapping[str, Any] = llm[key]
                return entry
        return None

    def llm_call(self, family: str, model: str) -> tuple[float, Usage]:
        entry = self.entry(family, model)
        if entry is None:
            return DEFAULT_CALL_S * self.scale, DEFAULT_USAGE
        seconds = self._draw(entry["latency_ms"]) / 1000.0
        usage = Usage(input_tokens=entry["input_tokens"], output_tokens=entry["output_tokens"])
        return seconds * self.scale, usage

    def localize_seconds(self) -> float:
        predict = self._draw(self.profile["localize"]["predict_seconds"])
        return (predict + LOCALIZE_OVERHEAD_S) * self.scale

    def azure_di_seconds(self) -> float:
        return float(self._draw(self.profile["azure_di"]["seconds"])) * self.scale


# Per room type: (label, count) as a per-photo model would list them. Built so a 52-photo
# study of tooling/load's plan makes about as many assets as the live study (280) with a
# similar share routed to the classifier (live: 153 of 280); the labels marked * have no
# rules-table row (basis.domain.rules.lookup is None) and go to the classifier.
ROOM_TEMPLATES: Final[Mapping[str, tuple[tuple[str, int], ...]]] = {
    "kitchen": (
        ("kitchen_cabinet", 8),
        ("countertop", 1),
        ("sink", 1),
        ("refrigerator", 1),
        ("dishwasher", 1),
        ("stove", 1),
        ("range_hood", 1),
        ("light_fixture", 2),
        ("electrical_outlet", 5),  # *
        ("window", 2),
        ("wall_finish", 1),
        ("ceiling_finish", 1),
        ("interior_door", 1),  # *
    ),
    "bathroom": (
        ("bathroom_vanity", 1),
        ("toilet", 1),
        ("bathtub", 1),
        ("mirror", 1),
        ("light_fixture", 1),
        ("bathroom_accessory", 2),
        ("electrical_outlet", 1),
        ("hvac_vent", 1),
        ("wall_finish", 1),
        ("interior_door", 1),  # *
    ),
    "bedroom": (
        ("carpet", 1),
        ("light_fixture", 1),
        ("ceiling_fan", 1),
        ("closet_shelving", 1),
        ("smoke_detector", 1),
        ("window", 2),
        ("electrical_outlet", 4),
        ("wall_finish", 1),
        ("ceiling_finish", 1),  # *
        ("interior_door", 1),
        ("baseboard", 1),  # *
    ),
    "living_room": (
        ("hardwood_floor", 1),
        ("light_fixture", 2),
        ("ceiling_fan", 1),
        ("blinds", 3),
        ("window", 3),
        ("electrical_outlet", 5),
        ("fireplace", 1),
        ("crown_molding", 1),  # *
        ("wall_finish", 1),
        ("baseboard", 1),  # *
    ),
    "garage": (
        ("concrete_floor", 1),
        ("light_fixture", 2),
        ("water_heater", 1),
        ("garage_door", 1),
        ("electrical_outlet", 3),
        ("electrical_panel", 1),  # *
        ("exterior_door", 1),  # *
    ),
    "exterior": (
        ("outdoor_lighting", 2),
        ("driveway", 1),
        ("sidewalk", 1),
        ("fence", 1),
        ("roof", 1),
        ("siding", 1),
        ("gutter", 1),
        ("window", 6),
        ("exterior_door", 1),  # *
        ("porch", 1),  # *
    ),
    "mechanical": (
        ("water_heater", 1),
        ("thermostat", 1),
        ("ac_condenser", 1),
        ("hvac_unit", 1),
        ("electrical_panel", 1),  # *
    ),
}


@cache
def _room_types() -> dict[str, str]:
    """``{prepared image sha256: room type}`` of the fixture photos."""
    return {digest: a.room_type.value for digest, a in _analysis_by_image().items()}


def analysis_for_image(digest: str | None) -> ModelPhotoAnalysis:
    """The fixture photo's room and floor with its room type's component template; an
    unknown image (or the ambiguous fixture) answers as the ambiguous fixture does."""
    ambiguous = analysis_for(photo_labels()["photos/08_ambiguous.jpg"])
    base = _analysis_by_image().get(digest or "") or ambiguous
    template = ROOM_TEMPLATES.get(base.room_type.value)
    if template is None:
        return base
    return base.model_copy(
        update={
            "components": [
                Component(
                    label=label,
                    note=None,
                    count=count,
                    material="unknown",
                    attachment="permanent",
                    confidence=0.85,
                )
                for label, count in template
            ]
        }
    )


def room_inventory(request: ChatRequest) -> ModelRoomInventory:
    """The merge of the request's images' analyses (largest count per label)."""
    merged: dict[str, dict[str, Any]] = {}
    for index, digest in enumerate(_image_hashes(request), start=1):
        for c in analysis_for_image(digest).components:
            entry = merged.setdefault(c.label.value, {"c": c, "count": 0, "indices": []})
            entry["count"] = max(entry["count"], c.count)
            entry["indices"].append(index)
    return ModelRoomInventory(
        objects=[
            ModelInventoryObject(
                label=e["c"].label,
                count=e["count"],
                image_indices=e["indices"],
                material=e["c"].material,
                attachment=e["c"].attachment,
                confidence=e["c"].confidence,
                note=e["c"].note,
            )
            for e in merged.values()
        ]
    )


def _first_chunk(request: ChatRequest) -> tuple[str, str] | None:
    """``(chunk id, text)`` of the first hit in the last tool result, if any."""
    for message in reversed(request.messages):
        if message.role != "tool":
            continue
        try:
            payload = json.loads(message.content)
        except ValueError:
            return None
        hits = payload.get("results") or payload.get("chunks") or payload.get("hits") or []
        for hit in hits if isinstance(hits, list) else []:
            if isinstance(hit, dict) and hit.get("chunk_id") and hit.get("text"):
                return str(hit["chunk_id"]), str(hit["text"])
        return None
    return None


def classifier_turn(request: ChatRequest) -> tuple[Classification | None, tuple[ToolCall, ...]]:
    """The residual classifier's loop: search once when a tool is offered and no tool
    has answered yet, then classify, citing the first chunk the search returned."""
    searched = any(m.role == "tool" for m in request.messages)
    if request.tools and not searched:
        last = request.messages[-1].content if request.messages else ""
        query = " ".join(last.split()[:12]) or "building component"
        names = [t.name for t in request.tools]
        tool = "search_reference" if "search_reference" in names else names[0]
        call = ToolCall(id="load-1", name=tool, arguments={"query": query, "k": 3})
        return None, (call,)
    chunk = _first_chunk(request)
    citations = [] if chunk is None else [CitationClaim(chunk_id=chunk[0], excerpt=chunk[1][:200])]
    answer = Classification(
        category="Building component (load test)",
        section="1250",
        bucket="y27_5",
        rationale="load-test answer",
        citations=citations,
        confidence=0.6,
        needs_review=not citations,
        reason=None if citations else "no citation (load test)",
    )
    return answer, ()


@dataclass
class LoadProvider:
    """A :class:`~basis.llm.provider.ChatProvider` for every call the worker makes."""

    latency: LatencyModel
    name: str = "load-fake"
    calls: Counter[str] = field(default_factory=Counter)

    def answer(self, request: ChatRequest) -> tuple[BaseModel | None, tuple[ToolCall, ...]]:
        model = request.response_model
        hashes = _image_hashes(request)
        if model is ModelPhotoAnalysis:
            analysis = analysis_for_image(hashes[0] if hashes else None)
            return for_prompt_family(analysis, request.prompt_version), ()
        if model is ModelRoomInventory:
            return room_inventory(request), ()
        if model is ModelSurfaceMaterial:
            return fixture_second_look(request), ()
        if model is CropAnswer:
            return fixture_crop(request), ()
        if model is FallbackAnswer:
            return fixture_field_fallback(request, self.name).parsed, ()
        if model is Classification:
            return classifier_turn(request)
        raise AssertionError(f"LoadProvider got an unexpected request ({request.activity})")

    async def complete(self, request: ChatRequest) -> ChatResponse:
        family = request.prompt_version.split("/", 1)[0]
        seconds, usage = self.latency.llm_call(family, request.model)
        parsed, tool_calls = self.answer(request)
        self.calls[f"{family}@{request.model}"] += 1
        await asyncio.sleep(seconds)
        return ChatResponse(
            parsed=parsed,
            tool_calls=tool_calls,
            text=parsed.model_dump_json() if parsed is not None else "",
            usage=usage,
            model=request.model,
            provider=self.name,
            latency_ms=round(seconds * 1000),
            raw={"load_fake": True},
        )


@dataclass
class LatencyAzureDi:
    """The synthetic Azure DI result, after a live analysis time."""

    latency: LatencyModel
    calls: int = 0

    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        self.calls += 1
        await asyncio.sleep(self.latency.azure_di_seconds())
        return load_azure_fixture()


class _OnePerPhrase(Mapping[str, Sequence[Instance]]):
    """FakeLocalizer script: one instance for any phrase (about the live 13 per photo)."""

    def __getitem__(self, text: str) -> Sequence[Instance]:
        offset = (sum(map(ord, text)) % 50) / 100
        box = Box(x1=offset, y1=offset, x2=offset + 0.2, y2=offset + 0.2)
        return (Instance(label="other", phrase=text, score=0.8, box=box, area=0.04),)

    def __iter__(self) -> Any:
        return iter(())

    def __len__(self) -> int:
        return 0

    def get(self, key: str, default: Any = None) -> Sequence[Instance]:  # type: ignore[override]
        return self[key]


@dataclass
class LatencyLocalizer:
    """SAM 3 as Replicate serves it: at most ``concurrency`` predictions in flight in the
    process, each taking a live predict time plus the create-and-poll overhead."""

    latency: LatencyModel
    concurrency: int
    inner: FakeLocalizer = field(default_factory=lambda: FakeLocalizer(script=_OnePerPhrase()))

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.concurrency)

    async def localize(
        self,
        image: PreparedImage,
        phrases: Sequence[Phrase],
        *,
        threshold: float,
        dedupe: bool = True,
    ) -> Localization:
        async with self._semaphore:
            seconds = self.latency.localize_seconds()
            await asyncio.sleep(seconds)
            self.inner.seconds_per_call = seconds
            return await self.inner.localize(image, phrases, threshold=threshold, dedupe=dedupe)

    async def segment_prompt(self, image: PreparedImage, **kwargs: Any) -> list[Instance]:
        async with self._semaphore:
            await asyncio.sleep(self.latency.localize_seconds())
            return await self.inner.segment_prompt(image, **kwargs)
