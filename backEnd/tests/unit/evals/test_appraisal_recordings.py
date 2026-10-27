"""Appraisal eval v2 recordings (``evals/appraisal/recordings.py``): hash, record, replay.

No network: the providers are scripted and the Azure DI SDK result is rebuilt from a
fabricated ``as_dict()`` form.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from basis.extraction.azure_di import AzureDiResult, from_sdk
from basis.extraction.fallback import FallbackAnswer, FallbackValue
from basis.llm import ScriptedProvider, ScriptedTurn
from basis.llm.errors import ProviderAccountError, ProviderBadResponse, ProviderError
from basis.llm.provider import ChatRequest, ImagePart, Message
from evals.appraisal import recordings

RAW: dict[str, Any] = {
    "apiVersion": "2024-11-30",
    "modelId": "prebuilt-layout",
    "content": "Cost Approach\nOpinion of Site Value = $ 85,000",
    "pages": [
        {
            "pageNumber": 1,
            "spans": [],
            "lines": [
                {"content": "Cost Approach", "polygon": [0, 0, 1, 0, 1, 1, 0, 1], "spans": []},
                {"content": "Opinion of Site Value = $ 85,000", "polygon": [], "spans": []},
            ],
            "selectionMarks": [
                {
                    "state": "selected",
                    "polygon": [],
                    "confidence": 0.9,
                    "span": {"offset": 0, "length": 1},
                }
            ],
        }
    ],
    "keyValuePairs": [
        {
            "key": {
                "content": "Opinion of Site Value",
                "boundingRegions": [{"pageNumber": 1, "polygon": []}],
                "spans": [],
            },
            "value": {
                "content": "$ 85,000",
                "boundingRegions": [{"pageNumber": 1, "polygon": []}],
                "spans": [],
            },
            "confidence": 0.97,
        }
    ],
    "tables": [
        {
            "rowCount": 1,
            "columnCount": 2,
            "boundingRegions": [{"pageNumber": 1, "polygon": []}],
            "spans": [],
            "cells": [
                {
                    "kind": "columnHeader",
                    "rowIndex": 0,
                    "columnIndex": 0,
                    "content": "a",
                    "spans": [],
                },
                {"rowIndex": 0, "columnIndex": 1, "content": "b", "spans": []},
            ],
        }
    ],
}


def request(**changes: Any) -> ChatRequest:
    base: dict[str, Any] = {
        "model": "gpt-4o-mini",
        "system": "system prompt",
        "messages": (Message(role="user", content="fields and page text"),),
        "response_model": FallbackAnswer,
        "max_tokens": 800,
        "reasoning_effort": "low",
        "prompt_version": "appraisal_field_fallback@v1",
        "study_id": "s1",
        "activity": "extract_fields",
    }
    return ChatRequest(**{**base, **changes})


ANSWER = FallbackAnswer(
    fields=[FallbackValue(key="cost_approach.site_value", value="85,000", page=1, confidence=0.9)]
)


def test_hash_ignores_the_study_and_activity_only() -> None:
    base = recordings.request_hash(request())
    assert recordings.request_hash(request(study_id="s2", activity="other")) == base
    assert recordings.request_hash(request(prompt_version="x@v2")) == base  # text is hashed
    image = ImagePart(data_base64="AAAA")
    with_image = (Message(role="user", content="fields and page text", images=(image,)),)
    other_image = (
        Message(
            role="user", content="fields and page text", images=(ImagePart(data_base64="BBBB"),)
        ),
    )
    changed = [
        request(model="gpt-4.1"),
        request(system="another prompt"),
        request(messages=(Message(role="user", content="other text"),)),
        request(messages=with_image),
        request(messages=other_image),
        request(response_model=None),
        request(reasoning_effort="high"),
        request(max_tokens=900),
        request(temperature=0.5),
    ]
    hashes = {recordings.request_hash(r) for r in changed}
    assert base not in hashes and len(hashes) == len(changed)


async def test_record_then_replay_round_trip(tmp_path: Path) -> None:
    scripted = ScriptedProvider([ScriptedTurn(parsed=ANSWER), ScriptedTurn(parsed=ANSWER)])
    recorder = recordings.RecordingProvider(scripted, tmp_path)
    live = await recorder.complete(request())
    files = list(tmp_path.glob("*.json"))
    assert [f.stem for f in files] == [recordings.request_hash(request())]
    stored = json.loads(files[0].read_text())
    assert "fields and page text" not in json.dumps(stored["request"])  # content is not kept
    replay = recordings.ReplayProvider(tmp_path)
    again = await replay.complete(request(study_id="another run"))
    assert again.parsed == live.parsed and isinstance(again.parsed, FallbackAnswer)
    assert (again.usage, again.model, again.provider) == (live.usage, live.model, live.provider)
    assert replay.hits == 1


async def test_a_hash_is_written_once_per_run(tmp_path: Path) -> None:
    second = FallbackAnswer(fields=[])
    scripted = ScriptedProvider([ScriptedTurn(parsed=ANSWER), ScriptedTurn(parsed=second)])
    recorder = recordings.RecordingProvider(scripted, tmp_path)
    await recorder.complete(request())
    await recorder.complete(request())  # repeat 2 answers differently
    replayed = await recordings.ReplayProvider(tmp_path).complete(request())
    assert replayed.parsed == ANSWER  # the first repeat's answer


async def test_a_later_run_adds_a_take_and_never_replaces_the_first(tmp_path: Path) -> None:
    # Two live runs (two recorders) of the same request: the sweep's second run used to
    # overwrite the first answer, so the baseline accepted from it no longer replayed.
    second = FallbackAnswer(fields=[])
    for answer in (ANSWER, second, second):
        recorder = recordings.RecordingProvider(
            ScriptedProvider([ScriptedTurn(parsed=answer)]), tmp_path
        )
        await recorder.complete(request())
    digest = recordings.request_hash(request())
    assert sorted(f.name for f in tmp_path.glob("*.json")) == sorted(
        [f"{digest}.json", f"{digest}.take2.json", f"{digest}.take3.json"]
    )
    replayed = await recordings.ReplayProvider(tmp_path).complete(request())
    assert replayed.parsed == ANSWER  # the first take


async def test_replay_miss_raises_missing_recording(tmp_path: Path) -> None:
    with pytest.raises(recordings.MissingRecording):
        await recordings.ReplayProvider(tmp_path).complete(request())
    # Not a provider error: the pipeline's fallback must not swallow it.
    assert not issubclass(recordings.MissingRecording, ProviderError)


async def test_provider_errors_are_recorded_but_account_errors_are_not(tmp_path: Path) -> None:
    bad = ProviderBadResponse("unusable answer", provider="openai", status=200)
    recorder = recordings.RecordingProvider(
        ScriptedProvider([ScriptedTurn(raise_error=bad)]), tmp_path
    )
    with pytest.raises(ProviderBadResponse):
        await recorder.complete(request())
    with pytest.raises(ProviderBadResponse, match="unusable answer"):
        await recordings.ReplayProvider(tmp_path).complete(request())

    account = ProviderAccountError("no credit", provider="openai", status=401)
    other = tmp_path / "account"
    recorder = recordings.RecordingProvider(
        ScriptedProvider([ScriptedTurn(raise_error=account)]), other
    )
    with pytest.raises(ProviderAccountError):
        await recorder.complete(request(model="gpt-4.1"))
    assert not other.exists()


class FakeRawAzure:
    def __init__(self, raw: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.raw = raw
        self.error = error
        self.calls = 0

    async def analyze_raw(self, pdf_bytes: bytes) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.raw is not None
        return recordings.rebuild_sdk_result(self.raw)


def test_sdk_result_round_trips_through_as_dict() -> None:
    sdk = recordings.rebuild_sdk_result(RAW)
    direct = from_sdk(sdk)
    assert from_sdk(recordings.rebuild_sdk_result(json.loads(json.dumps(sdk.as_dict())))) == direct
    assert direct.key_value_pairs[0].key == "Opinion of Site Value"
    assert direct.key_value_pairs[0].page == 1
    assert direct.tables[0].cells[0].kind == "columnHeader"


async def test_recording_azure_client_and_replay(tmp_path: Path) -> None:
    client = recordings.RecordingAzureClient(FakeRawAzure(RAW), tmp_path)
    live = await client.analyze(b"%PDF-")
    assert (tmp_path / recordings.AZURE_RAW).is_file()
    assert (client.calls, client.pages, client.result) == (1, 1, live)
    replay = recordings.ReplayAzureClient(tmp_path)
    assert await replay.analyze(b"%PDF-") == live
    assert recordings.load_azure(tmp_path) == live


def test_a_converted_result_is_accepted_and_absence_is_missing(tmp_path: Path) -> None:
    with pytest.raises(recordings.MissingRecording):
        recordings.load_azure(tmp_path)
    converted = from_sdk(recordings.rebuild_sdk_result(RAW))
    (tmp_path / recordings.AZURE_CONVERTED).write_text(converted.model_dump_json())
    assert recordings.load_azure(tmp_path) == converted
    assert isinstance(recordings.load_azure(tmp_path), AzureDiResult)


async def test_a_refused_document_is_recorded_and_refused_again(tmp_path: Path) -> None:
    refused = ProviderBadResponse("unsupported file", provider="azure_di", status=400)
    client = recordings.RecordingAzureClient(FakeRawAzure(error=refused), tmp_path)
    with pytest.raises(ProviderBadResponse):
        await client.analyze(b"%PDF-")
    with pytest.raises(ProviderBadResponse, match="unsupported file"):
        await recordings.ReplayAzureClient(tmp_path).analyze(b"%PDF-")
    # A later successful analysis replaces the refusal.
    await recordings.RecordingAzureClient(FakeRawAzure(RAW), tmp_path).analyze(b"%PDF-")
    assert not (tmp_path / recordings.AZURE_ERROR).exists()
    assert (await recordings.ReplayAzureClient(tmp_path).analyze(b"%PDF-")).pages


def test_context_round_trip(tmp_path: Path) -> None:
    assert recordings.read_context(tmp_path) is None
    recordings.write_context(tmp_path, {"text_model": "m", "output_budget": 800})
    assert recordings.read_context(tmp_path) == {"text_model": "m", "output_budget": 800}
