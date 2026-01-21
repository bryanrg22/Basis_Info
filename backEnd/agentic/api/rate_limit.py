"""
Rate limiting configuration using slowapi.

Provides rate limiters for different endpoint categories.
Phase 6: Added Slack alerts for rate limit exceeded.
"""

import asyncio
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)


def get_user_key(request) -> str:
    """
    Get rate limit key from authenticated user or fall back to IP.

    Uses user.uid if authenticated, otherwise client IP address.
    """
    # Try to get user from request state (set by auth dependency)
    user = getattr(request.state, "user", None)
    if user and hasattr(user, "uid"):
        return f"user:{user.uid}"

    # Fall back to IP address
    return get_remote_address(request)


# Create limiter instance
limiter = Limiter(key_func=get_user_key)

# Rate limit configurations
WORKFLOW_LIMIT = "10/minute"  # For expensive workflow operations
STATUS_LIMIT = "60/minute"  # For lightweight status checks


async def _send_rate_limit_alert(request: Request, limit: str):
    """Send alert for rate limit exceeded."""
    try:
        from ..observability.alerts import send_alert

        user = getattr(request.state, "user", None)
        user_id = user.uid if user else "anonymous"
        client_ip = request.client.host if request.client else "unknown"

        await send_alert(
            study_id="RATE_LIMIT",
            stage=f"API:{request.url.path}",
            error_type="RATE_LIMIT_EXCEEDED",
            error_message=f"Rate limit exceeded: {limit}",
            severity="warning",
            context={
                "user_id": user_id,
                "client_ip": client_ip,
                "path": str(request.url.path),
                "method": request.method,
                "limit": limit,
            },
        )
    except Exception as e:
        logger.warning(f"Failed to send rate limit alert: {e}")


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Custom rate limit exceeded handler with Slack alerting.
    """
    logger.warning(
        "Rate limit exceeded [%s %s]: %s",
        request.method,
        request.url.path,
        str(exc.detail),
    )

    # Send alert (non-blocking)
    asyncio.create_task(_send_rate_limit_alert(request, str(exc.detail)))

    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}"},
    )
