"""Tests for MCP server and tools."""

import pytest

from agentic.mcp_server.schemas import (
    SearchInput,
    HybridSearchInput,
    GetTableInput,
    GetChunkInput,
)


class TestSchemas:
    """Tests for MCP input/output schemas."""

    def test_search_input_defaults(self):
        """Test SearchInput default values."""
        inp = SearchInput(doc_id="TEST_DOC", query="test query")
        assert inp.top_k == 10
        assert inp.corpus == "reference"
        assert inp.study_id is None

    def test_search_input_validation(self):
        """Test SearchInput validation."""
        inp = SearchInput(doc_id="TEST_DOC", query="test", top_k=25)
        assert inp.top_k == 25

        # Test top_k bounds
        with pytest.raises(ValueError):
            SearchInput(doc_id="TEST_DOC", query="test", top_k=100)

    def test_hybrid_search_input(self):
        """Test HybridSearchInput with weight."""
        inp = HybridSearchInput(
            doc_id="TEST_DOC",
            query="test",
            bm25_weight=0.7,
        )
        assert inp.bm25_weight == 0.7

    def test_get_table_input(self):
        """Test GetTableInput schema."""
        inp = GetTableInput(
            doc_id="IRS_PUB946",
            table_id="IRS_PUB946_p45_t0",
        )
        assert inp.corpus == "reference"

    def test_get_chunk_input(self):
        """Test GetChunkInput schema."""
        inp = GetChunkInput(
            doc_id="IRS_PUB946",
            chunk_id="IRS_PUB946_chunk_15",
            corpus="study",
            study_id="STUDY_001",
        )
        assert inp.study_id == "STUDY_001"
