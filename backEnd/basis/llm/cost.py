"""What a model call costs: one price table, dollars per 1M tokens.

Prices are OpenAI's standard tier as published at developers.openai.com/api/docs/pricing
(checked 2026-10-15) and Anthropic's base prices at
platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-15; cache and batch rates
are not used here). Azure OpenAI global deployments bill the same per-token rates for
these models. The legacy tracker (``agentic/observability/cost_tracker.py``) priced per 1K
tokens and still had gpt-4o at $5/$15; those numbers are not carried over.

Claude models think before answering and bill the thinking as output tokens that count
toward ``max_tokens`` (platform.claude.com/docs/en/build-with-claude/thinking), so a request's
``max_tokens`` (the answer budget) becomes :func:`output_ceiling` on the wire and in the
estimate: the answer budget plus :data:`THINKING_HEADROOM_TOKENS`. Their images cost
``ceil(w / 28) * ceil(h / 28)`` tokens, :data:`CLAUDE_IMAGE_TOKENS` for a prepared 1024 px
image.

Model names are matched by longest prefix, so a dated snapshot the API reports
(``gpt-4o-mini-2024-07-18``) is priced as its family (``gpt-4o-mini``). A model that is not
in the table is priced at :data:`UNKNOWN_MODEL_PRICE`, deliberately the most expensive
row, so a misconfigured model makes the budget stricter, never looser; a warning is logged
so the table gets the new row.

Arithmetic is in :class:`~decimal.Decimal` and returned as ``float`` rounded to the
``llm_calls.cost_usd`` precision (6 decimal places), so sums are reproducible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from basis.llm.provider import Usage

logger = logging.getLogger("basis.llm.cost")

PER_MILLION: Final = Decimal(1_000_000)
COST_QUANTUM: Final = Decimal("0.000001")  # llm_calls.cost_usd is Numeric(10, 6)

# Characters per token for the pre-call estimate (OpenAI's rule of thumb for English).
CHARS_PER_TOKEN: Final = 4
# Tokens one prepared image (<= 1024 px on the long side, detail "high") costs at most:
# 85 base + 170 per 512 px tile, and a 1024 x 1024 image is four tiles = 765.
IMAGE_TOKENS: Final = 765
# Claude: 28 x 28 px patches, so a 1024 x 1024 image is 37 x 37 = 1369 visual tokens.
CLAUDE_IMAGE_TOKENS: Final = 1369
# Output tokens a Claude model may spend thinking on top of the request's answer budget
# (``anthropic_effort`` low; a photo call at low effort stays well under this).
THINKING_HEADROOM_TOKENS: Final = 4096


@dataclass(frozen=True, slots=True)
class Price:
    input_per_million: Decimal
    output_per_million: Decimal


def _p(input_usd: str, output_usd: str) -> Price:
    return Price(Decimal(input_usd), Decimal(output_usd))


PRICES: Final[dict[str, Price]] = {
    "gpt-4o": _p("2.50", "10.00"),
    "gpt-4o-mini": _p("0.15", "0.60"),
    "gpt-4.1": _p("2.00", "8.00"),
    "gpt-4.1-mini": _p("0.40", "1.60"),
    "gpt-4.1-nano": _p("0.10", "0.40"),
    "gpt-5": _p("1.25", "10.00"),
    "gpt-5-mini": _p("0.25", "2.00"),
    "gpt-5-nano": _p("0.05", "0.40"),
    "gpt-5.1": _p("1.25", "10.00"),
    "gpt-5.2": _p("1.75", "14.00"),
    # 2026-10-16, developers.openai.com/api/docs/pricing (standard, short context)
    "gpt-5.4": _p("2.50", "15.00"),
    "gpt-5.4-mini": _p("0.75", "4.50"),
    "gpt-5.4-nano": _p("0.20", "1.25"),
    "gpt-5.5": _p("5.00", "30.00"),
    "gpt-6-astra": _p("10.00", "50.00"),
    "gpt-6-luna": _p("0.10", "0.50"),
    "gpt-6-sol": _p("2.00", "10.00"),
    "gpt-6.1-sol": _p("2.00", "10.00"),
    # Anthropic (base input / output; Fable 5.1 cache reads $0.25, Opus 5.5 $0.20: unused)
    "claude-fable-5-1": _p("10.00", "50.00"),
    "claude-opus-5-5": _p("4.00", "20.00"),
    "claude-sonnet-5-5": _p("2.00", "10.00"),
}
UNKNOWN_MODEL_PRICE: Final = Price(
    max(p.input_per_million for p in PRICES.values()),
    max(p.output_per_million for p in PRICES.values()),
)


def is_anthropic_model(model: str) -> bool:
    """``claude-*`` names are served by the Anthropic provider and priced as thinking models."""
    return model.strip().lower().startswith("claude")


def output_ceiling(model: str, max_tokens: int) -> int:
    """The wire ``max_tokens`` for an answer budget of ``max_tokens`` on ``model``.

    Claude models count their thinking toward ``max_tokens``; the headroom keeps a short
    answer from being cut off mid-thought. Other models get the budget unchanged.
    """
    if max_tokens < 0:
        raise ValueError("max_tokens cannot be negative")
    return max_tokens + THINKING_HEADROOM_TOKENS if is_anthropic_model(model) else max_tokens


def image_tokens_for(model: str) -> int:
    return CLAUDE_IMAGE_TOKENS if is_anthropic_model(model) else IMAGE_TOKENS


def price_for(model: str) -> Price:
    """The price row for ``model`` (longest matching prefix), or the unknown-model price."""
    name = model.strip().lower()
    best: str | None = None
    for family in PRICES:
        if (name == family or name.startswith(family + "-")) and (
            best is None or len(family) > len(best)
        ):
            best = family
    if best is None:
        logger.warning("no price for model %r; charging the most expensive row", model)
        return UNKNOWN_MODEL_PRICE
    return PRICES[best]


def is_known(model: str) -> bool:
    name = model.strip().lower()
    return any(name == f or name.startswith(f + "-") for f in PRICES)


def _usd(price: Price, input_tokens: int, output_tokens: int) -> Decimal:
    total = (
        Decimal(input_tokens) * price.input_per_million
        + Decimal(output_tokens) * price.output_per_million
    ) / PER_MILLION
    return total.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def cost_decimal(model: str, usage: Usage) -> Decimal:
    return _usd(price_for(model), usage.input_tokens, usage.output_tokens)


def cost_usd(model: str, usage: Usage) -> float:
    """Dollars for one call with ``usage`` on ``model``."""
    return float(cost_decimal(model, usage))


def estimate_tokens(prompt_chars: int, images: int = 0, *, per_image: int = IMAGE_TOKENS) -> int:
    if prompt_chars < 0 or images < 0:
        raise ValueError("prompt_chars and images cannot be negative")
    return -(-prompt_chars // CHARS_PER_TOKEN) + images * per_image


def estimate_usd(model: str, max_tokens: int, prompt_chars: int, images: int = 0) -> float:
    """Worst-case-ish dollars for a call, for :meth:`Budget.reserve` before calling.

    Input is estimated from the prompt length (4 characters per token, rounded up) plus
    :func:`image_tokens_for` the model per image; output is :func:`output_ceiling`
    (``max_tokens``, plus the thinking headroom on Claude), which the provider never exceeds.
    """
    tokens = estimate_tokens(prompt_chars, images, per_image=image_tokens_for(model))
    return float(_usd(price_for(model), tokens, output_ceiling(model, max_tokens)))
