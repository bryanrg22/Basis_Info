"""Provider errors, mapped from SDK exceptions so callers never import an SDK.

* :class:`ProviderRateLimited` (HTTP 429) and :class:`ProviderUnavailable` (5xx, timeouts,
  connection failures) are transient: the provider retries them once itself, the pool then
  tries the secondary provider, and an activity lets Temporal retry what is left.
* :class:`ProviderBadResponse` means the request reached the model but the answer is
  unusable (invalid JSON for the schema, a refusal, truncation, or a 4xx such as a schema
  the API rejects). Retrying the same request elsewhere does not help; the caller decides
  (the vision call retries once, then returns a ``needs_review`` analysis).
* :class:`ProviderAccountError` means the provider refused the account, not the request:
  a bad or revoked key (401), a key without access (403), or no credit or quota left
  (Anthropic's 400/402 "credit balance is too low", OpenAI's 429 ``insufficient_quota``).
  Every later call would fail the same way, so nothing retries it and no caller treats it
  as one unusable answer: it stops the activity (non-retryable) or the evaluation run.
* :class:`ProviderNotConfigured` means no provider has credentials.
"""

from __future__ import annotations

from typing import Any

from basis.llm.provider import Usage


class ProviderError(RuntimeError):
    """Base class for model-provider failures.

    ``usage``/``model``/``raw`` are set when the call reached the model (a bad response
    still costs money, so the caller records and charges it).
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status: int | None = None,
        usage: Usage | None = None,
        model: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.usage = usage
        self.model = model
        self.raw = raw


class ProviderRateLimited(ProviderError):
    """HTTP 429 from the provider."""


class ProviderUnavailable(ProviderError):
    """HTTP 5xx, a timeout, or a connection failure."""


class ProviderBadResponse(ProviderError):
    """The call completed but its output cannot be used."""


class ProviderAccountError(ProviderError):
    """The provider rejected the credentials or the account (key, permission, credit)."""


class ProviderNotConfigured(ProviderError):
    """No model provider has credentials in the settings."""


TRANSIENT = (ProviderRateLimited, ProviderUnavailable)


def is_retryable_status(error: ProviderError) -> bool:
    """429 or 5xx: what a provider retries once and the pool falls back on."""
    return isinstance(error, ProviderRateLimited) or (
        isinstance(error, ProviderUnavailable) and error.status is not None and error.status >= 500
    )
