"""The capture worker: ``python -m basis.capture`` polls ``CAPTURE_TASK_QUEUE`` for the
``PropertyCaptureWorkflow`` and its activities, one activity at a time
(``max_concurrent_activities=1``: one browser per process; the lease in Postgres keeps it one
capture at a time across processes), and writes ``capture_state.worker_seen_at`` every 30
seconds, which ``GET /v1/ops/health`` reads. It needs the ``capture`` dependency group
(Playwright) and an installed Chromium; the API and the study worker need neither."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from temporalio.client import Client
from temporalio.worker import Worker

from basis.capture.activities import CaptureActivities
from basis.capture.governor import GovernorConfig
from basis.capture.health import WORKER_BEAT
from basis.capture.pg_store import PgCaptureStore
from basis.capture.runner import CaptureRunner
from basis.capture.workflow import PropertyCaptureWorkflow
from basis.config import Settings, load_settings
from basis.db.engine import create_engine
from basis.db.session import create_sessionmaker
from basis.obs.logging import configure_logging
from basis.temporal.client import connect

logger = logging.getLogger(__name__)


def build_capture_worker(
    client: Client, settings: Settings, store: PgCaptureStore, runner: Callable[..., CaptureRunner]
) -> Worker:
    activities = CaptureActivities(runner, store)
    return Worker(
        client,
        task_queue=settings.capture_task_queue,
        workflows=[PropertyCaptureWorkflow],
        activities=[activities.capture_property, activities.fail_capture],
        max_concurrent_activities=1,
    )


async def run_capture_worker(settings: Settings, stop: asyncio.Event) -> None:
    from basis.api.app import default_storage
    from basis.capture.playwright_browser import playwright_factory

    client = await connect(settings)
    engine = create_engine(settings)
    store = PgCaptureStore(create_sessionmaker(engine))
    storage, bucket = default_storage(settings)
    holder = f"capture-{uuid.uuid4()}"
    config = GovernorConfig.of(settings)
    browser = playwright_factory(settings)

    def runner(heartbeat: Callable[[str], None]) -> CaptureRunner:
        return CaptureRunner(
            store,
            storage,
            bucket,
            browser,
            config=config,
            holder=holder,
            settle_s=settings.capture_settle_s,
            source_text=settings.capture_source_text,
            heartbeat=heartbeat,
        )

    async def beat() -> None:
        while True:
            try:
                await store.worker_seen(datetime.now(UTC))
            except Exception:
                logger.warning("capture worker heartbeat failed", exc_info=True)
            await asyncio.sleep(WORKER_BEAT.total_seconds())

    worker = build_capture_worker(client, settings, store, runner)
    beating = asyncio.create_task(beat(), name="capture-worker-seen")
    polling = asyncio.create_task(worker.run())
    logger.info(
        "capture worker started",
        extra={"task_queue": settings.capture_task_queue, "holder": holder},
    )
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
        beating.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beating
        await store.release_lease(holder)
        await engine.dispose()
        logger.info("capture worker stopped")


def main(argv: Sequence[str] | None = None) -> None:
    argparse.ArgumentParser(prog="python -m basis.capture", description=__doc__).parse_args(argv)
    settings = load_settings()
    configure_logging(settings.log_level, json=settings.env != "dev")

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run_capture_worker(settings, stop)

    asyncio.run(_run())
