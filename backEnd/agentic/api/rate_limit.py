"""
Rate limiting configuration using slowapi.

Provides rate limiters for different endpoint categories.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address


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
