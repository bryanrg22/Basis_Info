"""W5: search-tool results must turn into citations on the LLM path.

Reproduced 2026-10-15 (REVIEW_AND_PLAN.md A.3 W5, Appendix 2). Three independent
causes in agentic/agents/base_agent.py make _extract_citations_from_messages()
return [] for every real tool result:

1. BaseStageAgent.run() builds the ToolMessage without ``name``; the filter
   requires a name.
2. The registered LangChain tools are named ``*_search_tool``; the filter accepts
   only ``bm25_search`` / ``vector_search`` / ``hybrid_search``.
3. run() serializes the tool's list[dict] result with ``str()`` (Python repr),
   which ``json.loads`` rejects.

Consequence: _determine_confidence() returns 0.3 and needs_review is always set.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import ToolMessage
from pydantic import BaseModel

SAMPLE_RESULT: list[dict] = [
    {
        "chunk_id": "c1",
        "doc_id": "IRS_PUB946",
        "page_span": [12, 12],
        "text": "Section 1245 property ...",
        "score": 1.0,
    }
]


class _Out(BaseModel):
    x: int = 0


@pytest.fixture
def agent():
    from agentic.agents.base_agent import BaseStageAgent

    class _Agent(BaseStageAgent):
        def get_system_prompt(self) -> str:
            return "p"

        def get_output_schema(self):
            return _Out

        def parse_output(self, response, tool_calls):
            return _Out()

    return _Agent("asset")


def _tool_message_as_built_by_run(tool_result, tool_call_id: str = "call_1") -> ToolMessage:
    """Same construction as BaseStageAgent.run() (agentic/agents/base_agent.py)."""
    return ToolMessage(
        content=str(tool_result) if not isinstance(tool_result, str) else tool_result,
        tool_call_id=tool_call_id,
    )


@pytest.mark.regression
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "W5: ToolMessage from run() has no name and str(list) content; the filter "
        "also expects names without the _tool suffix -> 0 citations"
    ),
)
def test_tool_message_as_built_by_run_yields_citation(agent) -> None:
    message = _tool_message_as_built_by_run(SAMPLE_RESULT)
    citations = agent._extract_citations_from_messages([message])
    assert len(citations) >= 1


@pytest.mark.regression
def test_citation_filter_current_behavior_documents_the_three_causes(agent) -> None:
    """Pins today's behavior per cause. Passes now; update it when W5 is fixed."""
    import json

    from agentic.mcp_server.server import get_all_evidence_tools

    # Cause 2: the real tool names carry a _tool suffix the filter does not accept.
    tool_names = {t.name for t in get_all_evidence_tools()}
    assert {
        "bm25_search_tool",
        "vector_search_tool",
        "hybrid_search_tool",
    } <= tool_names
    assert not tool_names & {"bm25_search", "vector_search", "hybrid_search"}

    as_json = json.dumps(SAMPLE_RESULT)
    as_repr = str(SAMPLE_RESULT)

    def count(content: str, name: str | None) -> int:
        msg = ToolMessage(content=content, tool_call_id="call_1", name=name)
        return len(agent._extract_citations_from_messages([msg]))

    # Cause 1: no name (as built by run()) -> dropped even with valid JSON.
    assert count(as_json, None) == 0
    # Cause 2: the real registered name -> dropped.
    assert count(as_json, "hybrid_search_tool") == 0
    # Cause 3: accepted name but Python-repr content -> json.loads fails, dropped.
    assert count(as_repr, "hybrid_search") == 0
    # Only the combination the filter was written for produces a citation.
    assert count(as_json, "hybrid_search") == 1
