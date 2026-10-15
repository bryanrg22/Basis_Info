"""``python -m basis.worker`` as a real process against a real Temporal dev server.

S2_SPEC sections 5 and 10: the worker process connects with ``TEMPORAL_ADDRESS``,
registers the search attributes on the dev server, serves ``/health`` (503 until it
polls, then 200) and Prometheus ``/metrics``, runs a synthetic study end to end through
its real activities and Postgres, and on SIGTERM drains and exits 0 well inside Cloud
Run's 10 s grace period.

Uses the installed ``temporal`` CLI (``WorkflowEnvironment.start_local`` with
``dev_server_existing_path``); skipped when it is not on ``PATH``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import urllib.request
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from dbtest_support import BACKEND_ROOT, db_settings, truncate_all
from lifecycletest_support import seed_study
from temporalio.client import WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from basis.db.engine import create_engine
from basis.db.enums import Stage, StudyStatus
from basis.db.models import Study
from basis.db.session import create_sessionmaker, session_scope
from basis.workflow.contracts import Decision, StudyWorkflowInput, StudyWorkflowResult
from basis.workflow.study_workflow import StudyWorkflow, workflow_id_for

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("temporal") is None, reason="temporal CLI not installed"),
]

TASK_QUEUE = "basis-studies-process-test"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - localhost only
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


@pytest_asyncio.fixture
async def dev_server() -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal"),
        data_converter=pydantic_data_converter,
    )
    try:
        yield env
    finally:
        await env.shutdown()


@pytest.fixture
def ports() -> tuple[int, int]:
    return free_port(), free_port()


@pytest.fixture
def worker_process(
    dev_server: WorkflowEnvironment, database_url: str, ports: tuple[int, int], tmp_path: Path
) -> Iterator[subprocess.Popen[str]]:
    health_port, metrics_port = ports
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "BASIS_ENV": "test",
        "DATABASE_URL": database_url,
        "TEMPORAL_ADDRESS": dev_server.client.service_client.config.target_host,
        "TEMPORAL_TASK_QUEUE": TASK_QUEUE,
        "GIT_SHA": "process-test",
        "LOG_LEVEL": "INFO",
    }
    log = tmp_path / "worker.log"
    with log.open("w") as out:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "basis.worker",
                "--health-port",
                str(health_port),
                "--metrics-port",
                str(metrics_port),
            ],
            cwd=BACKEND_ROOT,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(10)


async def wait_healthy(port: int, process: subprocess.Popen[str]) -> None:
    for _ in range(300):
        assert process.poll() is None, "worker exited during startup"
        try:
            if get(f"http://127.0.0.1:{port}/health")[0] == 200:
                return
        except OSError:
            pass
        await asyncio.sleep(0.1)
    raise AssertionError("worker never became healthy")


async def wait_gate(handle: WorkflowHandle[StudyWorkflow, StudyWorkflowResult], stage: str) -> None:
    for _ in range(300):
        status = await handle.query(StudyWorkflow.status)
        if status.awaiting == stage:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"never reached {stage}")


async def test_worker_process_runs_a_study_and_drains_on_sigterm(
    dev_server: WorkflowEnvironment,
    worker_process: subprocess.Popen[str],
    database_url: str,
    ports: tuple[int, int],
) -> None:
    health_port, metrics_port = ports
    await wait_healthy(health_port, worker_process)

    engine = create_engine(db_settings(database_url))
    try:
        await truncate_all(engine)
        sessionmaker = create_sessionmaker(engine)
        study = await seed_study(sessionmaker, photos=("kitchen_1.jpg", "bathroom_1.jpg"))
        client = dev_server.client
        handle = await client.start_workflow(
            StudyWorkflow.run,
            StudyWorkflowInput(
                study_id=str(study.study_id),
                org_id=str(study.org_id),
                input_revision=1,
                workflow_version="process-test",
                budget_usd=25.0,
            ),
            id=workflow_id_for(str(study.study_id)),
            task_queue=TASK_QUEUE,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        for stage in ("appraisal_review", "rooms_review", "assets_review"):
            await wait_gate(handle, stage)
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
            )
        result = await asyncio.wait_for(handle.result(), 60)
        assert result.outcome == "completed"
        async with session_scope(sessionmaker) as session:
            row = await session.get(Study, study.study_id)
            assert row is not None
            assert (row.current_stage, row.status) == (Stage.COMPLETED, StudyStatus.COMPLETED)

        # Search attributes were registered by the worker and upserted by the workflow.
        description = await handle.describe()
        stage_values = [
            value
            for key, value in description.typed_search_attributes
            if key.name == "Stage"  # type: ignore[attr-defined]
        ]
        assert stage_values == ["completed"]

        status, metrics = get(f"http://127.0.0.1:{metrics_port}/metrics")
        assert status == 200 and "temporal_" in metrics
    finally:
        await engine.dispose()

    loop = asyncio.get_running_loop()
    began = loop.time()
    worker_process.send_signal(signal.SIGTERM)
    code = await loop.run_in_executor(None, worker_process.wait, 15)
    assert code == 0
    assert loop.time() - began < 10
