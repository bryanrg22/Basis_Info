"""The embedding loop of the ingest pipeline reports progress (the activity's heartbeat)
before the first batch, after every batch, and while a batch runs (EVAL_REPORT 38).

On a fresh worker the first ``embed`` of the process loaded the model inside the first
batch: 187 s on a cold cache, 7 s warm, against the activity's 30 s heartbeat timeout, and
nothing heartbeat until the batch returned. The worker now loads the model before polling;
these tests pin the loop's own protection, which holds without it.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from basis.retrieval.embed import HashEmbedder
from basis.retrieval.ingest import EMBED_BATCH, EMBED_PROGRESS_S, _embed


class Recording(HashEmbedder):
    """Records the calls in the same log as the progress reports, with a per-batch delay
    (``time.sleep`` on the worker thread, like a real encode)."""

    def __init__(self, log: list[str], delay: float = 0.0) -> None:
        super().__init__()
        self.log = log
        self.delay = delay

    def embed(self, texts: Any) -> list[list[float]]:
        self.log.append(f"embed:{len(texts)}:{threading.current_thread().name}")
        if self.delay:
            time.sleep(self.delay)
        return super().embed(texts)


async def test_progress_precedes_the_first_batch_and_follows_every_batch() -> None:
    log: list[str] = []
    texts = [f"text {i}" for i in range(EMBED_BATCH + 1)]  # two batches
    vectors = await _embed(Recording(log), texts, log.append)
    assert len(vectors) == len(texts)
    assert vectors == HashEmbedder().embed(texts)  # the loop only batches
    n = len(texts)
    assert log[0] == f"embedding 0/{n}"  # before any embed call: covers the model load
    assert [entry for entry in log if entry.startswith("embed:")] == [
        f"embed:{EMBED_BATCH}:{log[1].split(':')[2]}",
        f"embed:1:{log[3].split(':')[2]}",
    ]
    assert log[2] == f"embedded {EMBED_BATCH}/{n}" and log[4] == f"embedded {n}/{n}"
    assert not log[1].endswith("MainThread")  # embedded on a worker thread


async def test_progress_keeps_reporting_while_a_batch_runs() -> None:
    log: list[str] = []
    started = time.perf_counter()
    await _embed(Recording(log, delay=0.5), ["one", "two"], log.append, every=0.1)
    ticks = [entry for entry in log if entry == "embedding 0/2"]
    # One before the batch, then one per 0.1 s while the 0.5 s batch runs (at least 3 of
    # the 4 possible ticks even on a loaded machine), then the completion report.
    assert len(ticks) >= 4, log
    assert log[-1] == "embedded 2/2"
    assert log.index("embed:2:" + log[1].split(":")[2]) == 1  # the batch started once
    assert time.perf_counter() - started < 2.0  # the ticker adds no wait after the batch


async def test_no_report_for_nothing_to_embed() -> None:
    log: list[str] = []
    assert await _embed(Recording(log), [], log.append) == []
    assert log == []  # a scanned appraisal has no text: nothing embedded, nothing said


async def test_a_short_count_is_an_error() -> None:
    class ShortChanged(HashEmbedder):
        def embed(self, texts: Any) -> list[list[float]]:
            return super().embed(texts)[:-1]

    with pytest.raises(RuntimeError, match="different number of vectors"):
        await _embed(ShortChanged(), ["a", "b"], lambda _: None)


async def test_cancellation_stops_the_reports() -> None:
    log: list[str] = []
    task = asyncio.ensure_future(_embed(Recording(log, delay=0.4), ["x"], log.append, every=0.05))
    await asyncio.sleep(0.12)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    reported = len(log)
    await asyncio.sleep(0.2)
    assert len(log) == reported  # no tick after the cancellation


def test_the_report_interval_fits_the_heartbeat_timeout() -> None:
    from basis.worker.main import MAX_HEARTBEAT_THROTTLE
    from basis.workflow.activity_options import options_for

    heartbeat = options_for("ingest_appraisal").heartbeat
    assert heartbeat is not None
    # Several reports per heartbeat window, and no finer than the worker sends them.
    assert EMBED_PROGRESS_S * 3 <= heartbeat.total_seconds()
    assert EMBED_PROGRESS_S >= MAX_HEARTBEAT_THROTTLE.total_seconds()
