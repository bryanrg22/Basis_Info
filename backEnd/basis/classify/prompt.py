"""The versioned classification prompt (``basis/prompts/classify/v1.md``).

Prompts are files under ``basis/prompts/<name>/<version>.md`` (S3_SPEC section 0);
``prompt_version`` is recorded on every model call. S3-B owns the general prompt
registry; this loader reads the one prompt the classifier uses and is replaced by the
registry at merge without changing the file.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path
from typing import Final

PROMPTS_DIR: Final = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_NAME: Final = "classify"
PROMPT_VERSION_ID: Final = "v1"
PROMPT_VERSION: Final = f"{PROMPT_NAME}/{PROMPT_VERSION_ID}"


@cache
def load_prompt(name: str = PROMPT_NAME, version: str = PROMPT_VERSION_ID) -> str:
    path = PROMPTS_DIR / name / f"{version}.md"
    return path.read_text(encoding="utf-8")


def prompt_sha256(name: str = PROMPT_NAME, version: str = PROMPT_VERSION_ID) -> str:
    return hashlib.sha256(load_prompt(name, version).encode()).hexdigest()
