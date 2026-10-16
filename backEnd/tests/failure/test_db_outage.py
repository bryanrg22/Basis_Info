"""The database goes away: activities retry without partial rows, the API answers 503.

BUILD_PLAN 6.9 and S2_SPEC section 9 (``test_db_outage.py``). The outage is real, not
mocked: the code under test reaches PostgreSQL through :class:`TcpProxy`, a TCP proxy in
front of the test container. ``stop()`` closes the listener and aborts every connection
through it (both sides, so the server rolls back what was in flight), after which a new
connection is refused, as with a database that is down or partitioned away; ``start()``
listens on the same port again. The tests read the database directly, never through the
proxy.

* Engine: connection-establishment failures become ``DatabaseUnavailableError``; an
  authentication failure and an error on an established connection do not; the
  translation wraps only ``asyncpg.connect``, after every ``do_connect`` listener.
* Activities, on the time-skipping test server with the real stub activities and a
  worker whose ``Deps`` use the proxied engine:
  - the database is unreachable for the whole first attempt of ``process_assets`` /
    ``export_report``: the attempt fails before claiming its key, nothing is written,
    the activity is retried under its retry policy and the study completes;
  - the connection is lost in the middle of ``analyze_photo``'s work transaction (after
    its room row is inserted): the server rolls the transaction back, the claim row is
    left ``running`` (recording the failure needs the database too), and the retry
    completes the same key with exactly one set of rows.
  Each injected attempt is gated with events, so the retry starts only after the test
  has inspected the database and brought it back: exactly two attempts, every time.
* API: ``POST /v1/studies/{id}/decisions`` is a 503 problem with ``Retry-After`` (not a
  500) when the database is unreachable, cold or after a working start; the same
  idempotency key succeeds once it is back; a failing query is still a 500.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from apitest_support import (
    BASE_URL,
    REFUSED_DATABASE_URL,
    Api,
    FakeVerifier,
    Seed,
    api_settings,
    build_api_app,
)
from dbtest_support import db_settings, execute_admin, truncate_all
from faulttest_support import no_open_transactions, writing_transactions
from lifecycle_support import Harness
from lifecycletest_support import make_deps, seed_reference_corpus
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from basis.activities import build_activities
from basis.api.errors import DB_UNAVAILABLE_DETAIL, PROBLEM_JSON, as_api_error
from basis.db.engine import connect_or_unavailable, create_engine
from basis.db.enums import Stage
from basis.db.errors import DatabaseUnavailableError
from basis.db.models import (
    ActivityResult,
    Asset,
    Citation,
    Component,
    CostEstimate,
    OutboxMessage,
    PhotoRoom,
    Review,
    Room,
    Takeoff,
)
from basis.db.repositories.activity_result import COMPLETED, RUNNING
from basis.db.session import create_sessionmaker, session_scope
from basis.domain.rules import lookup
from basis.storage import LocalStorage
from basis.worker.search_attributes import ensure_search_attributes
from basis.workflow.activity_options import options_for
from basis.workflow.contracts import (
    AnalyzePhotoInput,
    AnalyzePhotoOutput,
    ExportReportInput,
    ExportReportOutput,
    ExtractFieldsInput,
    ExtractFieldsOutput,
    ProcessAssetsInput,
    ProcessAssetsOutput,
    SimulateFault,
)

pytestmark = pytest.mark.integration

IO_TYPES: dict[str, tuple[type[Any], type[Any]]] = {
    "analyze_photo": (AnalyzePhotoInput, AnalyzePhotoOutput),
    "extract_fields": (ExtractFieldsInput, ExtractFieldsOutput),
    "process_assets": (ProcessAssetsInput, ProcessAssetsOutput),
    "export_report": (ExportReportInput, ExportReportOutput),
}


# --------------------------------------------------------------------------- the proxy


class TcpProxy:
    """A TCP proxy on 127.0.0.1 that can be taken down and brought back on the same port."""

    def __init__(self, target_host: str, target_port: int) -> None:
        self.target = (target_host, target_port)
        self.port = 0
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def running(self) -> bool:
        return self._server is not None

    async def start(self) -> None:
        assert self._server is None, "already running"
        self._server = await asyncio.start_server(
            self._accept, "127.0.0.1", self.port, reuse_address=True
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        for writer in list(self._writers):
            writer.transport.abort()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await server.wait_closed()

    async def _accept(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        try:
            server_r, server_w = await asyncio.open_connection(*self.target)
        except OSError:
            client_w.transport.abort()
            return
        self._writers.update((client_w, server_w))
        pipes = [
            asyncio.create_task(self._pipe(client_r, server_w)),
            asyncio.create_task(self._pipe(server_r, client_w)),
        ]
        self._tasks.update(pipes)
        try:
            await asyncio.gather(*pipes, return_exceptions=True)
        finally:
            self._tasks.difference_update(pipes)
            for writer in (client_w, server_w):
                self._writers.discard(writer)
                writer.transport.abort()

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(ConnectionError, OSError):
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        writer.transport.abort()


@pytest.fixture
async def proxy(database_url: str) -> AsyncIterator[TcpProxy]:
    url = make_url(database_url)
    assert url.host is not None and url.port is not None
    tcp = TcpProxy(url.host, url.port)
    await tcp.start()
    try:
        yield tcp
    finally:
        await tcp.stop()


def via(database_url: str, proxy: TcpProxy) -> str:
    """``database_url`` rewritten to go through ``proxy``."""
    url = make_url(database_url).set(host="127.0.0.1", port=proxy.port)
    return url.render_as_string(hide_password=False)


@pytest.fixture
async def proxied_engine(database_url: str, proxy: TcpProxy) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(db_settings(via(database_url, proxy), db_pool_size=10))
    try:
        yield engine
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- engine


async def test_connection_errors_are_translated_and_query_errors_are_not(
    database_url: str, proxied_engine: AsyncEngine, proxy: TcpProxy
) -> None:
    # Refused (nothing listens): translated, with the driver error as the cause and no
    # address or credentials in the message.
    refused = create_engine(db_settings(REFUSED_DATABASE_URL))
    try:
        with pytest.raises(DatabaseUnavailableError) as caught:
            async with refused.connect() as connection:
                await connection.execute(text("SELECT 1"))
        assert isinstance(caught.value.__cause__, ConnectionRefusedError)
        assert str(caught.value) == "could not connect to the database (ConnectionRefusedError)"
    finally:
        await refused.dispose()

    # Wrong password: a configuration fault, passed through unchanged.
    bad = make_url(database_url).set(password="wrong-password")  # pragma: allowlist secret
    wrong = create_engine(db_settings(bad.render_as_string(hide_password=False)))
    try:
        with pytest.raises(DBAPIError) as caught_auth:
            async with wrong.connect() as connection:
                await connection.execute(text("SELECT 1"))
        assert not isinstance(caught_auth.value, DatabaseUnavailableError)
        assert getattr(caught_auth.value.orig, "sqlstate", None) == "28P01"
    finally:
        await wrong.dispose()

    # The server refuses a connection right now (SQLSTATE 53300, here a role whose
    # connection limit is 0): an asyncpg PostgresError, adapted by SQLAlchemy at connect
    # time, translated through its cause.
    role = f"no_slots_{uuid.uuid4().hex[:8]}"
    await execute_admin(database_url, f"CREATE ROLE {role} LOGIN PASSWORD 'pw' CONNECTION LIMIT 0")
    limited = create_engine(
        db_settings(
            make_url(database_url)
            .set(username=role, password="pw")  # pragma: allowlist secret
            .render_as_string(hide_password=False)
        )
    )
    try:
        with pytest.raises(DatabaseUnavailableError) as caught_busy:
            async with limited.connect() as connection:
                await connection.execute(text("SELECT 1"))
        assert str(caught_busy.value).endswith("(TooManyConnectionsError)")
    finally:
        await limited.dispose()
        await execute_admin(database_url, f"DROP ROLE {role}")

    # A connection lost on an established connection is a query error: not translated.
    async with proxied_engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
        await proxy.stop()
        with pytest.raises(DBAPIError) as lost:
            await connection.execute(text("SELECT 1"))
        assert not isinstance(lost.value, DatabaseUnavailableError)
        assert lost.value.connection_invalidated
    # While it is down, a new connection (pre-ping replaces the dead one) is unavailable.
    with pytest.raises(DatabaseUnavailableError):
        async with proxied_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    # And it recovers by itself once the database is back.
    await proxy.start()
    async with proxied_engine.connect() as connection:
        assert (await connection.execute(text("SELECT 1"))).scalar() == 1


async def test_do_connect_listeners_run_before_the_translating_connect() -> None:
    """The wrapper replaces only ``asyncpg.connect``: a ``do_connect`` listener still sees
    (and could change) every parameter, including the wrapper itself."""
    engine = create_engine(db_settings("postgresql://u:x@db.invalid:5432/d"))
    seen: dict[str, Any] = {}

    class Stop(Exception):
        pass

    def capture(dialect: Any, record: Any, cargs: Any, cparams: dict[str, Any]) -> None:
        seen.update(cparams)
        raise Stop

    event.listen(engine.sync_engine, "do_connect", capture)
    try:
        with pytest.raises(Stop):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()
    assert seen["async_creator_fn"] is connect_or_unavailable
    assert seen["host"] == "db.invalid" and "statement_timeout" in seen["server_settings"]


def test_only_the_translated_error_maps_to_503() -> None:
    mapped = as_api_error(DatabaseUnavailableError("could not connect"))
    assert mapped is not None and mapped.status == 503
    assert mapped.headers["Retry-After"] == "5" and mapped.detail == DB_UNAVAILABLE_DETAIL
    for raw in (ConnectionRefusedError(), OSError(), TimeoutError(), RuntimeError("query")):
        assert as_api_error(raw) is None


# --------------------------------------------------------------------------- API


async def _decision_counts(sm: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with sm() as session:
        reviews = await session.scalar(select(func.count()).select_from(Review))
        outbox = await session.scalar(select(func.count()).select_from(OutboxMessage))
    return int(reviews or 0), int(outbox or 0)


def _assert_503(response: httpx.Response) -> None:
    assert response.status_code == 503, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.headers["retry-after"] == "5"
    body = response.json()
    assert body["type"].endswith("/service-unavailable")
    assert body["detail"] == DB_UNAVAILABLE_DETAIL
    assert body["request_id"] == response.headers["x-request-id"]
    assert "127.0.0.1" not in response.text and "password" not in response.text.lower()


@contextlib.asynccontextmanager
async def _api(
    database_url: str, sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> AsyncIterator[Api]:
    app = build_api_app(
        api_settings(database_url), sessionmaker, LocalStorage(tmp_path), FakeVerifier()
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
        yield Api(http)


async def test_decision_while_the_database_was_never_reachable_is_503(
    database_url: str, tmp_path: Path
) -> None:
    unreachable = create_engine(db_settings(REFUSED_DATABASE_URL))
    try:
        async with _api(database_url, create_sessionmaker(unreachable), tmp_path) as api:
            response = await api.post(
                "alice",
                f"/v1/studies/{uuid.uuid4()}/decisions",
                {"stage": "appraisal_review", "revision": 1, "decision": "approve"},
            )
            _assert_503(response)
            _assert_503(await api.get("alice", "/v1/studies"))
    finally:
        await unreachable.dispose()


async def test_decision_during_an_outage_is_503_then_the_same_key_succeeds(
    database_url: str,
    sm: async_sessionmaker[AsyncSession],
    proxied_engine: AsyncEngine,
    proxy: TcpProxy,
    tmp_path: Path,
) -> None:
    async with _api(database_url, create_sessionmaker(proxied_engine), tmp_path) as api:
        sid = (await api.create_study("alice"))["id"]  # works: the pool has live connections
        await Seed(sm).stage(sid, Stage.APPRAISAL_REVIEW)
        path, key = f"/v1/studies/{sid}/decisions", str(uuid.uuid4())
        body = {"stage": "appraisal_review", "revision": 1, "decision": "approve"}

        await proxy.stop()
        _assert_503(await api.post("alice", path, body, key=key))
        assert await _decision_counts(sm) == (0, 0)

        await proxy.start()
        accepted = await api.post("alice", path, body, key=key)
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["current_stage"] == "rooms_review"
        assert await _decision_counts(sm) == (1, 1)


async def test_a_failing_query_is_still_a_500(
    database_url: str,
    sm: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mapping is narrow: an error on an established connection is not a 503."""
    from basis.db.repositories import ReviewRepository

    async def broken(self: ReviewRepository, *args: Any, **kwargs: Any) -> Any:
        await self._session.execute(text("SELECT * FROM no_such_table"))

    async with _api(database_url, sm, tmp_path) as api:
        sid = (await api.create_study("alice"))["id"]
        await Seed(sm).stage(sid, Stage.APPRAISAL_REVIEW)
        monkeypatch.setattr(ReviewRepository, "record_decision", broken)
        response = await api.post(
            "alice",
            f"/v1/studies/{sid}/decisions",
            {"stage": "appraisal_review", "revision": 1, "decision": "approve"},
        )
    assert response.status_code == 500, response.text


# --------------------------------------------------------------------------- activities


@dataclass
class Outage:
    harness: Harness
    proxy: TcpProxy
    storage: LocalStorage
    failures: list[BaseException] = field(default_factory=list)


@pytest.fixture
async def outage(
    database_url: str, proxied_engine: AsyncEngine, proxy: TcpProxy, tmp_path: Path
) -> AsyncIterator[Outage]:
    """A harness whose worker reaches the database through ``proxy``; the test does not."""
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    direct = create_engine(db_settings(database_url))
    try:
        await ensure_search_attributes(env.client)
        await truncate_all(direct)
        settings = db_settings(database_url)
        storage = LocalStorage(tmp_path / "storage")
        store = await seed_reference_corpus(create_sessionmaker(direct))
        deps = make_deps(create_sessionmaker(proxied_engine), storage, settings, chunk_store=store)
        harness = Harness(
            env=env, sessionmaker=create_sessionmaker(direct), deps=deps, settings=settings
        )
        try:
            yield Outage(harness=harness, proxy=proxy, storage=storage)
        finally:
            await harness.cleanup()
    finally:
        await direct.dispose()
        await env.shutdown()


def gated(
    name: str,
    real: Callable[[Any], Awaitable[Any]],
    first_attempt: Callable[[Any], Awaitable[Any]],
    before_retry: Callable[[], Awaitable[None]],
) -> Callable[..., Any]:
    """Activity ``name``: attempt 1 runs ``first_attempt``, later attempts run
    ``before_retry`` and then the real activity. Typed like the real one, so the SDK
    converts its input and output."""
    input_type, output_type = IO_TYPES[name]

    async def wrapped(input: Any) -> Any:
        if activity.info().attempt == 1:
            return await first_attempt(input)
        await before_retry()
        return await real(input)

    wrapped.__annotations__ = {"input": input_type, "return": output_type}
    return activity.defn(name=name)(wrapped)


async def attempts_of(handle: WorkflowHandle[Any, Any], activity_type: str) -> list[Any]:
    """``ActivityTaskStarted`` attributes of every execution of ``activity_type``."""
    history = await handle.fetch_history()
    scheduled = {
        e.event_id: e.activity_task_scheduled_event_attributes
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
        and e.activity_task_scheduled_event_attributes.activity_type.name == activity_type
    }
    return [
        (scheduled[e.activity_task_started_event_attributes.scheduled_event_id], e)
        for e in history.events
        if e.HasField("activity_task_started_event_attributes")
        and e.activity_task_started_event_attributes.scheduled_event_id in scheduled
    ]


async def assert_retried_once(handle: WorkflowHandle[Any, Any], activity_type: str) -> None:
    """One execution, started twice, under the activity's own retry policy.

    The time-skipping test server leaves ``last_failure`` empty on the started event, so
    the failed attempt's exception is captured by the test's wrapper instead.
    """
    ((scheduled, started),) = await attempts_of(handle, activity_type)
    attrs = started.activity_task_started_event_attributes
    assert attrs.attempt == 2
    policy = scheduled.retry_policy
    assert policy.maximum_attempts == options_for(activity_type).maximum_attempts
    assert policy.initial_interval.ToTimedelta().total_seconds() == 1


async def assert_each_key_once_completed(harness: Harness, workflow_id: str) -> None:
    results = await harness.activity_results(workflow_id)
    keys = [r.idempotency_key for r in results]
    assert len(keys) == len(set(keys))
    assert {r.status for r in results} == {COMPLETED}


async def count(sm: async_sessionmaker[AsyncSession], model: Any, study_id: uuid.UUID) -> int:
    async with session_scope(sm) as session:
        stmt = select(func.count()).select_from(model).where(model.study_id == study_id)
        return int(await session.scalar(stmt) or 0)


async def rule_citation_count(sm: async_sessionmaker[AsyncSession], study_id: uuid.UUID) -> int:
    async with session_scope(sm) as session:
        rows = (
            await session.scalars(select(Component).where(Component.study_id == study_id))
        ).all()
    total = 0
    for component in rows:
        hit = lookup(component.label, component.material, component.attachment)
        assert hit is not None, component.label  # every stub component is a rules-table hit
        total += len(hit.citations)
    return total


async def claims(sm: async_sessionmaker[AsyncSession], name: str) -> list[ActivityResult]:
    async with session_scope(sm) as session:
        stmt = select(ActivityResult).where(ActivityResult.activity == name)
        return list((await session.scalars(stmt)).all())


@pytest.mark.parametrize("name", ["process_assets", "export_report"])
async def test_database_unreachable_for_one_attempt(outage: Outage, name: str) -> None:
    harness, proxy = outage.harness, outage.proxy
    sm = harness.sessionmaker
    real = build_activities(harness.deps)[name]
    snapshots: list[dict[str, Any]] = []
    study = await harness.seed(photos=["kitchen_1.jpg", "bathroom_1.jpg"])
    export = outage.storage.path_for(f"gs://basis-test/studies/{study.study_id}/exports/rev-1.json")

    async def first_attempt(input: Any) -> Any:
        await proxy.stop()  # the database goes away as this attempt starts
        try:
            return await real(input)
        except Exception as exc:
            outage.failures.append(exc)
            raise

    async def before_retry() -> None:
        snapshots.append(
            {
                "claims": len(await claims(sm, name)),
                "assets": await count(sm, Asset, study.study_id),
                "export_exists": export.exists(),
            }
        )
        await proxy.start()  # and comes back before the retry runs

    async with harness.worker(**{name: gated(name, real, first_attempt, before_retry)}):
        started = await harness.start(study)
        await harness.approve_all(started.handle)
        result = await harness.result(started.handle)

    assert result.outcome == "completed" and result.export_uri is not None
    # Attempt 1 failed before claiming its key: nothing written, no claim row.
    (failure,) = outage.failures
    assert isinstance(failure, DatabaseUnavailableError)
    expected_assets = 0 if name == "process_assets" else await count(sm, Component, study.study_id)
    assert snapshots == [{"claims": 0, "assets": expected_assets, "export_exists": False}]
    await assert_retried_once(started.handle, name)
    await assert_each_key_once_completed(harness, started.handle.id)
    components = await count(sm, Component, study.study_id)
    assert components == 4  # two photos, two stub components each
    for model in (Asset, Takeoff, CostEstimate):
        assert await count(sm, model, study.study_id) == components, model.__name__
    # One citation row per rule citation: the seeded reference corpus holds every page
    # the rules table cites, so each resolves (a rule may cite more than one page).
    assert await count(sm, Citation, study.study_id) == await rule_citation_count(
        sm, study.study_id
    )
    assert outage.storage.path_for(result.export_uri).exists()


async def test_connection_lost_in_the_middle_of_an_activity(outage: Outage) -> None:
    harness, proxy = outage.harness, outage.proxy
    sm = harness.sessionmaker
    activities = build_activities(harness.deps)
    extracted, in_flight, failed, back = (asyncio.Event() for _ in range(4))
    snapshot: dict[str, Any] = {}

    async def extract_then_signal(input: Any) -> Any:
        output = await activities["extract_fields"](input)
        extracted.set()
        return output

    async def photo_first_attempt(input: Any) -> Any:
        await extracted.wait()  # nothing else touches the database from here on
        in_flight.set()
        try:
            return await activities["analyze_photo"](input)
        except Exception as exc:
            outage.failures.append(exc)
            failed.set()
            raise

    async def photo_before_retry() -> None:
        await back.wait()

    async def noop() -> None:
        return None

    study = await harness.seed(photos=["kitchen_1.jpg"])
    photo = str(study.photo_ids[0])
    async with harness.worker(
        extract_fields=gated(
            "extract_fields", activities["extract_fields"], extract_then_signal, noop
        ),
        analyze_photo=gated(
            "analyze_photo", activities["analyze_photo"], photo_first_attempt, photo_before_retry
        ),
    ):
        started = await harness.start(
            study, simulate_photos={photo: SimulateFault(sleep_seconds=3)}
        )
        await asyncio.wait_for(in_flight.wait(), 30)
        await asyncio.sleep(1)  # inside the work transaction: the room row is inserted
        assert await writing_transactions(sm) == 1  # it really is mid-transaction
        await proxy.stop()
        await asyncio.wait_for(failed.wait(), 30)

        # Between the attempts: the server rolled the work back; the claim is still
        # "running" because recording the failure needed the database too.
        await no_open_transactions(sm)
        for model in (Room, PhotoRoom, Component):
            snapshot[model.__name__] = await count(sm, model, study.study_id)
        (claim,) = await claims(sm, "analyze_photo")
        snapshot["claim"] = (claim.status, claim.error)

        await proxy.start()
        back.set()
        await harness.approve_all(started.handle)
        result = await harness.result(started.handle)

    assert snapshot == {"Room": 0, "PhotoRoom": 0, "Component": 0, "claim": (RUNNING, None)}
    (failure,) = outage.failures
    assert isinstance(failure, DBAPIError) and failure.connection_invalidated
    assert result.outcome == "completed" and result.flagged_photos == ()
    ((_, started_event),) = await attempts_of(started.handle, "analyze_photo")
    assert started_event.activity_task_started_event_attributes.attempt == 2
    (claim,) = await claims(sm, "analyze_photo")
    assert claim.status == COMPLETED
    await assert_each_key_once_completed(harness, started.handle.id)
    assert (
        await count(sm, Room, study.study_id),
        await count(sm, PhotoRoom, study.study_id),
        await count(sm, Component, study.study_id),
    ) == (1, 1, 2)
