"""Router factories and shared dependencies for the ``/v1`` API.

Every ``/v1`` route is on one of two kinds of router:

* :func:`read_router`: ``GET``. Dependencies: read rate limit, then the current user.
* :func:`write_router`: ``POST``/``DELETE``, ``route_class=IdempotentRoute``.
  Dependencies: mutation rate limit, then the current user, then idempotency.

The order is deliberate. The rate limit runs first so a flood is refused before any
database work (it keys on the verified uid, and token verification runs once per request
because FastAPI caches dependencies). The current user runs before idempotency because
the Postgres idempotency store scopes keys by ``users.id`` and needs the user committed.

Error responses are documented per router with :func:`problems`, so the OpenAPI document
lists every status a route can answer with and its ``application/problem+json`` body.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, Depends, Request

from basis.api.errors import PROBLEM_JSON, status_phrase
from basis.api.idempotency import IdempotentRoute, idempotency
from basis.api.ratelimit import rate_limited
from basis.api.users import get_current_user
from basis.storage import Storage

API_PREFIX: Final = "/v1"
PROBLEM_SCHEMA_REF: Final = "#/components/schemas/ProblemDetail"

_PROBLEM_DESCRIPTIONS: Final[dict[int, str]] = {
    400: "Missing or malformed `Idempotency-Key`.",
    401: "Missing, malformed, expired or revoked bearer token.",
    403: "Your role is too low for this action (the study or firm is visible to you); "
    "`no-firm`: you are not a member of any firm; `email-unverified`: your sign-in does not "
    "verify the invited email.",
    404: "Not found, or not visible to you (the two are indistinguishable).",
    409: "Conflicts with the current state (stale stage or revision, duplicate, archived, "
    "upload mismatch, or the same `Idempotency-Key` still in flight).",
    412: "`precondition-failed`: the `If-Match` entity tag is not the current one (the "
    "resource changed since you read it); nothing was written. Read it again and retry with "
    "the new `ETag`.",
    413: "The request body is larger than 1 MiB.",
    422: "Validation failed (`errors` lists each field), or an `Idempotency-Key` was reused "
    "with a different request.",
    428: "`precondition-required`: this deployment requires `If-Match` on this write.",
    429: "Rate limit exceeded; see `Retry-After`.",
    503: "A dependency (the database) is unavailable; see `Retry-After`.",
}


def problems(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` entries whose body is a problem details object."""
    return {
        status: {
            "description": _PROBLEM_DESCRIPTIONS.get(status, status_phrase(status)),
            "content": {PROBLEM_JSON: {"schema": {"$ref": PROBLEM_SCHEMA_REF}}},
        }
        for status in statuses
    }


def read_router(*, tags: list[str]) -> APIRouter:
    return APIRouter(
        prefix=API_PREFIX,
        tags=list(tags),
        dependencies=[Depends(rate_limited("read")), Depends(get_current_user)],
        responses=problems(401, 422, 429, 503),
    )


def write_router(*, tags: list[str]) -> APIRouter:
    return APIRouter(
        prefix=API_PREFIX,
        tags=list(tags),
        route_class=IdempotentRoute,
        dependencies=[
            Depends(rate_limited("mutation")),
            Depends(get_current_user),
            Depends(idempotency),
        ],
        responses=problems(400, 401, 409, 413, 422, 429, 503),
    )


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


def get_storage_bucket(request: Request) -> str:
    bucket: str = request.app.state.storage_bucket
    return bucket
