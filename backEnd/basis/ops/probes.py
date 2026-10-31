"""Free metadata probes of the worker's providers (docs/api/OPS_HEALTH.md section 4).

Every probe is one ``GET`` to an endpoint the provider documents as a lookup, never a
billed operation:

* OpenAI: ``GET /v1/models/{model}`` per model routed to it (the key is accepted and the
  model is visible to it).
* Azure OpenAI: ``GET {endpoint}/openai/models?api-version=...`` (the endpoint and key are
  accepted; the resource's deployments are not listed by the data plane, so they are not
  verified).
* Anthropic: ``GET /v1/models/{model}`` per Claude model routed to it.
* Replicate: ``GET /v1/account`` (the token is accepted), and ``GET
  /v1/models/{owner}/{name}/versions/{id}`` for the SAM 3 and Mask2Former versions the
  worker uses (the version exists and is visible to the token).
* Azure Document Intelligence: ``GET {endpoint}/documentintelligence/info`` (the resource
  answers and the key is accepted).
* A self-hosted SAM 3 Cog server: ``GET {url}/health-check`` (``READY`` means setup ran,
  so the model is loaded), only with ``OPS_PROBE_LOCALIZER=true``.

None of them proves quota, credit, vision input, structured output or that the next
inference succeeds; each result says what it established. Results are sanitized: a status
category and the HTTP status code, never the provider's error text, a key or an endpoint.
:func:`ProbeClient` refuses any method but ``GET`` (``tests/unit/ops/test_probes.py``).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal
from urllib.parse import quote

import httpx

from basis.config import Settings

ProbeStatus = Literal["healthy", "degraded", "down"]
ProbeTarget = Literal[
    "openai", "azure_openai", "anthropic", "replicate", "azure_di", "sam3", "mask2former"
]

PROBE_TIMEOUT_S: Final = 5.0
OPENAI_API: Final = "https://api.openai.com/v1"
ANTHROPIC_API: Final = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION: Final = "2023-06-01"
REPLICATE_API: Final = "https://api.replicate.com/v1"
AZURE_DI_API_VERSION: Final = "2024-11-30"
SAM3_REPLICATE_MODEL: Final = "vufinder/sam3"
MASK2FORMER_REPLICATE_MODEL: Final = "oitoito/mask2former"
_RANK: Final[dict[str, int]] = {"healthy": 0, "degraded": 1, "down": 2}

TokenSource = Callable[[], Awaitable[str | None]]
Clock = Callable[[], datetime]


class ProbeMethodRefused(RuntimeError):
    """A probe tried a method other than GET (a defect; never sent)."""


class MalformedResponse(Exception):
    """The endpoint answered 200 with a body that is not what it documents."""


Check = Callable[[Any], str]


@dataclass(frozen=True, slots=True)
class Probe:
    target: ProbeTarget
    label: str  # what was looked up, safe to show (a model name, "account", "resource")
    url: str
    check: Check  # the 200 body -> a short safe summary; raises MalformedResponse
    establishes: str
    headers: Mapping[str, str] = field(default_factory=dict)
    token: TokenSource | None = None  # a bearer token fetched at probe time
    # The answer's summary -> (status, detail), for an endpoint that reports a state (the
    # Cog health check); None: a documented body is healthy.
    states: Mapping[str, tuple[ProbeStatus, str]] | None = None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    target: ProbeTarget
    label: str
    status: ProbeStatus
    checked_at: datetime
    latency_ms: float
    detail: str
    establishes: str
    http_status: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "status": self.status,
            "checked_at": self.checked_at.isoformat(),
            "latency_ms": self.latency_ms,
            "detail": self.detail,
            "establishes": self.establishes,
            "http_status": self.http_status,
        }


def utcnow() -> datetime:
    return datetime.now(UTC)


def _has(*keys: str) -> Check:
    def check(body: Any) -> str:
        if not isinstance(body, dict) or any(key not in body for key in keys):
            raise MalformedResponse
        return "answered"

    return check


def _model_listing(body: Any) -> str:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise MalformedResponse
    return f"{len(body['data'])} models listed"


def _cog_health(body: Any) -> str:
    if not isinstance(body, dict) or not isinstance(body.get("status"), str):
        raise MalformedResponse
    return str(body["status"])


COG_STATUS: Final[dict[str, tuple[ProbeStatus, str]]] = {
    "READY": ("healthy", "ready: setup succeeded, so the model is loaded"),
    "BUSY": ("healthy", "busy: running a prediction, so the model is loaded"),
    "STARTING": ("degraded", "starting: setup (model load) has not finished"),
    "SETUP_FAILED": ("down", "setup failed: the model did not load"),
    "DEFUNCT": ("down", "defunct: the server cannot run predictions"),
}


def classify_status(code: int) -> tuple[ProbeStatus, str]:
    if code in (401, 403):
        return "down", f"credentials rejected (HTTP {code})"
    if code == 404:
        return "down", "not found for these credentials (HTTP 404)"
    if code == 429:
        return "degraded", "rate limited (HTTP 429)"
    if code >= 500:
        return "down", f"provider error (HTTP {code})"
    return "down", f"unexpected answer (HTTP {code})"


async def run_probe(
    client: httpx.AsyncClient,
    probe: Probe,
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    clock: Clock = utcnow,
) -> ProbeResult:
    """One bounded GET; every failure becomes a sanitized result, never an exception."""
    started = time.perf_counter()
    http_status: int | None = None
    try:
        async with asyncio.timeout(timeout_s):
            headers = dict(probe.headers)
            if probe.token is not None:
                token = await probe.token()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            response = await client.get(probe.url, headers=headers)
            http_status = response.status_code
            if response.status_code != 200:
                status, detail = classify_status(response.status_code)
            else:
                try:
                    summary = probe.check(response.json())
                except (ValueError, MalformedResponse):
                    status, detail = (
                        "degraded",
                        "malformed response: HTTP 200 without the documented body",
                    )
                else:
                    status, detail = "healthy", summary
                    if probe.states is not None:
                        status, detail = probe.states.get(
                            summary, ("degraded", "malformed response: unknown server status")
                        )
    except TimeoutError:
        status, detail = "down", f"timed out after {timeout_s:g} s"
    except httpx.TimeoutException:
        status, detail = "down", "timed out"
    except httpx.HTTPError:
        status, detail = "down", "network error: the endpoint could not be reached"
    except Exception:  # a token source or anything else: never leak its message
        status, detail = "down", "probe failed before an answer"
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return ProbeResult(
        target=probe.target,
        label=probe.label,
        status=status,
        checked_at=clock(),
        latency_ms=latency_ms,
        detail=detail,
        establishes=probe.establishes,
        http_status=http_status,
    )


def _secret(value: Any) -> str | None:
    if value is None:
        return None
    raw = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    return raw.strip() or None


def build_probes(
    settings: Settings,
    *,
    openai_models: Sequence[str] = (),
    anthropic_models: Sequence[str] = (),
    azure_openai: bool = False,
    localizer_adapter: str | None = None,
    segmenter_adapter: str | None = None,
    localizer_token: TokenSource | None = None,
) -> list[Probe]:
    """The probes for what the worker actually uses: the models its pool routes to each
    provider, the localizer and segmenter adapters it built, Azure DI when configured."""
    probes: list[Probe] = []
    openai_key = _secret(settings.openai_api_key)
    if openai_key:
        for model in openai_models:
            probes.append(
                Probe(
                    "openai",
                    model,
                    f"{OPENAI_API}/models/{quote(model, safe='')}",
                    _has("id"),
                    "The OpenAI key is accepted and each model looked up is visible to it. "
                    "Does not prove quota, vision, structured output or inference.",
                    headers={"Authorization": f"Bearer {openai_key}"},
                )
            )
    azure_key = _secret(settings.azure_openai_api_key)
    if azure_openai and azure_key and settings.azure_openai_endpoint:
        endpoint = settings.azure_openai_endpoint.rstrip("/")
        version = quote(settings.azure_openai_api_version, safe="")
        probes.append(
            Probe(
                "azure_openai",
                "resource",
                f"{endpoint}/openai/models?api-version={version}",
                _model_listing,
                "The Azure OpenAI endpoint and key are accepted (model listing). The "
                "deployments are not verified (the data plane does not list them); does not "
                "prove quota or inference.",
                headers={"api-key": azure_key},
            )
        )
    anthropic_key = _secret(settings.anthropic_api_key)
    if anthropic_key:
        for model in anthropic_models:
            probes.append(
                Probe(
                    "anthropic",
                    model,
                    f"{ANTHROPIC_API}/models/{quote(model, safe='')}",
                    _has("id"),
                    "The Anthropic key is accepted and each model looked up is visible to "
                    "it. Does not prove credit, rate limits or inference.",
                    headers={"x-api-key": anthropic_key, "anthropic-version": ANTHROPIC_VERSION},
                )
            )
    replicate_token = _secret(settings.replicate_api_token)
    if replicate_token and (localizer_adapter == "replicate" or segmenter_adapter == "replicate"):
        auth = {"Authorization": f"Bearer {replicate_token}"}
        probes.append(
            Probe(
                "replicate",
                "account",
                f"{REPLICATE_API}/account",
                _has("username"),
                "The Replicate token is accepted (account lookup). Does not prove billing "
                "credit or that a prediction would start.",
                headers=auth,
            )
        )
        for target, adapter, model, version in (
            ("sam3", localizer_adapter, SAM3_REPLICATE_MODEL, settings.sam3_replicate_version),
            (
                "mask2former",
                segmenter_adapter,
                MASK2FORMER_REPLICATE_MODEL,
                settings.surface_segmenter_replicate_version,
            ),
        ):
            if adapter != "replicate":
                continue
            probes.append(
                Probe(
                    target,  # type: ignore[arg-type]
                    f"{model}@{version[:12]}",
                    f"{REPLICATE_API}/models/{model}/versions/{quote(version, safe='')}",
                    _has("id"),
                    f"The {model} version is visible to the token (version lookup). No "
                    "prediction was run: does not prove cold-start time or inference.",
                    headers=auth,
                )
            )
    di_key = _secret(settings.azure_document_intelligence_key)
    if di_key and settings.azure_document_intelligence_endpoint:
        endpoint = settings.azure_document_intelligence_endpoint.rstrip("/")
        probes.append(
            Probe(
                "azure_di",
                "resource",
                f"{endpoint}/documentintelligence/info?api-version={AZURE_DI_API_VERSION}",
                _has("customDocumentModels"),
                "The Document Intelligence resource answers and the key is accepted "
                "(resource details). No document was analyzed.",
                headers={"Ocp-Apim-Subscription-Key": di_key},
            )
        )
    probe_cog = localizer_adapter == "self-hosted" and settings.ops_probe_localizer
    if probe_cog and settings.localizer_url:
        probes.append(
            Probe(
                "sam3",
                "health-check",
                f"{settings.localizer_url.rstrip('/')}/health-check",
                _cog_health,
                "The self-hosted SAM 3 server's health check (no prediction).",
                token=localizer_token,
                states=COG_STATUS,
            )
        )
    return probes


def combine(results: Sequence[ProbeResult]) -> dict[str, Any]:
    """One target's results as one entry: the worst status, the oldest check, every part."""
    worst = max(results, key=lambda r: _RANK[r.status])
    oldest = min(r.checked_at for r in results)
    return {
        "status": worst.status,
        "checked_at": oldest.isoformat(),
        "latency_ms": max(r.latency_ms for r in results),
        "detail": "; ".join(f"{r.label}: {r.detail}" for r in results),
        "establishes": " ".join(dict.fromkeys(r.establishes for r in results)),
        "parts": [r.to_json() for r in results],
    }


class GetOnlyTransport(httpx.AsyncBaseTransport):
    """Wraps a transport and refuses every method but GET before anything is sent."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise ProbeMethodRefused(f"probe method {request.method} refused")
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def ProbeClient(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:  # noqa: N802
    """The HTTP client every probe goes through: GET only, no redirects, bounded."""
    return httpx.AsyncClient(
        transport=GetOnlyTransport(transport),
        timeout=httpx.Timeout(PROBE_TIMEOUT_S),
        follow_redirects=False,
    )


async def run_probes(
    probes: Sequence[Probe],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock = utcnow,
) -> dict[str, Any]:
    """Run every probe concurrently; ``{target: combined result}``."""
    if not probes:
        return {}
    async with ProbeClient(transport) as client:
        results = await asyncio.gather(*(run_probe(client, p, clock=clock) for p in probes))
    by_target: dict[str, list[ProbeResult]] = {}
    for result in results:
        by_target.setdefault(result.target, []).append(result)
    return {target: combine(parts) for target, parts in by_target.items()}
