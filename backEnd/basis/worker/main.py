"""The Temporal worker process: ``python -m basis.worker`` (S2_SPEC section 5).

* Settings from the environment (``BASIS_ENV`` etc.), JSON logs outside dev.
* Temporal client from ``basis.temporal.client.connect`` (API key + TLS when
  ``TEMPORAL_API_KEY`` is set) on a ``Runtime`` that serves Prometheus metrics on
  ``:9464/metrics``.
* On the dev server (no API key) the search attributes are registered at startup.
* The StudyWorkflow and every activity on ``settings.temporal_task_queue``; activities get
  their dependencies through one :class:`~basis.activities.deps.Deps` built by
  :func:`build_deps` (providers, Azure DI, the Postgres chunk store, the SAM 3 localizer).
* Worker versioning (``WorkerDeploymentConfig``, auto-upgrade by default) when
  ``TEMPORAL_WORKER_VERSIONING`` is true, with deployment ``temporal_deployment_name`` and
  build id ``temporal_build_id`` (default: the git sha).
* ``/health`` on ``:8080`` for the Cloud Run worker-pool startup probe.
* The latest published reference corpus's BM25 index is built before polling starts
  (S3_SPEC section 3); a failure there is logged and the index is built on first use.
* SIGTERM/SIGINT: stop polling, give in-flight activities ``SHUTDOWN_GRACE`` to finish,
  then cancel them and exit. Cloud Run sends SIGKILL 10 s after SIGTERM, so the drain is
  8 s, leaving time to close the database pool and exit cleanly. A cancelled activity is
  retried by another worker and its idempotency key makes the retry safe.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.client import Client
from temporalio.common import VersioningBehavior, WorkerDeploymentVersion
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig
from temporalio.worker import Interceptor, Worker, WorkerDeploymentConfig

from basis.activities import Deps, build_activities
from basis.api.app import default_storage
from basis.config import Settings, load_settings
from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker
from basis.domain.cost_table import cost_table_for
from basis.extraction.azure_di import AzureDocumentIntelligence
from basis.llm import prompts
from basis.llm.pool import ProviderPool
from basis.obs.logging import configure_logging
from basis.retrieval import default_retrieval
from basis.retrieval.store import PostgresChunkStore, warm_latest_reference
from basis.temporal.client import build_id, connect
from basis.vision import fusion, room_step, surface_crops
from basis.vision.analyze import prompts_for_mode
from basis.vision.localizer_config import build_localizer
from basis.vision.surface_segmenter import SurfaceSegmenter, build_segmenter
from basis.worker.health import HealthServer
from basis.worker.interceptors import LoggingInterceptor
from basis.worker.search_attributes import ensure_search_attributes
from basis.workflow.study_workflow import StudyWorkflow

HEALTH_PORT: Final = 8080
METRICS_PORT: Final = 9464
SHUTDOWN_GRACE: Final = timedelta(seconds=8)
# The SDK sends at most one heartbeat per min(0.8 x heartbeat_timeout, this interval).
# Activities heartbeat every second; capping the send interval at 5 s keeps "heartbeat
# every <= 15 s" true and delivers a cancellation to a running activity within ~5 s.
MAX_HEARTBEAT_THROTTLE: Final = timedelta(seconds=5)

logger = logging.getLogger("basis.worker")

DepsFactory = Callable[[Settings, AsyncEngine], Deps]


def build_deps(settings: Settings, engine: AsyncEngine) -> Deps:
    """The production composition root: everything the activities get from the process.

    One :class:`~basis.llm.pool.ProviderPool` serves photo analysis, the extraction
    fallback and the residual classifier; the chunk store is Postgres, sharing the
    process BM25 registry with retrieval. A missing provider is logged, not fatal: photo
    analysis then fails non-retryably (flagged), extraction uses the text layer only and
    residual components are written ``classifier_unavailable``. The embedding model is
    loaded on first use, not here. The per-photo vision step's prompts
    (``VISION_PHOTO_MODE``: one ``photo_analysis`` call, or the ``photo_countables`` +
    ``photo_surfaces`` pair) and the room step's (``ROOM_PROMPT_VERSION`` of
    ``room_inventory`` and the ``surface_material`` second look) are loaded and checked
    against the manifest here, so a bad release fails at startup rather than on the first
    photo or room. The production configuration of each step is logged.
    """
    vision_prompts = prompts_for_mode(settings.vision_photo_mode)
    logger.info(
        "photo analysis: %s, mode %s (%s), reasoning effort %s, output budget %d%s",
        settings.vision_model,
        settings.vision_photo_mode,
        ", ".join(p.version_id for p in vision_prompts),
        settings.vision_reasoning_effort,
        settings.vision_output_budget,
        f"; surfaces call {settings.vision_surfaces_model} (budget "
        f"{settings.vision_surfaces_output_budget})"
        if settings.vision_photo_mode == "decomposed"
        else "",
    )
    logger.info(
        "count fusion: %s%s (%s at effort %s, table %s)",
        settings.vision_count_fusion,
        ""
        if settings.vision_count_fusion_active or settings.vision_count_fusion == "off"
        else ", inactive: VISION_PHOTO_MODE=single has no countables call",
        settings.vision_fusion_model,
        settings.vision_fusion_reasoning_effort,
        fusion.TABLE_VERSION,
    )
    if settings.room_reconcile:
        rooms = room_step.load_config(
            prompt_version=settings.room_prompt_version,
            model=settings.room_model,
            reasoning_effort=settings.room_reasoning_effort,
            single_photo=settings.room_single_photo,
            materials=settings.room_materials,
            mode=settings.room_step_mode,
            room_call_model=settings.room_call_model,
            room_call_reasoning_effort=settings.room_call_reasoning_effort,
            materials_model=settings.room_materials_model,
            materials_reasoning_effort=settings.room_materials_reasoning_effort,
        )
        logger.info(
            "room step: %s on %s at effort %s, one-photo rooms %s, second look %s (%s on %s "
            "at effort %s)",
            rooms.room_prompt.version_id,
            rooms.model,
            rooms.reasoning_effort,
            rooms.single_photo,
            rooms.materials,
            rooms.materials_prompt.version_id if rooms.materials_prompt else "-",
            rooms.look_model,
            rooms.look_reasoning_effort,
        )
        if rooms.room_call is not None:
            logger.info(
                "room step mode room_call: interior rooms %s on %s at effort %s; %s rooms "
                "keep the reconcile path",
                rooms.room_call.prompt.version_id,
                rooms.room_call.model,
                rooms.room_call.reasoning_effort,
                "/".join(sorted(room_step.RECONCILE_ROOM_TYPES)),
            )
        else:
            logger.info("room step mode: reconcile (every room)")
        if settings.materials_second_look == "surface_crops":
            crop_prompt = prompts.load(surface_crops.PROMPT_NAME, surface_crops.PROMPT_VERSION)
            logger.info(
                "materials second look: surface crops (%s on %s at effort %s, segmenter %s)",
                crop_prompt.version_id,
                settings.surface_crop_model,
                settings.surface_crop_reasoning_effort,
                settings.surface_segmenter,
            )
        else:
            logger.info("materials second look: per_photo")
    else:
        logger.info("room step: off (assets from the per-photo components)")
    logger.info(
        "text steps: extraction fallback %s at effort %s (budget %d); classifier %s at effort %s",
        settings.text_model,
        settings.extraction_reasoning_effort,
        settings.extraction_output_budget,
        settings.classify_model,
        settings.classify_reasoning_effort,
    )
    # Load (and validate) the cost table now, so a broken file stops the worker at start.
    table = cost_table_for(settings.cost_table_path)
    if table is None:
        logger.warning("COST_TABLE_PATH not set: only the six legacy cost rows price assets")
    else:
        logger.info(
            "cost table: %s (%d rows, base %d)", table.name, len(table.rows), table.base_year
        )
    storage, bucket = default_storage(settings)
    sessionmaker = create_sessionmaker(engine)
    retrieval = default_retrieval(settings)
    llm = ProviderPool.from_settings(settings)
    azure_di = AzureDocumentIntelligence.from_settings(settings)
    if llm is None:
        logger.warning(
            "no model provider configured: photo analysis will fail and residual "
            "components are written classifier_unavailable"
        )
    if azure_di is None:
        logger.warning("Azure DI not configured: appraisals use the text layer only")
    localizer = build_localizer(settings)
    if localizer is None:
        logger.warning(
            "neither LOCALIZER_URL nor REPLICATE_API_TOKEN is set: photo localization is skipped"
        )
    return Deps(
        sessionmaker=sessionmaker,
        storage=storage,
        bucket=bucket,
        settings=settings,
        retrieval=retrieval,
        llm=llm,
        azure_di=azure_di,
        chat_provider=llm,
        chunk_store=PostgresChunkStore(sessionmaker, registry=retrieval.registry),
        localizer=localizer,
        segmenter=build_surface_segmenter(settings),
    )


def build_surface_segmenter(settings: Settings) -> SurfaceSegmenter | None:
    """The ``SURFACE_SEGMENTER`` backend when ``MATERIALS_SECOND_LOOK=surface_crops`` and
    the room step is on, else None. The local model is loaded on first use, not here."""
    if not settings.room_reconcile or settings.materials_second_look != "surface_crops":
        return None
    token = settings.replicate_api_token
    return build_segmenter(
        settings.surface_segmenter,
        token=token.get_secret_value() if token is not None else None,
        version=settings.surface_segmenter_replicate_version,
        concurrency=settings.localizer_concurrency,
    )


def deployment_config(settings: Settings) -> WorkerDeploymentConfig | None:
    """Worker Versioning settings, or None when versioning is off (the default)."""
    if not settings.temporal_worker_versioning:
        return None
    return WorkerDeploymentConfig(
        version=WorkerDeploymentVersion(
            deployment_name=settings.temporal_deployment_name, build_id=build_id(settings)
        ),
        use_worker_versioning=True,
        default_versioning_behavior=VersioningBehavior.AUTO_UPGRADE,
    )


def build_worker(
    client: Client,
    settings: Settings,
    deps: Deps,
    *,
    task_queue: str | None = None,
    activities: dict[str, Callable[..., Any]] | None = None,
    interceptors: Sequence[Interceptor] = (),
    **worker_kwargs: Any,
) -> Worker:
    """The worker for the StudyWorkflow and its activities.

    ``activities`` defaults to the real ones bound to ``deps``; tests pass a dict with
    some entries replaced by mocks registered under the same activity names.
    """
    registered = build_activities(deps) if activities is None else activities
    config = deployment_config(settings)
    if config is not None:
        worker_kwargs["deployment_config"] = config
    worker_kwargs.setdefault("graceful_shutdown_timeout", SHUTDOWN_GRACE)
    worker_kwargs.setdefault("max_heartbeat_throttle_interval", MAX_HEARTBEAT_THROTTLE)
    return Worker(
        client,
        task_queue=task_queue or settings.temporal_task_queue,
        workflows=[StudyWorkflow],
        activities=list(registered.values()),
        interceptors=[LoggingInterceptor(), *interceptors],
        **worker_kwargs,
    )


async def warm_reference_index(deps: Deps) -> None:
    """Build the reference BM25 index now so the first classification is not cold."""
    registry = deps.retrieval_deps().registry
    try:
        version = await warm_latest_reference(deps.sessionmaker, registry)
    except Exception:  # never block startup: the index is built on first use instead
        logger.warning("could not warm the reference BM25 index", exc_info=True)
        return
    logger.info("reference BM25 index warm", extra={"corpus_version_id": str(version)})


def metrics_runtime(port: int) -> Runtime:
    return Runtime(
        telemetry=TelemetryConfig(metrics=PrometheusConfig(bind_address=f"0.0.0.0:{port}"))
    )


async def run_worker(
    settings: Settings,
    *,
    health_port: int = HEALTH_PORT,
    metrics_port: int = METRICS_PORT,
    stop: asyncio.Event | None = None,
    deps_factory: DepsFactory = build_deps,
) -> None:
    """Run until ``stop`` is set (SIGTERM/SIGINT set it when run from the command line).

    ``deps_factory`` is the composition root (:func:`build_deps` in production; the
    worker-kill tests pass one that swaps the providers for offline fixtures).
    """
    stop = stop or asyncio.Event()
    health = HealthServer(port=health_port)
    await health.start()
    client = await connect(settings, runtime=metrics_runtime(metrics_port))
    if settings.temporal_api_key is None:
        await ensure_search_attributes(client)
    engine = create_engine(settings)
    try:
        deps = deps_factory(settings, engine)
        await warm_reference_index(deps)
        worker = build_worker(client, settings, deps)
        polling = asyncio.create_task(worker.run())
        health.status = "ok"
        logger.info(
            "worker started",
            extra={
                "task_queue": settings.temporal_task_queue,
                "namespace": settings.temporal_namespace,
                "temporal_address": settings.temporal_address,
                "build_id": build_id(settings),
                "worker_versioning": settings.temporal_worker_versioning,
                "metrics_port": metrics_port,
                "health_port": health.port,
                "llm_providers": deps.llm.name if deps.llm is not None else None,
                "vision_photo_mode": settings.vision_photo_mode,
                "azure_di": deps.azure_di is not None,
                "localizer": type(deps.localizer).__name__ if deps.localizer else None,
                "chunk_store": type(deps.chunk_store).__name__,
            },
        )
        stopping = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({polling, stopping}, return_when=asyncio.FIRST_COMPLETED)
        health.status = "draining"
        if polling in done:
            stopping.cancel()
            polling.result()  # the worker failed: raise its error
        else:
            logger.info("shutdown requested; draining for up to %ss", SHUTDOWN_GRACE.seconds)
            await worker.shutdown()
            with contextlib.suppress(asyncio.CancelledError):
                await polling
        logger.info("worker stopped")
    finally:
        await engine.dispose()
        await health.stop()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m basis.worker", description=__doc__)
    parser.add_argument("--health-port", type=int, default=HEALTH_PORT)
    parser.add_argument("--metrics-port", type=int, default=METRICS_PORT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, deps_factory: DepsFactory = build_deps) -> None:
    args = parse_args(argv)
    settings = load_settings()
    configure_logging(settings.log_level, json=settings.env != "dev")

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run_worker(
            settings,
            health_port=args.health_port,
            metrics_port=args.metrics_port,
            stop=stop,
            deps_factory=deps_factory,
        )

    asyncio.run(_run())
