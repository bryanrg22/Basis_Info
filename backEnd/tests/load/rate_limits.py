"""Our accounts' provider rate limits, emulated for the fake-provider worker.

``LOAD_PROVIDER_LIMITS=<provider_limits.json>`` (the headers ``tooling/load/provider_limits.py``
read from the real providers) makes :class:`load_fakes.LoadProvider` answer 429 the way the
real providers do when a model's limits are spent, so a load step on fake providers meets
the ceilings it would meet on real ones (EVAL_REPORT 31.6).

Model: one token bucket per model and limit, as Anthropic documents its limits ("capacity
is continuously replenished up to your maximum limit"; OpenAI's per-minute limits behave
the same way at our rates): capacity = the per-minute limit, refilled at limit / 60 per
second. Anthropic models have RPM, ITPM and OTPM buckets; OpenAI models RPM and TPM (input
plus output). A request takes its tokens when it starts (the profile's mean tokens of its
prompt family), all buckets at once or none.

What a spent bucket does mirrors the real providers (``basis.llm.providers``,
``basis.llm.anthropic_provider``): wait the Retry-After (the time until the bucket refills
enough, capped at ``RETRY_AFTER_CAP_S``), try once more, and raise
:class:`~basis.llm.errors.ProviderRateLimited` (HTTP 429) if it is still spent. Callers
then do what they do in production: the activity retries, the classifier records a
``provider_error`` and flags the component.

At a time scale ``s`` the work runs ``1/s`` times faster, so the refill rate is divided by
``s`` too: the headroom against the limit is the real one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from basis.llm.errors import ProviderRateLimited
from basis.llm.provider import Usage

RETRY_AFTER_CAP_S: Final = 10.0  # the real providers' cap (basis.llm.providers)

logger = logging.getLogger("basis.load.rate_limits")


@dataclass
class Bucket:
    capacity: float
    refill_per_s: float
    level: float = -1.0
    stamp: float = 0.0

    def refill(self, now: float) -> None:
        if self.level < 0:
            self.level, self.stamp = self.capacity, now
            return
        self.level = min(self.capacity, self.level + (now - self.stamp) * self.refill_per_s)
        self.stamp = now

    def wait_for(self, amount: float) -> float:
        """Seconds until ``amount`` fits (0 when it fits now)."""
        if amount <= self.level:
            return 0.0
        if amount > self.capacity:
            return float("inf")
        return (amount - self.level) / self.refill_per_s


@dataclass
class ModelLimits:
    """The buckets of one model: ``{"RPM": Bucket, "ITPM": Bucket, ...}``."""

    buckets: dict[str, Bucket]

    def demand(self, usage: Usage) -> dict[str, float]:
        tokens = usage.input_tokens + usage.output_tokens
        amounts = {
            "RPM": 1.0,
            "ITPM": usage.input_tokens,
            "OTPM": usage.output_tokens,
            "TPM": tokens,
        }
        return {k: float(amounts[k]) for k in self.buckets}


HEADERS: Final = {
    "anthropic-ratelimit-requests-limit": "RPM",
    "anthropic-ratelimit-input-tokens-limit": "ITPM",
    "anthropic-ratelimit-output-tokens-limit": "OTPM",
    "x-ratelimit-limit-requests": "RPM",
    "x-ratelimit-limit-tokens": "TPM",
}


@dataclass
class RateLimits:
    """Per-model buckets; :meth:`acquire` takes a request's share or raises 429."""

    models: dict[str, ModelLimits]
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Any] = asyncio.sleep
    throttled: dict[str, int] = field(default_factory=dict)
    refused: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_headers(cls, limits: Mapping[str, Any], *, scale: float = 1.0) -> RateLimits:
        if scale <= 0:
            raise ValueError("rate limits need a positive time scale")
        models: dict[str, ModelLimits] = {}
        for model, headers in limits.items():
            buckets = {}
            for header, value in headers.items():
                name = HEADERS.get(header.lower())
                if name is not None:
                    per_minute = float(value)
                    buckets[name] = Bucket(per_minute, per_minute / 60.0 / scale)
            if buckets:
                models[model] = ModelLimits(buckets)
        return cls(models)

    @classmethod
    def from_file(cls, path: Path, *, scale: float = 1.0) -> RateLimits:
        return cls.from_headers(json.loads(path.read_text("utf-8")), scale=scale)

    def _try(self, model: str, usage: Usage) -> float:
        """Take the request's share now (0.0) or return the wait until it fits."""
        limits = self.models.get(model)
        if limits is None:
            return 0.0
        now = self.clock()
        demand = limits.demand(usage)
        wait = 0.0
        for name, amount in demand.items():
            bucket = limits.buckets[name]
            bucket.refill(now)
            wait = max(wait, bucket.wait_for(amount))
        if wait == 0.0:
            for name, amount in demand.items():
                limits.buckets[name].level -= amount
        return wait

    async def acquire(self, model: str, usage: Usage, provider: str) -> None:
        wait = self._try(model, usage)
        if wait == 0.0:
            return
        self.throttled[model] = self.throttled.get(model, 0) + 1
        delay = min(wait, RETRY_AFTER_CAP_S)
        # The real providers' line (counted by tooling/load/report.py as provider_429).
        logger.warning(
            "%s returned 429; retrying once in %.2fs (emulated %s)", provider, delay, model
        )
        await self.sleep(delay)  # the provider's one retry
        if self._try(model, usage) == 0.0:
            return
        self.refused[model] = self.refused.get(model, 0) + 1
        raise ProviderRateLimited(
            f"{provider} returned 429 (emulated {model} limit)", provider=provider, status=429
        )
