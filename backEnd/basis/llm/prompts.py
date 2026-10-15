"""Versioned prompts: ``basis/prompts/<name>/<version>.md`` plus a hash manifest.

A prompt file is the system prompt, verbatim. Versions are ``v1``, ``v2``, ... and are
immutable once released: ``basis/prompts/manifest.json`` records the sha256 of every
version, :func:`load` refuses a file whose bytes no longer match (so an edited prompt
cannot silently change behavior that ``llm_calls.prompt_version`` says was ``v1``), and
the unit tests check that every file is in the manifest. To change a prompt, add the
next version and its manifest entry; callers that use :func:`latest` pick it up.

``Prompt.version_id`` (``"<name>/<version>"``) is what ``ChatRequest.prompt_version`` and
``llm_calls.prompt_version`` record.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Final

PACKAGE: Final = "basis.prompts"
MANIFEST: Final = "manifest.json"
_VERSION = re.compile(r"^v([1-9]\d*)$")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class PromptError(LookupError):
    """A prompt or version does not exist, or its file no longer matches the manifest."""


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str
    text: str
    sha256: str

    @property
    def version_id(self) -> str:
        return f"{self.name}/{self.version}"


def _root() -> Traversable:
    return resources.files(PACKAGE)


def version_number(version: str) -> int:
    match = _VERSION.match(version)
    if match is None:
        raise PromptError(f"prompt version {version!r} is not of the form v<N>")
    return int(match.group(1))


@cache
def manifest() -> dict[str, dict[str, str]]:
    """``{name: {version: sha256}}`` from ``basis/prompts/manifest.json``."""
    data = json.loads(_root().joinpath(MANIFEST).read_text(encoding="utf-8"))
    released: dict[str, dict[str, dict[str, str]]] = data["prompts"]
    return {
        name: {version: entry["sha256"] for version, entry in entries.items()}
        for name, entries in released.items()
    }


def names() -> list[str]:
    return sorted(manifest())


def versions(name: str) -> list[str]:
    """Released versions of ``name``, oldest first."""
    if name not in manifest():
        raise PromptError(f"unknown prompt {name!r}")
    return sorted(manifest()[name], key=version_number)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@cache
def load(name: str, version: str) -> Prompt:
    """One released prompt version, verified against the manifest."""
    if not _NAME.match(name):
        raise PromptError(f"invalid prompt name {name!r}")
    version_number(version)
    expected = manifest().get(name, {}).get(version)
    if expected is None:
        raise PromptError(f"prompt {name}/{version} is not released (not in {MANIFEST})")
    path = _root().joinpath(name, f"{version}.md")
    if not path.is_file():
        raise PromptError(f"prompt file {name}/{version}.md is missing")
    text = path.read_text(encoding="utf-8")
    actual = sha256_text(text)
    if actual != expected:
        raise PromptError(
            f"prompt {name}/{version} changed after release (sha256 {actual[:12]} != "
            f"{expected[:12]}); add a new version instead of editing a released one"
        )
    return Prompt(name=name, version=version, text=text, sha256=actual)


def latest(name: str) -> Prompt:
    """The newest released version of ``name``."""
    return load(name, versions(name)[-1])


def files_on_disk() -> dict[str, list[str]]:
    """``{name: [versions]}`` for every ``<name>/<version>.md`` file in the package."""
    found: dict[str, list[str]] = {}
    for entry in _root().iterdir():
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        for item in entry.iterdir():
            if item.is_file() and item.name.endswith(".md"):
                found.setdefault(entry.name, []).append(item.name.removesuffix(".md"))
    return {name: sorted(vs, key=version_number) for name, vs in found.items()}
