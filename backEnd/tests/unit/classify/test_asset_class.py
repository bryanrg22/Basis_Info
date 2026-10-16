"""An over-long ``asset_class`` is reduced to its code or clipped, not a loop failure.

OpenAI's strict schema does not enforce ``maxLength``; before the clip an answer such as
"Building or Building Component" failed validation in the provider (EVAL_REPORT section
17.2: 43 OpenAI loop failures). The provider parses with ``model_validate_json``, so the
clip must hold there. The parametrized values are every distinct value of those 43.
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
        # The 43 section 17.2 failures, by distinct value (count in the comment).
        ("00.3 Land Improvements", "00.3"),  # 21
        ("57.0 Distributive Trades and Services", "57.0"),  # 10
        ("00.11 Office Furniture, Fixtures, and Equipment", "00.11"),  # 3
        ("Nonresidential Real Property - 39 Year", "Nonresidentia..."),  # 2
        ("Millwork - General", "Millwork - Ge..."),  # 2
        ("00.11 Office Furniture, ... and Equipment - 7 Year", "00.11"),  # 2
        (LONG, "Building or B..."),  # 1
        ("00.3 Land Improvement", "00.3"),  # 1
        ("00.3 Land Improvement \u2013 15 Years", "00.3"),  # 1
        # Accepted answers are unchanged.
        ("57.0", "57.0"),
        ("00.11", "00.11"),
        ("06", "06"),
        (None, None),
        # A short code with a title is reduced too, so the field has one form.
        ("57.0 Distrib", "57.0"),
        (" 00.3", "00.3"),
        # Not a class code at the start: the length rule alone.
        ("x" * ASSET_CLASS_MAX, "x" * ASSET_CLASS_MAX),  # at the limit: unchanged
        ("Class 57.0 Distributive", "Class 57.0 Di..."),
        ("57.0.1 Something long here", "57.0.1 Someth..."),  # not a code (two dots)
        ("570.1 Something long here", "570.1 Somethi..."),  # not a code (three digits)
        ("39 Year Nonresidential", "39 Year Nonre..."),
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


def test_the_schema_keeps_the_code_of_a_long_titled_class() -> None:
    """The commonest failure (21 of 43): a code with its title."""
    parsed = Classification.model_validate_json(json.dumps(answer("00.3 Land Improvements")))
    assert parsed.asset_class == "00.3"


def test_a_non_string_asset_class_still_fails() -> None:
    with pytest.raises(ValueError):
        Classification.model_validate(answer(57.0))


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
