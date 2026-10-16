"""Model families, by name, for the decisions that depend on the family alone.

Kept free of provider imports so :mod:`basis.config` can use it (the providers import the
configuration, not the other way round).
"""

from __future__ import annotations

import re

_REASONING_MODEL = re.compile(r"^(o\d|gpt-[5-9])", re.IGNORECASE)
# OpenAI reasoning models that refuse ``reasoning_effort="none"`` (EVAL_REPORT section 17.2:
# gpt-6.1-sol answers 400 to it), so they cannot run a Chat Completions tool loop at all.
_NO_NONE_EFFORT = re.compile(r"^gpt-6\.1-sol", re.IGNORECASE)
# Claude's effort levels (``output_config.effort``); OpenAI's "none" and "minimal" are not.
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def is_reasoning_model(model: str) -> bool:
    """OpenAI reasoning models (``o1``/``o3``/``o4``, ``gpt-5`` and later families).

    They take ``max_completion_tokens`` and ``reasoning_effort`` and spend part of the
    output budget on reasoning before the answer.
    """
    return bool(_REASONING_MODEL.match(model.strip()))


def is_claude_model(model: str) -> bool:
    """``claude-*`` names: served by the Anthropic provider, effort from :data:`CLAUDE_EFFORTS`."""
    return model.strip().lower().startswith("claude")


def refuses_no_effort(model: str) -> bool:
    """An OpenAI reasoning model that refuses ``reasoning_effort="none"``."""
    return bool(_NO_NONE_EFFORT.match(model.strip()))
