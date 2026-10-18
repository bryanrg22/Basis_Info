"""The emulated provider limits (``tests/load/rate_limits.py``)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from rate_limits import RETRY_AFTER_CAP_S, RateLimits  # noqa: E402

from basis.llm.errors import ProviderRateLimited, is_retryable_status  # noqa: E402
from basis.llm.provider import Usage  # noqa: E402

HEADERS = {
    "claude-sonnet-5-5": {
        "anthropic-ratelimit-requests-limit": "600",
        "anthropic-ratelimit-input-tokens-limit": "60000",
        "anthropic-ratelimit-output-tokens-limit": "6000",
        "anthropic-ratelimit-tokens-limit": "66000",  # the combined view: not a bucket
        "status": 200,
    },
    "gpt-6.1-sol": {"x-ratelimit-limit-requests": "60", "x-ratelimit-limit-tokens": "6000"},
}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def limits(scale: float = 1.0) -> tuple[RateLimits, Clock]:
    clock = Clock()
    rl = RateLimits.from_headers(HEADERS, scale=scale)
    rl.clock, rl.sleep = clock, clock.sleep
    return rl, clock


def test_buckets_come_from_the_limit_headers() -> None:
    rl, _ = limits()
    assert set(rl.models["claude-sonnet-5-5"].buckets) == {"RPM", "ITPM", "OTPM"}
    assert set(rl.models["gpt-6.1-sol"].buckets) == {"RPM", "TPM"}
    itpm = rl.models["claude-sonnet-5-5"].buckets["ITPM"]
    assert (itpm.capacity, itpm.refill_per_s) == (60000, 1000)


async def test_a_full_bucket_admits_its_capacity_then_waits_for_the_refill() -> None:
    rl, clock = limits()
    usage = Usage(input_tokens=20000, output_tokens=100)
    for _ in range(3):  # 3 x 20k = the 60k input-token capacity
        await rl.acquire("claude-sonnet-5-5", usage, "anthropic")
    assert clock.slept == []
    # 5k more needs 5 s of refill at 1k/s: one wait of 5 s, then admitted.
    await rl.acquire("claude-sonnet-5-5", Usage(input_tokens=5000, output_tokens=0), "anthropic")
    assert clock.slept == [pytest.approx(5.0)]
    assert rl.throttled == {"claude-sonnet-5-5": 1} and rl.refused == {}


async def test_still_spent_after_the_one_retry_is_a_retryable_429() -> None:
    rl, clock = limits()
    usage = Usage(input_tokens=30000, output_tokens=0)
    await rl.acquire("claude-sonnet-5-5", usage, "anthropic")
    await rl.acquire("claude-sonnet-5-5", usage, "anthropic")
    with pytest.raises(ProviderRateLimited) as caught:  # needs 30 s, retried after 10 s
        await rl.acquire("claude-sonnet-5-5", usage, "anthropic")
    assert caught.value.status == 429 and is_retryable_status(caught.value)
    assert rl.refused == {"claude-sonnet-5-5": 1}
    clock.now += 30  # refilled
    await rl.acquire("claude-sonnet-5-5", usage, "anthropic")


async def test_openai_counts_input_plus_output_and_requests() -> None:
    rl, clock = limits()
    for _ in range(2):
        await rl.acquire("gpt-6.1-sol", Usage(input_tokens=2000, output_tokens=1000), "openai")
    assert rl.models["gpt-6.1-sol"].buckets["TPM"].level == 0
    assert rl.models["gpt-6.1-sol"].buckets["RPM"].level == 58


async def test_unlisted_models_are_not_limited() -> None:
    rl, clock = limits()
    await rl.acquire("claude-haiku-5-5", Usage(input_tokens=10**9, output_tokens=0), "anthropic")
    assert clock.slept == []


async def test_a_refused_request_takes_nothing_from_the_other_buckets() -> None:
    rl, clock = limits()
    sonnet = rl.models["claude-sonnet-5-5"].buckets
    await rl.acquire("claude-sonnet-5-5", Usage(input_tokens=1, output_tokens=6000), "anthropic")
    big = Usage(input_tokens=1000, output_tokens=6000)  # OTPM spent: 60 s, retried after 10
    with pytest.raises(ProviderRateLimited):
        await rl.acquire("claude-sonnet-5-5", big, "anthropic")
    assert clock.slept == [RETRY_AFTER_CAP_S]
    assert sonnet["ITPM"].level == 60000  # refilled to capacity; the 1,000 were not taken
    assert sonnet["OTPM"].level == pytest.approx(1000)  # 10 s x 100/s


def test_the_refill_follows_the_time_scale() -> None:
    rl, _ = limits(scale=0.1)
    assert rl.models["claude-sonnet-5-5"].buckets["ITPM"].refill_per_s == pytest.approx(10000)
    with pytest.raises(ValueError):
        RateLimits.from_headers(HEADERS, scale=0)
