"""Provider errors, mapped from SDK exceptions so callers never import an SDK.

* :class:`ProviderRateLimited` (HTTP 429) and :class:`ProviderUnavailable` (5xx, 408/409,
  timeouts, connection failures) are transient: the provider retries 429/5xx once itself,
  the pool then tries the secondary provider, and an activity lets Temporal retry what is
  left.
* :class:`ProviderBadResponse` means the call completed but its output is unusable (invalid
  JSON for the schema, a refusal, truncation). Retrying the same request elsewhere does not
  help; the caller decides (the vision call retries once, then returns a ``needs_review``
  analysis).
* :class:`ProviderAccountError` means the provider refused the account, not the request:
  out of credit or quota (OpenAI 429 ``insufficient_quota``/``credit_balance_exhausted``/
  spend limits, Anthropic 402 ``billing_error``, 400 "credit balance is too low" or a spend
  limit, 429 ``enforced_spend_limit_reached``) or a missing, invalid or revoked key and
  permissions (401/403). Every later call fails the same way until someone fixes the
  account, so it is never retried, never sent to the secondary provider, and stops what is
  running (an evaluation run, a study stage) instead of being counted as one bad answer.
* :class:`ProviderConfigError` (a :class:`ProviderAccountError`, so every account stop
  applies to it unchanged) means the provider refused the request as configured: the
  model or deployment does not exist (404, ``model_not_found``, Azure
  ``DeploymentNotFound``), a parameter or value the model does not take
  (``unsupported_parameter``, ``unsupported_value``), a schema it rejects, an operation the
  deployment does not support (Azure ``OperationNotSupported``), and by default any other
  400/404/422 whose code and message do not point at this request's content
  (:func:`classify_rejection`). The same request template fails for every input, so it is as
  systemic as a dead account. Before this class such a 4xx was a bad response: an Azure
  deployment that could not run the configured model answered 400 to every photo, each was
  retried once and became an ``ambiguous`` analysis with no component and no cost, and the
  study went on to review with no vision at all. :class:`ProviderNotConfigured` (no
  configured provider serves the model) is one too.
* :class:`ProviderRequestRejected` means the provider refused this request's content: an
  image it cannot process, a request too large (413), a content-policy or content-filter
  block of the input. Another input may well work, so the caller fails that item visibly
  (``analyze_photo`` fails the photo, which the workflow flags) instead of stopping
  everything. It is never retried (the same input gets the same answer) and is not a
  :class:`ProviderBadResponse` (nothing was answered).

Sources for the split (read 2026-10-23): OpenAI's error-codes guide
(developers.openai.com/api/docs/guides/error-codes: ``BadRequestError`` "the request was
malformed or missing required parameters", ``NotFoundError`` "the requested resource doesn't
exist"); Anthropic's (platform.claude.com/docs/en/api/errors: 400 ``invalid_request_error``
"an issue with the format or content of your request", 404 ``not_found_error``, 413
``request_too_large``); Azure OpenAI content filtering (learn.microsoft.com/azure/ai-foundry/
openai/concepts/content-filter: a filtered prompt is HTTP 400 with ``code:
"content_filter"``); Microsoft Q&A on ``DeploymentNotFound`` (404) and
``OperationNotSupported`` (an operation the deployed model does not support); Google AIP-194
("Retrying a request with an invalid argument will never succeed"; ``NOT_FOUND`` and
``FAILED_PRECONDITION`` are not retried until the client changes something); the ``openai``
and ``anthropic`` SDKs' own retry rule (``_should_retry``: 408, 409, 429 and 5xx are
transient, every other 4xx is not); and Nygard, *Release It!* (2nd ed.), "Fail Fast": a
request that cannot succeed should be refused at once, not after spending resources.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Final

from basis.llm.provider import Usage

# API keys as providers might echo them back in errors: OpenAI and Anthropic keys
# (``sk-...``, ``sk-proj-...``, ``sk-ant-...``; OpenAI prints a masked prefix and suffix of
# an incorrect key, and nothing guarantees every message is masked) and Azure's 32-hex keys.
_SECRET = re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_\-*]{4,}|\b[0-9a-fA-F]{32}\b")
MAX_ACCOUNT_MESSAGE: Final = 500
CONFIG_HINT: Final = "check the model and deployment settings"
# 408 Request Timeout and 409 Conflict: both SDKs retry them as transient (``_should_retry``).
TRANSIENT_4XX: Final = frozenset({408, 409})
# Error codes that name this request's content, not its template: Azure's content filter
# (``content_filter``; its inner ``ResponsibleAIPolicyViolation``), OpenAI's image and
# policy codes, and Anthropic's 413 type. A context-window overflow is not here: every
# request this service sends is bounded by settings (prepared images, prompt files,
# ``*_MAX_TOKENS``, ``EXTRACTION_FALLBACK_CONTEXT_CHARS``), so it means the settings do not
# fit the model, a configuration error.
INPUT_CODES: Final = frozenset(
    {
        "content_filter",
        "content_policy_violation",
        "responsibleaipolicyviolation",
        "invalid_image",
        "invalid_image_format",
        "invalid_image_url",
        "image_too_large",
        "image_parse_error",
        "invalid_base64",
        "request_too_large",
    }
)
# Messages that name this request's content (an image the provider cannot read, a size
# limit, a policy block). Deliberately narrow: "image_url is only supported by certain
# models" is about the model (configuration), so a bare "image" is not enough.
_INPUT_MESSAGE = re.compile(
    r"could not process image|invalid image|unsupported image|image (?:is )?too large"
    r"|image exceeds|image dimensions exceed|image does not match the provided media type"
    r"|invalid base64|content management policy|content filter|filtered due to"
    r"|safety system|request too large|request exceeds the maximum allowed",
    re.IGNORECASE,
)


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
        response_id: str | None = None,
        request_id: str | None = None,
        finish_reason: str | None = None,
        endpoint: str | None = None,
        request_params: dict[str, Any] | None = None,
        latency_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.usage = usage
        self.model = model
        self.raw = raw
        # The ledger fields of a call that reached the model (a bad response): the same
        # ones :class:`~basis.llm.provider.ChatResponse` carries.
        self.response_id = response_id
        self.request_id = request_id
        self.finish_reason = finish_reason
        self.endpoint = endpoint
        self.request_params = request_params
        self.latency_ms = latency_ms


class ProviderRateLimited(ProviderError):
    """HTTP 429 from the provider."""


class ProviderUnavailable(ProviderError):
    """HTTP 5xx, 408 or 409, a timeout, or a connection failure."""


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
        return f"{self.provider} account error ({_where(self.status, self.code)}): {self.args[0]}"


class ProviderConfigError(ProviderAccountError):
    """The provider refused the request as configured (module doc): an unknown model or
    deployment, a parameter or value the model does not take, a schema it rejects.

    A :class:`ProviderAccountError` on purpose: retrying, the secondary provider and the
    next input all get the same refusal, so every path that stops on a dead account (the
    activity's non-retryable error, the workflow's ``provider-account-stop-v1`` hold and
    ``POST /retry``, the evaluation runners' exit 3) stops on this too. Only the text
    differs: it says the request was rejected and what to check, not "account error".
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message, provider=provider, status=status, code=code)

    def __str__(self) -> str:
        return (
            f"{self.provider} rejected the request ({_where(self.status, self.code)}): "
            f"{self.args[0]}; {CONFIG_HINT}"
        )


class ProviderNotConfigured(ProviderConfigError):
    """No configured provider serves the model (or none has credentials at all).

    A configuration error: the worker refuses to start when a model its pipeline calls is
    not served (``basis.worker.main``), and a call that still finds no provider stops the
    stage like any other configuration error instead of being skipped.
    """

    def __str__(self) -> str:
        return f"{self.args[0]}; configure a provider that serves it or change the model setting"


class ProviderRequestRejected(ProviderError):
    """The provider refused this request's content (module doc): an image it cannot
    process, a request too large, a content-policy block. ``code`` is the provider's error
    code or type; the message is the provider's own, without anything shaped like a key."""

    def __init__(
        self, message: str, *, provider: str, status: int | None, code: str | None = None
    ) -> None:
        super().__init__(
            redact_secrets(message)[:MAX_ACCOUNT_MESSAGE], provider=provider, status=status
        )
        self.code = code

    def __str__(self) -> str:
        where = _where(self.status, self.code)
        return f"{self.provider} rejected the input ({where}): {self.args[0]}"


TRANSIENT = (ProviderRateLimited, ProviderUnavailable)


def _where(status: int | None, code: str | None) -> str:
    where = f"HTTP {status}" if status is not None else "no status"
    return f"{where}, {code}" if code else where


def redact_secrets(text: str) -> str:
    """``text`` with every API-key-shaped token replaced by ``[redacted]``."""
    return _SECRET.sub("[redacted]", text)


# Keys as they could appear inside a request or response body stored by the ledger: the
# vendors' prefixed keys (OpenAI ``sk-``/``sk-proj-``, Anthropic ``sk-ant-``, Replicate
# ``r8_``) and credential headers written out as text. Deliberately without the bare 32-hex
# rule of :data:`_SECRET`: a stored prompt legitimately holds document and chunk ids in hex.
_API_KEY = re.compile(
    r"\b(?:sk|rk)-[A-Za-z0-9_\-]{8,}"
    r"|\br8_[A-Za-z0-9]{8,}"
    r"|(?i:(?:bearer|api-key|x-api-key|authorization)\s*[:=]\s*)[^\s\"']{8,}"
)


def redact_api_keys(text: str) -> str:
    """``text`` with every API key or credential header replaced by ``[redacted]``, for the
    ledger's stored payloads (:mod:`basis.llm.recorder`). Hex ids are left alone."""
    return _API_KEY.sub("[redacted]", text)


def is_retryable_status(error: ProviderError) -> bool:
    """429 or 5xx: what a provider retries once and the pool falls back on."""
    return isinstance(error, ProviderRateLimited) or (
        isinstance(error, ProviderUnavailable) and error.status is not None and error.status >= 500
    )


def classify_rejection(
    status: int, codes: Iterable[str | None], message: str, *, provider: str
) -> ProviderError:
    """A 4xx that is neither an account error nor a 429 (both are decided before this), as
    the request's fault or the configuration's.

    ``codes`` are the provider's ``error.code``/``error.type`` (and any inner code); the
    first one set is kept on the error. 408/409 are transient (:data:`TRANSIENT_4XX`); 413,
    an input code (:data:`INPUT_CODES`) or a message naming the input is
    :class:`ProviderRequestRejected`; anything else (404, ``model_not_found``,
    ``DeploymentNotFound``, ``unsupported_parameter``, ``OperationNotSupported``, an unknown
    400/422) is :class:`ProviderConfigError`. Unknown errors default to configuration: a
    systemic refusal must stop the study; a misread input refusal stops it too, visibly, with
    the provider's message, which the engineer can act on.
    """
    named = [c for c in codes if c]
    code = named[0] if named else None
    if status in TRANSIENT_4XX:
        return ProviderUnavailable(
            f"HTTP {status}: {redact_secrets(message)}", provider=provider, status=status
        )
    lowered = {c.lower() for c in named}
    if status == 413 or lowered & INPUT_CODES or _INPUT_MESSAGE.search(message):
        return ProviderRequestRejected(message, provider=provider, status=status, code=code)
    return ProviderConfigError(message, provider=provider, status=status, code=code)
