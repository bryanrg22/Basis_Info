"""``GET /v1/ops/health``: the internal system-health document (docs/api/OPS_HEALTH.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from basis.api.schemas.common import ResponseModel
from basis.ops.services import LastInference, ModelRoute, RouteHop, Service

ServiceId = Literal[
    "api",
    "db",
    "temporal",
    "worker",
    "relay",
    "storage",
    "firebase",
    "openai",
    "azure_openai",
    "anthropic",
    "replicate",
    "azure_di",
    "appraisal",
    "vision",
    "sam3",
    "mask2former",
    "room_inventory",
    "embeddings",
    "retrieval",
    "classification",
    "takeoff",
    "structure",
    "rsmeans",
    "cost_indices",
    "exports",
]


class LastInferenceOut(ResponseModel):
    """The latest successful paid call the ledger recorded for this service: evidence of
    inference, separate from the metadata probe that sets ``status``."""

    at: datetime
    model: str
    kind: Literal["chat", "document_analysis", "localization", "segmentation"]

    @classmethod
    def of(cls, last: LastInference | None) -> LastInferenceOut | None:
        if last is None:
            return None
        return cls(at=last.at, model=last.model, kind=last.kind)  # type: ignore[arg-type]


class RouteHopOut(ResponseModel):
    """One provider on a model's route, as the worker's pool orders them."""

    provider: str = Field(description="The pool's provider name: `openai`, `azure`, `anthropic`.")
    service: str = Field(description="The service id of that provider in this document.")
    target: str = Field(description="The model, or the Azure deployment, the request is sent to.")

    @classmethod
    def of(cls, hop: RouteHop) -> RouteHopOut:
        return cls(provider=hop.provider, service=hop.service, target=hop.target)


class ModelRouteOut(ResponseModel):
    """A model a service calls, by the setting that names it, with its route from the
    worker's heartbeat report: the primary provider, then the fallbacks in order."""

    setting: str = Field(
        description="The worker setting that names the model, e.g. `VISION_MODEL`."
    )
    model: str
    primary: RouteHopOut | None = Field(description="Null when no configured provider serves it.")
    fallbacks: list[RouteHopOut]

    @classmethod
    def of(cls, route: ModelRoute) -> ModelRouteOut:
        return cls(
            setting=route.setting,
            model=route.model,
            primary=None if route.primary is None else RouteHopOut.of(route.primary),
            fallbacks=[RouteHopOut.of(h) for h in route.fallbacks],
        )


class ServiceHealthOut(ResponseModel):
    id: ServiceId
    status: Literal["healthy", "degraded", "down", "unknown", "disabled"] = Field(
        description="`disabled`: an optional dependency the effective configuration does not "
        "use. Evidence past `expires_at` is reported `unknown`."
    )
    checked_at: datetime | None = Field(
        description="When the evidence was gathered (a heartbeat, a probe, this request's "
        "check). Not refreshed when a cached answer is served. Null when nothing was checked."
    )
    expires_at: datetime | None = Field(description="When the evidence stops standing.")
    check_kind: Literal["probe", "heartbeat", "model_run", "configuration"] = Field(
        description="`configuration` alone is never `healthy`."
    )
    latency_ms: float | None = Field(ge=0)
    required: bool = Field(description="Whether studies need it under the effective settings.")
    detail: str = Field(description="What the check established; never secrets or raw errors.")
    adapter: str | None = Field(description="The implementation in effect.")
    model: str | None = Field(
        description="The model or deployment in effect, as the worker runs it."
    )
    last_inference: LastInferenceOut | None = None
    hosting: str | None = Field(
        default=None,
        description="Where it runs, as a display label the runtime establishes (a provider, "
        "Cloud Run, a Docker container, a local process, Temporal Cloud ...). Null when the "
        "runtime does not establish it; never a host name, address or path.",
    )
    fallbacks: list[str] | None = Field(
        default=None,
        description="The ordered backups: display labels of the fallback routes of the models "
        "it calls (`model → provider · target`). Empty when the code has no fallback for it; "
        "null when not known (no current worker report).",
    )
    models: list[ModelRouteOut] = Field(
        default_factory=list,
        description="The models it calls, one entry per setting that names a model, each with "
        "its route (primary provider first). Empty for a service that calls no chat model.",
    )

    @classmethod
    def of(cls, service: Service) -> ServiceHealthOut:
        return cls(
            id=service.id,  # type: ignore[arg-type]
            status=service.status,
            checked_at=service.checked_at,
            expires_at=service.expires_at,
            check_kind=service.check_kind,
            latency_ms=service.latency_ms,
            required=service.required,
            detail=service.detail,
            adapter=service.adapter,
            model=service.model,
            last_inference=LastInferenceOut.of(service.last_inference),
            hosting=service.hosting,
            fallbacks=None if service.fallbacks is None else list(service.fallbacks),
            models=[ModelRouteOut.of(r) for r in service.models],
        )


class OpsHealthOut(ResponseModel):
    schema_version: Literal[1] = 1
    services: list[ServiceHealthOut]
