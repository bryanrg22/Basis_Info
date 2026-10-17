"""The price table, cost of a call, and the pre-call estimate."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from basis.llm.cost import (
    IMAGE_TOKENS,
    PRICES,
    UNKNOWN_MODEL_PRICE,
    Price,
    cost_decimal,
    cost_usd,
    estimate_tokens,
    estimate_usd,
    is_known,
    price_for,
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
    assert PRICES["gpt-5.6-luna"] == Price(Decimal("0.20"), Decimal("1.20"))
    assert PRICES["gpt-5.6-terra"] == Price(Decimal("2.00"), Decimal("12.00"))
    assert PRICES["gpt-5.6-sol"] == Price(Decimal("4.00"), Decimal("20.00"))
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
