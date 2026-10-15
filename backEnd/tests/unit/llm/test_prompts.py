"""The prompt registry: versions, ``latest``, and released files that cannot change."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from basis.llm import prompts
from basis.llm.prompts import Prompt, PromptError, latest, load, version_number, versions

PROMPTS_DIR = Path(prompts.__file__).resolve().parents[1] / "prompts"


def test_every_file_is_released_and_every_release_has_its_file() -> None:
    on_disk = prompts.files_on_disk()
    released = {name: sorted(v, key=version_number) for name, v in prompts.manifest().items()}
    assert on_disk == released


@pytest.mark.parametrize(
    ("name", "version"),
    [(name, version) for name, vs in prompts.manifest().items() for version in vs],
)
def test_released_prompts_are_unchanged(name: str, version: str) -> None:
    """Immutability: the bytes of a released version match the manifest's sha256.

    If this fails, a released prompt was edited. Revert the edit and add the next version
    (``<name>/v<N+1>.md`` plus its manifest entry) instead.
    """
    data = (PROMPTS_DIR / name / f"{version}.md").read_bytes()
    assert hashlib.sha256(data).hexdigest() == prompts.manifest()[name][version]
    assert load(name, version).sha256 == prompts.manifest()[name][version]


def test_the_prompts_this_stage_uses_exist() -> None:
    assert latest("photo_analysis").version_id == "photo_analysis/v1"
    assert latest("appraisal_field_fallback").version_id == "appraisal_field_fallback/v1"


def test_latest_is_the_highest_version(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = {"demo": {"v2": "b", "v10": "c", "v1": "a"}}
    monkeypatch.setattr(prompts, "manifest", lambda: fake)
    assert versions("demo") == ["v1", "v2", "v10"]


def test_an_edited_release_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    load.cache_clear()
    real = prompts.manifest()
    tampered = {**real, "photo_analysis": {"v1": "0" * 64}}
    monkeypatch.setattr(prompts, "manifest", lambda: tampered)
    try:
        with pytest.raises(PromptError, match="changed after release"):
            load("photo_analysis", "v1")
    finally:
        load.cache_clear()


@pytest.mark.parametrize(
    ("name", "version", "message"),
    [
        ("photo_analysis", "v99", "not released"),
        ("nope", "v1", "not released"),
        ("photo_analysis", "1", "of the form"),
        ("../etc", "v1", "invalid prompt name"),
    ],
)
def test_lookup_errors(name: str, version: str, message: str) -> None:
    with pytest.raises(PromptError, match=message):
        load(name, version)


def test_unknown_name_has_no_versions() -> None:
    with pytest.raises(PromptError):
        versions("nope")


def test_prompt_identity() -> None:
    prompt = latest("photo_analysis")
    assert isinstance(prompt, Prompt) and prompt.text.strip()
    assert prompt.version_id == f"{prompt.name}/{prompt.version}"
