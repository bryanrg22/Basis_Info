"""The geo worker: ``python -m basis.geo`` polls ``GEO_TASK_QUEUE`` for ``PublicGeoWorkflow`` and
its activities, two gathers at a time per process. One :class:`~basis.geo.http.PoliteClient` is
shared by every gather in the process, so the one-request-a-second spacing per host holds
across them (run one geo worker per deployment: the spacing is per process). It needs the
``geo`` dependency group (laspy, lazrs, geographiclib); the API and the study worker do not."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable, Sequence

from temporalio.client import Client
from temporalio.worker import Worker

from basis.config import Settings, load_settings
from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker
from basis.geo.activities import GeoActivities
from basis.geo.gather import GatherConfig, Gatherer
from basis.geo.http import PoliteClient
from basis.geo.pg_store import PgGeoStore
from basis.geo.workflow import PublicGeoWorkflow
from basis.obs.logging import configure_logging
from basis.temporal.client import connect

logger = logging.getLogger(__name__)
MAX_CONCURRENT_GATHERS = 2


def build_geo_worker(
    client: Client, settings: Settings, store: PgGeoStore, gatherer: Callable[..., Gatherer]
) -> Worker:
    activities = GeoActivities(gatherer, store)
    return Worker(
        client,
        task_queue=settings.geo_task_queue,
        workflows=[PublicGeoWorkflow],
        activities=[activities.gather_public_geo, activities.fail_public_geo],
        max_concurrent_activities=MAX_CONCURRENT_GATHERS,
    )


async def run_geo_worker(settings: Settings, stop: asyncio.Event) -> None:
    from basis.api.app import default_storage

    client = await connect(settings)
    engine = create_engine(settings)
    store = PgGeoStore(create_sessionmaker(engine))
    storage, bucket = default_storage(settings)
    http = PoliteClient(
        host_interval_s=settings.geo_host_interval_s,
        timeout_s=settings.geo_request_timeout_s,
        retries=settings.geo_retries,
    )
    config = GatherConfig.of(settings)

    def gatherer(heartbeat: Callable[[str], None]) -> Gatherer:
        return Gatherer(store, storage, bucket, http, config=config, heartbeat=heartbeat)

    worker = build_geo_worker(client, settings, store, gatherer)
    polling = asyncio.create_task(worker.run())
    logger.info("geo worker started", extra={"task_queue": settings.geo_task_queue})
    try:
        stopping = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({polling, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if polling in done:
            stopping.cancel()
            polling.result()
        else:
            await worker.shutdown()
            with contextlib.suppress(asyncio.CancelledError):
                await polling
    finally:
        await http.aclose()
        await engine.dispose()
        logger.info("geo worker stopped")


def main(argv: Sequence[str] | None = None) -> None:
    argparse.ArgumentParser(prog="python -m basis.geo", description=__doc__).parse_args(argv)
    settings = load_settings()
    configure_logging(settings.log_level, json=settings.env != "dev")

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run_geo_worker(settings, stop)

    asyncio.run(_run())
