"""Primary/secondary providers with per-call fallback and a concurrency cap.

Replaces the legacy ``_use_openai_fallback`` module global (``agentic/config/
llm_providers.py``), which switched every later call of the process to OpenAI after one
Azure 429 and never switched back. Here the decision is made per call: each call tries
the primary first, and only that call moves to the secondary, and only when the primary
failed with HTTP 429 or 5xx after its own single retry. Any other error (a bad response,
a timeout, a 4xx) is raised as is: the secondary would get the same request and the same
answer, or the activity's Temporal retry is the better place to wait.

The semaphore bounds concurrent model calls in one worker process (the legacy
``HybridVisionPool`` bounded photos per provider; with per-call fallback one bound is
enough). It is held for the whole call, including the fallback attempt.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from basis.config import Settings
from basis.llm.errors import ProviderError, ProviderNotConfigured
from basis.llm.provider import ChatProvider, ChatRequest, ChatResponse
from basis.llm.providers import is_retryable_status, providers_from_settings

logger = logging.getLogger("basis.llm.pool")


class ProviderPool:
    """A :class:`ChatProvider` over one or two providers (primary first)."""

    def __init__(self, providers: Sequence[ChatProvider], *, concurrency: int = 4) -> None:
        if not providers:
            raise ProviderNotConfigured("a provider pool needs at least one provider")
        if len(providers) > 2:
            raise ValueError("a provider pool has a primary and at most one secondary")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._providers = list(providers)
        self._semaphore = asyncio.Semaphore(concurrency)
        self.concurrency = concurrency
        self.name = "+".join(p.name for p in self._providers)
        self.fallbacks = 0  # calls served by the secondary (for metrics and tests)

    @property
    def providers(self) -> tuple[ChatProvider, ...]:
        return tuple(self._providers)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        async with self._semaphore:
            primary = self._providers[0]
            try:
                return await primary.complete(request)
            except ProviderError as error:
                if len(self._providers) < 2 or not is_retryable_status(error):
                    raise
                secondary = self._providers[1]
                logger.warning(
                    "%s failed with %s for %s; this call falls back to %s",
                    primary.name,
                    error.status,
                    request.activity,
                    secondary.name,
                )
                self.fallbacks += 1
                return await secondary.complete(request)

    @classmethod
    def from_settings(cls, settings: Settings) -> ProviderPool | None:
        """The pool for the configured providers, or ``None`` when none is configured."""
        providers = providers_from_settings(settings)
        if not providers:
            return None
        return cls(providers, concurrency=settings.llm_concurrency)
