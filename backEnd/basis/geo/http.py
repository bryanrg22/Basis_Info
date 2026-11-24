"""The HTTP client every public source is fetched through (EVAL_REPORT 101).

These are public government servers, so the client is polite and bounded:

* **one request at a time per host, at least ``host_interval_s`` apart** (1 s by default), across
  every coroutine of the process: an ``asyncio.Lock`` and the last request's start per host;
* **timeouts:** 10 s to connect, ``timeout_s`` (60 s) for the whole response;
* **retries with backoff:** a timeout, a connection error, HTTP 429 or 5xx is tried again up
  to ``retries`` more times, waiting ``2, 4, 8 ...`` s (times 0.5 to 1.5 at random), or the
  ``Retry-After`` the server sent when it is longer (capped at 120 s). Other 4xx are not retried:
  asking again would get the same answer;
* a plain ``User-Agent`` naming the client, and nothing that identifies a study or a person
  (only the coordinates the request needs).

Failures raise :class:`SourceError` with the ``BASIS-GEO-<nnn>`` code the gatherer records.
"""

from __future__ import annotations

import asyncio
import email.utils
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from basis.errors.codes import E

USER_AGENT: Final = "Basis-public-geo/1.0 (property records; polite client)"
MAX_RETRY_AFTER_S: Final = 120.0
RETRY_STATUSES: Final = frozenset({429, 500, 502, 503, 504})


class SourceError(RuntimeError):
    """A public source did not give a usable answer."""

    def __init__(
        self, code: str, detail: str, *, url: str | None = None, status: int | None = None
    ) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.url = url
        self.status = status


@dataclass(frozen=True, slots=True)
class Fetched:
    url: str
    status: int
    content_type: str
    content: bytes
    seconds: float
    attempts: int

    def json(self) -> Any:
        import json

        try:
            return json.loads(self.content)
        except ValueError as exc:
            raise SourceError(
                E.GEO_SOURCE_BAD_RESPONSE, "the answer is not JSON", url=self.url
            ) from exc


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


@dataclass
class PoliteClient:
    """See the module docstring. ``sleep`` and ``clock`` are injectable so the tests run
    without waiting."""

    host_interval_s: float = 1.0
    timeout_s: float = 60.0
    retries: int = 3
    transport: httpx.AsyncBaseTransport | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    jitter: Callable[[], float] = random.random
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _last: dict[str, float] = field(default_factory=dict)
    _client: httpx.AsyncClient | None = None
    requests: int = 0

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self.transport,
                timeout=httpx.Timeout(self.timeout_s, connect=min(10.0, self.timeout_s)),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _turn(self, host: str) -> None:
        wait = self._last.get(host, -1e18) + self.host_interval_s - self.clock()
        if wait > 0:
            await self.sleep(wait)
        self._last[host] = self.clock()

    async def get(
        self,
        url: str,
        params: Mapping[str, str | int | float] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Fetched:
        host = urlsplit(url).netloc.lower()
        lock = self._locks.setdefault(host, asyncio.Lock())
        attempt = 0
        started = self.clock()
        while True:
            attempt += 1
            async with lock:
                await self._turn(host)
                self.requests += 1
                try:
                    response = await self._http().get(url, params=params, headers=headers)
                except httpx.TimeoutException as exc:
                    error: SourceError | None = SourceError(
                        E.GEO_SOURCE_TIMEOUT, f"no answer within {self.timeout_s:g} s", url=url
                    )
                    retry_after = None
                    cause: Exception = exc
                except httpx.HTTPError as exc:
                    error = SourceError(
                        E.GEO_SOURCE_HTTP, f"connection failed: {type(exc).__name__}", url=url
                    )
                    retry_after = None
                    cause = exc
                else:
                    if response.status_code < 400:
                        return Fetched(
                            url=str(response.url),
                            status=response.status_code,
                            content_type=response.headers.get("content-type", ""),
                            content=response.content,
                            seconds=self.clock() - started,
                            attempts=attempt,
                        )
                    error = SourceError(
                        E.GEO_SOURCE_HTTP,
                        f"HTTP {response.status_code}",
                        url=url,
                        status=response.status_code,
                    )
                    cause = error
                    if response.status_code not in RETRY_STATUSES:
                        raise error
                    retry_after = _retry_after(response.headers.get("retry-after"))
            assert error is not None
            if attempt > self.retries:
                raise error from cause
            backoff = (2.0**attempt) * (0.5 + self.jitter())
            if retry_after is not None:
                backoff = max(backoff, min(retry_after, MAX_RETRY_AFTER_S))
            await self.sleep(backoff)
