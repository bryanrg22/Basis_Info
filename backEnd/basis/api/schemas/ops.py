"""``GET /v1/ops/health``: the internal system-health document (docs/api/OPS_HEALTH.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from basis.api.schemas.common import ResponseModel
from basis.ops.services import LastInference, Service

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
        )


class OpsHealthOut(ResponseModel):
    schema_version: Literal[1] = 1
    services: list[ServiceHealthOut]
