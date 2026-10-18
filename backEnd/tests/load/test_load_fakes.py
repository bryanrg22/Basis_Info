"""The load-test fakes (``tests/load/load_fakes.py``) and the synthetic study plan
(``tooling/load/plan.py``) behave as docs/perf/LOAD_TESTING.md section 3 says."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLING = HERE.parents[2] / "tooling" / "load"
for extra in (HERE, TOOLING):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fixtures import fixture_path  # noqa: E402
from load_fakes import (  # noqa: E402
    PROFILE,
    ROOM_TEMPLATES,
    LatencyLocalizer,
    LatencyModel,
    LoadProvider,
    analysis_for_image,
    classifier_turn,
)
from plan import FIXTURES, ROOMS, photo_plan, unique_bytes  # noqa: E402

from basis.classify.schemas import Classification  # noqa: E402
from basis.domain.rules import lookup  # noqa: E402
from basis.llm.pool import ProviderPool  # noqa: E402
from basis.llm.provider import ChatRequest, ImagePart, Message, ToolSpec  # noqa: E402
from basis.vision.localize import Phrase  # noqa: E402
from basis.vision.prep import prepare_image  # noqa: E402
from basis.vision.reconcile import ModelRoomInventory  # noqa: E402
from basis.vision.rooms import is_room_hint, name_hint  # noqa: E402
from basis.vision.schemas import ComponentLabel, ModelPhotoAnalysis  # noqa: E402

PROFILE_DATA = json.loads(PROFILE.read_text())


def image(name: str) -> ImagePart:
    prepared = prepare_image(fixture_path("photos", name).read_bytes())
    return ImagePart(data_base64=prepared.base64)


def request(prompt: str, model: str, response_model: type, **kw: object) -> ChatRequest:
    defaults: dict[str, object] = {
        "system": "s",
        "messages": (Message(role="user", content="photo", images=(image("01_kitchen.jpg"),)),),
        "study_id": "s",
        "activity": "analyze_photo",
    }
    defaults.update(kw)
    return ChatRequest(
        model=model, prompt_version=prompt, response_model=response_model, **defaults
    )


# --------------------------------------------------------------------------- profile


def test_profile_holds_the_live_study_calls() -> None:
    llm = PROFILE_DATA["llm"]
    assert sum(e["calls"] for e in llm.values()) == 550  # EVAL_REPORT 27.8's llm_calls rows
    assert llm["classify@claude-sonnet-5-5"]["calls"] == 359
    for entry in llm.values():
        assert len(entry["latency_ms"]) == entry["calls"]
        assert entry["latency_ms"] == sorted(entry["latency_ms"])
        assert entry["input_tokens"] > 0 and entry["output_tokens"] > 0
    assert len(PROFILE_DATA["localize"]["predict_seconds"]) == 52
    assert PROFILE_DATA["azure_di"]["seconds"]


def test_latency_draws_come_from_the_samples_and_scale() -> None:
    model = LatencyModel(PROFILE_DATA, scale=1.0, seed=1)
    samples = {ms / 1000 for ms in PROFILE_DATA["llm"]["classify@claude-sonnet-5-5"]["latency_ms"]}
    for _ in range(200):
        seconds, usage = model.llm_call("classify", "claude-sonnet-5-5")
        assert seconds in samples
        assert (
            usage.input_tokens == PROFILE_DATA["llm"]["classify@claude-sonnet-5-5"]["input_tokens"]
        )
    half = LatencyModel(PROFILE_DATA, scale=0.5, seed=1)
    full = LatencyModel(PROFILE_DATA, scale=1.0, seed=1)
    assert half.llm_call("classify", "claude-sonnet-5-5")[0] == pytest.approx(
        full.llm_call("classify", "claude-sonnet-5-5")[0] / 2
    )
    assert LatencyModel(PROFILE_DATA, scale=0.0).azure_di_seconds() == 0.0


def test_unmeasured_model_falls_back_to_the_family_then_a_default() -> None:
    model = LatencyModel(PROFILE_DATA, seed=2)
    # photo_surfaces runs on gpt-6-luna since the cost sweep; only sol was measured live.
    entry = model.entry("photo_surfaces", "gpt-6-luna")
    assert entry is PROFILE_DATA["llm"]["photo_surfaces@gpt-6.1-sol"]
    seconds, usage = model.llm_call("no_such_family", "m")
    assert seconds == 2.0 and usage.input_tokens == 2000


def test_negative_scale_is_refused() -> None:
    with pytest.raises(ValueError):
        LatencyModel(PROFILE_DATA, scale=-1)


# --------------------------------------------------------------------------- answers


async def test_photo_calls_split_by_prompt_family() -> None:
    provider = LoadProvider(LatencyModel(PROFILE_DATA, scale=0.0))
    countables = await provider.complete(
        request("photo_countables/v3", "gpt-6.1-sol", ModelPhotoAnalysis)
    )
    surfaces = await provider.complete(
        request("photo_surfaces/v2", "gpt-6-luna", ModelPhotoAnalysis)
    )
    assert isinstance(countables.parsed, ModelPhotoAnalysis)
    assert isinstance(surfaces.parsed, ModelPhotoAnalysis)
    assert countables.parsed.room_type.value == "kitchen"
    labels_c = {c.label.value for c in countables.parsed.components}
    labels_s = {c.label.value for c in surfaces.parsed.components}
    template = {label for label, _ in ROOM_TEMPLATES["kitchen"]}
    assert labels_c | labels_s == template
    assert not labels_c & labels_s
    assert countables.model == "gpt-6.1-sol" and surfaces.model == "gpt-6-luna"
    assert provider.calls == Counter(
        {"photo_countables@gpt-6.1-sol": 1, "photo_surfaces@gpt-6-luna": 1}
    )


async def test_room_call_merges_its_photos() -> None:
    provider = LoadProvider(LatencyModel(PROFILE_DATA, scale=0.0))
    two = Message(
        role="user", content="room", images=(image("01_kitchen.jpg"), image("01_kitchen.jpg"))
    )
    answer = await provider.complete(
        request("room_inventory/v3", "claude-sonnet-5-5", ModelRoomInventory, messages=(two,))
    )
    assert isinstance(answer.parsed, ModelRoomInventory)
    by_label = {o.label.value: o for o in answer.parsed.objects}
    assert by_label["kitchen_cabinet"].count == 8  # largest count, not the sum
    assert by_label["kitchen_cabinet"].image_indices == [1, 2]


def test_classifier_searches_once_then_cites_the_first_hit() -> None:
    tools = (
        ToolSpec(name="search_study", description="d", parameters={}),
        ToolSpec(name="search_reference", description="d", parameters={}),
    )
    first = request(
        "classify/v1",
        "claude-sonnet-5-5",
        Classification,
        tools=tools,
        messages=(Message(role="user", content="Component: window"),),
    )
    answer, calls = classifier_turn(first)
    assert answer is None and calls[0].name == "search_reference"
    hit = {"chunk_id": "c1", "text": "Windows are structural components of the building."}
    tool_reply = Message(
        role="tool",
        name="search_reference",
        tool_call_id="load-1",
        content=json.dumps({"corpus": "reference", "hits": [hit]}),
    )
    second = first.model_copy(update={"messages": (*first.messages, tool_reply)})
    answer, calls = classifier_turn(second)
    assert calls == ()
    assert isinstance(answer, Classification)
    assert answer.citations[0].chunk_id == "c1"
    assert answer.citations[0].excerpt in hit["text"]  # verbatim
    assert not answer.needs_review
    empty = second.model_copy(
        update={
            "messages": (
                *first.messages,
                tool_reply.model_copy(update={"content": json.dumps({"hits": []})}),
            )
        }
    )
    answer, _ = classifier_turn(empty)
    assert isinstance(answer, Classification) and answer.needs_review and not answer.citations


async def test_unexpected_request_fails_loudly() -> None:
    provider = LoadProvider(LatencyModel(PROFILE_DATA, scale=0.0))
    with pytest.raises(AssertionError):
        await provider.complete(request("x/v1", "m", ToolSpec))  # no call answers a ToolSpec


# --------------------------------------------------------------------------- timing


async def test_the_pool_bounds_the_fake_like_a_real_provider() -> None:
    """LLM_CONCURRENCY is the production semaphore around the fake: 6 calls of 0.2 s at
    concurrency 2 take at least 0.6 s, at concurrency 6 about 0.2 s."""
    profile = {
        **PROFILE_DATA,
        "llm": {
            "classify@m": {
                "activity": "a",
                "calls": 1,
                "latency_ms": [200],
                "input_tokens": 1,
                "output_tokens": 1,
            }
        },
    }
    req = request("classify/v1", "m", Classification, messages=(Message(role="user", content="c"),))

    async def elapsed(concurrency: int) -> float:
        pool = ProviderPool([LoadProvider(LatencyModel(profile))], concurrency=concurrency)
        t0 = time.monotonic()
        await asyncio.gather(*(pool.complete(req) for _ in range(6)))
        return time.monotonic() - t0

    assert await elapsed(2) >= 0.6
    assert await elapsed(6) < 0.45


async def test_localizer_bounds_predictions_and_finds_one_instance_per_phrase() -> None:
    profile = {**PROFILE_DATA, "localize": {"predict_seconds": [0.0]}}
    import load_fakes

    model = LatencyModel(profile, scale=0.1)  # 1.3 s overhead -> 0.13 s per prediction
    localizer = LatencyLocalizer(model, concurrency=2)
    prepared = prepare_image(fixture_path("photos", "01_kitchen.jpg").read_bytes())
    phrases = [
        Phrase(text="window", label=ComponentLabel.WINDOW),
        Phrase(text="electrical outlet", label=ComponentLabel.ELECTRICAL_OUTLET),
    ]
    t0 = time.monotonic()
    results = await asyncio.gather(
        *(localizer.localize(prepared, phrases, threshold=0.5) for _ in range(4))
    )
    assert time.monotonic() - t0 >= 2 * load_fakes.LOCALIZE_OVERHEAD_S * 0.1
    assert [len(r.instances) for r in results] == [2, 2, 2, 2]
    assert {i.label for i in results[0].instances} == {
        ComponentLabel.WINDOW,
        ComponentLabel.ELECTRICAL_OUTLET,
    }


# --------------------------------------------------------------------------- the study


def test_templates_use_real_labels_and_send_a_live_share_to_the_classifier() -> None:
    labels = [label for template in ROOM_TEMPLATES.values() for label, _ in template]
    assert all(label in ComponentLabel.__members__.values() for label in labels)
    no_rule = [lb for lb in labels if lookup(lb, "unknown", "permanent") is None]
    share = len(no_rule) / len(labels)
    assert 0.4 <= share <= 0.65  # live 27.8: 153 of 280 assets by the classifier (0.55)


def test_the_plan_makes_the_live_package_rooms() -> None:
    photos = photo_plan()
    assert len(photos) == 52 == sum(r.photos for r in ROOMS)
    rooms = Counter(name_hint(p.filename).room_id for p in photos if is_room_hint(p.filename))
    assert len(rooms) == 32  # + the one unnamed photo the model puts in Unassigned = 33
    assert sum(1 for n in rooms.values() if n > 1) == 11
    assert [p.filename for p in photos if not is_room_hint(p.filename)] == ["IMG_0052.jpg"]
    assert len({p.filename for p in photos}) == 52


def test_unique_bytes_keep_the_prepared_image() -> None:
    data = (FIXTURES / "photos" / "01_kitchen.jpg").read_bytes()
    a, b = unique_bytes(data, "s:1"), unique_bytes(data, "s:2")
    assert a != b
    assert prepare_image(a).sha256 == prepare_image(b).sha256 == prepare_image(data).sha256
    assert analysis_for_image(prepare_image(a).sha256).room_type.value == "kitchen"


async def test_with_limits_a_spent_model_answers_429_through_the_pool() -> None:
    from rate_limits import RateLimits

    from basis.llm.errors import ProviderRateLimited

    profile = {
        **PROFILE_DATA,
        "llm": {
            "classify@claude-sonnet-5-5": {
                "activity": "a",
                "calls": 1,
                "latency_ms": [0],
                "input_tokens": 600,
                "output_tokens": 1,
            }
        },
    }
    limits = RateLimits.from_headers(
        {"claude-sonnet-5-5": {"anthropic-ratelimit-input-tokens-limit": "1000"}}
    )

    async def no_wait(_: float) -> None:
        return None

    limits.sleep = no_wait
    pool = ProviderPool([LoadProvider(LatencyModel(profile), limits=limits)], concurrency=4)
    req = request(
        "classify/v1",
        "claude-sonnet-5-5",
        Classification,
        messages=(Message(role="user", content="c"),),
    )
    await pool.complete(req)  # 600 of 1,000
    with pytest.raises(ProviderRateLimited):
        await pool.complete(req)  # 600 more do not fit, still not after the one retry
    assert limits.throttled == {"claude-sonnet-5-5": 1}
