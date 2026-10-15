"""Invariants for idempotency keys (S1_SPEC section 5).

* POST and DELETE under the idempotent router require ``Idempotency-Key`` (UUID or up to
  128 visible ASCII characters): missing or malformed is a 400 problem and the handler
  does not run. Safe methods need no key.
* Same key + same request (method, path, query, canonical JSON body): the original
  status, body and headers are replayed with ``Idempotent-Replayed: true``; the handler
  runs once.
* Same key + different request: 422, handler not run.
* Same key while the first request is still running: 409.
* Keys are scoped per principal uid.
* Records expire after 24 h; an in-flight reservation expires too.
* Deliberate 4xx problems are stored and replayed; 5xx and unexpected exceptions are
  not, and request validation failures are not, so the client may retry with the key.
* The dependency refuses to run on a route without ``IdempotentRoute``.
* ``/health`` is untouched.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from basis.api.app import create_app
from basis.api.auth import get_token_verifier
from basis.api.errors import PROBLEM_JSON, PROBLEM_TYPE_BASE, ApiError, Conflict, Unauthorized
from basis.api.idempotency import (
    IDEMPOTENCY_HEADER,
    IDEMPOTENCY_TTL,
    IN_FLIGHT_TTL,
    REPLAYED_HEADER,
    IdempotentRoute,
    InMemoryIdempotencyStore,
    StoredResponse,
    UserKey,
    canonical_body,
    idempotency,
    parse_idempotency_key,
    request_hash,
)
from basis.auth.firebase import Principal
from basis.config import Settings


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeVerifier:
    def verify(self, token: str) -> Principal:
        if token in ("alice", "bob"):
            return Principal(uid=token)
        raise Unauthorized("bad token")


class Thing(BaseModel):
    name: str
    tags: list[str] = []


class Calls:
    def __init__(self) -> None:
        self.count = 0
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(clock: FakeClock) -> InMemoryIdempotencyStore:
    return InMemoryIdempotencyStore(clock=clock)


@pytest.fixture
def calls() -> Calls:
    return Calls()


@pytest.fixture
def app(settings: Settings, store: InMemoryIdempotencyStore, calls: Calls) -> FastAPI:
    app = create_app(settings)
    app.state.idempotency_store = store
    app.dependency_overrides[get_token_verifier] = FakeVerifier

    router = APIRouter(
        prefix="/v1", route_class=IdempotentRoute, dependencies=[Depends(idempotency)]
    )

    @router.post("/things", status_code=201)
    async def create_thing(thing: Thing, response: Response) -> dict[str, Any]:
        calls.count += 1
        response.headers["Location"] = f"/v1/things/{calls.count}"
        return {"id": calls.count, "name": thing.name, "tags": thing.tags}

    @router.get("/things")
    async def list_things() -> list[int]:
        calls.count += 1
        return []

    @router.delete("/things/{thing_id}", status_code=204)
    async def delete_thing(thing_id: int) -> None:
        calls.count += 1

    @router.post("/conflict")
    async def conflict() -> None:
        calls.count += 1
        raise Conflict(f"stale revision (call {calls.count})")

    @router.post("/boom")
    async def boom() -> None:
        calls.count += 1
        raise RuntimeError("boom")

    @router.post("/unavailable")
    async def unavailable() -> None:
        calls.count += 1
        raise ApiError(503, detail="try later")

    @router.post("/returns-503")
    async def returns_503() -> JSONResponse:
        calls.count += 1
        return JSONResponse({"busy": True}, status_code=503)

    @router.post("/stream")
    async def stream() -> StreamingResponse:
        calls.count += 1

        async def body() -> AsyncIterator[bytes]:
            yield b"chunk"

        return StreamingResponse(body())

    @router.post("/slow")
    async def slow() -> dict[str, int]:
        calls.count += 1
        calls.entered.set()
        assert calls.gate is not None
        await calls.gate.wait()
        return {"call": calls.count}

    app.include_router(router)

    plain = APIRouter(prefix="/v1/plain", dependencies=[Depends(idempotency)])

    @plain.post("/things")
    async def plain_thing() -> dict[str, str]:
        calls.count += 1
        return {}

    app.include_router(plain)
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://basis.test") as c:
        yield c


def headers(key: str | None = None, user: str = "alice", **extra: str) -> dict[str, str]:
    out = {"Authorization": f"Bearer {user}", **extra}
    if key is not None:
        out[IDEMPOTENCY_HEADER] = key
    return out


@pytest.fixture
def key() -> Iterator[str]:
    yield str(uuid.uuid4())


# --------------------------------------------------------------------------- key rules


async def test_missing_key_is_400(client: httpx.AsyncClient, calls: Calls) -> None:
    response = await client.post("/v1/things", json={"name": "a"}, headers=headers())
    assert response.status_code == 400
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["type"] == PROBLEM_TYPE_BASE + "idempotency-key-missing"
    assert calls.count == 0


@pytest.mark.parametrize("bad", ["", "   ", "a" * 129, "has space", "tab\tkey", '""', "ü"])
async def test_malformed_key_is_400(client: httpx.AsyncClient, calls: Calls, bad: str) -> None:
    response = await client.post(
        "/v1/things",
        json={"name": "a"},
        headers=[
            (b"authorization", b"Bearer alice"),
            (IDEMPOTENCY_HEADER.encode(), bad.encode("utf-8")),
        ],
    )
    assert response.status_code == 400
    assert response.json()["type"] == PROBLEM_TYPE_BASE + "idempotency-key-invalid"
    assert calls.count == 0


@pytest.mark.parametrize("good", [str(uuid.uuid4()), "a" * 128, "k", "order-42:retry_1"])
def test_valid_keys(good: str) -> None:
    assert parse_idempotency_key(good) == good


def test_structured_field_quotes_are_accepted() -> None:
    assert parse_idempotency_key('"abc-123"') == "abc-123"


async def test_safe_methods_need_no_key(client: httpx.AsyncClient, calls: Calls) -> None:
    response = await client.get("/v1/things", headers=headers())
    assert response.status_code == 200
    assert calls.count == 1


async def test_delete_requires_key(client: httpx.AsyncClient, calls: Calls) -> None:
    response = await client.delete("/v1/things/1", headers=headers())
    assert response.status_code == 400
    assert calls.count == 0


async def test_authentication_comes_first(client: httpx.AsyncClient, key: str) -> None:
    response = await client.post(
        "/v1/things", json={"name": "a"}, headers={IDEMPOTENCY_HEADER: key}
    )
    assert response.status_code == 401


async def test_health_is_not_affected(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200


# --------------------------------------------------------------------------- replay


async def test_replay_returns_original_response(
    client: httpx.AsyncClient, calls: Calls, key: str
) -> None:
    first = await client.post("/v1/things", json={"name": "a"}, headers=headers(key))
    second = await client.post("/v1/things", json={"name": "a"}, headers=headers(key))
    assert first.status_code == second.status_code == 201
    assert first.content == second.content
    assert second.headers["location"] == first.headers["location"] == "/v1/things/1"
    assert second.headers.get_list("content-type") == first.headers.get_list("content-type")
    assert second.headers.get_list("content-length") == [str(len(first.content))]
    assert second.headers[REPLAYED_HEADER] == "true"
    assert REPLAYED_HEADER not in first.headers
    assert calls.count == 1


async def test_replay_has_its_own_request_id(client: httpx.AsyncClient, key: str) -> None:
    await client.post(
        "/v1/things", json={"name": "a"}, headers=headers(key, **{"X-Request-Id": "first"})
    )
    replay = await client.post(
        "/v1/things", json={"name": "a"}, headers=headers(key, **{"X-Request-Id": "second"})
    )
    assert replay.headers.get_list("x-request-id") == ["second"]


async def test_reserialized_body_still_matches(
    client: httpx.AsyncClient, calls: Calls, key: str
) -> None:
    await client.post(
        "/v1/things",
        content=b'{"name": "a", "tags": ["x"]}',
        headers=headers(key, **{"content-type": "application/json"}),
    )
    replay = await client.post(
        "/v1/things",
        content=b'{\n  "tags":["x"],\n  "name":"a"\n}',
        headers=headers(key, **{"content-type": "application/json"}),
    )
    assert replay.headers[REPLAYED_HEADER] == "true"
    assert calls.count == 1


async def test_delete_replays_204(client: httpx.AsyncClient, calls: Calls, key: str) -> None:
    first = await client.delete("/v1/things/7", headers=headers(key))
    second = await client.delete("/v1/things/7", headers=headers(key))
    assert first.status_code == second.status_code == 204
    assert second.content == b""
    assert second.headers[REPLAYED_HEADER] == "true"
    assert calls.count == 1


async def test_deliberate_4xx_is_stored_and_replayed(
    client: httpx.AsyncClient, calls: Calls, key: str
) -> None:
    first = await client.post("/v1/conflict", headers=headers(key))
    second = await client.post("/v1/conflict", headers=headers(key))
    assert first.status_code == second.status_code == 409
    assert first.headers["content-type"] == second.headers["content-type"] == PROBLEM_JSON
    assert second.json()["detail"] == first.json()["detail"] == "stale revision (call 1)"
    assert second.headers[REPLAYED_HEADER] == "true"
    assert calls.count == 1


# --------------------------------------------------------------------------- mismatch


async def test_same_key_different_body_is_422(
    client: httpx.AsyncClient, calls: Calls, key: str
) -> None:
    await client.post("/v1/things", json={"name": "a"}, headers=headers(key))
    response = await client.post("/v1/things", json={"name": "b"}, headers=headers(key))
    assert response.status_code == 422
    assert response.json()["type"] == PROBLEM_TYPE_BASE + "idempotency-key-reused"
    assert calls.count == 1


async def test_same_key_different_path_is_422(
    client: httpx.AsyncClient, calls: Calls, key: str
) -> None:
    await client.delete("/v1/things/1", headers=headers(key))
    response = await client.delete("/v1/things/2", headers=headers(key))
    assert response.status_code == 422
    assert calls.count == 1


async def test_same_key_different_query_is_422(client: httpx.AsyncClient, key: str) -> None:
    await client.delete("/v1/things/1?force=false", headers=headers(key))
    response = await client.delete("/v1/things/1?force=true", headers=headers(key))
    assert response.status_code == 422


# --------------------------------------------------------------------------- scoping


async def test_keys_are_scoped_per_user(client: httpx.AsyncClient, calls: Calls, key: str) -> None:
    alice = await client.post("/v1/things", json={"name": "a"}, headers=headers(key, "alice"))
    bob = await client.post("/v1/things", json={"name": "a"}, headers=headers(key, "bob"))
    assert alice.status_code == bob.status_code == 201
    assert REPLAYED_HEADER not in bob.headers
    assert alice.json()["id"] == 1 and bob.json()["id"] == 2
    assert calls.count == 2


async def test_other_user_with_different_body_is_not_422(
    client: httpx.AsyncClient, key: str
) -> None:
    await client.post("/v1/things", json={"name": "a"}, headers=headers(key, "alice"))
    bob = await client.post("/v1/things", json={"name": "z"}, headers=headers(key, "bob"))
    assert bob.status_code == 201


# --------------------------------------------------------------------------- expiry


async def test_replay_until_ttl_then_fresh(
    client: httpx.AsyncClient, calls: Calls, clock: FakeClock, key: str
) -> None:
    await client.post("/v1/things", json={"name": "a"}, headers=headers(key))
    clock.advance(IDEMPOTENCY_TTL.total_seconds() - 1)
    replay = await client.post("/v1/things", json={"name": "a"}, headers=headers(key))
    assert replay.headers[REPLAYED_HEADER] == "true"
    clock.advance(1)
    fresh = await client.post("/v1/things", json={"name": "b"}, headers=headers(key))
    assert fresh.status_code == 201
    assert REPLAYED_HEADER not in fresh.headers
    assert calls.count == 2


def test_ttl_is_24_hours() -> None:
    assert timedelta(hours=24) == IDEMPOTENCY_TTL


# --------------------------------------------------------------------------- failures


async def test_unexpected_error_releases_key(
    client: httpx.AsyncClient, calls: Calls, store: InMemoryIdempotencyStore, key: str
) -> None:
    first = await client.post("/v1/boom", headers=headers(key))
    second = await client.post("/v1/boom", headers=headers(key))
    assert first.status_code == second.status_code == 500
    assert REPLAYED_HEADER not in second.headers
    assert calls.count == 2
    assert len(store) == 0


async def test_raised_5xx_releases_key(client: httpx.AsyncClient, calls: Calls, key: str) -> None:
    await client.post("/v1/unavailable", headers=headers(key))
    second = await client.post("/v1/unavailable", headers=headers(key))
    assert second.status_code == 503
    assert calls.count == 2


async def test_returned_5xx_releases_key(client: httpx.AsyncClient, calls: Calls, key: str) -> None:
    await client.post("/v1/returns-503", headers=headers(key))
    await client.post("/v1/returns-503", headers=headers(key))
    assert calls.count == 2


async def test_validation_failure_is_not_stored(
    client: httpx.AsyncClient, calls: Calls, key: str
) -> None:
    bad = await client.post("/v1/things", json={"nope": 1}, headers=headers(key))
    assert bad.status_code == 422
    assert bad.json()["type"] == PROBLEM_TYPE_BASE + "validation"
    good = await client.post("/v1/things", json={"name": "a"}, headers=headers(key))
    assert good.status_code == 201
    assert calls.count == 1


async def test_streaming_response_is_not_stored(
    client: httpx.AsyncClient, calls: Calls, key: str
) -> None:
    await client.post("/v1/stream", headers=headers(key))
    second = await client.post("/v1/stream", headers=headers(key))
    assert second.content == b"chunk"
    assert calls.count == 2


async def test_dependency_on_plain_route_fails_loudly(
    client: httpx.AsyncClient, calls: Calls, key: str, caplog: pytest.LogCaptureFixture
) -> None:
    response = await client.post("/v1/plain/things", headers=headers(key))
    assert response.status_code == 500
    assert calls.count == 0
    (record,) = [r for r in caplog.records if r.getMessage() == "unhandled exception"]
    assert record.exc_info is not None
    assert "route_class=IdempotentRoute" in str(record.exc_info[1])


# --------------------------------------------------------------------------- in flight


async def test_concurrent_duplicate_is_409(
    client: httpx.AsyncClient, calls: Calls, key: str
) -> None:
    calls.gate = asyncio.Event()
    first = asyncio.create_task(client.post("/v1/slow", headers=headers(key)))
    await asyncio.wait_for(calls.entered.wait(), timeout=5)

    duplicate = await client.post("/v1/slow", headers=headers(key))
    assert duplicate.status_code == 409
    assert duplicate.json()["type"] == PROBLEM_TYPE_BASE + "idempotency-key-in-flight"

    calls.gate.set()
    original = await first
    assert original.status_code == 200

    replay = await client.post("/v1/slow", headers=headers(key))
    assert replay.headers[REPLAYED_HEADER] == "true"
    assert replay.json() == original.json()
    assert calls.count == 1


async def test_in_flight_with_different_body_is_422(
    client: httpx.AsyncClient, calls: Calls, key: str
) -> None:
    calls.gate = asyncio.Event()
    first = asyncio.create_task(client.post("/v1/slow", headers=headers(key)))
    await asyncio.wait_for(calls.entered.wait(), timeout=5)
    other = await client.post("/v1/slow", json={"x": 1}, headers=headers(key))
    assert other.status_code == 422
    calls.gate.set()
    await first


# --------------------------------------------------------------------------- fingerprint


def test_canonical_body_ignores_key_order_and_whitespace() -> None:
    assert canonical_body(b'{"b": 1, "a": [1, 2]}') == canonical_body(b'{"a":[1,2],"b":1}')


def test_canonical_body_keeps_values_distinct() -> None:
    assert canonical_body(b'{"a": 1}') != canonical_body(b'{"a": 2}')
    assert canonical_body(b'{"a": [1, 2]}') != canonical_body(b'{"a": [2, 1]}')


def test_canonical_body_of_empty_and_non_json() -> None:
    assert canonical_body(b"") == b""
    assert canonical_body(b"  \n") == b""
    assert canonical_body(b"\xff\xfe not json") == b"\xff\xfe not json"


def test_request_hash_covers_method_path_query_body() -> None:
    base = request_hash("POST", "/v1/x", "", b"{}")
    assert base == request_hash("post", "/v1/x", "", b"{ }")
    assert base != request_hash("DELETE", "/v1/x", "", b"{}")
    assert base != request_hash("POST", "/v1/y", "", b"{}")
    assert base != request_hash("POST", "/v1/x", "a=1", b"{}")
    assert base != request_hash("POST", "/v1/x", "", b'{"a":1}')
    assert len(base) == 64


# --------------------------------------------------------------------------- store


async def test_store_reserve_then_existing(clock: FakeClock) -> None:
    store = InMemoryIdempotencyStore(clock=clock)
    user_key = UserKey("u", "k")
    assert await store.reserve(user_key, "h1", IN_FLIGHT_TTL) is None
    existing = await store.reserve(user_key, "h2", IN_FLIGHT_TTL)
    assert existing is not None and existing.in_progress and existing.request_hash == "h1"


async def test_store_put_replaces_reservation(clock: FakeClock) -> None:
    store = InMemoryIdempotencyStore(clock=clock)
    user_key = UserKey("u", "k")
    await store.reserve(user_key, "h", IN_FLIGHT_TTL)
    await store.put(user_key, "h", 201, b"{}", [("location", "/x")], IDEMPOTENCY_TTL)
    record = await store.get(user_key)
    assert record == StoredResponse("h", 201, b"{}", (("location", "/x"),), clock.now + 86400)
    assert not record.in_progress


async def test_store_release_drops_only_reservations(clock: FakeClock) -> None:
    store = InMemoryIdempotencyStore(clock=clock)
    reserved, done = UserKey("u", "r"), UserKey("u", "d")
    await store.reserve(reserved, "h", IN_FLIGHT_TTL)
    await store.put(done, "h", 200, b"", [], IDEMPOTENCY_TTL)
    await store.release(reserved)
    await store.release(done)
    await store.release(UserKey("u", "missing"))
    assert await store.get(reserved) is None
    assert await store.get(done) is not None


async def test_store_in_flight_reservation_expires(clock: FakeClock) -> None:
    store = InMemoryIdempotencyStore(clock=clock)
    user_key = UserKey("u", "k")
    await store.reserve(user_key, "h", IN_FLIGHT_TTL)
    clock.advance(IN_FLIGHT_TTL.total_seconds())
    assert await store.reserve(user_key, "h", IN_FLIGHT_TTL) is None


async def test_store_sweeps_expired_records(clock: FakeClock) -> None:
    store = InMemoryIdempotencyStore(clock=clock)
    for i in range(5):
        await store.put(UserKey("u", str(i)), "h", 200, b"", [], timedelta(seconds=30))
    assert len(store) == 5
    clock.advance(store.SWEEP_INTERVAL_S)
    await store.reserve(UserKey("u", "new"), "h", IN_FLIGHT_TTL)
    assert len(store) == 1
