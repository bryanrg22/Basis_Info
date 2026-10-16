"""Offline stand-ins for the model providers and Azure DI, driven by the synthetic fixtures.

Imported as a top-level module (``tests/`` is on ``sys.path``). Nothing here calls a
network; CI has no provider credentials and real-provider evaluation is run separately.

* :class:`FixtureVisionProvider` answers vision requests by recognising the prepared image
  (sha256 of :func:`basis.vision.prep.prepare_image` of each fixture photo) and returning
  that photo's labels from ``photos/labels.json`` as a :class:`ModelPhotoAnalysis`. It is
  order-independent, so it works under the workflow's parallel photo analysis where a
  sequential :class:`~basis.llm.ScriptedProvider` would not. It also honours the prompt
  family it is sent: a ``photo_countables`` request gets the photo's countable labels only
  and a ``photo_surfaces`` request its surface labels only (``SURFACE_LABELS``, the
  surfaces prompt's vocabulary), so the decomposed step's merge is exercised for real and
  reproduces the full label set; ``photo_analysis`` gets everything. Each call reports
  ``PHOTO_USAGE`` on ``PHOTO_MODEL``, which the cost table prices at exactly
  ``PHOTO_COST_USD`` (the cost the Stage 2 stub charged, so the lifecycle budget tests
  keep their arithmetic).
* :class:`PromptKeyedProvider` replays scripted turns per prompt family (``photo_countables``,
  ``photo_surfaces``, ...), so the decomposed step's two concurrent calls each get their own
  script whatever order the event loop runs them in; a family without turns raises.
* :class:`FixtureAzureDi` returns a hand-built Azure DI result from
  ``tests/fixtures/synthetic/azure_di/``.
* :func:`fixture_bytes_for` gives seeded documents real bytes:
  the appraisal is ``urar_sample.pdf``; a photo named like ``kitchen_2.jpg`` gets the
  fixture photo of the room type its name contains, ``ambiguous_*`` gets the blurred
  ``08_ambiguous.jpg``, and any other name (``office_1.jpg``) the bedroom scene, so a
  seeded photo is only ambiguous when a test asks for it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import cache
from pathlib import PurePosixPath
from typing import Any, Final

from fixtures import fixture_path, load_fixture_json

from basis.extraction.azure_di import AzureDiResult
from basis.llm.cost import cost_usd
from basis.llm.provider import ChatRequest, ChatResponse, ScriptedTurn, Usage
from basis.vision import localize
from basis.vision.prep import prepare_image
from basis.vision.schemas import Component, ModelPhotoAnalysis

PHOTO_MODEL: Final = "gpt-4.1-nano"
PHOTO_USAGE: Final = Usage(input_tokens=20_000, output_tokens=20_000)
PHOTO_COST_USD: Final = 0.01
assert cost_usd(PHOTO_MODEL, PHOTO_USAGE) == PHOTO_COST_USD

APPRAISAL_FIXTURE: Final = "urar_sample.pdf"
ROOM_CONFIDENCE: Final = 0.9
# The labels basis/prompts/photo_surfaces/v1.md asks for; every other label (``other``
# included: loose furniture) belongs to the countables call.
SURFACE_LABELS: Final = frozenset(label.value for label in localize.SURFACE_LABELS)


@cache
def photo_labels() -> dict[str, dict[str, Any]]:
    """``{fixture file: label entry}`` from ``photos/labels.json``."""
    data = load_fixture_json("photos", "labels.json")
    return {entry["file"]: entry for entry in data["photos"]}


def analysis_for(entry: dict[str, Any]) -> ModelPhotoAnalysis:
    return ModelPhotoAnalysis(
        room_type=entry["room_type"],
        room_confidence=0.3 if entry["room_type"] == "ambiguous" else ROOM_CONFIDENCE,
        floor=entry["floor"],
        room_ordinal=1,
        components=[
            Component(
                label=c["label"],
                note=c.get("note") or ("unlisted item" if c["label"] == "other" else None),
                count=c["count"],
                material="unknown",
                attachment="permanent",
                confidence=0.85,
            )
            for c in entry["components"]
        ],
        description=f"Synthetic {entry['room_type'].replace('_', ' ')} scene.",
    )


@cache
def _analysis_by_image() -> dict[str, ModelPhotoAnalysis]:
    by_hash: dict[str, ModelPhotoAnalysis] = {}
    for file, entry in photo_labels().items():
        prepared = prepare_image(fixture_path(*file.split("/")).read_bytes())
        by_hash[prepared.sha256] = analysis_for(entry)
    return by_hash


def for_prompt_family(analysis: ModelPhotoAnalysis, prompt_version: str) -> ModelPhotoAnalysis:
    """What a narrow prompt would answer: the countables call lists the countable labels
    (``other`` included), the surfaces call the surface labels; any other prompt gets all."""
    family = prompt_version.split("/", 1)[0]
    if family == "photo_countables":
        kept = [c for c in analysis.components if c.label.value not in SURFACE_LABELS]
    elif family == "photo_surfaces":
        kept = [c for c in analysis.components if c.label.value in SURFACE_LABELS]
    else:
        return analysis
    return analysis.model_copy(update={"components": kept})


def _image_sha256(request: ChatRequest) -> str | None:
    import base64

    for message in request.messages:
        for image in message.images:
            return hashlib.sha256(base64.b64decode(image.data_base64)).hexdigest()
    return None


@dataclass
class FixtureVisionProvider:
    """Answers photo requests from the fixture labels; anything else fails the test."""

    name: str = "fixture"
    requests: list[ChatRequest] = field(default_factory=list)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        digest = _image_sha256(request)
        if digest is None or request.response_model is not ModelPhotoAnalysis:
            raise AssertionError(
                f"FixtureVisionProvider got a non-vision request ({request.activity})"
            )
        analysis = _analysis_by_image().get(digest) or analysis_for(
            photo_labels()["photos/08_ambiguous.jpg"]
        )
        analysis = for_prompt_family(analysis, request.prompt_version)
        return ChatResponse(
            parsed=analysis,
            text=analysis.model_dump_json(),
            usage=PHOTO_USAGE,
            model=PHOTO_MODEL,
            provider=self.name,
            latency_ms=1,
            raw={"fixture": True, "image_sha256": digest},
        )


@dataclass
class PromptKeyedProvider:
    """Scripted turns keyed by prompt family: ``{"photo_countables": [turn, ...], ...}``."""

    turns: dict[str, list[ScriptedTurn]]
    name: str = "keyed"
    model: str = "gpt-4o-mini"
    requests: list[ChatRequest] = field(default_factory=list)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        family = request.prompt_version.split("/", 1)[0]
        script = self.turns.get(family)
        if not script:
            raise AssertionError(f"no scripted turn left for prompt family {family!r}")
        turn = script.pop(0)
        if turn.raise_error is not None:
            raise turn.raise_error
        return ChatResponse(
            parsed=turn.parsed,
            text=turn.text,
            usage=turn.usage,
            model=self.model,
            provider=self.name,
            latency_ms=1,
            raw={"keyed": family},
        )


@dataclass
class FixtureAzureDi:
    """Returns a stored ``AzureDiResult`` fixture for any PDF."""

    fixture: str = "urar_sample.json"
    calls: int = 0

    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        self.calls += 1
        return load_azure_fixture(self.fixture)


def load_azure_fixture(name: str = "urar_sample.json") -> AzureDiResult:
    return AzureDiResult.model_validate_json(fixture_path("azure_di", name).read_text("utf-8"))


ROOM_PHOTOS: Final = {
    "kitchen": "photos/01_kitchen.jpg",
    "bathroom": "photos/02_bathroom.jpg",
    "bedroom": "photos/03_bedroom.jpg",
    "living": "photos/04_living_room.jpg",
    "garage": "photos/05_garage.jpg",
    "exterior": "photos/06_exterior_front.jpg",
    "condenser": "photos/07_ac_condenser.jpg",
    "ambiguous": "photos/08_ambiguous.jpg",
}
DEFAULT_PHOTO: Final = "photos/03_bedroom.jpg"


def fixture_bytes_for(filename: str, *, appraisal: bool) -> bytes:
    if appraisal:
        return fixture_path(APPRAISAL_FIXTURE).read_bytes()
    stem = PurePosixPath(filename).stem.lower()
    for word, file in ROOM_PHOTOS.items():
        if word in stem:
            return fixture_path(*file.split("/")).read_bytes()
    return fixture_path(*DEFAULT_PHOTO.split("/")).read_bytes()
