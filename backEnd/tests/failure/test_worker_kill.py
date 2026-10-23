"""The worker process dies or is stopped while an activity is mid-flight (S2_SPEC 9).

Real processes, as in production: worker subprocesses running ``basis.worker.main`` with
the offline providers composed in (``kill_worker.py``; the pattern of
``tests/integration/worker/test_worker_process.py``) against a Temporal dev server
(``WorkflowEnvironment.start_local`` with the installed ``temporal`` CLI) and the
session's Postgres container. A synthetic study is started whose first photo's
``analyze_photo`` sleeps inside its work transaction (``SimulateFault``, honoured because
the worker runs with ``BASIS_ENV=test``), after its room row is inserted.

* SIGKILL: the worker is killed while that transaction is open (checked in
  ``pg_stat_activity``). PostgreSQL rolls it back; nothing of the photo is left. A new
  worker takes over: the workflow resumes from its history, the photo activity is retried
  once its 30 s heartbeat timeout expires, the study is approved and exported. Every
  ``activity_results`` key appears once, ``completed``, matching the activities the
  history started; rooms, photo assignments, components and assets have no duplicates;
  the export is written and its sha256 matches the result.
* SIGTERM: the worker logs the drain, stops polling and exits 0 within Cloud Run's 10 s.
  Either the in-flight activity finishes inside the 8 s drain (attempt 1 completes), or
  the drain runs out, the activity is cancelled and rolled back, and the next worker runs
  it once more (attempt 2). Both cases end with the same single set of rows.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from dbtest_support import BACKEND_ROOT, db_settings, truncate_all
from faulttest_support import no_open_transactions, wait_for_writing_transaction
from lifecycletest_support import seed_reference_corpus, seed_study
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from temporalio.client import WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from basis.db.engine import create_engine
from basis.db.models import ActivityResult, Asset, Component, PhotoRoom, Room
from basis.db.repositories.activity_result import COMPLETED
from basis.db.session import create_sessionmaker, session_scope
from basis.storage import LocalStorage
from basis.workflow.contracts import (
    Decision,
    SimulateFault,
    StudyWorkflowInput,
    StudyWorkflowResult,
)
from basis.workflow.study_workflow import StudyWorkflow, workflow_id_for

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("temporal") is None, reason="temporal CLI not installed"),
    # The SIGKILL case waits out analyze_photo's 30 s heartbeat timeout.
    pytest.mark.timeout(240),
]

TASK_QUEUE = "basis-studies-kill-test"
# The production worker with the offline providers composed in (see its docstring).
KILL_WORKER = Path(__file__).with_name("kill_worker.py")
PHOTOS = ("Kitchen photo 1.jpg", "Bathroom photo 1.jpg", "Kitchen photo 2.jpg")
# The slowed photo is the only one of its room, so its transaction inserts the room row
# (it has written when the worker dies) and the room's advisory lock it holds while
# sleeping does not make the kitchen photos wait for it.
SLOW_PHOTO = "Bathroom photo 1.jpg"
DRAIN_LINE = "shutdown requested; draining for up to 8s"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - localhost only
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code


@dataclass
class WorkerProcess:
    process: subprocess.Popen[str]
    log: Path
    health_port: int

    def lines(self) -> list[dict[str, Any]]:
        """The worker's JSON log lines (non-JSON output, e.g. a traceback, is skipped)."""
        parsed = []
        for line in self.log.read_text().splitlines():
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return parsed

    def messages(self) -> list[str]:
        return [str(line.get("message")) for line in self.lines()]

    async def healthy(self) -> None:
        for _ in range(300):
            assert self.process.poll() is None, f"worker exited:\n{self.log.read_text()}"
            try:
                if http_status(f"http://127.0.0.1:{self.health_port}/health") == 200:
                    return
            except OSError:
                pass
            await asyncio.sleep(0.1)
        raise AssertionError(f"worker never became healthy:\n{self.log.read_text()}")

    async def wait(self, timeout: float) -> int:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.process.wait, timeout)


@dataclass
class Rig:
    server: WorkflowEnvironment
    database_url: str
    tmp_path: Path
    sessionmaker: async_sessionmaker[AsyncSession]
    workers: list[WorkerProcess]

    @property
    def storage(self) -> LocalStorage:
        # The worker process's LocalStorage root: tempfile.gettempdir() is TMPDIR.
        return LocalStorage(self.tmp_path / "basis-local-storage")

    async def worker(self, name: str) -> WorkerProcess:
        health_port = free_port()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "TMPDIR": str(self.tmp_path),
            "BASIS_ENV": "test",
            "DATABASE_URL": self.database_url,
            "TEMPORAL_ADDRESS": self.server.client.service_client.config.target_host,
            "TEMPORAL_TASK_QUEUE": TASK_QUEUE,
            "GIT_SHA": "kill-test",
            "LOG_LEVEL": "INFO",
        }
        log = self.tmp_path / f"{name}.log"
        with log.open("w") as out:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(KILL_WORKER),
                    "--health-port",
                    str(health_port),
                    "--metrics-port",
                    str(free_port()),
                ],
                cwd=BACKEND_ROOT,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                text=True,
            )
        worker = WorkerProcess(process=process, log=log, health_port=health_port)
        self.workers.append(worker)
        await worker.healthy()
        return worker


@pytest_asyncio.fixture
async def rig(database_url: str, tmp_path: Path) -> AsyncIterator[Rig]:
    server = await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal"),
        data_converter=pydantic_data_converter,
        dev_server_log_level="error",
    )
    engine: AsyncEngine = create_engine(db_settings(database_url))
    rig = Rig(server, database_url, tmp_path, create_sessionmaker(engine), [])
    try:
        await truncate_all(engine)
        yield rig
    finally:
        for worker in rig.workers:
            if worker.process.poll() is None:
                worker.process.kill()
                worker.process.wait(10)
        await truncate_all(engine)
        await engine.dispose()
        await server.shutdown()


# --------------------------------------------------------------------------- helpers


async def start_study(rig: Rig, sleep_seconds: float) -> tuple[Any, str, WorkflowHandle[Any, Any]]:
    await seed_reference_corpus(rig.sessionmaker)  # resolved by the worker's Postgres store
    # Real files in the worker process's storage: ingest_appraisal and analyze_photo read them.
    study = await seed_study(rig.sessionmaker, photos=PHOTOS, storage=rig.storage)
    slow = str(study.photo_ids[PHOTOS.index(SLOW_PHOTO)])
    handle = await rig.server.client.start_workflow(
        StudyWorkflow.run,
        StudyWorkflowInput(
            study_id=str(study.study_id),
            org_id=str(study.org_id),
            input_revision=1,
            workflow_version="kill-test",
            budget_usd=25.0,
            simulate_photos={slow: SimulateFault(sleep_seconds=sleep_seconds)},
        ),
        id=workflow_id_for(str(study.study_id)),
        task_queue=TASK_QUEUE,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    return study, slow, handle


async def wait_status(
    handle: WorkflowHandle[Any, Any], predicate: Any, what: str, timeout: float = 90.0
) -> Any:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last = None
    while loop.time() < deadline:
        try:
            last = await handle.query(StudyWorkflow.status)
            if predicate(last):
                return last
        except Exception:  # no worker answering queries right now
            pass
        await asyncio.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}; last status: {last}")


async def photo_in_flight(rig: Rig, handle: WorkflowHandle[Any, Any]) -> None:
    """The other photos are done and the slow one is inside its write transaction."""
    await wait_status(
        handle,
        lambda s: s.completed_photos == len(PHOTOS) - 1 and s.pending_photos == 1,
        "only the slow photo in flight",
    )
    await wait_for_writing_transaction(rig.sessionmaker)


async def approve_and_finish(handle: WorkflowHandle[Any, Any]) -> StudyWorkflowResult:
    # v0.12: the components review follows the rooms review (patch components-gate-v1).
    for stage in ("appraisal_review", "rooms_review", "components_review", "assets_review"):
        await wait_status(handle, lambda s, stage=stage: s.awaiting == stage, stage)
        await handle.execute_update(
            StudyWorkflow.decide,
            Decision(
                review_id=f"review-{stage}",
                stage=stage,  # type: ignore[arg-type]
                revision=1,
                decision="approve",
                reviewer_id="reviewer",
                submitted_at=datetime.now(UTC),
            ),
            id=f"decision-review-{stage}",
        )
    result: StudyWorkflowResult = await asyncio.wait_for(handle.result(), 90)
    return result


async def counts(sm: async_sessionmaker[AsyncSession], study_id: uuid.UUID) -> dict[str, int]:
    async with session_scope(sm) as session:

        async def n(stmt: Any) -> int:
            return int(await session.scalar(stmt) or 0)

        return {
            "rooms": await n(
                select(func.count()).select_from(Room).where(Room.study_id == study_id)
            ),
            "room_names": await n(
                select(func.count(func.distinct(Room.name))).where(Room.study_id == study_id)
            ),
            "photo_rooms": await n(
                select(func.count()).select_from(PhotoRoom).where(PhotoRoom.study_id == study_id)
            ),
            "components": await n(
                select(func.count()).select_from(Component).where(Component.study_id == study_id)
            ),
            "assets": await n(
                select(func.count()).select_from(Asset).where(Asset.study_id == study_id)
            ),
            "asset_components": await n(
                select(func.count(func.distinct(Asset.component_id))).where(
                    Asset.study_id == study_id
                )
            ),
        }


async def photo_rows(sm: async_sessionmaker[AsyncSession], photo: str) -> int:
    async with session_scope(sm) as session:
        stmt = (
            select(func.count())
            .select_from(PhotoRoom)
            .where(PhotoRoom.document_id == uuid.UUID(photo))
        )
        return int(await session.scalar(stmt) or 0)


async def started_attempts(handle: WorkflowHandle[Any, Any]) -> dict[str, int]:
    """Final attempt number of every activity id the history started."""
    history = await handle.fetch_history()
    scheduled = {
        e.event_id: e.activity_task_scheduled_event_attributes.activity_id
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    }
    started: Counter[str] = Counter()
    attempts: dict[str, int] = {}
    for e in history.events:
        if e.HasField("activity_task_started_event_attributes"):
            attrs = e.activity_task_started_event_attributes
            activity_id = scheduled[attrs.scheduled_event_id]
            started[activity_id] += 1
            attempts[activity_id] = attrs.attempt
    assert set(started.values()) == {1}, started  # one execution per activity id
    return attempts


async def assert_recovered(
    rig: Rig, study: Any, handle: WorkflowHandle[Any, Any], result: StudyWorkflowResult
) -> dict[str, int]:
    assert result.outcome == "completed" and result.flagged_photos == ()
    # The export exists and is the document the result describes.
    assert result.export_uri is not None and result.export_sha256 is not None
    data = rig.storage.path_for(result.export_uri).read_bytes()
    assert hashlib.sha256(data).hexdigest() == result.export_sha256

    attempts = await started_attempts(handle)
    async with session_scope(rig.sessionmaker) as session:
        rows = (
            await session.scalars(
                select(ActivityResult).where(
                    ActivityResult.idempotency_key.startswith(f"{handle.id}:")
                )
            )
        ).all()
    keys = [row.idempotency_key for row in rows]
    assert len(keys) == len(set(keys))
    assert {row.status for row in rows} == {COMPLETED}
    # Every activity the history started recorded its key once (the keys are
    # ``{workflow_id}:{run_id}:{activity_id}``).
    assert sorted(key.split(":", 2)[2] for key in keys) == sorted(attempts)

    outputs = await counts(rig.sessionmaker, study.study_id)
    assert outputs == {
        "rooms": 2,  # kitchen, bathroom
        "room_names": 2,
        "photo_rooms": len(PHOTOS),
        # The worker process runs the production defaults, room step included
        # (EVAL_REPORT 16.5): two per-photo rows per photo plus one inventory per room
        # (kitchen: cabinets and countertop, counted once over its two photos; bathroom:
        # bathtub and toilet), and the assets are built from the four inventory rows.
        "components": 2 * len(PHOTOS) + 4,
        "assets": 4,
        "asset_components": 4,
    }
    return attempts


# --------------------------------------------------------------------------- tests


async def test_sigkill_mid_activity_then_a_new_worker_completes_the_study(rig: Rig) -> None:
    first = await rig.worker("worker-1")
    study, slow, handle = await start_study(rig, sleep_seconds=8)
    await photo_in_flight(rig, handle)

    first.process.send_signal(signal.SIGKILL)
    assert await first.wait(10) == -signal.SIGKILL
    # The killed worker's open transaction is rolled back by the server.
    await no_open_transactions(rig.sessionmaker)
    assert await photo_rows(rig.sessionmaker, slow) == 0
    assert DRAIN_LINE not in first.messages()  # no chance to drain

    second = await rig.worker("worker-2")
    result = await approve_and_finish(handle)

    attempts = await assert_recovered(rig, study, handle, result)
    assert attempts[f"analyze_photo:{slow}:r1"] == 2  # killed once, retried once
    assert "worker started" in second.messages()


@pytest.mark.parametrize(
    ("sleep_seconds", "expected_attempt"),
    [(5, 1), (16, 2)],
    ids=["finishes-inside-the-drain", "drain-runs-out"],
)
async def test_sigterm_drains_then_a_new_worker_completes_the_study(
    rig: Rig, sleep_seconds: float, expected_attempt: int
) -> None:
    first = await rig.worker("worker-1")
    study, slow, handle = await start_study(rig, sleep_seconds=sleep_seconds)
    await photo_in_flight(rig, handle)

    loop = asyncio.get_running_loop()
    began = loop.time()
    first.process.send_signal(signal.SIGTERM)
    assert await first.wait(15) == 0
    assert loop.time() - began < 10  # inside Cloud Run's grace period
    messages = first.messages()
    assert DRAIN_LINE in messages
    assert messages.index(DRAIN_LINE) < messages.index("worker stopped")
    await no_open_transactions(rig.sessionmaker)
    # Finished inside the drain: its rows are committed. Cancelled: rolled back.
    assert await photo_rows(rig.sessionmaker, slow) == (1 if expected_attempt == 1 else 0)

    await rig.worker("worker-2")
    result = await approve_and_finish(handle)

    attempts = await assert_recovered(rig, study, handle, result)
    assert attempts[f"analyze_photo:{slow}:r1"] == expected_attempt
