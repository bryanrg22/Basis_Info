"""Primary/secondary providers with per-call fallback and a concurrency cap.

Replaces the legacy ``_use_openai_fallback`` module global (``agentic/config/
llm_providers.py``), which switched every later call of the process to OpenAI after one
Azure 429 and never switched back. Here the decision is made per call: each call tries
the primary first, and only that call moves to the secondary, and only when the primary
failed with HTTP 429 or 5xx after its own single retry. Any other error (a bad response,
a timeout, a 4xx) is raised as is: the secondary would get the same request and the same
answer, or the activity's Temporal retry is the better place to wait.

An account error (:class:`~basis.llm.errors.ProviderAccountError`: no credit or quota, a bad
key) never falls back either. Every request names its model, so moving it to the other
provider would answer it with a different account and, on Azure, possibly a different
deployment than the model the run or study asked for (an evaluation pinned to ``gpt-4.1``
was once answered by Azure that way when the OpenAI account ran dry); and the outage would
stay hidden until the second account ran dry too. The error is raised at once so the run
or the stage stops and someone fixes the account. A configuration error
(:class:`~basis.llm.errors.ProviderConfigError`, an account error by type: an unknown model
or deployment, a parameter the model refuses) is raised the same way, and so is a refused
input (:class:`~basis.llm.errors.ProviderRequestRejected`): neither is a status the
secondary could answer differently for this model.

Routing by model: a provider may declare ``accepts(model)`` (the Anthropic provider serves
``claude-*`` names, the OpenAI and Azure providers everything else). Each call considers only
the providers that accept its model, in the pool's order: the first is its primary, the
second its one fallback. A ``claude-*`` request is therefore never sent to OpenAI, whatever
``LLM_PRIMARY`` says, and a ``gpt-*`` request never to Anthropic. A model no configured
provider serves is :class:`ProviderNotConfigured` (a configuration error; the worker refuses
to start when its pipeline names such a model, :func:`unserved_models`).

The semaphore bounds concurrent model calls in one worker process (the legacy
``HybridVisionPool`` bounded photos per provider; with per-call fallback one bound is
enough). It is held for the whole call, including the fallback attempt.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence

from basis.config import Settings
from basis.llm.errors import (
    ProviderAccountError,
    ProviderError,
    ProviderNotConfigured,
    is_retryable_status,
)
from basis.llm.provider import ChatProvider, ChatRequest, ChatResponse
from basis.llm.providers import providers_from_settings

logger = logging.getLogger("basis.llm.pool")


def accepts(provider: ChatProvider, model: str) -> bool:
    """Whether ``provider`` serves ``model`` (a provider without ``accepts`` serves any)."""
    check = getattr(provider, "accepts", None)
    return True if check is None else bool(check(model))


def unserved_models(providers: Sequence[ChatProvider], models: Iterable[str]) -> list[str]:
    """The models of ``models`` (each once, in order) that none of ``providers`` serves.

    Routing only: Azure accepts every non-Claude name (it sends it to its deployment), so a
    served model can still be refused at call time; that is a
    :class:`~basis.llm.errors.ProviderConfigError` then, which stops the stage."""
    seen: list[str] = []
    for model in models:
        if model not in seen and not any(accepts(p, model) for p in providers):
            seen.append(model)
    return seen


class ProviderPool:
    """A :class:`ChatProvider` over the configured providers (primary first per model)."""

    def __init__(self, providers: Sequence[ChatProvider], *, concurrency: int = 4) -> None:
        if not providers:
            raise ProviderNotConfigured("a provider pool needs at least one provider")
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

    def candidates(self, model: str) -> list[ChatProvider]:
        """The providers that serve ``model``, in pool order (primary, then the fallback)."""
        return [p for p in self._providers if accepts(p, model)]

    async def complete(self, request: ChatRequest) -> ChatResponse:
        async with self._semaphore:
            candidates = self.candidates(request.model)
            if not candidates:
                raise ProviderNotConfigured(
                    f"no configured provider serves model {request.model!r} "
                    f"(configured: {self.name})"
                )
            primary = candidates[0]
            try:
                return await primary.complete(request)
            except ProviderAccountError as error:  # configuration errors included
                logger.error("%s stopped %s: %s", primary.name, request.activity, error)
                raise
            except ProviderError as error:
                if len(candidates) < 2 or not is_retryable_status(error):
                    raise
                secondary = candidates[1]
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
