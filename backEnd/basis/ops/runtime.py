"""What the worker reports about its effective runtime in its heartbeat.

:class:`WorkerRuntime` is built once from the process's :class:`~basis.activities.deps.Deps`
and the models its pipeline calls (:func:`basis.worker.main.pipeline_models`), so the report
names what this process actually runs: each model with the providers its pool routes it to
(primary first, the Azure deployment a request is sent to), the localizer and segmenter
adapters it built, the step switches that decide what runs, the cost table it loaded, the
embedder warm-up and the reference index. :meth:`WorkerRuntime.probe_loop` refreshes the
provider probes (:mod:`basis.ops.probes`) every ``OPS_PROVIDER_PROBE_SECONDS``.

Nothing secret is reported: no key, endpoint, URL or host name; model and deployment names
and version ids only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from basis.activities.deps import Deps
from basis.domain.cost_table import CostTable
from basis.llm.pool import ProviderPool
from basis.ops import probes as probe_mod
from basis.retrieval.embed import ModelLoad
from basis.temporal.client import build_id

# The ledger and pool name of each provider -> the service id of GET /v1/ops/health.
PROVIDER_SERVICE: Final[dict[str, str]] = {
    "openai": "openai",
    "azure": "azure_openai",
    "anthropic": "anthropic",
}

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def localizer_adapter(deps: Deps) -> str | None:
    if deps.localizer is None:
        return None
    provider = getattr(deps.localizer, "provider", None)
    return {"cog": "self-hosted", "replicate": "replicate"}.get(str(provider), str(provider))


def segmenter_adapter(deps: Deps) -> str | None:
    if deps.segmenter is None:
        return None
    name = getattr(deps.segmenter, "name", "")
    return "replicate" if str(name).startswith("replicate:") else "local"


def model_routes(deps: Deps, models: Mapping[str, Sequence[str]]) -> list[dict[str, Any]]:
    """``[{model, settings, route: [{provider, service, target}]}]`` in pipeline order."""
    pool = deps.llm
    routes: list[dict[str, Any]] = []
    for model, settings in models.items():
        route: list[dict[str, Any]] = []
        candidates = pool.candidates(model) if isinstance(pool, ProviderPool) else []
        for provider in candidates:
            target = provider.target_model(model) if hasattr(provider, "target_model") else model
            route.append(
                {
                    "provider": provider.name,
                    "service": PROVIDER_SERVICE.get(provider.name, provider.name),
                    "target": target,
                }
            )
        routes.append({"model": model, "settings": list(settings), "route": route})
    return routes


class WorkerRuntime:
    def __init__(
        self,
        deps: Deps,
        models: Mapping[str, Sequence[str]],
        *,
        cost_table: CostTable | None,
        localizer_token: probe_mod.TokenSource | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings = deps.settings
        self.settings = settings
        self.deps = deps
        self.routes = model_routes(deps, models)
        self.localizer = localizer_adapter(deps)
        self.segmenter = segmenter_adapter(deps)
        self.cost_table = (
            None
            if cost_table is None
            else {
                "name": cost_table.name,
                "rows": len(cost_table.rows),
                "base_year": cost_table.base_year,
            }
        )
        self.embedder: dict[str, Any] = {"status": "pending"}
        self.reference_index: dict[str, Any] = {"status": "pending"}
        self.probes: dict[str, Any] = {}
        self._transport = transport
        self._probe_list = probe_mod.build_probes(
            settings,
            openai_models=self._routed("openai"),
            anthropic_models=self._routed("anthropic"),
            azure_openai=bool(self._routed("azure")),
            localizer_adapter=self.localizer,
            segmenter_adapter=self.segmenter,
            localizer_token=localizer_token,
        )

    def _routed(self, provider: str) -> list[str]:
        """The models routed to ``provider`` (any position), in pipeline order."""
        return [
            r["model"] for r in self.routes if any(p["provider"] == provider for p in r["route"])
        ]

    # ------------------------------------------------------------------ warm-up
    def embedder_warmed(self, load: ModelLoad | None, name: str) -> None:
        if load is None:
            self.embedder = {
                "status": "ready",
                "model": name,
                "source": "none_needed",
                "at": utcnow().isoformat(),
            }
            return
        self.embedder = {
            "status": "ready",
            "model": load.model,
            "revision": load.revision,
            "source": load.source,
            "seconds": round(load.seconds, 2),
            "at": utcnow().isoformat(),
        }

    def embedder_warming(self, name: str) -> None:
        self.embedder = {"status": "warming", "model": name, "since": utcnow().isoformat()}

    def embedder_failed(self, name: str) -> None:
        self.embedder = {"status": "failed", "model": name, "at": utcnow().isoformat()}

    def reference_warmed(self, version: Any) -> None:
        self.reference_index = {
            "status": "ready" if version is not None else "no_corpus",
            "corpus_version_id": None if version is None else str(version),
            "at": utcnow().isoformat(),
        }

    def reference_failed(self) -> None:
        self.reference_index = {"status": "failed", "at": utcnow().isoformat()}

    # ------------------------------------------------------------------ probes
    async def probe_once(self) -> None:
        try:
            self.probes = await probe_mod.run_probes(self._probe_list, transport=self._transport)
        except Exception:  # never let a probe defect stop the worker
            logger.warning("provider probes failed", exc_info=True)

    async def probe_loop(self) -> None:
        interval = self.settings.ops_provider_probe_seconds
        if interval <= 0 or not self._probe_list:
            return
        while True:
            await self.probe_once()
            await asyncio.sleep(interval)

    # ------------------------------------------------------------------ report
    def providers_configured(self) -> list[str]:
        pool = self.deps.llm
        if not isinstance(pool, ProviderPool):
            return []
        return sorted({PROVIDER_SERVICE.get(p.name, p.name) for p in pool.providers})

    def report(self) -> dict[str, Any]:
        s = self.settings
        segmenter_loaded = getattr(self.deps.segmenter, "loaded", None)
        return {
            "build": {
                "git_sha": s.git_sha,
                "build_id": build_id(s),
                "worker_versioning": s.temporal_worker_versioning,
                "task_queue": s.temporal_task_queue,
                "env": s.env,
            },
            "routes": self.routes,
            "providers_configured": self.providers_configured(),
            "llm_primary": s.llm_primary,
            "steps": {
                "extraction_method": s.extraction_method,
                "vision_photo_mode": s.vision_photo_mode,
                "count_fusion_active": s.vision_count_fusion_active,
                "room_reconcile": s.room_reconcile,
                "room_step_mode": s.room_step_mode,
                "room_materials": s.room_materials,
                "materials_second_look": s.materials_second_look,
                "rooms_gate_waits_for_localization": s.rooms_gate_waits_for_localization,
                "structure_step": s.structure_step,
                "takeoff_areas": s.takeoff_areas,
                "cost_index": s.cost_index,
                "retrieval_search": s.retrieval_search,
            },
            "azure_di": self.deps.azure_di is not None,
            "localizer": {
                "adapter": self.localizer,
                "version": s.sam3_replicate_version[:12] if self.localizer else None,
                "probed": self.localizer == "self-hosted" and s.ops_probe_localizer,
            },
            "segmenter": {
                "adapter": self.segmenter,
                "loaded": segmenter_loaded if isinstance(segmenter_loaded, bool) else None,
            },
            "cost_table": self.cost_table,
            "embedder": self.embedder,
            "reference_index": self.reference_index,
            "probes": self.probes,
            "probe_interval_s": s.ops_provider_probe_seconds,
        }
