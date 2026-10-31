"""``GET /v1/ops/health`` against PostgreSQL (docs/api/OPS_HEALTH.md): operator-only access,
the document built from real heartbeat rows, the outbox and the cost index store, the cache,
the rate limit, partial answers, bounded checks, and no billable call on a GET.

The worker side is real code: :class:`basis.ops.heartbeat.HeartbeatWriter` writes the rows
and :class:`basis.ops.runtime.WorkerRuntime` builds the report (its probes answered by a
mock transport). Temporal is replaced by a stub namespace check and a stub poller reader.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from apitest_support import (
    BASE_URL,
    REFUSED_DATABASE_URL,
    FakeVerifier,
    api_settings,
    bearer,
    build_api_app,
)
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.activities.deps import Deps
from basis.api.routes import ops as ops_route
from basis.config import Settings
from basis.db.engine import create_engine
from basis.db.enums import OutboxStatus
from basis.db.models import OutboxMessage, ServiceHeartbeat
from basis.db.repositories import OutboxRepository
from basis.db.session import create_sessionmaker, session_scope
from basis.errors.codes import E
from basis.llm.pool import ProviderPool
from basis.ops.evidence import Pollers
from basis.ops.heartbeat import PRUNE_AFTER, HeartbeatWriter
from basis.ops.runtime import WorkerRuntime
from basis.ops.services import SERVICE_IDS
from basis.storage import LocalStorage
from basis.worker.main import pipeline_models, room_config

pytestmark = pytest.mark.integration

OPERATOR = "ops-admin"
WORKER_KEYS = {
    "openai_api_key": "sk-test-WORKER-OPENAI-SECRET",  # pragma: allowlist secret
    "anthropic_api_key": "sk-ant-WORKER-SECRET",  # pragma: allowlist secret
    "replicate_api_token": "r8_WORKER-SECRET",  # pragma: allowlist secret
}


class Workflows:
    """The namespace check the API's /health uses."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def check(self) -> None:
        if self.fail:
            raise RuntimeError("temporal at 10.1.2.3:7233 refused")


async def pollers_ok() -> Pollers:
    return Pollers(workflow=1, activity=1)


def ops_settings(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {"ops_operator_uids": (OPERATOR,), **overrides}
    return api_settings(database_url, **values)


def make_app(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    *,
    workflows: Any = None,
    pollers: Any = pollers_ok,
) -> FastAPI:
    app = build_api_app(
        settings, sessionmaker, storage, verifier, workflows=workflows or Workflows()
    )
    app.state.ops_pollers = pollers
    return app


@pytest.fixture
def ops_app(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> FastAPI:
    return make_app(ops_settings(database_url), sessionmaker, storage, verifier)


@pytest.fixture
async def client(ops_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ops_app), base_url=BASE_URL
    ) as c:
        yield c


def worker_settings(settings: Settings) -> Settings:
    """A production-shaped worker: OpenAI for GPT models, Claude for the rest, SAM 3 on
    Replicate (tests/conftest.py pins a smaller pipeline)."""
    return settings.model_copy(
        update={
            **{k: SecretStr(v) for k, v in WORKER_KEYS.items()},
            "vision_model": "gpt-6.1-sol",
            "vision_photo_mode": "decomposed",
            "vision_count_fusion": "per_class",
            "room_reconcile": True,
            "extraction_method": "grounded",
            "llm_primary": "openai",
            "git_sha": "feedface00112233",
        }
    )


class FakeLocalizer:
    provider = "replicate"


def answered(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/account"):
        return httpx.Response(200, json={"username": "basis"})
    return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1]})


async def start_worker(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    state: str = "polling",
    probe: bool = True,
) -> tuple[HeartbeatWriter, WorkerRuntime]:
    """What basis.worker.main.run_worker writes: a heartbeat with the runtime report."""
    ws = worker_settings(settings)
    deps = Deps(
        sessionmaker=sessionmaker,
        storage=None,  # type: ignore[arg-type]
        bucket="unused",
        settings=ws,
        llm=ProviderPool.from_settings(ws),
        localizer=FakeLocalizer(),  # type: ignore[arg-type]
    )
    runtime = WorkerRuntime(
        deps,
        pipeline_models(ws, room_config(ws)),
        cost_table=None,
        transport=httpx.MockTransport(answered),
    )
    if probe:
        await runtime.probe_once()
    if state == "polling":
        runtime.embedder_warmed(None, "hash-v1-384")
        runtime.reference_warmed(None)
    else:
        runtime.embedder_warming("sentence-transformers/all-MiniLM-L6-v2")
    writer = HeartbeatWriter(
        sessionmaker, service="worker", build_sha=ws.git_sha, interval_s=15, report=runtime.report
    )
    writer.state = state  # type: ignore[assignment]
    assert await writer.beat()
    return writer, runtime


async def start_relay(sessionmaker: async_sessionmaker[AsyncSession]) -> HeartbeatWriter:
    writer = HeartbeatWriter(
        sessionmaker,
        service="relay",
        build_sha="feedface00112233",
        interval_s=15,
        report=lambda: {"poll_seconds": 2.0, "last_poll_age_s": 0.5, "healthy": True},
    )
    writer.state = "polling"
    assert await writer.beat()
    return writer


def services(response: httpx.Response) -> dict[str, dict[str, Any]]:
    body = response.json()
    assert body["schema_version"] == 1
    return {s["id"]: s for s in body["services"]}


# ------------------------------------------------------------------------ access


async def test_no_token_is_401_without_diagnostics(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/ops/health")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert "services" not in response.json()


async def test_a_rejected_token_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/ops/health", headers={"Authorization": "Bearer expired"})
    assert response.status_code == 401 and "services" not in response.json()


async def test_a_firm_owner_is_not_an_operator(client: httpx.AsyncClient) -> None:
    # The firm's owner (own_firms gives every new user a firm they own).
    me = await client.get("/v1/me", headers=bearer("owner-1"))
    assert me.status_code == 200
    response = await client.get("/v1/ops/health", headers=bearer("owner-1"))
    assert response.status_code == 403
    body = response.json()
    assert body["type"].endswith("/not-operator") and body["code"] == E.API_NOT_OPERATOR
    assert body["detail"] == ops_route.DETAIL_NOT_OPERATOR
    assert "services" not in body
    text = response.text.lower()
    for leak in ("worker", "database", "temporal", "openai"):
        assert leak not in text


async def test_an_empty_operator_list_refuses_everyone(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> None:
    app = make_app(
        api_settings(database_url), sessionmaker, storage, verifier
    )  # OPS_OPERATOR_UIDS unset
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as c:
        response = await c.get("/v1/ops/health", headers=bearer(OPERATOR))
    assert response.status_code == 403


async def test_an_operator_needs_no_database_user(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from basis.db.models import User

    response = await client.get("/v1/ops/health", headers=bearer(OPERATOR))
    assert response.status_code in (200, 503)
    async with sessionmaker() as session:
        assert (await session.execute(select(User))).first() is None


# ------------------------------------------------------------------------ the document


async def test_an_operator_reads_every_service_from_real_heartbeats(
    database_url: str, client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    settings = ops_settings(database_url)
    await start_worker(settings, sessionmaker)
    await start_relay(sessionmaker)
    response = await client.get("/v1/ops/health", headers=bearer(OPERATOR))
    assert response.headers["Cache-Control"] == "no-store"
    s = services(response)
    assert list(s) == list(SERVICE_IDS)
    for id in ("api", "db", "temporal", "worker", "relay", "storage", "firebase"):
        assert s[id]["status"] == "healthy", (id, s[id]["detail"])
    assert s["worker"]["model"] == "build feedface0011"
    # OpenAI is the primary for the GPT models; probed through the mock transport.
    assert s["openai"]["status"] == "healthy" and s["openai"]["required"] is True
    assert "gpt-6.1-sol" in s["openai"]["model"]
    assert s["anthropic"]["status"] == "healthy"
    assert s["azure_openai"]["status"] == "disabled"
    assert s["replicate"]["status"] == "healthy"
    assert s["sam3"]["status"] == "healthy" and s["sam3"]["adapter"] == "replicate"
    assert s["vision"]["status"] == "healthy"
    # The test database has no reference corpus and the worker no cost table: data, not
    # outages.
    assert s["retrieval"]["status"] == "degraded"
    assert s["rsmeans"]["status"] == "degraded"
    assert s["cost_indices"]["status"] == "down"  # nothing seeded in the test database
    assert response.status_code == 503  # cost_indices is required and down
    for item in s.values():
        if item["status"] != "unknown" and item["status"] != "disabled":
            assert item["checked_at"] and item["expires_at"], item["id"]


async def test_the_document_carries_no_secret_or_address(
    database_url: str, client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await start_worker(ops_settings(database_url), sessionmaker)
    await start_relay(sessionmaker)
    response = await client.get("/v1/ops/health", headers=bearer(OPERATOR))
    text = response.text
    for secret in WORKER_KEYS.values():
        assert secret not in text
    port = database_url.rsplit(":", 1)[-1].split("/")[0]
    assert database_url not in text and f":{port}" not in text
    assert "postgresql" not in text.lower() or "PostgreSQL" in text  # the adapter label only
    assert "10.1.2.3" not in text


async def test_a_cached_answer_keeps_its_times(
    database_url: str, ops_app: FastAPI, client: httpx.AsyncClient
) -> None:
    first = await client.get("/v1/ops/health", headers=bearer(OPERATOR))
    await asyncio.sleep(0.05)
    second = await client.get("/v1/ops/health", headers=bearer(OPERATOR))
    assert first.json() == second.json()
    cache = ops_app.state.ops_health_cache
    assert cache.evaluations == 1
    cache._value = (cache._value[0] - ops_route.CACHE_TTL_S - 1, *cache._value[1:])
    third = await client.get("/v1/ops/health", headers=bearer(OPERATOR))
    assert cache.evaluations == 2
    assert services(third)["api"]["checked_at"] > services(first)["api"]["checked_at"]


async def test_concurrent_requests_share_one_evaluation(
    ops_app: FastAPI, client: httpx.AsyncClient
) -> None:
    responses = await asyncio.gather(
        *(client.get("/v1/ops/health", headers=bearer(OPERATOR)) for _ in range(5))
    )
    assert {r.status_code for r in responses} <= {200, 503}
    assert ops_app.state.ops_health_cache.evaluations == 1


async def test_the_operator_rate_limit(client: httpx.AsyncClient) -> None:
    for _ in range(ops_route.OPS_RATE_LIMIT_PER_MIN):
        assert (await client.get("/v1/ops/health", headers=bearer(OPERATOR))).status_code in (
            200,
            503,
        )
    limited = await client.get("/v1/ops/health", headers=bearer(OPERATOR))
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
    assert "services" not in limited.json()


# ------------------------------------------------------------------------ outages


async def test_a_database_outage_is_a_partial_503(
    database_url: str, storage: LocalStorage, verifier: FakeVerifier
) -> None:
    engine = create_engine(api_settings(REFUSED_DATABASE_URL))
    try:
        app = make_app(
            ops_settings(REFUSED_DATABASE_URL), create_sessionmaker(engine), storage, verifier
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE_URL
        ) as c:
            response = await c.get("/v1/ops/health", headers=bearer(OPERATOR))
    finally:
        await engine.dispose()
    assert response.status_code == 503
    s = services(response)
    assert s["db"]["status"] == "down"
    assert s["worker"]["status"] == "unknown" and s["relay"]["status"] == "unknown"
    assert s["api"]["status"] == "healthy" and s["storage"]["status"] == "healthy"
    assert s["firebase"]["status"] == "healthy"
    assert "127.0.0.1" not in response.text and ":1/" not in response.text


async def test_a_hanging_poller_check_is_bounded(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> None:
    async def hang() -> Pollers:
        await asyncio.sleep(60)
        raise AssertionError("unreachable")

    settings = ops_settings(database_url)
    await start_worker(settings, sessionmaker)
    await start_relay(sessionmaker)
    app = make_app(
        settings, sessionmaker, storage, verifier, pollers=hang, workflows=Workflows(fail=True)
    )
    started = time.monotonic()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as c:
        response = await c.get("/v1/ops/health", headers=bearer(OPERATOR))
    assert time.monotonic() - started < 5
    s = services(response)
    assert s["worker"]["status"] == "degraded"
    assert "poller check unavailable" in s["worker"]["detail"]
    assert s["temporal"]["status"] == "down" and "10.1.2.3" not in response.text


async def test_worker_cold_start(
    database_url: str, client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await start_worker(ops_settings(database_url), sessionmaker, state="warming", probe=False)
    await start_relay(sessionmaker)
    s = services(await client.get("/v1/ops/health", headers=bearer(OPERATOR)))
    assert s["worker"]["status"] == "degraded" and "Cold start" in s["worker"]["detail"]
    assert s["embeddings"]["status"] == "degraded"
    assert s["openai"]["status"] == "unknown"  # not probed yet


async def test_relay_backlog_and_dead_messages(
    database_url: str, client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await start_worker(ops_settings(database_url), sessionmaker)
    await start_relay(sessionmaker)
    async with session_scope(sessionmaker) as session:
        repo = OutboxRepository(session)
        late = await repo.enqueue("decision", {"n": 1})
        dead = await repo.enqueue("decision", {"n": 2})
        await session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == late.id)
            .values(next_attempt_at=datetime.now(UTC) - timedelta(minutes=5))
        )
        await session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == dead.id)
            .values(status=OutboxStatus.DEAD, last_error="x")
        )
    relay = services(await client.get("/v1/ops/health", headers=bearer(OPERATOR)))["relay"]
    assert relay["status"] == "degraded"
    assert "1 pending, 1 due" in relay["detail"] and "1 dead message(s)" in relay["detail"]
    assert E.INF_RELAY_DEAD in relay["detail"] and "delivery is behind" in relay["detail"]


async def test_no_relay_heartbeat_is_down(
    database_url: str, client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await start_worker(ops_settings(database_url), sessionmaker)
    response = await client.get("/v1/ops/health", headers=bearer(OPERATOR))
    assert services(response)["relay"]["status"] == "down"
    assert response.status_code == 503


# ------------------------------------------------------------------------ nothing billable


async def test_a_get_makes_no_outbound_or_billable_call(
    database_url: str,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    ops_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every network transport and every paid entry point fails the test if touched."""
    await start_worker(ops_settings(database_url), sessionmaker)
    await start_relay(sessionmaker)
    touched: list[str] = []

    def record(name: str) -> Any:
        async def async_refuse(*args: Any, **kwargs: Any) -> Any:
            touched.append(name)
            raise AssertionError(f"{name} called during GET /v1/ops/health")

        def refuse(*args: Any, **kwargs: Any) -> Any:
            touched.append(name)
            raise AssertionError(f"{name} called during GET /v1/ops/health")

        return async_refuse, refuse

    import urllib3

    from basis.extraction.azure_di import AzureDocumentIntelligence
    from basis.llm.anthropic_provider import AnthropicProvider
    from basis.llm.providers import OpenAICompatibleProvider

    async_refuse, _ = record("httpx async transport")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_refuse)
    _, refuse = record("httpx sync transport")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    _, refuse = record("urllib3")
    monkeypatch.setattr(urllib3.connectionpool.HTTPConnectionPool, "urlopen", refuse)
    for cls, method in (
        (ProviderPool, "complete"),
        (OpenAICompatibleProvider, "complete"),
        (AnthropicProvider, "complete"),
        (AzureDocumentIntelligence, "analyze"),
    ):
        async_refuse, _ = record(f"{cls.__name__}.{method}")
        monkeypatch.setattr(cls, method, async_refuse, raising=False)

    class Localizer:
        def __getattr__(self, name: str) -> Any:
            touched.append(f"localizer.{name}")
            raise AssertionError("localizer used")

    ops_app.state.localizer = Localizer()
    response = await client.get("/v1/ops/health", headers=bearer(OPERATOR))
    assert response.status_code in (200, 503)
    assert touched == []
    assert len(services(response)) == len(SERVICE_IDS)


# ------------------------------------------------------------------------ the heartbeat row


async def test_the_heartbeat_upserts_one_row_and_prunes_dead_ones(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = [datetime.now(UTC)]
    writer = HeartbeatWriter(
        sessionmaker,
        service="relay",
        build_sha="abc",
        interval_s=15,
        report=lambda: {"healthy": True},
        clock=lambda: now[0],
    )
    old = HeartbeatWriter(
        sessionmaker,
        service="relay",
        build_sha="old",
        interval_s=15,
        clock=lambda: now[0] - PRUNE_AFTER - timedelta(minutes=1),
    )
    assert await old.beat()
    assert await writer.beat()
    now[0] += timedelta(seconds=15)
    writer.set_state("draining")
    assert await writer.beat()
    async with sessionmaker() as session:
        rows = (await session.execute(select(ServiceHeartbeat))).scalars().all()
    assert [(r.id, r.state, r.build_sha) for r in rows] == [(writer.instance_id, "draining", "abc")]
    [row] = rows
    assert row.heartbeat_at == now[0]
    assert row.report == {"healthy": True, "heartbeat_interval_s": 15}


async def test_a_failed_heartbeat_write_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_engine(api_settings(REFUSED_DATABASE_URL))
    try:
        writer = HeartbeatWriter(
            create_sessionmaker(engine), service="worker", build_sha="x", interval_s=15
        )
        with caplog.at_level("WARNING", logger="basis.ops.heartbeat"):
            assert await writer.beat() is False
    finally:
        await engine.dispose()
    [record] = [r for r in caplog.records if r.message == "heartbeat write failed"]
    assert record.error_code == E.INF_HEARTBEAT_WRITE  # type: ignore[attr-defined]
    assert "127.0.0.1" not in json.dumps(record.__dict__, default=str)


async def test_the_loop_writes_a_state_change_at_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    writer = HeartbeatWriter(sessionmaker, service="worker", build_sha="x", interval_s=300)
    writer.start()
    try:
        writer.set_state("polling")
        for _ in range(100):
            async with sessionmaker() as session:
                state = (await session.execute(select(ServiceHeartbeat.state))).scalar_one_or_none()
            if state == "polling":
                break
            await asyncio.sleep(0.02)
        assert state == "polling"
    finally:
        await writer.stop()
    async with sessionmaker() as session:
        assert (await session.execute(select(ServiceHeartbeat.state))).scalar_one() == "stopped"
