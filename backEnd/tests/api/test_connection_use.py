"""A request holds at most one pooled connection (EVAL_REPORT section 32.3).

The load test found every POST/DELETE holding two connections at once: the request's
transaction (``SessionDep``) was still open when ``IdempotentRoute`` stored the response
in the idempotency store's own transaction. With ``DB_POOL_SIZE`` concurrent mutations
each held one connection and waited for a second, the pool deadlocked, and every one was
answered 503 after ``pool_timeout`` (five studies created at once on a fresh API). Now the
response is stored after the request's transaction ends, so these hold:

* one mutation never holds more than one connection;
* more concurrent mutations than the pool has connections all complete;
* the stored response is of committed data: a commit that fails releases the key, so a
  retry with the same key runs the handler again instead of replaying a 2xx for rows that
  were never written.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, api_settings, build_api_app
from dbtest_support import db_settings, truncate_all
from fastapi import FastAPI
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Session

from basis.db.engine import create_engine
from basis.db.models import Study
from basis.db.session import create_sessionmaker
from basis.storage import LocalStorage

pytestmark = [pytest.mark.integration, pytest.mark.regression]

POOL = 2
POOL_TIMEOUT_S = 3.0


@pytest.fixture
async def small_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(
        db_settings(database_url, db_pool_size=POOL), pool_timeout=POOL_TIMEOUT_S
    )
    await truncate_all(engine)
    try:
        yield engine
    finally:
        await truncate_all(engine)
        await engine.dispose()


@pytest.fixture
def checkouts(small_engine: AsyncEngine) -> Iterator[dict[str, int]]:
    """The connections checked out right now and the most at any moment."""
    held = {"now": 0, "max": 0}
    pool = small_engine.sync_engine.pool

    def out(*_: Any) -> None:
        held["now"] += 1
        held["max"] = max(held["max"], held["now"])

    def back(*_: Any) -> None:
        held["now"] -= 1

    event.listen(pool, "checkout", out)
    event.listen(pool, "checkin", back)
    yield held
    event.remove(pool, "checkout", out)
    event.remove(pool, "checkin", back)


@pytest.fixture
def small_app(database_url: str, small_engine: AsyncEngine, tmp_path: Path) -> FastAPI:
    return build_api_app(
        api_settings(database_url, db_pool_size=POOL),
        create_sessionmaker(small_engine),
        LocalStorage(tmp_path / "objects"),
        FakeVerifier(),
    )


@pytest.fixture
async def small_api(small_app: FastAPI) -> AsyncIterator[Api]:
    transport = httpx.ASGITransport(app=small_app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield Api(client)


async def test_a_mutation_holds_one_connection_at_a_time(
    small_api: Api, checkouts: dict[str, int]
) -> None:
    study = await small_api.create_study("alice")  # first sight: the user is created too
    assert checkouts["max"] == 1
    checkouts["max"] = 0
    data = b"%PDF-1.4 tiny"
    response = await small_api.request_upload("alice", study["id"], data)
    assert response.status_code == 201, response.text
    assert checkouts["max"] == 1
    assert checkouts["now"] == 0


async def test_more_concurrent_mutations_than_connections_all_complete(
    small_api: Api, small_engine: AsyncEngine
) -> None:
    """Before the fix: each request held one connection and waited for a second until
    ``pool_timeout``, and they were answered 503."""
    users = [f"user-{i}" for i in range(4 * POOL)]
    t0 = time.monotonic()
    responses = await asyncio.gather(
        *(small_api.post(uid, "/v1/studies", {"property_name": uid}) for uid in users)
    )
    assert [r.status_code for r in responses] == [201] * len(users)
    assert time.monotonic() - t0 < POOL_TIMEOUT_S
    async with small_engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(Study)) == len(users)


async def test_a_failed_commit_releases_the_key_instead_of_storing_the_response(
    small_api: Api, small_engine: AsyncEngine
) -> None:
    def mark(session: Session, *_: Any) -> None:
        if any(isinstance(o, Study) and o.property_name == "commit fails" for o in session.new):
            session.info["fail_commit"] = True

    def refuse(session: Session) -> None:
        if session.info.pop("fail_commit", False):
            raise RuntimeError("commit refused (test)")

    key = str(uuid.uuid4())
    event.listen(Session, "before_flush", mark)
    event.listen(Session, "before_commit", refuse)
    try:
        failed = await small_api.post(
            "bob", "/v1/studies", {"property_name": "commit fails"}, key=key
        )
        assert failed.status_code == 500  # the app's handler answers the refused commit
    finally:
        event.remove(Session, "before_flush", mark)
        event.remove(Session, "before_commit", refuse)
    # The same key again: the handler runs (nothing was stored), and this time commits.
    retry = await small_api.post("bob", "/v1/studies", {"property_name": "commit fails"}, key=key)
    assert retry.status_code == 201, retry.text
    assert "idempotent-replayed" not in retry.headers
    got = await small_api.get("bob", f"/v1/studies/{retry.json()['id']}")
    assert got.status_code == 200
    async with small_engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(Study)) == 1
