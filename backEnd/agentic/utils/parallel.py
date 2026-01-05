"""
Parallel processing utilities for the agentic workflow.

Provides rate-limited concurrent execution to speed up LLM calls
while respecting API rate limits.
"""

import asyncio
import logging
import random
from typing import TypeVar, Callable, Awaitable, List, Optional

logger = logging.getLogger(__name__)

T = TypeVar('T')
R = TypeVar('R')


# Default retry settings for rate limit errors
DEFAULT_MAX_RETRIES = 8
DEFAULT_BASE_DELAY = 3.0  # seconds (longer initial wait)
DEFAULT_MAX_DELAY = 120.0  # seconds


def is_rate_limit_error(error: Exception) -> bool:
    """Check if an exception is a rate limit error (429)."""
    error_str = str(error).lower()
    return (
        "429" in error_str or
        "rate limit" in error_str or
        "rate_limit" in error_str or
        "too many requests" in error_str
    )


async def retry_with_backoff(
    async_fn: Callable[[], Awaitable[R]],
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
) -> R:
    """
    Execute an async function with exponential backoff retry on rate limit errors.

    Args:
        async_fn: Async function to execute
        max_retries: Maximum number of retries (default: 5)
        base_delay: Initial delay in seconds (default: 2.0)
        max_delay: Maximum delay in seconds (default: 60.0)

    Returns:
        Result from the async function

    Raises:
        The last exception if all retries fail
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await async_fn()
        except Exception as e:
            last_exception = e

            if not is_rate_limit_error(e):
                # Not a rate limit error, don't retry
                raise

            if attempt >= max_retries:
                # Out of retries
                raise

            # Calculate delay with exponential backoff + jitter
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)  # 10% jitter
            total_delay = delay + jitter

            logger.warning(
                f"Rate limit hit, retrying in {total_delay:.1f}s "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(total_delay)

    raise last_exception


async def parallel_map(
    items: List[T],
    async_fn: Callable[[T], Awaitable[R]],
    max_concurrent: int = 1,  # Sequential to respect 30k TPM limit
    desc: Optional[str] = None,
    return_exceptions: bool = False,
    stagger_delay: float = 3.0,  # 3 second delay between calls
    retry_on_rate_limit: bool = True,
) -> List[R]:
    """
    Process items in parallel with concurrency limit and rate limit handling.

    Uses a semaphore to limit the number of concurrent operations,
    with exponential backoff retry on rate limit errors.

    Args:
        items: List of items to process
        async_fn: Async function to apply to each item
        max_concurrent: Maximum simultaneous operations (default: 3)
        desc: Description for logging progress
        return_exceptions: If True, return exceptions instead of raising
        stagger_delay: Delay between starting tasks (default: 0.5s)
        retry_on_rate_limit: Retry with backoff on 429 errors (default: True)

    Returns:
        List of results in the same order as inputs

    Example:
        >>> async def fetch(url):
        ...     return await http_client.get(url)
        >>>
        >>> results = await parallel_map(
        ...     urls,
        ...     fetch,
        ...     max_concurrent=3
        ... )
    """
    if not items:
        return []

    semaphore = asyncio.Semaphore(max_concurrent)
    completed = 0
    total = len(items)

    async def process_with_limit(index: int, item: T) -> tuple[int, R]:
        nonlocal completed

        # Stagger task starts to avoid burst requests
        if stagger_delay > 0 and index > 0:
            await asyncio.sleep(stagger_delay * (index % max_concurrent))

        async with semaphore:
            try:
                if retry_on_rate_limit:
                    # Wrap the call in retry logic
                    result = await retry_with_backoff(
                        lambda: async_fn(item)
                    )
                else:
                    result = await async_fn(item)

                completed += 1
                if desc:
                    logger.info(f"{desc}: {completed}/{total}")
                return (index, result)
            except Exception as e:
                completed += 1
                if desc:
                    logger.warning(f"{desc}: {completed}/{total} (error: {e})")
                if return_exceptions:
                    return (index, e)
                raise

    # Create all tasks
    tasks = [
        process_with_limit(i, item)
        for i, item in enumerate(items)
    ]

    # Run all concurrently (semaphore limits actual concurrency)
    if return_exceptions:
        indexed_results = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        indexed_results = await asyncio.gather(*tasks)

    # Sort by original index to maintain order
    indexed_results = sorted(indexed_results, key=lambda x: x[0])

    return [result for _, result in indexed_results]


async def parallel_map_batched(
    items: List[T],
    batch_fn: Callable[[List[T]], Awaitable[List[R]]],
    batch_size: int = 10,
    max_concurrent: int = 5,
    desc: Optional[str] = None,
) -> List[R]:
    """
    Process items in batches, with batches running in parallel.

    Useful when you can process multiple items in a single LLM call,
    reducing total API calls while still parallelizing batch processing.

    Args:
        items: List of items to process
        batch_fn: Async function that processes a batch and returns list of results
        batch_size: Number of items per batch (default: 10)
        max_concurrent: Maximum simultaneous batches (default: 5)
        desc: Description for logging progress

    Returns:
        Flattened list of all results in original order

    Example:
        >>> async def classify_batch(objects):
        ...     # One LLM call for multiple objects
        ...     return await llm.classify_many(objects)
        >>>
        >>> results = await parallel_map_batched(
        ...     objects,
        ...     classify_batch,
        ...     batch_size=10,
        ...     max_concurrent=5
        ... )
    """
    if not items:
        return []

    # Split into batches, preserving indices
    batches_with_indices = []
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        start_idx = i
        batches_with_indices.append((start_idx, batch))

    async def process_batch(batch_data: tuple[int, List[T]]) -> tuple[int, List[R]]:
        start_idx, batch = batch_data
        results = await batch_fn(batch)
        return (start_idx, results)

    # Process batches in parallel
    batch_results = await parallel_map(
        batches_with_indices,
        process_batch,
        max_concurrent=max_concurrent,
        desc=desc,
    )

    # Flatten results maintaining original order
    all_results = [None] * len(items)
    for start_idx, results in batch_results:
        for j, result in enumerate(results):
            all_results[start_idx + j] = result

    return all_results


async def run_parallel_phases(*coroutines: Awaitable[R]) -> tuple:
    """
    Run multiple independent phases in parallel.

    Use this when you have multiple operations that don't depend
    on each other and can run simultaneously.

    Args:
        *coroutines: Variable number of coroutines to run in parallel

    Returns:
        Tuple of results from each coroutine

    Example:
        >>> takeoffs, classifications = await run_parallel_phases(
        ...     calculate_all_takeoffs(objects),
        ...     classify_all_objects(objects),
        ... )
    """
    return await asyncio.gather(*coroutines)


class RateLimiter:
    """
    Token bucket rate limiter for API calls.

    Ensures we don't exceed API rate limits by tracking
    request count over a sliding window.
    """

    def __init__(
        self,
        requests_per_minute: int = 500,
        tokens_per_minute: int = 30000,
    ):
        self.rpm = requests_per_minute
        self.tpm = tokens_per_minute
        self._request_times: List[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait until we can make another request."""
        async with self._lock:
            now = asyncio.get_event_loop().time()

            # Remove requests older than 1 minute
            self._request_times = [
                t for t in self._request_times
                if now - t < 60
            ]

            # If at limit, wait
            if len(self._request_times) >= self.rpm:
                wait_time = 60 - (now - self._request_times[0])
                if wait_time > 0:
                    logger.debug(f"Rate limit: waiting {wait_time:.1f}s")
                    await asyncio.sleep(wait_time)

            self._request_times.append(now)

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        pass
