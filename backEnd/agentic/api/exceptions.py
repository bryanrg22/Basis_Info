"""
Custom exceptions with safe error messages.

Provides exception classes and handlers that separate internal error details
from public error messages to prevent information leakage.

Phase 6 Enhancement: Sends Slack alerts for API errors.
"""

import asyncio
import logging
from typing import Optional

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse


logger = logging.getLogger(__name__)


async def _send_api_alert(request: Request, error_type: str, message: str, status_code: int):
    """Send alert for API errors (non-blocking)."""
    try:
        from ..observability.alerts import send_alert

        # Extract study_id from request if available
        study_id = "unknown"
        try:
            if request.method == "POST":
                body = await request.json()
                study_id = body.get("study_id", "unknown")
            elif "study_id" in request.path_params:
                study_id = request.path_params["study_id"]
        except Exception:
            pass

        # Determine severity
        severity = "critical" if status_code >= 500 else "error"

        await send_alert(
            study_id=study_id,
            stage=f"API:{request.url.path}",
            error_type=error_type,
            error_message=message,
            severity=severity,
            context={
                "method": request.method,
                "path": str(request.url.path),
                "status_code": status_code,
                "client_ip": request.client.host if request.client else "unknown",
            },
        )
    except Exception as e:
        logger.warning(f"Failed to send API alert: {e}")


class APIError(Exception):
    """
    Base API exception with safe public message.

    The public_message is returned to clients.
    The internal_message is logged but never exposed.
    """

    def __init__(
        self,
        public_message: str = "An error occurred",
        internal_message: Optional[str] = None,
        status_code: int = 500,
    ):
        self.public_message = public_message
        self.internal_message = internal_message or public_message
        self.status_code = status_code
        super().__init__(self.internal_message)


class WorkflowError(APIError):
    """Error during workflow execution."""

    def __init__(
        self,
        public_message: str = "Workflow operation failed",
        internal_message: Optional[str] = None,
    ):
        super().__init__(
            public_message=public_message,
            internal_message=internal_message,
            status_code=500,
        )


class ValidationError(APIError):
    """Input validation error."""

    def __init__(
        self,
        public_message: str = "Invalid input",
        internal_message: Optional[str] = None,
    ):
        super().__init__(
            public_message=public_message,
            internal_message=internal_message,
            status_code=422,
        )


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """
    Handle APIError exceptions.

    Logs the internal message and returns the safe public message.
    Sends Slack alert for server errors (5xx).
    """
    logger.error(
        "API Error [%s %s]: %s",
        request.method,
        request.url.path,
        exc.internal_message,
        exc_info=True,
    )

    # Send alert for server errors (5xx)
    if exc.status_code >= 500:
        asyncio.create_task(_send_api_alert(
            request,
            error_type=type(exc).__name__,
            message=exc.internal_message,
            status_code=exc.status_code,
        ))

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.public_message},
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all exception handler.

    Logs the full exception but returns a generic message to clients.
    Never leaks internal error details.
    Sends Slack alert for all unhandled exceptions.
    """
    logger.exception(
        "Unhandled exception [%s %s]: %s",
        request.method,
        request.url.path,
        str(exc),
    )

    # Send alert for unhandled exceptions
    asyncio.create_task(_send_api_alert(
        request,
        error_type=f"UNHANDLED_{type(exc).__name__}",
        message=str(exc),
        status_code=500,
    ))

    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred"},
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Handle FastAPI HTTPException (including auth errors).

    Sends Slack alert for server errors (5xx) and repeated auth failures.
    """
    logger.warning(
        "HTTP Exception [%s %s]: %d - %s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.detail,
    )

    # Send alert for server errors and auth failures on workflow endpoints
    if exc.status_code >= 500 or (exc.status_code == 401 and "/workflow" in request.url.path):
        asyncio.create_task(_send_api_alert(
            request,
            error_type=f"HTTP_{exc.status_code}",
            message=str(exc.detail),
            status_code=exc.status_code,
        ))

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )
