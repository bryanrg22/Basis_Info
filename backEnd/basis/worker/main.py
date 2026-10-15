"""The Temporal worker process: ``python -m basis.worker`` (S2_SPEC section 5).

* Settings from the environment (``BASIS_ENV`` etc.), JSON logs outside dev.
* Temporal client from ``basis.temporal.client.connect`` (API key + TLS when
  ``TEMPORAL_API_KEY`` is set) on a ``Runtime`` that serves Prometheus metrics on
  ``:9464/metrics``.
* On the dev server (no API key) the search attributes are registered at startup.
* The StudyWorkflow and every activity on ``settings.temporal_task_queue``; activities get
  their dependencies through one :class:`~basis.activities.deps.Deps` built by
  :func:`build_deps` (providers, Azure DI, the Postgres chunk store).
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
from basis.extraction.azure_di import AzureDocumentIntelligence
from basis.llm.pool import ProviderPool
from basis.obs.logging import configure_logging
from basis.retrieval import default_retrieval
from basis.retrieval.store import PostgresChunkStore, warm_latest_reference
from basis.temporal.client import build_id, connect
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
    loaded on first use, not here.
    """
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
                "azure_di": deps.azure_di is not None,
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
