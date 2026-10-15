"""RFC 9457 problem details for every error the API returns.

Shape (``application/problem+json``)::

    {"type": "...", "title": "...", "status": 404, "detail": "...",
     "instance": "/v1/studies/...", "request_id": "...", "errors": [...]}

* ``type`` is ``about:blank`` for plain HTTP errors (RFC 9457 section 4.2.1: the title is
  then the HTTP status phrase) and ``PROBLEM_TYPE_BASE + <suffix>`` for errors the API
  raises on purpose, so clients can branch on it.
* ``instance`` is the request path. ``request_id`` is the id bound by
  :class:`~basis.api.middleware.RequestIdMiddleware` (also sent as ``X-Request-Id``).
* ``errors`` is present only on validation failures: one entry per field with its
  location, message and pydantic error type. The rejected input is never echoed back,
  since it may contain secrets.

Unexpected exceptions never reach the client: :class:`UnhandledErrorMiddleware` logs
them with ``stack_trace`` and the request id, and answers a generic 500.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from basis.obs.logging import get_context

PROBLEM_JSON: Final = "application/problem+json"
# RFC 9457 recommends absolute type URIs that the API owner controls; they need not
# resolve. ``.invalid`` (RFC 2606) is reserved, so this cannot collide with anyone
# else's domain. Swap in the product domain once one exists; clients match on the suffix.
PROBLEM_TYPE_BASE: Final = "https://basis.invalid/problems/"
ABOUT_BLANK: Final = "about:blank"
INTERNAL_ERROR_DETAIL: Final = (
    "An unexpected error occurred. Quote the request id when reporting it."
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- models


class FieldError(BaseModel):
    """One validation failure."""

    model_config = ConfigDict(frozen=True)

    loc: list[str | int]
    detail: str
    type: str


class ProblemDetail(BaseModel):
    """RFC 9457 problem details object, plus ``request_id`` and ``errors`` extensions."""

    model_config = ConfigDict(frozen=True)

    type: str = ABOUT_BLANK
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None
    errors: list[FieldError] | None = None


def status_phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


# --------------------------------------------------------------------------- exceptions


class ApiError(Exception):
    """An error the API raises deliberately; rendered as a problem response.

    Subclasses fix ``status``/``title``/``type_suffix``; callers supply ``detail``, a
    human-readable explanation of this occurrence that is safe to show to the client.
    """

    status: int = 500
    title: str = "Internal Server Error"
    type_suffix: str | None = None

    def __init__(
        self,
        status: int | None = None,
        title: str | None = None,
        detail: str | None = None,
        type_suffix: str | None = None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if status is not None:
            self.status = status
        if title is not None:
            self.title = title
        elif status is not None:
            self.title = status_phrase(status)
        if type_suffix is not None:
            self.type_suffix = type_suffix
        self.detail = detail
        self.headers: dict[str, str] = dict(headers or {})
        super().__init__(detail or self.title)

    @property
    def type_uri(self) -> str:
        return PROBLEM_TYPE_BASE + self.type_suffix if self.type_suffix else ABOUT_BLANK


class BadRequest(ApiError):
    status = 400
    title = "Bad Request"
    type_suffix = "bad-request"

    def __init__(self, detail: str, *, type_suffix: str | None = None) -> None:
        super().__init__(detail=detail, type_suffix=type_suffix)


class Unauthorized(ApiError):
    """401 with ``WWW-Authenticate: Bearer`` (RFC 6750 section 3).

    ``error`` is the RFC 6750 error code: ``invalid_token`` when a token was presented and
    rejected, ``None`` when no usable credentials were presented at all.
    """

    status = 401
    title = "Unauthorized"
    type_suffix = "unauthorized"

    def __init__(self, detail: str, *, error: str | None = "invalid_token") -> None:
        challenge = f'Bearer error="{error}"' if error else "Bearer"
        super().__init__(detail=detail, headers={"WWW-Authenticate": challenge})


class Forbidden(ApiError):
    status = 403
    title = "Forbidden"
    type_suffix = "forbidden"

    def __init__(self, detail: str) -> None:
        super().__init__(detail=detail)


class NotFound(ApiError):
    status = 404
    title = "Not Found"
    type_suffix = "not-found"

    def __init__(self, detail: str) -> None:
        super().__init__(detail=detail)


class Conflict(ApiError):
    status = 409
    title = "Conflict"
    type_suffix = "conflict"

    def __init__(self, detail: str, *, type_suffix: str | None = None) -> None:
        super().__init__(detail=detail, type_suffix=type_suffix)


class Unprocessable(ApiError):
    status = 422
    title = "Unprocessable Content"
    type_suffix = "unprocessable"

    def __init__(self, detail: str, *, type_suffix: str | None = None) -> None:
        super().__init__(detail=detail, type_suffix=type_suffix)


class TooManyRequests(ApiError):
    """429 with ``Retry-After`` in whole seconds (RFC 9110 section 10.2.3), at least 1."""

    status = 429
    title = "Too Many Requests"
    type_suffix = "rate-limited"

    def __init__(self, detail: str, *, retry_after: int) -> None:
        self.retry_after = max(1, int(retry_after))
        super().__init__(detail=detail, headers={"Retry-After": str(self.retry_after)})


# --------------------------------------------------------------------------- rendering


def current_request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def problem_response(
    *,
    status: int,
    title: str | None = None,
    detail: str | None = None,
    type_uri: str = ABOUT_BLANK,
    instance: str | None = None,
    errors: Sequence[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """Build a problem response. ``request_id`` comes from the logging context."""
    problem = ProblemDetail(
        type=type_uri,
        title=title or status_phrase(status),
        status=status,
        detail=detail,
        instance=instance,
        request_id=current_request_id(),
        errors=list(errors) if errors is not None else None,
    )
    return Response(
        content=problem.model_dump_json(exclude_none=True),
        status_code=status,
        headers=dict(headers or {}),
        media_type=PROBLEM_JSON,
    )


def api_error_response(request: Request, exc: ApiError) -> Response:
    return problem_response(
        status=exc.status,
        title=exc.title,
        detail=exc.detail,
        type_uri=exc.type_uri,
        instance=request.url.path,
        headers=exc.headers,
    )


def field_errors(raw: Sequence[Any]) -> list[FieldError]:
    """Reduce pydantic error dicts to location, message and type; drop ``input``/``ctx``."""
    out: list[FieldError] = []
    for item in raw:
        loc = [part if isinstance(part, int) else str(part) for part in item.get("loc", ())]
        out.append(
            FieldError(loc=loc, detail=str(item.get("msg", "")), type=str(item.get("type", "")))
        )
    return out


# --------------------------------------------------------------------------- handlers


async def _handle_api_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ApiError)
    return api_error_response(request, exc)


async def _handle_validation_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    return problem_response(
        status=422,
        detail="The request did not pass validation; see errors.",
        type_uri=PROBLEM_TYPE_BASE + "validation",
        title="Unprocessable Content",
        instance=request.url.path,
        errors=field_errors(exc.errors()),
    )


async def _handle_http_exception(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    detail: str | None = exc.detail if isinstance(exc.detail, str) else None
    if detail == status_phrase(exc.status_code):
        detail = None  # Starlette's default detail just repeats the title.
    return problem_response(
        status=exc.status_code,
        detail=detail,
        instance=request.url.path,
        headers=exc.headers,
    )


class UnhandledErrorMiddleware:
    """Turn any exception that escaped the handlers into a logged, opaque 500.

    It must sit inside :class:`~basis.api.middleware.RequestIdMiddleware` so the request
    id is still bound when the exception is logged and the response still gets
    ``X-Request-Id``. Starlette's own ``Exception`` handler slot runs in the outermost
    middleware, outside the request-id context, which is why this is a middleware and
    not ``app.add_exception_handler(Exception, ...)``.

    If the response had already started, nothing can be sent; the exception is logged
    and re-raised so the server closes the connection.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            logger.exception(
                "unhandled exception",
                extra={
                    "http_method": scope.get("method"),
                    "path": scope.get("path"),
                    "exception_type": type(exc).__qualname__,
                },
            )
            if started:
                raise
            response = problem_response(
                status=500,
                detail=INTERNAL_ERROR_DETAIL,
                instance=scope.get("path"),
            )
            await response(scope, receive, send)


def install_error_handlers(app: FastAPI) -> None:
    """Register the problem-details handlers and the 500 middleware on ``app``.

    Call this before adding ``RequestIdMiddleware``: middleware added later wraps
    middleware added earlier, and the 500 middleware must be the inner one.
    """
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_middleware(UnhandledErrorMiddleware)
