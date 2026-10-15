"""IRS-aware tokenization (BUILD_PLAN 6.1 ``unit/retrieval/test_tokenizer.py``).

The 14 tests of ``evidence_layer/tests/test_tokenizers.py`` are migrated unchanged in the
first three classes (only the import differs); the last class covers what BUILD_PLAN
adds: hyphenated codes survive, and the exact legacy outputs for representative inputs
are pinned so a tokenizer change is a visible test change.
"""

from __future__ import annotations

import pytest

from basis.retrieval.tokenizer import get_tokenizer, irs_tokenize, simple_tokenize


class TestIRSTokenizer:
    """Tests for IRS-aware tokenization."""

    def test_section_symbol_preserved(self) -> None:
        """§1245 should produce both §1245 and 1245 tokens."""
        tokens = irs_tokenize("§1245 property")
        assert "§1245" in tokens or "§1245" in [t.lower() for t in tokens]
        assert "1245" in tokens
        assert "property" in tokens

    def test_parenthetical_reference_preserved(self) -> None:
        """168(e)(3) should stay as a single token."""
        tokens = irs_tokenize("Section 168(e)(3)(B) applies")
        assert any("168" in t and "e" in t for t in tokens)
        assert "section" in tokens
        assert "applies" in tokens

    def test_decimal_asset_class_preserved(self) -> None:
        """57.0 and 00.11 should stay together."""
        tokens = irs_tokenize("Asset class 57.0 applies to distributive trades")
        assert "57.0" in tokens
        assert "asset" in tokens
        assert "class" in tokens

    def test_double_digit_decimal_preserved(self) -> None:
        """00.11 format should be preserved."""
        tokens = irs_tokenize("Class 00.11 office furniture")
        assert "00.11" in tokens
        assert "office" in tokens
        assert "furniture" in tokens

    def test_multiple_codes_in_text(self) -> None:
        """Multiple IRS codes in one text."""
        tokens = irs_tokenize("Under §1245 and §1250, property classes 57.0 and 00.11")
        assert "1245" in tokens
        assert "1250" in tokens
        assert "57.0" in tokens
        assert "00.11" in tokens

    def test_variant_generation(self) -> None:
        """§1245 should generate variant without §."""
        tokens = irs_tokenize("§1245")
        assert "1245" in tokens

    def test_standard_words_lowercase(self) -> None:
        """Standard words should be lowercased."""
        tokens = irs_tokenize("DEPRECIATION Property")
        assert "depreciation" in tokens
        assert "property" in tokens

    def test_short_numbers_not_treated_as_codes(self) -> None:
        """Simple short numbers shouldn't be over-tokenized."""
        tokens = irs_tokenize("in 5 years")
        assert "years" in tokens
        assert "in" in tokens


class TestSimpleTokenizer:
    """Tests for simple (non-IRS) tokenization."""

    def test_basic_tokenization(self) -> None:
        tokens = simple_tokenize("Hello world test")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_lowercase(self) -> None:
        tokens = simple_tokenize("UPPERCASE Words")
        assert "uppercase" in tokens
        assert "words" in tokens

    def test_alphanumeric(self) -> None:
        tokens = simple_tokenize("test123 value456")
        assert "test123" in tokens
        assert "value456" in tokens


class TestRetrievalScenarios:
    """Test tokenization in realistic retrieval scenarios."""

    def test_irs_code_query_matches_text(self) -> None:
        """Query '1245' should match text with §1245."""
        text_tokens = irs_tokenize("Section §1245 property includes tangible personal property")
        query_tokens = irs_tokenize("1245")
        assert any(qt in text_tokens for qt in query_tokens)

    def test_asset_class_query(self) -> None:
        """Query '57.0' should match asset class text."""
        text_tokens = irs_tokenize("Asset class 57.0 Distributive Trades")
        query_tokens = irs_tokenize("57.0")
        assert any(qt in text_tokens for qt in query_tokens)

    def test_subsection_query(self) -> None:
        """Query '168(e)(3)' should match subsection reference."""
        text_tokens = irs_tokenize("Under IRC Section 168(e)(3)(B), qualified improvement property")
        irs_tokenize("168(e)(3)")
        assert "168" in text_tokens


class TestCodesSurvive:
    """BUILD_PLAN 6.1: 168(e)(3), 57.0, 1245 and hyphenated codes survive."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("§1245 property depreciation", ["§1245", "1245", "property", "depreciation"]),
            ("Section 168(e)(3)(B)", ["168(e)(3)(b)", "168", "section"]),
            ("Asset class 57.0", ["57.0", "asset", "class"]),
            ("in 5 years", ["in", "years"]),
        ],
    )
    def test_exact_outputs_are_pinned(self, text: str, expected: list[str]) -> None:
        assert irs_tokenize(text) == expected

    def test_exact_subsection_matches_itself(self) -> None:
        assert "168(e)(3)" in irs_tokenize("recovery classes under section 168(e)(3)")
        assert "168(e)(3)" in irs_tokenize("Section 168(e)(3) groups property")

    @pytest.mark.parametrize(
        ("text", "code"),
        [
            ("Rev. Proc. 87-56 lists the asset classes", "87-56"),
            ("Using Table A-1", "a-1"),
            ("Table B-1. Illustrative component treatment", "b-1"),
            ("Treas. Reg. 1.263(a)-3 repairs", "1.263(a)-3"),
        ],
    )
    def test_hyphenated_codes_survive(self, text: str, code: str) -> None:
        assert code in irs_tokenize(text)

    def test_hyphenated_words_are_not_codes(self) -> None:
        tokens = irs_tokenize("tack-down carpet, 10-year property, SYN-MLS-0001")
        assert "10-year" not in tokens and "mls-0001" not in tokens
        assert {"tack", "down", "carpet", "year", "property"} <= set(tokens)

    def test_hyphen_rule_only_adds_tokens(self) -> None:
        # Without the hyphen the legacy outputs are unchanged.
        assert irs_tokenize("Rev. Proc. 87 56") == ["rev", "proc"]
        assert irs_tokenize("Rev. Proc. 87-56") == ["87-56", "rev", "proc"]

    def test_empty_and_punctuation_only(self) -> None:
        assert irs_tokenize("") == []
        assert irs_tokenize(" -- , . ") == []

    def test_get_tokenizer(self) -> None:
        assert get_tokenizer("irs") is irs_tokenize
        assert get_tokenizer("rsmeans") is irs_tokenize
        assert get_tokenizer("appraisal") is simple_tokenize
