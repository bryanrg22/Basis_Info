"""Azure DI client and the SDK -> ``AzureDiResult`` mapping, offline (SDK types, no calls)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentAnalysisFeature
from azure.core.exceptions import HttpResponseError, ServiceRequestError
from fixtures import fixture_path

from basis.config import Settings
from basis.extraction import azure_di
from basis.extraction.azure_di import (
    AzureDiResult,
    AzureDocumentIntelligence,
    from_sdk,
    map_azure_error,
)
from basis.llm.errors import ProviderBadResponse, ProviderRateLimited, ProviderUnavailable

SDK_PAYLOAD: dict[str, Any] = {
    "apiVersion": "2024-11-30",
    "modelId": "prebuilt-layout",
    "content": "Improvements\nYear Built:\n1987",
    "pages": [
        {
            "pageNumber": 3,
            "lines": [
                {"content": "Improvements", "polygon": []},
                {"content": "Year Built:", "polygon": []},
                {"content": "1987", "polygon": []},
            ],
            "words": [],
            "spans": [],
        }
    ],
    "keyValuePairs": [
        {
            "key": {
                "content": "Year Built",
                "boundingRegions": [{"pageNumber": 3, "polygon": []}],
                "spans": [],
            },
            "value": {
                "content": "1987",
                "boundingRegions": [{"pageNumber": 3, "polygon": []}],
                "spans": [],
            },
            "confidence": 0.94,
        },
        {"key": {"content": "Checkbox only", "spans": []}, "confidence": 0.5},
        {"key": {"content": "  ", "spans": []}, "confidence": 0.5},
    ],
    "tables": [
        {
            "rowCount": 2,
            "columnCount": 2,
            "boundingRegions": [{"pageNumber": 6, "polygon": []}],
            "cells": [
                {"rowIndex": 0, "columnIndex": 0, "content": "Item", "kind": "columnHeader"},
                {"rowIndex": 0, "columnIndex": 1, "content": "Amount", "kind": "columnHeader"},
                {"rowIndex": 1, "columnIndex": 0, "content": "Total Depreciation"},
                {"rowIndex": 1, "columnIndex": 1, "content": "$50,000"},
            ],
        }
    ],
}


def test_from_sdk_keeps_lines_pairs_and_tables() -> None:
    result = from_sdk(AnalyzeResult(SDK_PAYLOAD))
    assert result.api_version == "2024-11-30" and result.model_id == "prebuilt-layout"
    assert result.pages[0].page_number == 3
    assert result.pages[0].lines == ("Improvements", "Year Built:", "1987")
    [pair, checkbox] = result.key_value_pairs  # the blank key is dropped
    assert (pair.key, pair.value, pair.confidence, pair.page) == ("Year Built", "1987", 0.94, 3)
    assert checkbox.value is None and checkbox.page is None
    [table] = result.tables
    assert table.page == 6
    assert table.rows() == [["Item", "Amount"], ["Total Depreciation", "$50,000"]]
    assert table.cells[0].kind == "columnHeader"
    assert not result.synthetic


def test_round_trips_through_json() -> None:
    result = from_sdk(AnalyzeResult(SDK_PAYLOAD))
    assert AzureDiResult.model_validate_json(result.model_dump_json()) == result


def test_the_synthetic_fixtures_parse() -> None:
    for name in ("urar_sample.json", "urar_sample_low_confidence.json"):
        result = AzureDiResult.model_validate_json(
            fixture_path("azure_di", name).read_text("utf-8")
        )
        assert result.synthetic and len(result.pages) == 7
        assert result.mean_confidence is not None and 0.6 < result.mean_confidence < 1.0
        assert len(result.page_texts()) == 7


def test_table_rows_tolerate_out_of_range_cells() -> None:
    table = azure_di.AzureDiTable(
        page=1,
        row_count=1,
        column_count=1,
        cells=(
            azure_di.AzureDiCell(row_index=0, column_index=0, content="a"),
            azure_di.AzureDiCell(row_index=3, column_index=3, content="lost"),
        ),
    )
    assert table.rows() == [["a"]]


def _http_error(status: int) -> HttpResponseError:
    error = HttpResponseError(message=f"status {status}")
    error.status_code = status
    return error


@pytest.mark.parametrize(
    ("error", "expected", "status"),
    [
        (_http_error(429), ProviderRateLimited, 429),
        (_http_error(503), ProviderUnavailable, 503),
        (_http_error(400), ProviderBadResponse, 400),
        (_http_error(401), ProviderBadResponse, 401),
        (ServiceRequestError("dns"), ProviderUnavailable, None),
        (TimeoutError(), ProviderUnavailable, None),
        (RuntimeError("?"), ProviderUnavailable, None),
    ],
)
def test_error_mapping(error: Exception, expected: type[Exception], status: int | None) -> None:
    mapped = map_azure_error(error)
    assert type(mapped) is expected
    assert mapped.status == status and mapped.provider == "azure_di"


class FakePoller:
    def __init__(self, result: Any, delay: float) -> None:
        self._result = result
        self._delay = delay

    async def result(self) -> Any:
        await asyncio.sleep(self._delay)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeClient:
    calls: list[dict[str, Any]] = []
    result: Any = None
    delay: float = 0.0

    def __init__(self, endpoint: str, credential: Any) -> None:
        self.endpoint = endpoint
        self.key = credential.key

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def begin_analyze_document(self, model_id: str, body: Any, **kwargs: Any) -> FakePoller:
        FakeClient.calls.append(
            {"model_id": model_id, "bytes": body.bytes_source, "features": kwargs["features"]}
        )
        return FakePoller(FakeClient.result, FakeClient.delay)


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> type[FakeClient]:
    import azure.ai.documentintelligence.aio as aio

    FakeClient.calls = []
    FakeClient.result = AnalyzeResult(SDK_PAYLOAD)
    FakeClient.delay = 0.0
    monkeypatch.setattr(aio, "DocumentIntelligenceClient", FakeClient)
    return FakeClient


async def test_analyze_sends_layout_with_key_value_pairs(fake_sdk: type[FakeClient]) -> None:
    client = AzureDocumentIntelligence("https://x.cognitiveservices.azure.com", "k", timeout_s=5)
    result = await client.analyze(b"%PDF-1.7 data")
    [call] = fake_sdk.calls
    assert call == {
        "model_id": "prebuilt-layout",
        "bytes": b"%PDF-1.7 data",
        "features": [DocumentAnalysisFeature.KEY_VALUE_PAIRS],
    }
    assert result.key_value_pairs[0].key == "Year Built"


async def test_analyze_times_out_as_unavailable(fake_sdk: type[FakeClient]) -> None:
    fake_sdk.delay = 1.0
    client = AzureDocumentIntelligence("https://x", "k", timeout_s=0.05)
    with pytest.raises(ProviderUnavailable, match="timed out"):
        await client.analyze(b"%PDF-")


async def test_analyze_maps_service_errors(fake_sdk: type[FakeClient]) -> None:
    fake_sdk.result = _http_error(429)
    client = AzureDocumentIntelligence("https://x", "k", timeout_s=5)
    with pytest.raises(ProviderRateLimited):
        await client.analyze(b"%PDF-")


async def test_the_real_async_client_builds_its_transport() -> None:
    """The SDK's async client needs an async HTTP transport (aiohttp, azure-core's ``aio``
    extra) when it is constructed. The fakes above replace the client, so without this test
    a missing transport only shows on a live run: every extraction failed with
    ``ModuleNotFoundError: No module named 'aiohttp'`` (EVAL_REPORT section 19)."""
    from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential

    async with DocumentIntelligenceClient(
        endpoint="https://example.invalid", credential=AzureKeyCredential("k")
    ):
        pass  # construction and close only: no request is sent


def test_from_settings_needs_endpoint_and_key() -> None:
    unconfigured = Settings(  # type: ignore[call-arg]
        _env_file=None,
        azure_document_intelligence_endpoint=None,
        azure_document_intelligence_key=None,
    )
    assert AzureDocumentIntelligence.from_settings(unconfigured) is None
    configured = Settings(  # type: ignore[call-arg]
        _env_file=None,
        azure_document_intelligence_endpoint="https://x.cognitiveservices.azure.com",
        azure_document_intelligence_key="secret",
        azure_di_timeout_seconds=42,
    )
    client = AzureDocumentIntelligence.from_settings(configured)
    assert client is not None and client._timeout_s == 42
