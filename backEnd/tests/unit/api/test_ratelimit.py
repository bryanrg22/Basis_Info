"""Invariants for rate limiting (S1_SPEC section 5, BUILD_PLAN 6.4).

* Defaults: 60 reads and 10 mutations per minute per caller, from settings.
* Sliding-window log: never more than ``limit`` accepted in any 60 s interval; capacity
  returns exactly when the oldest request leaves the window; rejected requests do not
  extend the lockout.
* Over the limit: 429 problem with ``Retry-After`` in whole seconds, rounded up, >= 1.
* Authenticated callers are keyed by uid (changing IP does not reset the budget; two
  users behind one IP do not share one); unauthenticated callers by client IP.
* Reads and mutations have separate budgets.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from fastapi import Depends, FastAPI
from pydantic import ValidationError

from basis.api.app import create_app
from basis.api.auth import CurrentPrincipal, get_token_verifier
from basis.api.errors import PROBLEM_JSON, PROBLEM_TYPE_BASE, Unauthorized
from basis.api.ratelimit import (
    WINDOW,
    InMemoryRateLimiter,
    RateLimitDecision,
    rate_limited,
)
from basis.auth.firebase import Principal
from basis.config import Settings, load_settings


class FakeClock:
    def __init__(self) -> None:
        self.now = 5_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeVerifier:
    def verify(self, token: str) -> Principal:
        if token in ("alice", "bob"):
            return Principal(uid=token)
        raise Unauthorized("bad token")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def app(settings: Settings, clock: FakeClock) -> FastAPI:
    app = create_app(settings)
    app.state.rate_limiter = InMemoryRateLimiter(clock=clock)
    app.dependency_overrides[get_token_verifier] = FakeVerifier

    @app.get("/v1/read", dependencies=[Depends(rate_limited("read"))])
    async def read(principal: CurrentPrincipal) -> dict[str, str]:
        return {"uid": principal.uid}

    @app.post("/v1/mutate", dependencies=[Depends(rate_limited("mutation"))])
    async def mutate(principal: CurrentPrincipal) -> dict[str, str]:
        return {"uid": principal.uid}

    @app.get("/v1/public", dependencies=[Depends(rate_limited("read"))])
    async def public() -> dict[str, bool]:
        return {"ok": True}

    return app


def client_for(app: FastAPI, ip: str = "203.0.113.1") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 40000))
    return httpx.AsyncClient(transport=transport, base_url="http://basis.test")


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(app) as c:
        yield c


def auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user}"}


async def burn(client: httpx.AsyncClient, n: int, method: str, path: str, user: str | None) -> None:
    headers = auth(user) if user else {}
    for i in range(n):
        response = await client.request(method, path, headers=headers)
        assert response.status_code == 200, f"request {i + 1} of {n} was {response.status_code}"


# --------------------------------------------------------------------------- settings


def test_default_limits(settings: Settings) -> None:
    assert settings.rate_limit_reads_per_min == 60
    assert settings.rate_limit_mutations_per_min == 10


def test_limits_come_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_READS_PER_MIN", "5")
    monkeypatch.setenv("RATE_LIMIT_MUTATIONS_PER_MIN", "2")
    settings = load_settings()
    assert (settings.rate_limit_reads_per_min, settings.rate_limit_mutations_per_min) == (5, 2)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_non_positive_limits_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("RATE_LIMIT_READS_PER_MIN", value)
    with pytest.raises(ValidationError):
        load_settings()


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError):
        rate_limited("write")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- limiter


async def test_allows_limit_then_rejects(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(clock=clock)
    decisions = [await limiter.hit("k", 3, WINDOW) for _ in range(3)]
    assert [d.remaining for d in decisions] == [2, 1, 0]
    assert all(d.allowed for d in decisions)
    assert await limiter.hit("k", 3, WINDOW) == RateLimitDecision(False, 3, 0, 60.0)


async def test_window_slides_rather_than_resets(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(clock=clock)
    await limiter.hit("k", 2, WINDOW)  # t=0
    clock.advance(30)
    await limiter.hit("k", 2, WINDOW)  # t=30
    clock.advance(29.9)
    denied = await limiter.hit("k", 2, WINDOW)  # t=59.9
    assert not denied.allowed
    assert denied.retry_after == pytest.approx(0.1)
    clock.advance(0.1)  # t=60: the t=0 request has left the window
    assert (await limiter.hit("k", 2, WINDOW)).allowed
    assert not (await limiter.hit("k", 2, WINDOW)).allowed  # t=30 one is still inside


async def test_never_more_than_limit_in_any_window(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(clock=clock)
    accepted: list[float] = []
    for _ in range(600):  # one attempt every 0.5 s for 5 minutes
        if (await limiter.hit("k", 10, WINDOW)).allowed:
            accepted.append(clock.now)
        clock.advance(0.5)
    for i, start in enumerate(accepted):
        in_window = [t for t in accepted[i:] if t < start + 60]
        assert len(in_window) <= 10
    assert len(accepted) == 50  # 10 per minute, sustained


async def test_rejections_do_not_extend_lockout(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(clock=clock)
    await limiter.hit("k", 1, WINDOW)
    for _ in range(50):
        clock.advance(1)
        await limiter.hit("k", 1, WINDOW)
    clock.advance(10)  # t=60
    assert (await limiter.hit("k", 1, WINDOW)).allowed


async def test_keys_are_independent(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(clock=clock)
    await limiter.hit("a", 1, WINDOW)
    assert not (await limiter.hit("a", 1, WINDOW)).allowed
    assert (await limiter.hit("b", 1, WINDOW)).allowed


async def test_lowered_limit_is_enforced(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(clock=clock)
    for _ in range(5):
        await limiter.hit("k", 5, WINDOW)
        clock.advance(1)
    decision = await limiter.hit("k", 2, WINDOW)  # t=5, hits at 0..4
    assert not decision.allowed
    assert decision.retry_after == pytest.approx(58)  # the hit at t=3 leaves at t=63


async def test_non_positive_limit_is_an_error(clock: FakeClock) -> None:
    with pytest.raises(ValueError):
        await InMemoryRateLimiter(clock=clock).hit("k", 0, WINDOW)


async def test_idle_keys_are_swept(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(clock=clock)
    for i in range(20):
        await limiter.hit(f"k{i}", 5, WINDOW)
    clock.advance(max(limiter.SWEEP_INTERVAL_S, 60))
    await limiter.hit("fresh", 5, WINDOW)
    assert len(limiter) == 1


async def test_custom_window(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(clock=clock)
    await limiter.hit("k", 1, timedelta(seconds=5))
    clock.advance(5)
    assert (await limiter.hit("k", 1, timedelta(seconds=5))).allowed


# --------------------------------------------------------------------------- dependency


async def test_mutations_limited_to_ten_per_minute(
    client: httpx.AsyncClient, clock: FakeClock
) -> None:
    await burn(client, 10, "POST", "/v1/mutate", "alice")
    response = await client.post("/v1/mutate", headers=auth("alice"))
    assert response.status_code == 429
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.headers["retry-after"] == "60"
    body = response.json()
    assert body["type"] == PROBLEM_TYPE_BASE + "rate-limited"
    assert body["detail"] == "Rate limit of 10 mutation requests per minute exceeded."
    assert body["request_id"] == response.headers["x-request-id"]


async def test_reads_limited_to_sixty_per_minute(client: httpx.AsyncClient) -> None:
    await burn(client, 60, "GET", "/v1/read", "alice")
    assert (await client.get("/v1/read", headers=auth("alice"))).status_code == 429


async def test_limit_resets_after_window(client: httpx.AsyncClient, clock: FakeClock) -> None:
    await burn(client, 10, "POST", "/v1/mutate", "alice")
    clock.advance(59)
    assert (await client.post("/v1/mutate", headers=auth("alice"))).status_code == 429
    clock.advance(1)
    await burn(client, 10, "POST", "/v1/mutate", "alice")


async def test_retry_after_rounds_up(client: httpx.AsyncClient, clock: FakeClock) -> None:
    await burn(client, 10, "POST", "/v1/mutate", "alice")
    clock.advance(59.5)
    response = await client.post("/v1/mutate", headers=auth("alice"))
    assert response.headers["retry-after"] == "1"
    clock.advance(0.25)
    response = await client.post("/v1/mutate", headers=auth("alice"))
    assert response.headers["retry-after"] == "1"


async def test_reads_and_mutations_have_separate_budgets(client: httpx.AsyncClient) -> None:
    await burn(client, 10, "POST", "/v1/mutate", "alice")
    await burn(client, 60, "GET", "/v1/read", "alice")


async def test_keyed_by_uid_not_ip(app: FastAPI) -> None:
    async with (
        client_for(app, "198.51.100.1") as first_ip,
        client_for(app, "198.51.100.2") as other,
    ):
        await burn(first_ip, 10, "POST", "/v1/mutate", "alice")
        # Same user from another address: still limited.
        assert (await other.post("/v1/mutate", headers=auth("alice"))).status_code == 429
        # Another user from the same address: own budget.
        assert (await first_ip.post("/v1/mutate", headers=auth("bob"))).status_code == 200


async def test_unauthenticated_falls_back_to_ip(app: FastAPI) -> None:
    async with client_for(app, "192.0.2.10") as a, client_for(app, "192.0.2.11") as b:
        await burn(a, 60, "GET", "/v1/public", None)
        assert (await a.get("/v1/public")).status_code == 429
        assert (await b.get("/v1/public")).status_code == 200


async def test_authenticated_user_does_not_spend_ip_budget(app: FastAPI) -> None:
    async with client_for(app, "192.0.2.20") as c:
        await burn(c, 60, "GET", "/v1/public", "alice")
        assert (await c.get("/v1/public")).status_code == 200


async def test_rejected_token_is_401_not_counted_as_ip(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/public", headers=auth("mallory"))
    assert response.status_code == 401
