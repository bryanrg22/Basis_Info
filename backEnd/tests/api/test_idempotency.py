"""Idempotency keys on the real routes with the PostgreSQL store (S1_SPEC section 5).

The in-memory store's rules are pinned in ``tests/unit/api/test_idempotency.py``; this
file proves the Postgres-backed store gives the same behavior end to end, plus what only
a database can show (stored rows, expiry, user scoping by ``users.id``, reclaimed leases).
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from apitest_support import Api, Seed, bearer
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.idempotency import (
    IDEMPOTENCY_HEADER,
    IDEMPOTENCY_TTL,
    IN_FLIGHT_TTL,
    REPLAYED_HEADER,
    UserKey,
    request_hash,
)
from basis.api.idempotency_pg import (
    PostgresIdempotencyStore,
    UnknownUserError,
    decode_response,
    encode_response,
)
from basis.db.models import IdempotencyKey, Study
from basis.db.repositories import StudyRepository, scoped_key

pytestmark = pytest.mark.integration

CREATE = "/v1/studies"


async def study_count(sm: async_sessionmaker[AsyncSession]) -> int:
    async with sm() as session:
        return int(await session.scalar(select(func.count()).select_from(Study)) or 0)


async def key_rows(sm: async_sessionmaker[AsyncSession]) -> list[IdempotencyKey]:
    async with sm() as session:
        return list((await session.scalars(select(IdempotencyKey))).all())


def fingerprint(path: str, body: dict[str, Any], method: str = "POST") -> str:
    return request_hash(method, path, "", json.dumps(body).encode())


def test_the_app_uses_the_postgres_store(api_app: FastAPI) -> None:
    assert isinstance(api_app.state.idempotency_store, PostgresIdempotencyStore)


async def test_same_key_and_body_replays_without_running_twice(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    key, body = str(uuid.uuid4()), {"property_name": "Harbor"}

    first = await api.post("alice", CREATE, body, key=key)
    second = await api.post("alice", CREATE, body, key=key)

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.content == first.content
    assert second.headers["location"] == first.headers["location"]
    assert second.headers[REPLAYED_HEADER] == "true"
    assert second.headers["x-request-id"] != first.headers["x-request-id"]
    assert await study_count(sessionmaker) == 1


async def test_reordered_json_is_the_same_request(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession], seed: Seed
) -> None:
    org = str(await seed.personal_org("alice"))
    key = str(uuid.uuid4())
    first = await api.client.post(
        CREATE,
        content=f'{{"property_name": "A", "org_id": "{org}"}}',
        headers={**bearer("alice"), IDEMPOTENCY_HEADER: key, "Content-Type": "application/json"},
    )
    second = await api.client.post(
        CREATE,
        content=f'{{"org_id":"{org}","property_name":"A"}}',
        headers={**bearer("alice"), IDEMPOTENCY_HEADER: key, "Content-Type": "application/json"},
    )
    assert (first.status_code, second.status_code) == (201, 201)
    assert second.headers[REPLAYED_HEADER] == "true"
    assert await study_count(sessionmaker) == 1


async def test_same_key_different_body_is_422(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    key = str(uuid.uuid4())
    await api.post("alice", CREATE, {"property_name": "A"}, key=key)

    response = await api.post("alice", CREATE, {"property_name": "B"}, key=key)

    assert response.status_code == 422
    assert response.json()["type"].endswith("/idempotency-key-reused")
    assert await study_count(sessionmaker) == 1


async def test_same_key_on_another_route_is_422(api: Api) -> None:
    key = str(uuid.uuid4())
    study = await api.create_study("alice")
    await api.post("alice", CREATE, {"property_name": "A"}, key=key)
    response = await api.delete("alice", f"/v1/studies/{study['id']}", key=key)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "header",
    [None, "", "   ", "has space", "x" * 129, "café".encode(), '""'],
    ids=["missing", "empty", "blank", "space", "too-long", "non-ascii", "empty-quoted"],
)
async def test_missing_or_malformed_key_is_400_and_nothing_runs(
    http: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    header: str | bytes | None,
) -> None:
    headers: dict[str, str | bytes] = {**bearer("alice")}
    if header is not None:
        headers[IDEMPOTENCY_HEADER] = header
    response = await http.post(CREATE, json={"property_name": "A"}, headers=headers)

    assert response.status_code == 400
    assert response.json()["type"].endswith(
        "/idempotency-key-missing" if header is None else "/idempotency-key-invalid"
    )
    assert await study_count(sessionmaker) == 0
    assert await key_rows(sessionmaker) == []


async def test_quoted_key_form_is_accepted(api: Api) -> None:
    response = await api.post("alice", CREATE, {"property_name": "A"}, key='"abc-123"')
    assert response.status_code == 201


async def test_reads_need_no_key(api: Api) -> None:
    assert (await api.get("alice", CREATE)).status_code == 200


async def test_keys_are_scoped_per_user(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    key, body = "shared-key", {"property_name": "Same"}

    alice = await api.post("alice", CREATE, body, key=key)
    bob = await api.post("bob", CREATE, body, key=key)

    assert (alice.status_code, bob.status_code) == (201, 201)
    assert REPLAYED_HEADER not in bob.headers
    assert alice.json()["id"] != bob.json()["id"]
    stored = {row.key for row in await key_rows(sessionmaker)}
    alice_id, bob_id = (await seed.user("alice")).id, (await seed.user("bob")).id
    assert stored == {scoped_key(alice_id, key), scoped_key(bob_id, key)}


async def test_the_stored_row(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    key, body = str(uuid.uuid4()), {"property_name": "Stored"}
    response = await api.post("alice", CREATE, body, key=key)

    (row,) = await key_rows(sessionmaker)
    alice = await seed.user("alice")
    assert row.key == scoped_key(alice.id, key)
    assert row.user_id == alice.id
    assert row.route == "POST /v1/studies"
    assert row.request_hash == fingerprint(CREATE, body)
    assert row.status_code == 201
    stored_body, stored_headers = decode_response(row.response)
    assert stored_body == response.content
    assert ("location", response.headers["location"]) in stored_headers
    assert all(name.lower() != "x-request-id" for name, _ in stored_headers)
    # A completed response is kept for the replay window, not the in-flight lease.
    remaining = row.expires_at - row.created_at
    assert (
        IDEMPOTENCY_TTL - timedelta(minutes=1) < remaining <= IDEMPOTENCY_TTL + timedelta(minutes=1)
    )


async def test_deliberate_4xx_is_stored_and_replayed(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    key = str(uuid.uuid4())
    path = f"/v1/studies/{uuid.uuid4()}"
    first = await api.delete("alice", path, key=key)
    second = await api.delete("alice", path, key=key)
    assert (first.status_code, second.status_code) == (404, 404)
    assert second.headers[REPLAYED_HEADER] == "true"
    assert second.headers["content-type"] == "application/problem+json"
    assert [row.status_code for row in await key_rows(sessionmaker)] == [404]


async def test_validation_failures_are_not_stored(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    key = str(uuid.uuid4())
    bad = await api.post("alice", CREATE, {"property_name": ""}, key=key)
    assert bad.status_code == 422
    assert await key_rows(sessionmaker) == []
    good = await api.post("alice", CREATE, {"property_name": ""}, key=key)
    assert good.status_code == 422  # validated again, not replayed
    assert REPLAYED_HEADER not in good.headers


async def test_unexpected_errors_release_the_key(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = StudyRepository.create
    calls = {"n": 0}

    async def flaky(self: StudyRepository, *args: Any, **kwargs: Any) -> Study:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database hiccup")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(StudyRepository, "create", flaky)
    key, body = str(uuid.uuid4()), {"property_name": "Retry me"}

    failed = await api.post("alice", CREATE, body, key=key)
    assert failed.status_code == 500
    assert await key_rows(sessionmaker) == []

    retried = await api.post("alice", CREATE, body, key=key)
    assert retried.status_code == 201
    assert REPLAYED_HEADER not in retried.headers
    assert await study_count(sessionmaker) == 1


async def test_in_flight_key_is_409_and_a_different_body_is_422(
    api: Api, api_app: FastAPI, seed: Seed
) -> None:
    store: PostgresIdempotencyStore = api_app.state.idempotency_store
    await seed.user("alice")
    key, body = str(uuid.uuid4()), {"property_name": "Busy"}
    assert (
        await store.reserve(UserKey("alice", key), fingerprint(CREATE, body), IN_FLIGHT_TTL) is None
    )

    same = await api.post("alice", CREATE, body, key=key)
    other = await api.post("alice", CREATE, {"property_name": "Other"}, key=key)

    assert same.status_code == 409
    assert same.json()["type"].endswith("/idempotency-key-in-flight")
    assert other.status_code == 422


async def test_expired_key_runs_the_request_again(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    key, body = str(uuid.uuid4()), {"property_name": "Again"}
    first = await api.post("alice", CREATE, body, key=key)
    async with sessionmaker() as session, session.begin():
        await session.execute(
            update(IdempotencyKey).values(expires_at=func.now() - timedelta(seconds=1))
        )

    second = await api.post("alice", CREATE, body, key=key)

    assert second.status_code == 201
    assert REPLAYED_HEADER not in second.headers
    assert second.json()["id"] != first.json()["id"]
    assert await study_count(sessionmaker) == 2


# --------------------------------------------------------------------------- store


async def test_store_round_trip(sessionmaker: async_sessionmaker[AsyncSession], seed: Seed) -> None:
    store = PostgresIdempotencyStore(sessionmaker)
    await seed.user("alice")
    user_key = UserKey("alice", "k1")

    assert await store.get(user_key) is None
    assert await store.reserve(user_key, "h" * 64, IN_FLIGHT_TTL, route="POST /x") is None
    reserved = await store.get(user_key)
    assert reserved is not None and reserved.in_progress and reserved.request_hash == "h" * 64
    assert reserved.expires_at <= time.time() + IN_FLIGHT_TTL.total_seconds() + 5

    again = await store.reserve(user_key, "h" * 64, IN_FLIGHT_TTL)
    assert again is not None and again.in_progress

    await store.put(
        user_key, "h" * 64, 201, b'{"ok":true}', [("location", "/x/1")], IDEMPOTENCY_TTL
    )
    done = await store.get(user_key)
    assert done is not None
    assert (done.status, done.body, done.headers) == (201, b'{"ok":true}', (("location", "/x/1"),))
    assert done.expires_at > time.time() + IDEMPOTENCY_TTL.total_seconds() - 60

    await store.release(user_key)  # a completed record is not released
    assert await store.get(user_key) is not None


async def test_store_release_drops_only_an_in_flight_reservation(
    sessionmaker: async_sessionmaker[AsyncSession], seed: Seed
) -> None:
    store = PostgresIdempotencyStore(sessionmaker)
    await seed.user("alice")
    user_key = UserKey("alice", "k2")
    await store.reserve(user_key, "a" * 64, IN_FLIGHT_TTL)
    await store.release(user_key, request_hash="b" * 64)  # someone else's fingerprint
    assert await store.get(user_key) is not None
    await store.release(user_key, request_hash="a" * 64)
    assert await store.get(user_key) is None


async def test_store_does_not_complete_a_reservation_taken_over_after_expiry(
    sessionmaker: async_sessionmaker[AsyncSession], seed: Seed
) -> None:
    """A slow request whose lease expired must not write onto the new owner's claim."""
    store = PostgresIdempotencyStore(sessionmaker)
    await seed.user("alice")
    user_key = UserKey("alice", "k3")
    await store.reserve(user_key, "a" * 64, IN_FLIGHT_TTL)
    async with sessionmaker() as session, session.begin():
        await session.execute(
            update(IdempotencyKey).values(expires_at=func.now() - timedelta(seconds=1))
        )
    assert await store.reserve(user_key, "b" * 64, IN_FLIGHT_TTL) is None  # new owner

    await store.put(user_key, "a" * 64, 201, b"late", [], IDEMPOTENCY_TTL)
    await store.release(user_key, request_hash="a" * 64)

    current = await store.get(user_key)
    assert current is not None and current.in_progress and current.request_hash == "b" * 64


async def test_store_requires_a_provisioned_user(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresIdempotencyStore(sessionmaker)
    with pytest.raises(UnknownUserError):
        await store.reserve(UserKey("nobody", "k"), "h" * 64, IN_FLIGHT_TTL)


def test_response_encoding_round_trips_text_and_binary() -> None:
    headers = [("content-type", "application/json"), ("location", "/v1/x")]
    for body in (b'{"a":"\xc3\xa9"}', b"\xff\xfe\x00binary", b""):
        assert decode_response(encode_response(body, headers)) == (body, tuple(headers))
    assert decode_response(None) == (b"", ())
