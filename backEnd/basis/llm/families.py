"""Model families, by name, for the decisions that depend on the family alone.

Kept free of provider imports so :mod:`basis.config` can use it (the providers import the
configuration, not the other way round).
"""

from __future__ import annotations

import re

_REASONING_MODEL = re.compile(r"^(o\d|gpt-[5-9])", re.IGNORECASE)


def is_reasoning_model(model: str) -> bool:
    """OpenAI reasoning models (``o1``/``o3``/``o4``, ``gpt-5`` and later families).

    They take ``max_completion_tokens`` and ``reasoning_effort`` and spend part of the
    output budget on reasoning before the answer.
    """
    return bool(_REASONING_MODEL.match(model.strip()))
