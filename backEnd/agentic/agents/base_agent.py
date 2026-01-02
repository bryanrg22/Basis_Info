"""
Base agent class for all Basis workflow stages.

Every stage agent follows the same pattern:
1. Load study context
2. Search evidence using MCP tools
3. Generate structured output with citations
4. Return evidence-backed result with review flags
"""

from abc import ABC, abstractmethod
from typing import Any, Generic, Optional, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from ..config.llm_providers import get_llm_for_stage
from ..mcp_server.server import get_all_evidence_tools


# =============================================================================
# Type Variables
# =============================================================================

TInput = TypeVar("TInput", bound=BaseModel)
TOutput = TypeVar("TOutput", bound=BaseModel)


# =============================================================================
# Core Schemas
# =============================================================================


class Citation(BaseModel):
    """Evidence citation for any claim."""

    chunk_id: Optional[str] = Field(
        default=None, description="Chunk ID from search result"
    )
    table_id: Optional[str] = Field(
        default=None, description="Table ID if citing a table"
    )
    doc_id: str = Field(..., description="Source document ID")
    page: int = Field(..., description="Page number (1-indexed)")
    excerpt: str = Field(
        ..., max_length=500, description="Relevant excerpt from source"
    )

    def to_reference(self) -> str:
        """Format citation as a reference string."""
        ref = f"{self.doc_id}, p.{self.page}"
        if self.table_id:
            ref += f", {self.table_id}"
        return ref


class StageContext(BaseModel):
    """Context passed to stage agents."""

    study_id: str = Field(..., description="Study identifier")
    property_name: Optional[str] = Field(default=None)
    corpus: str = Field(default="reference", description="Default corpus to search")

    # Available document IDs for this study
    reference_doc_ids: list[str] = Field(
        default_factory=list,
        description="Reference corpus doc IDs (IRS, RSMeans)",
    )
    study_doc_ids: list[str] = Field(
        default_factory=list,
        description="Study corpus doc IDs (appraisals, invoices)",
    )


class AgentOutput(BaseModel, Generic[TOutput]):
    """Standardized agent output with evidence backing."""

    result: Any = Field(..., description="Structured result from agent")
    citations: list[Citation] = Field(
        default_factory=list, description="Evidence citations"
    )
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Confidence score"
    )
    needs_review: bool = Field(
        default=False, description="Flag if engineer review needed"
    )
    review_reason: Optional[str] = Field(
        default=None, description="Reason for review flag"
    )
    raw_response: Optional[str] = Field(
        default=None, description="Raw LLM response for debugging"
    )


# =============================================================================
# Base Stage Agent
# =============================================================================


class BaseStageAgent(ABC, Generic[TInput, TOutput]):
    """
    Abstract base class for stage-specific agents.

    Subclasses implement:
    - get_system_prompt(): Stage-specific instructions
    - get_tools(): Required tools for this stage
    - parse_output(): Extract structured output from agent response
    """

    def __init__(
        self,
        stage_name: str,
        model_override: Optional[str] = None,
    ):
        """
        Initialize the stage agent.

        Args:
            stage_name: Name of the workflow stage
            model_override: Override the default model for this stage
        """
        self.stage_name = stage_name
        self._model_override = model_override
        self._llm: Optional[BaseChatModel] = None

    @property
    def llm(self) -> BaseChatModel:
        """Get the LLM for this agent (lazy loaded)."""
        if self._llm is None:
            self._llm = get_llm_for_stage(self.stage_name)
        return self._llm

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this stage."""
        pass

    @abstractmethod
    def get_output_schema(self) -> type[TOutput]:
        """Return the Pydantic model for structured output."""
        pass

    def get_tools(self) -> list[BaseTool]:
        """
        Return the tools available for this stage.

        Override to customize. Default returns all evidence tools.
        """
        return get_all_evidence_tools()

    @abstractmethod
    def parse_output(
        self,
        response: str,
        tool_calls: list[dict],
    ) -> TOutput:
        """
        Parse agent response into structured output.

        Args:
            response: Raw agent response text
            tool_calls: List of tool calls made during execution

        Returns:
            Structured output matching get_output_schema()
        """
        pass

    def _extract_citations_from_messages(self, messages: list) -> list[Citation]:
        """
        Extract citations from tool message results.

        Args:
            messages: List of messages from agent execution

        Returns:
            List of Citation objects from search results
        """
        import json
        citations = []

        for msg in messages:
            # Look for ToolMessage responses from search tools
            if isinstance(msg, ToolMessage):
                tool_name = getattr(msg, "name", "")

                # Only process search tool results
                if tool_name not in ("bm25_search", "vector_search", "hybrid_search"):
                    continue

                # Parse the tool output
                try:
                    content = msg.content
                    if isinstance(content, str):
                        results = json.loads(content)
                    else:
                        results = content

                    if isinstance(results, list):
                        for result in results[:5]:  # Limit to top 5 per search
                            if isinstance(result, dict):
                                citations.append(Citation(
                                    chunk_id=result.get("chunk_id"),
                                    table_id=result.get("table", {}).get("table_id") if result.get("table") else None,
                                    doc_id=result.get("doc_id", "unknown"),
                                    page=result.get("page_span", [0, 0])[0],
                                    excerpt=result.get("text", "")[:500],
                                ))
                except (json.JSONDecodeError, TypeError):
                    continue

        return citations

    def _determine_confidence(self, citations: list[Citation]) -> float:
        """
        Determine confidence based on evidence found.

        Args:
            citations: List of citations from tool calls

        Returns:
            Confidence score (0.0 - 1.0)
        """
        if not citations:
            return 0.3  # Low confidence without evidence
        if len(citations) >= 3:
            return 0.9  # High confidence with multiple sources
        if len(citations) >= 1:
            return 0.7  # Moderate confidence with some evidence
        return 0.5

    async def run(
        self,
        context: StageContext,
        input_data: TInput,
    ) -> AgentOutput[TOutput]:
        """
        Execute the agent for this stage.

        Args:
            context: Study context with available documents
            input_data: Stage-specific input data

        Returns:
            AgentOutput with structured result, citations, and review flags
        """
        tools = self.get_tools()
        system_prompt = self.get_system_prompt()

        # Create agent using LangGraph's create_react_agent
        agent = create_react_agent(
            model=self.llm,
            tools=tools,
            state_modifier=system_prompt,
        )

        # Build the full input with context
        full_input = self._format_input(context, input_data)

        # Execute agent with messages
        messages = [HumanMessage(content=full_input)]
        result = await agent.ainvoke({"messages": messages})

        # Extract components from result
        output_messages = result.get("messages", [])
        raw_response = ""
        tool_calls = []

        for msg in output_messages:
            if isinstance(msg, AIMessage):
                raw_response = msg.content if isinstance(msg.content, str) else str(msg.content)
                if hasattr(msg, 'tool_calls'):
                    tool_calls.extend(msg.tool_calls or [])

        # Build citations from tool calls
        citations = self._extract_citations_from_messages(output_messages)

        # Parse structured output
        try:
            parsed_output = self.parse_output(raw_response, tool_calls)
        except Exception as e:
            # If parsing fails, flag for review
            return AgentOutput(
                result=None,
                citations=citations,
                confidence=0.2,
                needs_review=True,
                review_reason=f"Failed to parse agent output: {str(e)}",
                raw_response=raw_response,
            )

        # Determine if review needed
        needs_review = len(citations) == 0
        review_reason = "No evidence found to support classification" if needs_review else None

        return AgentOutput(
            result=parsed_output,
            citations=citations,
            confidence=self._determine_confidence(citations),
            needs_review=needs_review,
            review_reason=review_reason,
            raw_response=raw_response,
        )

    def _format_input(self, context: StageContext, input_data: TInput) -> str:
        """
        Format the input for the agent.

        Args:
            context: Study context
            input_data: Stage-specific input

        Returns:
            Formatted input string
        """
        return f"""
## Study Context
- Study ID: {context.study_id}
- Property: {context.property_name or 'N/A'}

## Available Documents
Reference corpus (IRS/RSMeans): {', '.join(context.reference_doc_ids) or 'None loaded'}
Study corpus (property docs): {', '.join(context.study_doc_ids) or 'None loaded'}

## Task Input
{input_data.model_dump_json(indent=2)}

## Instructions
1. Search the reference corpus for relevant IRS guidance
2. Cite specific chunk_ids and page numbers for every claim
3. If no evidence found, flag needs_review=true
4. Output your response as valid JSON matching the output schema
"""

    def run_sync(
        self,
        context: StageContext,
        input_data: TInput,
    ) -> AgentOutput[TOutput]:
        """
        Synchronous wrapper for run().

        Use this when not in an async context.
        """
        import asyncio
        return asyncio.run(self.run(context, input_data))
