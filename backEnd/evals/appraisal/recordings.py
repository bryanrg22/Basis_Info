"""Record a live extraction's Azure DI analysis and model calls; replay them with no network.

Layout (``SPEC.md`` §2), under ``BASIS_EVAL_DATA_DIR/appraisal/recordings/<pdf sha256>/``:

* ``azure_di.json``: the raw SDK result (``AnalyzeResult.as_dict()``). Replay rebuilds the
  SDK object with ``AnalyzeResult(<dict>)`` and converts it with the production
  :func:`~basis.extraction.azure_di.from_sdk`, so a change to ``from_sdk`` is exercised by
  replay. Recording checks that the round trip gives the same :class:`AzureDiResult` as the
  live object and warns when it does not.
* ``azure_di_result.json`` (optional): an already converted :class:`AzureDiResult` (what a
  live study stores in ``extractions.raw["azure_di"]``); used when ``azure_di.json`` is
  absent.
* ``azure_1004.json`` (sweep systems ``azure_1004`` and ``production_plus_1004``): the raw
  ``prebuilt-mortgage.us.1004`` result (``as_dict()``), recorded and replayed like the layout
  analysis by :class:`RecordingRawModel` / :class:`ReplayRawModel`; a refused document is
  ``azure_1004_error.json``.
* ``context.json``: the extraction settings the recording was made with (model, effort,
  output budget, fallback scope and context size); replay builds the same context, so a
  recording made under ``BASIS_ENV=dev`` replays under ``BASIS_ENV=test``.
* ``llm/<request hash>.json``: one model call's answer (or its provider error), keyed by
  :func:`request_hash` over the request's model, system prompt, messages (images by
  digest), response schema name, reasoning effort, max tokens, temperature and tools. The
  study id and activity are not hashed (they change per run). The request's content is
  not stored, only its hash and a few settings.

A request with no recording raises :class:`MissingRecording`, which is not a provider error,
so it propagates through the pipeline's fallback and fails the document (the prompt or the
context changed: re-run live).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, Protocol

from basis.extraction.azure_di import AzureDiResult, from_sdk
from basis.llm import errors as llm_errors
from basis.llm.errors import ProviderAccountError, ProviderBadResponse, ProviderError
from basis.llm.provider import ChatProvider, ChatRequest, ChatResponse, ToolCall, Usage

logger = logging.getLogger("evals.appraisal.recordings")

SCHEMA: Final = 1
AZURE_RAW: Final = "azure_di.json"
AZURE_CONVERTED: Final = "azure_di_result.json"
AZURE_ERROR: Final = "azure_di_error.json"
AZURE_1004_RAW: Final = "azure_1004.json"
AZURE_1004_ERROR: Final = "azure_1004_error.json"
CONTEXT: Final = "context.json"
LLM_DIR: Final = "llm"
# Provider errors a replay can raise again (same constructor as ProviderError).
_REPLAYABLE_ERRORS: Final = ("ProviderRateLimited", "ProviderUnavailable", "ProviderBadResponse")


class MissingRecording(LookupError):
    """Replay found no recording for a request or for the Azure DI analysis."""


def recording_dir(appraisal: Path, sha256: str) -> Path:
    return appraisal / "recordings" / sha256


def _digest(text: str | bytes) -> str:
    data = text.encode() if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def request_hash(request: ChatRequest) -> str:
    """A stable hash of everything in ``request`` that shapes the model's answer."""
    payload = {
        "model": request.model,
        "system": request.system,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "name": m.name,
                "tool_call_id": m.tool_call_id,
                "images": [
                    {
                        "media_type": i.media_type,
                        "detail": i.detail,
                        "sha256": _digest(i.data_base64),
                    }
                    for i in m.images
                ],
                "tool_calls": [c.model_dump(mode="json") for c in m.tool_calls],
            }
            for m in request.messages
        ],
        "response_schema": request.response_model.__name__ if request.response_model else None,
        "reasoning_effort": request.reasoning_effort,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "tools": [t.model_dump(mode="json") for t in request.tools],
    }
    return _digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str) + "\n")


def _key_summary(request: ChatRequest) -> dict[str, Any]:
    return {
        "model": request.model,
        "response_schema": request.response_model.__name__ if request.response_model else None,
        "reasoning_effort": request.reasoning_effort,
        "max_tokens": request.max_tokens,
        "prompt_version": request.prompt_version,
        "messages": len(request.messages),
    }


def response_record(request: ChatRequest, response: ChatResponse) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "request": _key_summary(request),
        "response": {
            "parsed": response.parsed.model_dump(mode="json") if response.parsed else None,
            "tool_calls": [c.model_dump(mode="json") for c in response.tool_calls],
            "text": response.text,
            "usage": response.usage.model_dump(),
            "model": response.model,
            "provider": response.provider,
            "latency_ms": response.latency_ms,
            "raw": _jsonable(response.raw),
        },
    }


def error_record(request: ChatRequest, error: ProviderError) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "request": _key_summary(request),
        "error": {
            "type": type(error).__name__,
            "message": str(error.args[0]) if error.args else str(error),
            "provider": error.provider,
            "status": error.status,
            "model": error.model,
            "usage": error.usage.model_dump() if error.usage is not None else None,
        },
    }


class RecordingProvider:
    """Wraps the real provider; writes ``llm/<hash>.json`` for each answer.

    In one run a hash is written once (the first repeat's answer), so a replay reproduces
    the run's first repeat. Provider errors other than account errors are recorded too
    (the pipeline continues past them); an account error stops the document."""

    def __init__(self, inner: ChatProvider, directory: Path) -> None:
        self._inner = inner
        self._directory = directory
        self.name = getattr(inner, "name", "recording")
        self.written: set[str] = set()

    def _path(self, request: ChatRequest) -> tuple[str, Path]:
        digest = request_hash(request)
        return digest, self._directory / f"{digest}.json"

    async def complete(self, request: ChatRequest) -> ChatResponse:
        digest, path = self._path(request)
        try:
            response = await self._inner.complete(request)
        except ProviderAccountError:
            raise
        except ProviderError as error:
            if digest not in self.written:
                _write_json(path, error_record(request, error))
                self.written.add(digest)
            raise
        if digest not in self.written:
            _write_json(path, response_record(request, response))
            self.written.add(digest)
        return response


class ReplayProvider:
    """Answers each request from ``llm/<hash>.json``; no network."""

    name = "replay"

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self.hits = 0

    async def complete(self, request: ChatRequest) -> ChatResponse:
        digest = request_hash(request)
        path = self._directory / f"{digest}.json"
        if not path.is_file():
            raise MissingRecording(f"no recording for model request {digest}")
        record = json.loads(path.read_text())
        self.hits += 1
        if "error" in record:
            raise _replayed_error(record["error"])
        data = record["response"]
        parsed = None
        if data.get("parsed") is not None and request.response_model is not None:
            parsed = request.response_model.model_validate(data["parsed"])
        return ChatResponse(
            parsed=parsed,
            tool_calls=tuple(ToolCall.model_validate(c) for c in data.get("tool_calls", ())),
            text=data.get("text", ""),
            usage=Usage.model_validate(data["usage"]),
            model=data["model"],
            provider=data["provider"],
            latency_ms=int(data.get("latency_ms") or 0),
            raw=data.get("raw") or {},
        )


def _replayed_error(data: Mapping[str, Any]) -> ProviderError:
    name = data.get("type")
    cls: type[ProviderError] = (
        getattr(llm_errors, name) if name in _REPLAYABLE_ERRORS else llm_errors.ProviderUnavailable
    )
    usage = data.get("usage")
    return cls(
        str(data.get("message", "")),
        provider=str(data.get("provider") or ""),
        status=data.get("status"),
        usage=Usage.model_validate(usage) if usage else None,
        model=data.get("model"),
    )


# --------------------------------------------------------------------------- Azure DI


class RawAzureClient(Protocol):
    """The production client's seam: the SDK ``AnalyzeResult`` before ``from_sdk``."""

    async def analyze_raw(self, pdf_bytes: bytes) -> Any: ...


def rebuild_sdk_result(data: Mapping[str, Any]) -> Any:
    """The SDK ``AnalyzeResult`` from its ``as_dict()`` form."""
    from azure.ai.documentintelligence.models import AnalyzeResult

    return AnalyzeResult(dict(data))


class RecordingAzureClient:
    """Calls the real Azure DI client, writes ``azure_di.json``, returns ``from_sdk``."""

    name = "azure_di"

    def __init__(self, inner: RawAzureClient, directory: Path) -> None:
        self._inner = inner
        self._directory = directory
        self.calls = 0
        self.pages = 0
        self.result: AzureDiResult | None = None
        self.raw: dict[str, Any] | None = None  # the recorded ``as_dict()`` (words, marks)

    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        try:
            raw = await self._inner.analyze_raw(pdf_bytes)
        except ProviderBadResponse as error:
            # A refused document: the pipeline continues on the text layer; replay refuses
            # it the same way. Retryable errors and account errors are not recorded.
            _write_json(self._directory / AZURE_ERROR, error_record_azure(error))
            raise
        (self._directory / AZURE_ERROR).unlink(missing_ok=True)
        data = _jsonable(raw.as_dict())
        _write_json(self._directory / AZURE_RAW, data)
        result = from_sdk(raw)
        if from_sdk(rebuild_sdk_result(data)) != result:
            logger.warning(
                "the recorded Azure DI result does not round-trip through from_sdk; "
                "a replay of %s may differ from this run",
                self._directory.name,
            )
        self.calls += 1
        self.pages += len(result.pages)
        self.result = result
        self.raw = data
        return result


def error_record_azure(error: ProviderBadResponse) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "type": type(error).__name__,
        "message": str(error.args[0]) if error.args else str(error),
        "status": error.status,
    }


class ReplayAzureClient:
    """Answers from the document's recording: the analysis, a recorded refusal, or
    :class:`MissingRecording`."""

    name = "azure_di"

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self.result: AzureDiResult | None = None
        self.raw: dict[str, Any] | None = None  # None when only the converted result exists

    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        refused = self._directory / AZURE_ERROR
        if refused.is_file() and not (self._directory / AZURE_RAW).is_file():
            data = json.loads(refused.read_text())
            raise ProviderBadResponse(
                str(data.get("message", "")), provider="azure_di", status=data.get("status")
            )
        self.result = load_azure(self._directory)
        raw = self._directory / AZURE_RAW
        self.raw = json.loads(raw.read_text()) if raw.is_file() else None
        return self.result


class StaticAzureClient:
    """Returns one analysis (replay, and the repeats after the first in a live run)."""

    name = "azure_di"

    def __init__(self, result: AzureDiResult) -> None:
        self._result = result

    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        return self._result


def load_azure(directory: Path) -> AzureDiResult:
    """The recorded analysis (raw preferred, converted otherwise) or :class:`MissingRecording`."""
    raw = directory / AZURE_RAW
    if raw.is_file():
        return from_sdk(rebuild_sdk_result(json.loads(raw.read_text())))
    converted = directory / AZURE_CONVERTED
    if converted.is_file():
        return AzureDiResult.model_validate_json(converted.read_text())
    raise MissingRecording(f"no Azure DI recording in {directory.name}")


class RawModelClient(Protocol):
    """A live client for another Document Intelligence model (the raw SDK result)."""

    async def analyze_raw(self, pdf_bytes: bytes) -> Any: ...


class RecordingRawModel:
    """Calls a live Document Intelligence model, writes its ``as_dict()`` (``raw_name``) and
    returns it; a refused document (:class:`ProviderBadResponse`) is written to
    ``error_name`` and raised, like :class:`RecordingAzureClient`."""

    def __init__(
        self,
        inner: RawModelClient,
        directory: Path,
        *,
        raw_name: str = AZURE_1004_RAW,
        error_name: str = AZURE_1004_ERROR,
    ) -> None:
        self._inner = inner
        self._directory = directory
        self._raw_name = raw_name
        self._error_name = error_name
        self.calls = 0
        self.pages = 0

    async def analyze(self, pdf_bytes: bytes) -> dict[str, Any]:
        try:
            raw = await self._inner.analyze_raw(pdf_bytes)
        except ProviderBadResponse as error:
            _write_json(self._directory / self._error_name, error_record_azure(error))
            raise
        (self._directory / self._error_name).unlink(missing_ok=True)
        data: dict[str, Any] = _jsonable(raw.as_dict())
        _write_json(self._directory / self._raw_name, data)
        self.calls += 1
        self.pages += len(data.get("pages") or ())
        return data


class ReplayRawModel:
    """Answers from ``raw_name`` (or a recorded refusal, or :class:`MissingRecording`)."""

    def __init__(
        self,
        directory: Path,
        *,
        raw_name: str = AZURE_1004_RAW,
        error_name: str = AZURE_1004_ERROR,
    ) -> None:
        self._directory = directory
        self._raw_name = raw_name
        self._error_name = error_name

    async def analyze(self, pdf_bytes: bytes) -> dict[str, Any]:
        raw = self._directory / self._raw_name
        refused = self._directory / self._error_name
        if raw.is_file():
            data: dict[str, Any] = json.loads(raw.read_text())
            return data
        if refused.is_file():
            error = json.loads(refused.read_text())
            raise ProviderBadResponse(
                str(error.get("message", "")), provider="azure_di", status=error.get("status")
            )
        raise MissingRecording(f"no {self._raw_name} recording in {self._directory.name}")


def write_context(directory: Path, context: Mapping[str, Any]) -> None:
    _write_json(directory / CONTEXT, dict(context))


def read_context(directory: Path) -> dict[str, Any] | None:
    path = directory / CONTEXT
    return dict(json.loads(path.read_text())) if path.is_file() else None
