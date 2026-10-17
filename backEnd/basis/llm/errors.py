"""Provider errors, mapped from SDK exceptions so callers never import an SDK.

* :class:`ProviderRateLimited` (HTTP 429) and :class:`ProviderUnavailable` (5xx, timeouts,
  connection failures) are transient: the provider retries them once itself, the pool then
  tries the secondary provider, and an activity lets Temporal retry what is left.
* :class:`ProviderBadResponse` means the request reached the model but the answer is
  unusable (invalid JSON for the schema, a refusal, truncation, or a 4xx such as a schema
  the API rejects). Retrying the same request elsewhere does not help; the caller decides
  (the vision call retries once, then returns a ``needs_review`` analysis).
* :class:`ProviderAccountError` means the provider refused the account, not the request:
  out of credit or quota (OpenAI 429 ``insufficient_quota``/``credit_balance_exhausted``/
  spend limits, Anthropic 402 ``billing_error``, 400 "credit balance is too low" or a spend
  limit, 429 ``enforced_spend_limit_reached``) or a missing, invalid or revoked key and
  permissions (401/403). Every later call fails the same way until someone fixes the
  account, so it is never retried, never sent to the secondary provider, and stops what is
  running (an evaluation run, a study stage) instead of being counted as one bad answer.
* :class:`ProviderNotConfigured` means no provider has credentials.
"""

from __future__ import annotations

import re
from typing import Any, Final

from basis.llm.provider import Usage

# API keys as providers might echo them back in errors: OpenAI and Anthropic keys
# (``sk-...``, ``sk-proj-...``, ``sk-ant-...``; OpenAI prints a masked prefix and suffix of
# an incorrect key, and nothing guarantees every message is masked) and Azure's 32-hex keys.
_SECRET = re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_\-*]{4,}|\b[0-9a-fA-F]{32}\b")
MAX_ACCOUNT_MESSAGE: Final = 500


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
    """The provider refused the account: no credit or quota, or a bad key or permission.

    ``provider`` names the provider, ``status`` the HTTP status, ``code`` the provider's
    error code or type (``insufficient_quota``, ``billing_error``, ...), and the message is
    the provider's own, with anything shaped like an API key removed.
    """

    def __init__(
        self, message: str, *, provider: str, status: int | None, code: str | None = None
    ) -> None:
        super().__init__(
            redact_secrets(message)[:MAX_ACCOUNT_MESSAGE], provider=provider, status=status
        )
        self.code = code

    def __str__(self) -> str:
        where = f"HTTP {self.status}" if self.status is not None else "no status"
        code = f", {self.code}" if self.code else ""
        return f"{self.provider} account error ({where}{code}): {self.args[0]}"


class ProviderNotConfigured(ProviderError):
    """No model provider has credentials in the settings."""


TRANSIENT = (ProviderRateLimited, ProviderUnavailable)


def redact_secrets(text: str) -> str:
    """``text`` with every API-key-shaped token replaced by ``[redacted]``."""
    return _SECRET.sub("[redacted]", text)


def is_retryable_status(error: ProviderError) -> bool:
    """429 or 5xx: what a provider retries once and the pool falls back on."""
    return isinstance(error, ProviderRateLimited) or (
        isinstance(error, ProviderUnavailable) and error.status is not None and error.status >= 500
    )
