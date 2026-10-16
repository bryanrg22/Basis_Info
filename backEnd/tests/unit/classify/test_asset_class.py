"""An over-long ``asset_class`` is clipped, not a loop failure (EVAL_REPORT section 17.2).

OpenAI's strict schema does not enforce ``maxLength``; before the clip an answer such as
"Building or Building Component" failed validation in the provider (43 OpenAI loop
failures). The provider parses with ``model_validate_json``, so the clip must hold there.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from basis.classify.schemas import ASSET_CLASS_MAX, Classification, clip_asset_class
from basis.llm.provider import ChatRequest, Message
from basis.llm.providers import build_request_body, parse_completion

LONG = "Building or Building Component"


def answer(asset_class: Any) -> dict[str, Any]:
    return {
        "category": "Wall coverings",
        "section": "1245",
        "bucket": "y5",
        "asset_class": asset_class,
        "rationale": "",
        "citations": [],
        "confidence": 0.7,
        "needs_review": False,
        "reason": None,
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (LONG, "Building or B..."),
        ("57.0 Distributive", "57.0 Distribu..."),
        ("x" * ASSET_CLASS_MAX, "x" * ASSET_CLASS_MAX),  # at the limit: unchanged
        ("57.0", "57.0"),
        (None, None),
    ],
)
def test_clip_asset_class(value: str | None, expected: str | None) -> None:
    clipped = clip_asset_class(value)
    assert clipped == expected
    assert clipped is None or len(clipped) <= ASSET_CLASS_MAX


def test_a_trailing_space_before_the_cut_is_dropped() -> None:
    assert clip_asset_class("abcdefghijkl  mnopq") == "abcdefghijkl..."


def test_the_schema_accepts_a_long_asset_class_from_json() -> None:
    parsed = Classification.model_validate_json(json.dumps(answer(LONG)))
    assert parsed.asset_class == "Building or B..."
    assert len(parsed.asset_class) == ASSET_CLASS_MAX


def test_the_openai_provider_parses_a_long_asset_class() -> None:
    """The path that failed: a completion's JSON text through ``parse_completion``."""
    request = ChatRequest(
        model="gpt-6-luna",
        system="s",
        messages=(Message(role="user", content="u"),),
        response_model=Classification,
        max_tokens=1024,
        reasoning_effort="none",
        prompt_version="classify/v1",
        study_id="s",
        activity="process_assets",
    )
    content = json.dumps(answer(LONG))

    class Completion(SimpleNamespace):
        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            return {"content": content}

    completion = Completion(
        model="gpt-6-luna",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, refusal=None, tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None),
    )
    response = parse_completion(completion, request, provider="openai", latency_ms=1)
    assert isinstance(response.parsed, Classification)
    assert response.parsed.asset_class == "Building or B..."
    # And a reasoning OpenAI model at effort none sends it (how section 17.2 ran luna).
    assert build_request_body(request)["reasoning_effort"] == "none"


def test_other_limits_still_hold() -> None:
    with pytest.raises(ValueError):
        Classification.model_validate(answer(LONG) | {"category": ""})
