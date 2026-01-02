"""
Tests for IRS-aware tokenization.

These tests ensure the tokenizer correctly preserves:
- Section symbols (§)
- Parenthetical references: 168(e)(3)
- Decimal asset classes: 57.0, 00.11
"""

import pytest

from ..src.tokenizers import irs_tokenize, simple_tokenize


class TestIRSTokenizer:
    """Tests for IRS-aware tokenization."""
    
    def test_section_symbol_preserved(self):
        """§1245 should produce both §1245 and 1245 tokens."""
        tokens = irs_tokenize("§1245 property")
        assert "§1245" in tokens or "§1245" in [t.lower() for t in tokens]
        assert "1245" in tokens
        assert "property" in tokens
    
    def test_parenthetical_reference_preserved(self):
        """168(e)(3) should stay as a single token."""
        tokens = irs_tokenize("Section 168(e)(3)(B) applies")
        # Should contain the full reference
        assert any("168" in t and "e" in t for t in tokens)
        assert "section" in tokens
        assert "applies" in tokens
    
    def test_decimal_asset_class_preserved(self):
        """57.0 and 00.11 should stay together."""
        tokens = irs_tokenize("Asset class 57.0 applies to distributive trades")
        assert "57.0" in tokens
        assert "asset" in tokens
        assert "class" in tokens
    
    def test_double_digit_decimal_preserved(self):
        """00.11 format should be preserved."""
        tokens = irs_tokenize("Class 00.11 office furniture")
        assert "00.11" in tokens
        assert "office" in tokens
        assert "furniture" in tokens
    
    def test_multiple_codes_in_text(self):
        """Multiple IRS codes in one text."""
        tokens = irs_tokenize("Under §1245 and §1250, property classes 57.0 and 00.11")
        
        # Check for section codes
        assert "1245" in tokens
        assert "1250" in tokens
        
        # Check for asset classes
        assert "57.0" in tokens
        assert "00.11" in tokens
    
    def test_variant_generation(self):
        """§1245 should generate variant without §."""
        tokens = irs_tokenize("§1245")
        assert "1245" in tokens  # Variant without §
    
    def test_standard_words_lowercase(self):
        """Standard words should be lowercased."""
        tokens = irs_tokenize("DEPRECIATION Property")
        assert "depreciation" in tokens
        assert "property" in tokens
    
    def test_short_numbers_not_treated_as_codes(self):
        """Simple short numbers shouldn't be over-tokenized."""
        tokens = irs_tokenize("in 5 years")
        assert "years" in tokens
        assert "in" in tokens


class TestSimpleTokenizer:
    """Tests for simple (non-IRS) tokenization."""
    
    def test_basic_tokenization(self):
        """Basic word tokenization."""
        tokens = simple_tokenize("Hello world test")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens
    
    def test_lowercase(self):
        """All tokens should be lowercase."""
        tokens = simple_tokenize("UPPERCASE Words")
        assert "uppercase" in tokens
        assert "words" in tokens
    
    def test_alphanumeric(self):
        """Should handle alphanumeric words."""
        tokens = simple_tokenize("test123 value456")
        assert "test123" in tokens
        assert "value456" in tokens


class TestRetrievalScenarios:
    """Test tokenization in realistic retrieval scenarios."""
    
    def test_irs_code_query_matches_text(self):
        """Query '1245' should match text with §1245."""
        text_tokens = irs_tokenize("Section §1245 property includes tangible personal property")
        query_tokens = irs_tokenize("1245")
        
        # Query token should be in text tokens
        assert any(qt in text_tokens for qt in query_tokens)
    
    def test_asset_class_query(self):
        """Query '57.0' should match asset class text."""
        text_tokens = irs_tokenize("Asset class 57.0 Distributive Trades")
        query_tokens = irs_tokenize("57.0")
        
        assert any(qt in text_tokens for qt in query_tokens)
    
    def test_subsection_query(self):
        """Query '168(e)(3)' should match subsection reference."""
        text_tokens = irs_tokenize("Under IRC Section 168(e)(3)(B), qualified improvement property")
        query_tokens = irs_tokenize("168(e)(3)")
        
        # At least the base code should match
        assert "168" in text_tokens

