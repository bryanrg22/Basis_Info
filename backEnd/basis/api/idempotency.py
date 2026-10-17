"""Idempotency keys for mutating requests (S1_SPEC section 5).

Rules, following the IETF ``Idempotency-Key`` header draft
(draft-ietf-httpapi-idempotency-key-header) and Stripe's published behaviour:

* ``POST`` and ``DELETE`` require ``Idempotency-Key``: 1 to 128 visible ASCII characters
  (a UUID is recommended). The draft's structured-field form ``"..."`` is accepted too.
  Missing or malformed: 400.
* Keys are scoped per principal: user A's key never matches user B's.
* The request fingerprint is ``sha256(method, path, query, canonical JSON body)``.
  Canonical JSON sorts keys and drops insignificant whitespace, so a client that
  re-serializes the same object still matches.
* First use: the key is reserved (in-flight) before the handler runs. The handler's
  response is stored for 24 hours when its status is below 500, including deliberate
  4xx problems such as a 409; 5xx and unexpected exceptions release the key so the
  client can retry with it.
* Same key, same fingerprint, completed: the stored status, body and headers are
  replayed with ``Idempotent-Replayed: true``; the handler does not run.
* Same key, different fingerprint: 422 (also while the first is in flight).
* Same key, same fingerprint, still in flight: 409, so a double submit cannot run the
  handler twice concurrently.
* Request validation failures (422 from FastAPI) are not stored: nothing executed.

Wiring: routers that mutate use ``route_class=IdempotentRoute`` and the
:func:`idempotency` dependency (router-level is fine; it ignores safe methods). The
dependency checks and reserves; the route class records the response. The dependency
raises a RuntimeError if it finds itself on a plain route, so a missing ``route_class``
fails loudly in tests instead of silently never storing responses. ``/health`` is not
on these routers and is unaffected.

The store is an :class:`IdempotencyStore`; :class:`InMemoryIdempotencyStore` is for a
single process and tests. :class:`basis.api.idempotency_pg.PostgresIdempotencyStore`
(table ``idempotency_keys``) is used whenever a database is configured; it treats a row
with a NULL status as in flight and expired rows as absent.

Repository errors raised by a handler (``NotVisibleError``, ``ConflictError``, ...) are
mapped with :func:`basis.api.errors.as_api_error` and stored like any deliberate 4xx.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Coroutine, Iterable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, Final, NamedTuple, Protocol

from fastapi import Depends, Header, Request
from fastapi.routing import APIRoute
from starlette.responses import Response

from basis.api.auth import CurrentPrincipal
from basis.api.errors import (
    BadRequest,
    Conflict,
    Unprocessable,
    api_error_response,
    as_api_error,
)

IDEMPOTENCY_HEADER: Final = "Idempotency-Key"
REPLAYED_HEADER: Final = "Idempotent-Replayed"
IDEMPOTENCY_TTL: Final = timedelta(hours=24)
# An in-flight reservation outlives any request (Cloud Run's maximum is 60 minutes, ours
# are far shorter) but frees the key if the process died before completing it.
IN_FLIGHT_TTL: Final = timedelta(minutes=10)
MAX_KEY_LENGTH: Final = 128
ENFORCED_METHODS: Final = frozenset({"POST", "DELETE"})
STATE_ATTR: Final = "idempotency"
# FastAPI (>= 0.121) keeps the exit stack of function-scoped dependencies here.
FUNCTION_STACK: Final = "fastapi_function_astack"

# Visible ASCII except the double quote, which delimits the optional quoted form
# (draft-ietf-httpapi-idempotency-key-header: the value is a quoted-string). This is
# exactly the pattern the OpenAPI document publishes (basis.api.openapi), so a key the
# document calls invalid is refused and a key it calls valid is accepted.
_KEY_PATTERN: Final = re.compile(rf"[\x21\x23-\x7e]{{1,{MAX_KEY_LENGTH}}}")
# Response headers that belong to one specific response and are regenerated on replay.
_NOT_REPLAYED: Final = frozenset({"content-length", "x-request-id", "date", "server"})

DETAIL_MISSING: Final = f"The {IDEMPOTENCY_HEADER} header is required for this request."
DETAIL_INVALID: Final = (
    f"{IDEMPOTENCY_HEADER} must be 1 to {MAX_KEY_LENGTH} visible ASCII characters "
    "(no double quotes; the whole key may be enclosed in them); a UUID is recommended."
)
DETAIL_REUSED: Final = f"This {IDEMPOTENCY_HEADER} was already used with a different request."
DETAIL_IN_FLIGHT: Final = (
    f"A request with this {IDEMPOTENCY_HEADER} is still being processed; retry shortly."
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- store


class UserKey(NamedTuple):
    """An idempotency key scoped to the principal that sent it."""

    uid: str
    key: str


@dataclass(frozen=True, slots=True)
class StoredResponse:
    """A reservation (``status is None``) or a completed response."""

    request_hash: str
    status: int | None
    body: bytes
    headers: tuple[tuple[str, str], ...]
    expires_at: float

    @property
    def in_progress(self) -> bool:
        return self.status is None


class IdempotencyStore(Protocol):
    async def get(self, user_key: UserKey) -> StoredResponse | None:
        """The live (unexpired) record for ``user_key``, if any."""
        ...

    async def reserve(
        self, user_key: UserKey, request_hash: str, ttl: timedelta, *, route: str = ""
    ) -> StoredResponse | None:
        """Atomically create an in-flight record unless a live one exists.

        Returns ``None`` when this call created the reservation, otherwise the existing
        record (in flight or completed). ``route`` (``"POST /v1/studies"``) is recorded for
        diagnostics only; it is not part of the key.
        """
        ...

    async def put(
        self,
        user_key: UserKey,
        request_hash: str,
        status: int,
        body: bytes,
        headers: Iterable[tuple[str, str]],
        ttl: timedelta,
    ) -> None:
        """Store the completed response, replacing the reservation."""
        ...

    async def release(self, user_key: UserKey, *, request_hash: str | None = None) -> None:
        """Drop an in-flight reservation; a completed record is left alone.

        With ``request_hash``, only a reservation for that fingerprint is dropped, so a
        request whose reservation expired and was re-made by another request cannot
        release the newer one.
        """
        ...


class InMemoryIdempotencyStore:
    """Process-local store. Safe within one event loop: no method awaits mid-update."""

    SWEEP_INTERVAL_S: Final = 60.0

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._records: dict[UserKey, StoredResponse] = {}
        self._next_sweep = clock() + self.SWEEP_INTERVAL_S

    def __len__(self) -> int:
        return len(self._records)

    def _live(self, user_key: UserKey) -> StoredResponse | None:
        record = self._records.get(user_key)
        if record is not None and record.expires_at <= self._clock():
            del self._records[user_key]
            return None
        return record

    def _sweep(self) -> None:
        now = self._clock()
        if now < self._next_sweep:
            return
        self._next_sweep = now + self.SWEEP_INTERVAL_S
        expired = [k for k, r in self._records.items() if r.expires_at <= now]
        for k in expired:
            del self._records[k]

    async def get(self, user_key: UserKey) -> StoredResponse | None:
        return self._live(user_key)

    async def reserve(
        self, user_key: UserKey, request_hash: str, ttl: timedelta, *, route: str = ""
    ) -> StoredResponse | None:
        self._sweep()
        existing = self._live(user_key)
        if existing is not None:
            return existing
        self._records[user_key] = StoredResponse(
            request_hash=request_hash,
            status=None,
            body=b"",
            headers=(),
            expires_at=self._clock() + ttl.total_seconds(),
        )
        return None

    async def put(
        self,
        user_key: UserKey,
        request_hash: str,
        status: int,
        body: bytes,
        headers: Iterable[tuple[str, str]],
        ttl: timedelta,
    ) -> None:
        self._records[user_key] = StoredResponse(
            request_hash=request_hash,
            status=status,
            body=body,
            headers=tuple(headers),
            expires_at=self._clock() + ttl.total_seconds(),
        )

    async def release(self, user_key: UserKey, *, request_hash: str | None = None) -> None:
        record = self._records.get(user_key)
        if record is None or not record.in_progress:
            return
        if request_hash is None or record.request_hash == request_hash:
            del self._records[user_key]


# --------------------------------------------------------------------------- fingerprint


def parse_idempotency_key(raw: str | None) -> str:
    if raw is None:
        raise BadRequest(DETAIL_MISSING, type_suffix="idempotency-key-missing")
    value = raw
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if not _KEY_PATTERN.fullmatch(value):
        raise BadRequest(DETAIL_INVALID, type_suffix="idempotency-key-invalid")
    return value


def canonical_body(body: bytes) -> bytes:
    """Canonical JSON for a JSON body; the raw bytes for anything else."""
    if not body.strip():
        return b""
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return body
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def request_hash(method: str, path: str, query: str, body: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(f"{method.upper()}\n{path}\n{query}\n".encode())
    digest.update(canonical_body(body))
    return digest.hexdigest()


# --------------------------------------------------------------------------- wiring


def get_idempotency_store(request: Request) -> IdempotencyStore:
    store: IdempotencyStore = request.app.state.idempotency_store
    return store


class _Replay(Exception):
    """Raised by the dependency to short-circuit the handler with a stored response."""

    def __init__(self, stored: StoredResponse) -> None:
        super().__init__("idempotent replay")
        self.stored = stored


@dataclass(slots=True)
class _Pending:
    store: IdempotencyStore
    user_key: UserKey
    request_hash: str

    async def complete(self, response: Response) -> None:
        body = getattr(response, "body", None)
        if response.status_code >= 500 or not isinstance(body, bytes):
            if not isinstance(body, bytes):
                logger.warning("idempotent route returned a streaming response; not stored")
            await self.abandon()
            return
        headers = [
            (name.decode("latin-1"), value.decode("latin-1"))
            for name, value in response.raw_headers
            if name.decode("latin-1").lower() not in _NOT_REPLAYED
        ]
        await self.store.put(
            self.user_key, self.request_hash, response.status_code, body, headers, IDEMPOTENCY_TTL
        )

    async def abandon(self) -> None:
        try:
            await self.store.release(self.user_key, request_hash=self.request_hash)
        except Exception:
            # The reservation then expires after IN_FLIGHT_TTL; the original error matters more.
            logger.exception("failed to release idempotency reservation")


def replay_response(stored: StoredResponse) -> Response:
    assert stored.status is not None
    response = Response(content=stored.body, status_code=stored.status)
    response.raw_headers.extend(
        (name.encode("latin-1"), value.encode("latin-1")) for name, value in stored.headers
    )
    response.raw_headers.append((REPLAYED_HEADER.lower().encode("latin-1"), b"true"))
    return response


def _pending(request: Request) -> _Pending | None:
    pending = getattr(request.state, STATE_ATTR, None)
    return pending if isinstance(pending, _Pending) else None


async def idempotency(
    request: Request,
    principal: CurrentPrincipal,
    store: Annotated[IdempotencyStore, Depends(get_idempotency_store)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias=IDEMPOTENCY_HEADER,
            description="Required on POST and DELETE. 1 to 128 visible ASCII characters; "
            "a UUID is recommended. Retries with the same key and body replay the response.",
        ),
    ] = None,
) -> None:
    """Enforce idempotency keys on POST/DELETE; no-op on other methods."""
    if request.method not in ENFORCED_METHODS:
        return
    if not isinstance(request.scope.get("route"), IdempotentRoute):
        raise RuntimeError("the idempotency dependency requires route_class=IdempotentRoute")
    key = parse_idempotency_key(idempotency_key)
    fingerprint = request_hash(
        request.method,
        request.url.path,
        request.scope.get("query_string", b"").decode("latin-1"),
        await request.body(),
    )
    user_key = UserKey(principal.uid, key)
    route = request.scope.get("route")
    route_label = f"{request.method} {getattr(route, 'path', request.url.path)}"
    existing = await store.reserve(user_key, fingerprint, IN_FLIGHT_TTL, route=route_label)
    if existing is None:
        setattr(request.state, STATE_ATTR, _Pending(store, user_key, fingerprint))
        return
    if existing.request_hash != fingerprint:
        raise Unprocessable(DETAIL_REUSED, type_suffix="idempotency-key-reused")
    if existing.in_progress:
        raise Conflict(DETAIL_IN_FLIGHT, type_suffix="idempotency-key-in-flight")
    raise _Replay(existing)


Handler = Callable[[Request], Coroutine[Any, Any, Response]]


class IdempotentRoute(APIRoute):
    """Route class that records responses for requests reserved by :func:`idempotency`.

    The response is stored (or the reservation released) **after the request's own
    transaction has ended and before the response is sent**: a callback on FastAPI's
    function-scoped exit stack (``fastapi_function_astack``), registered before the
    route's dependencies are solved, so it exits after them (last in, first out), after
    ``SessionDep`` committed or rolled back. Two reasons (EVAL_REPORT section 31.3):

    * one pooled connection per request. Storing from inside the handler ran while the
      request's session still held its connection, so every POST and DELETE held two at
      once; with ``DB_POOL_SIZE`` concurrent mutations each held one and waited for a
      second, and the pool deadlocked until ``pool_timeout`` (every one answered 503);
    * the stored response describes committed data. Stored first, a commit that failed
      afterwards left a 2xx to replay for rows that were never written; now a failed
      commit releases the key instead (the client's retry runs the handler again).

    Without the function stack (a handler run outside FastAPI's request wrapper) the
    response is stored as the handler returns, as before.
    """

    def get_route_handler(self) -> Handler:
        handler = super().get_route_handler()

        async def idempotent_handler(request: Request) -> Response:
            stack = request.scope.get(FUNCTION_STACK)
            deferred = isinstance(stack, AsyncExitStack)
            outcome: list[Response] = []

            async def finish(
                exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any
            ) -> bool:
                pending = _pending(request)
                if pending is not None:
                    if exc_type is None and outcome:
                        await pending.complete(outcome[0])
                    else:
                        await pending.abandon()
                return False  # never swallow the request's exception

            if deferred:
                assert isinstance(stack, AsyncExitStack)
                stack.push_async_exit(finish)

            async def record(response: Response) -> Response:
                pending = _pending(request)
                if pending is not None:
                    if deferred:
                        outcome.append(response)
                    else:
                        await pending.complete(response)
                return response

            try:
                response = await handler(request)
            except _Replay as replay:
                return replay_response(replay.stored)
            except BaseException as exc:
                pending = _pending(request)
                if pending is None:
                    raise
                api_error = as_api_error(exc) if isinstance(exc, Exception) else None
                if api_error is None or api_error.status >= 500:
                    if not deferred:
                        await pending.abandon()
                    raise  # deferred: finish() sees the exception and releases the key
                return await record(api_error_response(request, api_error))
            return await record(response)

        return idempotent_handler
