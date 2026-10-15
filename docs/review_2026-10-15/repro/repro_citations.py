"""Repro 1: citation collection from tool results in BaseStageAgent.run()."""
import json, asyncio
from langchain_core.messages import ToolMessage
from pydantic import BaseModel
from agentic.agents.base_agent import BaseStageAgent
from agentic.mcp_server.server import get_all_evidence_tools

class Out(BaseModel):
    x: int = 0

class A(BaseStageAgent):
    def get_system_prompt(self): return "p"
    def get_output_schema(self): return Out
    def parse_output(self, response, tool_calls): return Out()

a = A("asset")
sample = [{"chunk_id": "c1", "doc_id": "IRS_PUB946", "page_span": [12, 12], "text": "Section 1245 property...", "score": 1.0}]

print("registered LangChain tool names:", [t.name for t in get_all_evidence_tools()])
print("names accepted by _extract_citations_from_messages: ('bm25_search','vector_search','hybrid_search')")

# Case A: ToolMessage exactly as BaseStageAgent.run() builds it (base_agent.py:363-366): no name
m_a = ToolMessage(content=json.dumps(sample), tool_call_id="call_1")
print("A) as built in run() (no name)           -> citations:", len(a._extract_citations_from_messages([m_a])), "| msg.name =", repr(m_a.name))

# Case B: with the LangChain tool's real name
m_b = ToolMessage(content=json.dumps(sample), tool_call_id="call_1", name="hybrid_search_tool")
print("B) name='hybrid_search_tool' (real name)  -> citations:", len(a._extract_citations_from_messages([m_b])))

# Case C: with the name the filter expects
m_c = ToolMessage(content=json.dumps(sample), tool_call_id="call_1", name="hybrid_search")
print("C) name='hybrid_search' (expected name)   -> citations:", len(a._extract_citations_from_messages([m_c])))

# Case D: content serialized the way run() does it: str(list_of_dicts) (python repr, not JSON)
m_d = ToolMessage(content=str(sample), tool_call_id="call_1", name="hybrid_search")
print("D) name ok but content=str(result)        -> citations:", len(a._extract_citations_from_messages([m_d])), "| content starts:", repr(str(sample)[:30]))
