"""
MCP Server for Basis Evidence Layer.

Exposes retrieval tools via Model Context Protocol for agent consumption.
Can run as a standalone server or be used to get LangChain-compatible tools.
"""

import json
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.stdio import stdio_server

from .schemas import (
    SearchInput,
    HybridSearchInput,
    GetTableInput,
    GetChunkInput,
)

# Add evidence_layer to path for imports
EVIDENCE_LAYER_PATH = Path(__file__).parent.parent.parent / "evidence_layer"
if str(EVIDENCE_LAYER_PATH) not in sys.path:
    sys.path.insert(0, str(EVIDENCE_LAYER_PATH))

from src.retrieval import (
    bm25_search,
    vector_search,
    hybrid_search,
    get_table,
    get_chunk,
)


# =============================================================================
# MCP Server Definition
# =============================================================================

server = Server("basis-evidence")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Register available evidence tools."""
    return [
        Tool(
            name="bm25_search",
            description="""BM25 lexical search for exact token matching.

Best for:
- IRS codes: "1245", "168(e)(3)", "1250"
- Asset class numbers: "57.0", "00.11"
- Exact phrases: "tangible personal property"
- Section references: "Section 1245 property"

Returns chunks with page provenance for citations.""",
            inputSchema=SearchInput.model_json_schema(),
        ),
        Tool(
            name="vector_search",
            description="""Semantic vector search for conceptual similarity.

Best for:
- Paraphrases: "equipment used in business" matches "tangible personal property"
- Conceptual queries: "how to depreciate building improvements"
- Fuzzy matching when exact terms are unknown

Returns chunks with page provenance for citations.""",
            inputSchema=SearchInput.model_json_schema(),
        ),
        Tool(
            name="hybrid_search",
            description="""Combined BM25 + vector search with score fusion.

Recommended for general queries where both lexical and semantic matching help.
Automatically expands table surrogate hits to include full table data.

Use bm25_weight to tune:
- 0.0 = vector only (semantic)
- 1.0 = BM25 only (lexical)
- 0.5 = balanced (default)

Returns chunks with page provenance and expanded tables.""",
            inputSchema=HybridSearchInput.model_json_schema(),
        ),
        Tool(
            name="get_table",
            description="""Fetch a structured table by ID.

Tables are NEVER chunked in Basis - stored as complete JSON objects.
Use when you need exact table data (headers, rows, specific values).

Returns full table with headers, rows, caption, and markdown representation.""",
            inputSchema=GetTableInput.model_json_schema(),
        ),
        Tool(
            name="get_chunk",
            description="""Fetch a specific chunk by ID.

Use to retrieve full context of a search hit.
If the chunk is a table surrogate, returns expanded table data.

Returns chunk text, page span, section path, and optional table expansion.""",
            inputSchema=GetChunkInput.model_json_schema(),
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute evidence tool and return results as JSON."""

    result: Any = None

    if name == "bm25_search":
        inp = SearchInput(**arguments)
        result = bm25_search(
            doc_id=inp.doc_id,
            query=inp.query,
            top_k=inp.top_k,
            corpus=inp.corpus,
            study_id=inp.study_id,
        )

    elif name == "vector_search":
        inp = SearchInput(**arguments)
        result = vector_search(
            doc_id=inp.doc_id,
            query=inp.query,
            top_k=inp.top_k,
            corpus=inp.corpus,
            study_id=inp.study_id,
        )

    elif name == "hybrid_search":
        inp = HybridSearchInput(**arguments)
        result = hybrid_search(
            doc_id=inp.doc_id,
            query=inp.query,
            top_k=inp.top_k,
            corpus=inp.corpus,
            study_id=inp.study_id,
            bm25_weight=inp.bm25_weight,
        )

    elif name == "get_table":
        inp = GetTableInput(**arguments)
        result = get_table(
            doc_id=inp.doc_id,
            table_id=inp.table_id,
            corpus=inp.corpus,
            study_id=inp.study_id,
        )

    elif name == "get_chunk":
        inp = GetChunkInput(**arguments)
        result = get_chunk(
            doc_id=inp.doc_id,
            chunk_id=inp.chunk_id,
            corpus=inp.corpus,
            study_id=inp.study_id,
        )

    else:
        raise ValueError(f"Unknown tool: {name}")

    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


# =============================================================================
# LangChain Tool Helpers
# =============================================================================

def get_all_evidence_tools():
    """Get all evidence tools as LangChain tools."""
    from .tools import (
        bm25_search_tool,
        vector_search_tool,
        hybrid_search_tool,
        get_table_tool,
        get_chunk_tool,
    )
    return [
        bm25_search_tool,
        vector_search_tool,
        hybrid_search_tool,
        get_table_tool,
        get_chunk_tool,
    ]


def get_search_tools():
    """Get only search tools (no direct fetch)."""
    from .tools import (
        bm25_search_tool,
        vector_search_tool,
        hybrid_search_tool,
    )
    return [
        bm25_search_tool,
        vector_search_tool,
        hybrid_search_tool,
    ]


def get_reference_corpus_tools():
    """Get tools pre-configured for reference corpus (IRS/RSMeans)."""
    # These are the same tools - corpus is specified at call time
    return get_all_evidence_tools()


# =============================================================================
# Server Entry Point
# =============================================================================

async def main():
    """Run MCP server over stdio."""
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
