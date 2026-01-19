"""
Custom exceptions with safe error messages.

Provides exception classes and handlers that separate internal error details
from public error messages to prevent information leakage.
"""

import logging
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse


logger = logging.getLogger(__name__)


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
    """
    logger.error(
        "API Error [%s %s]: %s",
        request.method,
        request.url.path,
        exc.internal_message,
        exc_info=True,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.public_message},
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all exception handler.

    Logs the full exception but returns a generic message to clients.
    Never leaks internal error details.
    """
    logger.exception(
        "Unhandled exception [%s %s]: %s",
        request.method,
        request.url.path,
        str(exc),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred"},
    )
