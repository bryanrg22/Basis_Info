"""Base API client with retry logic and rate limiting."""

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Base exception for API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(APIError):
    """Raised when rate limit is exceeded."""

    def __init__(self, message: str = "Rate limit exceeded", retry_after: Optional[int] = None):
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class BaseAPIClient(ABC):
    """Base class for API clients with common retry and error handling."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazy initialization of HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers=self._get_headers(),
            )
        return self._client

    @abstractmethod
    def _get_headers(self) -> dict[str, str]:
        """Return headers for API requests."""
        pass

    @abstractmethod
    def _get_api_key_env_var(self) -> str:
        """Return the environment variable name for the API key."""
        pass

    def _resolve_api_key(self) -> str:
        """Resolve API key from init or environment."""
        key = self.api_key or os.getenv(self._get_api_key_env_var())
        if not key:
            raise ValueError(
                f"API key not provided. Set {self._get_api_key_env_var()} "
                f"environment variable or pass api_key to constructor."
            )
        return key

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "BaseAPIClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    def _handle_response_error(self, response: httpx.Response) -> None:
        """Handle error responses from API."""
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                f"Rate limited by API",
                retry_after=int(retry_after) if retry_after else None,
            )
        elif response.status_code >= 400:
            try:
                error_detail = response.json()
            except Exception:
                error_detail = response.text
            raise APIError(
                f"API error: {error_detail}",
                status_code=response.status_code,
            )

    @staticmethod
    def with_retry(func):
        """Decorator for adding retry logic to async methods."""
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type((httpx.TimeoutException, RateLimitError)),
            before_sleep=lambda retry_state: logger.warning(
                f"Retrying {func.__name__} after {retry_state.outcome.exception()}"
            ),
        )
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper


async def run_with_semaphore(
    semaphore: asyncio.Semaphore,
    coro,
):
    """Run a coroutine with semaphore for rate limiting."""
    async with semaphore:
        return await coro
