"""The price table, cost of a call, and the pre-call estimate."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from basis.config import Settings
from basis.llm.cost import (
    IMAGE_TOKENS,
    PRICES,
    PRICING_VERSION,
    UNKNOWN_MODEL_PRICE,
    UNKNOWN_PRICE_ROW,
    Price,
    cost_decimal,
    cost_usd,
    estimate_tokens,
    estimate_usd,
    is_known,
    price_for,
    price_row,
    priced_as,
)
from basis.llm.provider import Usage


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("gpt-4o", "gpt-4o"),
        ("gpt-4o-2024-08-06", "gpt-4o"),
        ("gpt-4o-mini", "gpt-4o-mini"),
        ("gpt-4o-mini-2024-07-18", "gpt-4o-mini"),
        ("GPT-4.1-MINI", "gpt-4.1-mini"),
        ("gpt-4.1-nano-2025-04-14", "gpt-4.1-nano"),
        ("gpt-5", "gpt-5"),
        ("gpt-5-nano", "gpt-5-nano"),
        ("gpt-5.1", "gpt-5.1"),
        ("gpt-5.2-2025-12-11", "gpt-5.2"),
        ("gpt-6.1-sol", "gpt-6.1-sol"),
        ("gpt-6-astra-2026-09-01", "gpt-6-astra"),
        ("gpt-6-sol", "gpt-6-sol"),
        ("gpt-5.6-luna", "gpt-5.6-luna"),
        ("gpt-5.6-luna-2026-09-15", "gpt-5.6-luna"),
        ("gpt-5.6-terra", "gpt-5.6-terra"),
        ("gpt-5.6-sol", "gpt-5.6-sol"),
    ],
)
def test_longest_prefix_wins(model: str, family: str) -> None:
    assert price_for(model) == PRICES[family]
    assert is_known(model)


def test_published_prices_per_million() -> None:
    # developers.openai.com/api/docs/pricing, standard tier, checked 2026-10-15
    assert PRICES["gpt-4o"].input_per_million == Decimal("2.50")
    assert PRICES["gpt-4o"].output_per_million == Decimal("10.00")
    assert PRICES["gpt-4o-mini"].input_per_million == Decimal("0.15")
    assert PRICES["gpt-4o-mini"].output_per_million == Decimal("0.60")
    assert PRICES["gpt-5-nano"].output_per_million == Decimal("0.40")


def test_gpt_5_6_family_prices() -> None:
    # developers.openai.com/api/docs/pricing, standard tier, short context, checked 2026-10-16
    assert PRICES["gpt-5.6-luna"] == Price(
        Decimal("0.20"), Decimal("1.20"), Decimal("0.02"), Decimal("0.20")
    )
    assert PRICES["gpt-5.6-terra"] == Price(
        Decimal("2.00"), Decimal("12.00"), Decimal("0.20"), Decimal("2.00")
    )
    assert PRICES["gpt-5.6-sol"] == Price(
        Decimal("4.00"), Decimal("20.00"), Decimal("0.40"), Decimal("4.00")
    )
    # 100k in + 10k out on luna: 0.02 + 0.012
    assert cost_usd("gpt-5.6-luna", Usage(input_tokens=100_000, output_tokens=10_000)) == 0.032
    # the family name alone is not a model and must not borrow a sibling's (cheaper) price
    assert not is_known("gpt-5.6")
    assert price_for("gpt-5.6") == UNKNOWN_MODEL_PRICE
    # gpt-5.6-* is not the gpt-5 family (prefix needs the "-" boundary)
    assert price_for("gpt-5.6-terra") != PRICES["gpt-5"]


def test_cost_of_a_call() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd("gpt-4o-mini", usage) == 0.75
    assert cost_usd("gpt-4o", Usage(input_tokens=2000, output_tokens=500)) == 0.01
    assert cost_decimal(
        "gpt-4.1-nano", Usage(input_tokens=20_000, output_tokens=20_000)
    ) == Decimal("0.010000")
    assert cost_usd("gpt-4o-mini", Usage(input_tokens=0, output_tokens=0)) == 0.0


def test_cost_is_rounded_to_the_column_precision() -> None:
    # 1 input token of gpt-5-nano is $0.00000005 -> rounds to $0.000000 (6 places)
    assert cost_decimal("gpt-5-nano", Usage(input_tokens=1, output_tokens=0)) == Decimal("0.000000")
    assert cost_decimal("gpt-5-nano", Usage(input_tokens=10, output_tokens=0)) == Decimal(
        "0.000001"
    )


def test_unknown_models_are_priced_at_the_most_expensive_row(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="basis.llm.cost"):
        price = price_for("my-azure-deployment")
    assert price == UNKNOWN_MODEL_PRICE
    assert all(p.input_per_million <= price.input_per_million for p in PRICES.values())
    assert all(p.output_per_million <= price.output_per_million for p in PRICES.values())
    assert "my-azure-deployment" in caplog.text
    assert not is_known("gpt-4omni")  # a family prefix needs a "-" boundary


def test_estimate_counts_prompt_images_and_full_output() -> None:
    assert estimate_tokens(4000) == 1000
    assert estimate_tokens(4001) == 1001  # rounds up
    assert estimate_tokens(0, images=2) == 2 * IMAGE_TOKENS
    estimate = estimate_usd("gpt-4o", max_tokens=1000, prompt_chars=4000, images=1)
    expected = (1000 + IMAGE_TOKENS) * 2.50 / 1e6 + 1000 * 10.00 / 1e6
    assert estimate == pytest.approx(expected, abs=1e-6)


def test_estimate_is_an_upper_bound_for_a_call_within_its_limits() -> None:
    estimate = estimate_usd("gpt-4o-mini", max_tokens=500, prompt_chars=8000)
    actual = cost_usd("gpt-4o-mini", Usage(input_tokens=2000, output_tokens=500))
    assert actual <= estimate


@pytest.mark.parametrize(("chars", "images", "max_tokens"), [(-1, 0, 1), (0, -1, 1), (0, 0, -1)])
def test_negative_inputs_are_rejected(chars: int, images: int, max_tokens: int) -> None:
    with pytest.raises(ValueError):
        estimate_usd("gpt-4o", max_tokens=max_tokens, prompt_chars=chars, images=images)


# ------------------------------------------------------------------ the ledger (section 40)


def test_every_part_of_the_usage_is_priced_at_its_own_rate() -> None:
    """OpenAI: ``prompt_tokens`` includes the cached tokens, priced at the cached-input
    rate; ``completion_tokens`` includes the reasoning tokens, all at the output rate."""
    usage = Usage(
        input_tokens=10_000,
        output_tokens=2_000,
        cache_read_input_tokens=4_000,
        reasoning_output_tokens=1_500,
    )
    # 6,000 x $2.00 + 4,000 x $0.50 + 2,000 x $8.00 per million
    assert cost_decimal("gpt-4.1", usage) == Decimal("0.030000")
    assert usage.uncached_input_tokens == 6_000
    # Anthropic: the ledger's input_tokens is uncached + written + read; writes 1.25x base,
    # reads at the model's cache-hit rate (Opus 5.5: $0.20).
    usage = Usage(
        input_tokens=10_000,
        output_tokens=1_000,
        cache_read_input_tokens=3_000,
        cache_creation_input_tokens=2_000,
    )
    # 5,000 x $4 + 2,000 x $5 + 3,000 x $0.20 + 1,000 x $20 per million
    assert cost_decimal("claude-opus-5-5", usage) == Decimal("0.050600")
    # A provider whose totals disagree with its parts never prices a negative remainder.
    odd = Usage(input_tokens=100, output_tokens=0, cache_read_input_tokens=400)
    assert odd.uncached_input_tokens == 0
    assert cost_decimal("claude-opus-5-5", odd) == Decimal("0.000080")


def test_cache_rates_follow_the_published_multipliers() -> None:
    # platform.claude.com/docs/en/about-claude/pricing, checked 2026-10-29
    assert PRICES["claude-fable-5-1"].cache_read_per_million == Decimal("0.25")  # 0.025x
    assert PRICES["claude-fable-5-1"].cache_write_per_million == Decimal("12.50")  # 1.25x
    assert PRICES["claude-opus-5-5"].cache_read_per_million == Decimal("0.20")  # 0.05x
    assert PRICES["claude-sonnet-5-5"].cache_read_per_million == Decimal("0.10")  # 0.05x
    assert PRICES["claude-haiku-5-5"].cache_read_per_million == Decimal("0.01")  # 0.1x
    # developers.openai.com/api/docs/pricing, cached input column, checked 2026-10-29
    assert PRICES["gpt-6.1-sol"].cache_read_per_million == Decimal("0.10")
    assert PRICES["gpt-6-luna"].cache_read_per_million == Decimal("0.01")
    assert PRICES["gpt-4o"].cache_read_per_million == Decimal("1.25")
    for family, price in PRICES.items():
        assert price.cache_read_per_million <= price.input_per_million, family
        assert price.cache_write_per_million >= price.input_per_million, family


def test_the_price_row_is_named_and_the_table_is_versioned() -> None:
    assert priced_as("gpt-4o-mini-2024-07-18") == "gpt-4o-mini"
    assert priced_as("claude-opus-5-5") == "claude-opus-5-5"
    assert priced_as("my-azure-deployment") == UNKNOWN_PRICE_ROW
    assert price_row("gpt-5.6") is None
    assert PRICING_VERSION == "2026-10-29"


def test_every_model_the_settings_name_is_in_the_price_table() -> None:
    """A model added to the pipeline without a price row would be charged the most
    expensive row and recorded ``price_row = unknown``: this fails first."""
    settings = Settings(env="test")  # type: ignore[call-arg]
    named = {
        name: getattr(settings, name)
        for name in type(settings).model_fields
        if name.endswith("_model") and name != "embedding_model"
    }
    unpriced = {name: model for name, model in named.items() if not is_known(model)}
    assert named and not unpriced, unpriced
